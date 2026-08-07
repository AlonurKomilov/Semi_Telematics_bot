"""warehouse reader facade.

Thin layer that the API routes call.  Inspects the
``WAREHOUSE_READS_ENABLED`` config flag and either:

  * **flag ON**  → returns rows from the per-tenant warehouse mixin
    (fast: ~10-30 ms; no external network).
  * **flag OFF** → falls back to live Samsara (legacy path; same
    latency as before this phase landed).

The fallback is the safety net that lets us ship the ingestor + backfill
to production *before* flipping the flag.  Once we've verified shadow
reads match within tolerance for ~24 h, ops sets
``WAREHOUSE_READS_ENABLED=1`` and dashboard p50 drops from 800-2000 ms
to <120 ms (per plan.md C verification).

Live-GPS map (``/api/location/vehicles/live``) deliberately does NOT
go through this module \u2014 it stays direct-to-Samsara per the locked
plan decision.
"""

from __future__ import annotations

import logging
from typing import Any

from infra import config
from infra.services import get_tenant_db as _real_get_tenant_db

logger = logging.getLogger(__name__)


async def get_tenant_db(account_id: int):
    """Tolerant wrapper around ``infra.services.get_tenant_db``.

    The platform router asserts when not yet initialised — that's a
    legitimate state during boot and also during unit tests that
    monkeypatch only the live-Samsara path. Treat both as "no tenant
    available" so the caller can fall back to live Samsara instead of
    raising.
    """
    try:
        return await _real_get_tenant_db(account_id)
    except (AssertionError, RuntimeError):
        return None


def _enabled() -> bool:
    """Single source of truth for the cutover flag.  Wrapped so tests
    can monkeypatch ``core.config.WAREHOUSE_READS_ENABLED`` freely."""
    return bool(getattr(config, "WAREHOUSE_READS_ENABLED", False))

# A warehouse row that is PRESENT but ancient is worse than an empty
# one: the empty case falls back loudly, the stale case used to serve
# a 43-hour-old fleet as "current" with nothing amiss (2026-07-27
# outage).  Readers therefore fall back on AGE, not just emptiness --
# Contract 2 (docs/architecture/warehouse.md).  30 min
# matches the operator console's ingest-freshness card.
_STATE_STALE_MIN = 30.0


def _rows_are_stale(rows, sla_min, *keys):
    """True when the freshest timestamp across ``rows`` (first present
    key per row wins) is older than ``sla_min`` -- or unknowable."""
    from capabilities.data_lifecycle.staleness import freshest, is_stale

    newest = None
    for r in rows:
        for k in keys:
            v = r.get(k)
            if v:
                newest = freshest(newest, str(v))
                break
    return is_stale(newest, sla_min)


def _warehouse_row_to_overview(row: dict[str, Any]) -> dict[str, Any]:
    """Reshape a ``vehicle_state`` row back into the nested layout the
    live ``client.get_vehicles_overview()`` produces, so downstream
    ``_simplify`` / ``_normalize_detail`` helpers in the fleet routes
    continue to work unchanged when the flag flips.

    Lossy on fields the warehouse doesn't track (VIN, make/model,
    license plate, fault details) \u2014 those keep ``"N/A"``-shaped
    placeholders so JSON contracts stay stable.
    """
    odometer_miles = row.get("odometer_mi")
    odometer_time = row.get("odometer_time")
    engine_hours = row.get("engine_hours")
    engine_hours_time = row.get("engine_hours_time")
    return {
        "id":   row.get("vehicle_id"),
        "name": row.get("vehicle_name") or "",
        "_org": row.get("company_code") or "",
        "vin":            "N/A",
        "make":           "N/A",
        "model":          "N/A",
        "year":           "N/A",
        "license_plate":  "N/A",
        "location": {
            "latitude":           row.get("lat"),
            "longitude":          row.get("lon"),
            "speedMilesPerHour":  row.get("speed_mph"),
            "speed":              row.get("speed_mph"),
            "heading":            row.get("heading"),
            "address":            row.get("address") or "",
            "reverseGeo":         {"formattedLocation": row.get("address") or ""},
            "engineStates":       {"value": row.get("engine_state") or ""},
            "time":               row.get("captured_at") or "",
        },
        "fuel":      {"value": row.get("fuel_pct")} if row.get("fuel_pct") is not None else {},
        "def_level": {"value": row.get("def_pct")} if row.get("def_pct") is not None else {},
        # Odometer surfaced as a nested dict so ``_extract_odometer``
        # in the vehicles route can consume it without knowing whether
        # the row came from the warehouse or a live Samsara fallback.
        "odometer": {"miles": odometer_miles, "time": odometer_time} if odometer_miles is not None else {},
        # Engine hours follows the same pattern — nested dict so
        # ``_extract_engine_hours`` works the same against warehouse
        # rows and a hypothetical live shape.
        "engine_hours_reading": (
            {"hours": engine_hours, "time": engine_hours_time}
            if engine_hours is not None else {}
        ),
        # Approximate fault payload \u2014 enough for ``_extract_fault_count``
        # to return the warehouse-tracked count without reshaping.
        "fault_codes": {
            "j1939": {
                "diagnosticTroubleCodes": [{}] * int(row.get("fault_count") or 0),
                "checkEngineLights": (
                    {"red": True} if int(row.get("dtc_critical_count") or 0) else {}
                ),
            }
        },
    }


def _registry_only_overview(v: Any) -> dict[str, Any]:
    """Synthesize a fleet-overview dict for a registry vehicle that has
    NO live-state match (a trailer, or a truck without telematics).

    Carries the static spec the operator entered plus a
    ``_no_telemetry`` marker so ``_simplify`` renders status
    ``"no_telemetry"`` instead of mis-classifying a GPS-less row as
    "stopped".  Empty location/fuel/fault dicts keep the downstream
    extractors happy (they all tolerate ``{}``)."""
    return {
        "id":   f"registry:{v.id}",
        "name": v.unit_number,
        "_org": v.company_code,
        "vin":           v.vin or "N/A",
        "make":          v.make or "N/A",
        "model":         v.model or "N/A",
        "year":          v.year if v.year is not None else "N/A",
        "license_plate": v.plate_number or "N/A",
        "location": {},
        "fuel": {}, "def_level": {},
        "odometer": {}, "engine_hours_reading": {},
        "fault_codes": {},
        "vehicle_type": v.vehicle_type,
        "source": v.source,
        "_registry_id": v.id,
        "_no_telemetry": True,
    }


def merge_registry_with_live(
    registry: list[Any],
    live: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Overlay the vehicle registry (SSOT) onto the live-state list.

    The registry is the spine: every active registry vehicle appears,
    enriched with its live telematics when a match exists, or as a
    ``_no_telemetry`` row when it doesn't (trailers, manual trucks).
    Live vehicles NOT yet in the registry are still appended so nothing
    Samsara reports ever disappears before the ingestor registers it.

    Match priority: ``telematics_ref`` → live ``id`` (exact), else
    ``(company_code, unit_number)`` case-insensitive.  Each live row's
    ``vehicle_type``/``source`` are taken from the registry entry.
    """
    by_id: dict[str, dict] = {}
    by_unit: dict[tuple[str, str], dict] = {}
    for ov in live:
        vid = str(ov.get("id") or "")
        if vid:
            by_id[vid] = ov
        key = (str(ov.get("_org") or "").lower(),
               str(ov.get("name") or "").lower())
        by_unit[key] = ov

    out: list[dict[str, Any]] = []
    consumed: set[int] = set()
    for v in registry:
        match = None
        if v.telematics_ref and v.telematics_ref in by_id:
            match = by_id[v.telematics_ref]
        else:
            match = by_unit.get(
                (v.company_code.lower(), v.unit_number.lower()),
            )
        if match is not None:
            consumed.add(id(match))
            enriched = dict(match)
            enriched["vehicle_type"] = v.vehicle_type
            enriched["source"] = v.source
            enriched["_registry_id"] = v.id
            out.append(enriched)
        else:
            out.append(_registry_only_overview(v))

    # Append live vehicles the registry hasn't caught yet (safety —
    # after backfill + ongoing ingest this is empty in steady state).
    for ov in live:
        if id(ov) not in consumed:
            extra = dict(ov)
            extra.setdefault("vehicle_type", "truck")
            extra.setdefault("source", "samsara")
            out.append(extra)
    return out


async def get_current_vehicles(
    account_id: int,
    *,
    company: str | None = None,
    vehicle_nums: list[str] | None = None,
    samsara_fallback=None,
) -> list[dict[str, Any]]:
    """Return the current per-vehicle snapshot.

    Shape mirrors the live ``client.get_vehicles_overview()`` keys the
    callers already handle, so flipping the flag is invisible to them.

    ``samsara_fallback`` is a no-arg async callable that yields the
    legacy live-Samsara response.  Required so this module stays free
    of imports from ``features.vehicles.warehouse.service`` (which would
    create a cycle once that module starts calling us).
    """
    if not _enabled():
        if samsara_fallback is None:
            return []
        return await samsara_fallback()

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return await samsara_fallback() if samsara_fallback else []
    rows = await tenant.get_vehicle_state(
        account_id, company=company, vehicle_nums=vehicle_nums,
    )
    # Empty warehouse + a real fallback usually means the ingestor
    # hasn't run yet (cold start).  Prefer live data over an empty
    # dashboard during that window.
    if not rows and samsara_fallback is not None:
        logger.info("warehouse cold (vehicle_state empty) for acct=%d \u2014 using live Samsara", account_id)
        return await samsara_fallback()
    if rows and samsara_fallback is not None and _rows_are_stale(
            rows, _STATE_STALE_MIN, "source_ts", "captured_at"):
        logger.warning(
            "warehouse STALE (vehicle_state age > %.0f min) for acct=%d "
            "-- using live Samsara", _STATE_STALE_MIN, account_id)
        return await samsara_fallback()
    return [_warehouse_row_to_overview(r) for r in rows]


async def get_safety_events(
    account_id: int,
    *,
    days: int = 7,
    event_type: str | None = None,
    vehicle_id: str | None = None,
    driver_id: str | None = None,
    samsara_fallback=None,
    include_raw: bool = True,
) -> list[dict[str, Any]]:
    """Return safety events from the warehouse, or fall back to live.

    *include_raw* (passthrough to the DB layer) — set False from
    list-view callers (dashboard `/safety/events`) to skip the per-row
    ``json.loads(raw_json)``.  Alerting + reporting flows keep the
    default True because they need the full live-Samsara event shape.
    """
    if not _enabled():
        if samsara_fallback is None:
            return []
        return await samsara_fallback()

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return await samsara_fallback() if samsara_fallback else []
    rows = await tenant.get_safety_events_warehouse(
        account_id,
        days=days,
        event_type=event_type,
        vehicle_id=vehicle_id,
        driver_id=driver_id,
        include_raw=include_raw,
    )
    # Deliberately NO age-based fallback here: an event's age is a
    # fact about the WORLD (a quiet fleet has old events), not about
    # the pipe.  Pipe health for this sparse feed is the watchdog's
    # job (expect_rows=False + the ingest ledger).
    if not rows and samsara_fallback is not None:
        return await samsara_fallback()
    return rows


async def get_driver_efficiency_window(
    account_id: int,
    *,
    days: int = 7,
    driver_id: str | None = None,
    samsara_fallback=None,
) -> list[dict[str, Any]]:
    """Aggregate efficiency over a trailing window.  Same cold-start
    fallback semantics as the other readers."""
    if not _enabled():
        if samsara_fallback is None:
            return []
        return await samsara_fallback()

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return await samsara_fallback() if samsara_fallback else []
    rows = await tenant.get_driver_efficiency_window(
        account_id, days=days, driver_id=driver_id,
    )
    if not rows and samsara_fallback is not None:
        return await samsara_fallback()
    if rows and samsara_fallback is not None and _rows_are_stale(
            rows, 2 * 24 * 60.0, "source_ts", "day"):
        logger.warning(
            "warehouse STALE (driver_efficiency newest day > 2d) for "
            "acct=%d -- using live Samsara", account_id)
        return await samsara_fallback()
    return rows


async def get_vehicle_state_hour(
    account_id: int,
    *,
    vehicle_id: str | None = None,
    hours: int = 168,
) -> list[dict[str, Any]]:
    """Per-vehicle hourly roll-up window (VehicleDetail
    timeline).  Always reads from the warehouse: this data has no
    live-Samsara equivalent (it's an aggregation produced by the
    fan-out job).  Returns an empty list when the flag is off or the
    tenant DB isn't initialised yet so callers can render an empty
    chart instead of erroring out."""
    if not _enabled():
        return []
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    return await tenant.get_vehicle_state_hour(
        account_id, vehicle_id=vehicle_id, hours=hours,
    )


async def get_vehicle_state_day(
    account_id: int,
    *,
    vehicle_id: str | None = None,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Per-vehicle daily roll-up window.  Same fall-back semantics as
    the hourly reader: empty list when the flag is off or the table
    hasn't been populated yet."""
    if not _enabled():
        return []
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    return await tenant.get_vehicle_state_day(
        account_id, vehicle_id=vehicle_id, days=days,
    )


async def get_vehicle_usage_summary(
    account_id: int,
    vehicle_id: str,
    *,
    days: int = 30,
) -> dict[str, Any]:
    """Vehicle-level usage summary (miles, drive_h, idle_h,
    utilization %, cost-per-mile) over the window.  Returns a
    zero-filled shape when no data exists rather than ``None`` so
    callers can render the cards unconditionally."""
    zero = {
        "vehicle_id":       vehicle_id,
        "vehicle_name":     "",
        "days":             int(days),
        "days_with_data":   0,
        "total_miles":      0.0,
        "drive_hours":      0.0,
        "idle_hours":       0.0,
        "utilization_pct":  0.0,
        "max_speed_mph":    0.0,
        "avg_fuel_pct":     0.0,
        "harsh_events":     0,
        "total_cost":       0.0,
        "work_order_count": 0,
        "cost_per_mile":    None,
    }
    if not _enabled():
        return zero
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return zero
    return await tenant.get_vehicle_usage_summary(
        account_id, vehicle_id, days=days,
    )


async def get_account_utilization_summary(
    account_id: int,
    *,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Fleet-wide per-vehicle utilization summary.  Empty list when
    the flag is off or the daily table is still cold."""
    if not _enabled():
        return []
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    return await tenant.get_account_utilization_summary(
        account_id, days=days,
    )


# ── vehicle health snapshot ────────────────────────────────


async def get_vehicle_health(
    account_id: int,
    *,
    company: str | None = None,
    vehicle_nums: list[str] | None = None,
    samsara_fallback=None,
) -> list[dict[str, Any]]:
    """Return the current vehicle-health snapshot.

    Rows mirror the live ``client.get_vehicle_health()`` shape (the
    ingestor stores the per-vehicle dict verbatim in ``raw_json``).
    Falls back to live Samsara when the flag is off, when the tenant DB
    is unavailable, or on a cold-start empty warehouse.
    """
    if not _enabled():
        if samsara_fallback is None:
            return []
        return await samsara_fallback()

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return await samsara_fallback() if samsara_fallback else []
    rows = await tenant.get_vehicle_health_live(
        account_id, company=company, vehicle_nums=vehicle_nums,
    )
    if not rows and samsara_fallback is not None:
        logger.info("warehouse cold (vehicle_health_live empty) for acct=%d \u2014 using live Samsara", account_id)
        return await samsara_fallback()
    return rows


# ── vehicles with faults / critical faults ────────────────


async def get_vehicle_fault_live(
    account_id: int,
    vehicle_name: str,
) -> dict[str, Any] | None:
    """Read the fault snapshot (full DTC details) for one vehicle.

    Returns the parsed Samsara-shape dict including
    ``fault_codes.j1939.diagnosticTroubleCodes`` with real
    ``spnDescription`` / ``fmiDescription`` — so /vehicles/{name}/faults
    can render real names instead of the empty-dict placeholders the
    ``vehicle_state`` row only carries the count for.

    Returns None when the warehouse flag is off, the tenant DB isn't
    available, or no fault snapshot exists for the vehicle.
    """
    if not _enabled():
        return None
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return None
    try:
        return await tenant.get_vehicle_fault_live_by_name(
            account_id, vehicle_name,
        )
    except AttributeError:
        # Older mixin without the helper — graceful no-op.
        return None


async def get_vehicles_with_faults(
    account_id: int,
    *,
    company: str | None = None,
    samsara_fallback=None,
) -> tuple[list[dict[str, Any]], int, dict[str, dict[str, int]]]:
    """Return ``(faulted, total, breakdown)`` mirroring live shape."""
    if not _enabled():
        if samsara_fallback is None:
            return [], 0, {}
        return await samsara_fallback()

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return await samsara_fallback() if samsara_fallback else ([], 0, {})
    faulted, total, breakdown = await tenant.get_vehicles_with_faults_warehouse(
        account_id, company=company,
    )
    # Cold start: ``vehicle_state`` empty means ingest hasn't run.
    # An empty faulted list is *not* a cold-start signal — a healthy
    # fleet legitimately has zero faults — so we key cold-start off
    # ``total == 0`` instead.
    if total == 0 and samsara_fallback is not None:
        logger.info("warehouse cold (vehicle_state empty) for acct=%d \u2014 using live faults", account_id)
        return await samsara_fallback()
    return faulted, total, breakdown


# ── fleet weather ──────────────────────────────────────────


async def get_fleet_weather(
    account_id: int,
    *,
    company: str | None = None,
    samsara_fallback=None,
) -> list[dict[str, Any]]:
    if not _enabled():
        if samsara_fallback is None:
            return []
        return await samsara_fallback()

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return await samsara_fallback() if samsara_fallback else []
    rows = await tenant.get_weather_live(account_id, company=company)
    if not rows and samsara_fallback is not None:
        logger.info("warehouse cold (weather_live empty) for acct=%d \u2014 using live", account_id)
        return await samsara_fallback()
    return rows


# ── fleet efficiency ───────────────────────────────────────


async def get_fleet_efficiency(
    account_id: int,
    *,
    days: int = 7,
    company: str | None = None,
    samsara_fallback=None,
) -> list[dict[str, Any]]:
    """Return the windowed combined-efficiency payload.

    The ingestor only stores the *all-companies* (``company_code=""``)
    variant; per-company filtering happens in Python so we don't
    multiply the snapshot rows by company count.
    """
    if not _enabled():
        if samsara_fallback is None:
            return []
        return await samsara_fallback()

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return await samsara_fallback() if samsara_fallback else []
    payload = await tenant.get_efficiency_live(
        account_id, window_days=days, company_code="",
    )
    if not payload and samsara_fallback is not None:
        logger.info("warehouse cold (efficiency_live empty) for acct=%d days=%d \u2014 using live", account_id, days)
        return await samsara_fallback()
    if company:
        payload = [r for r in payload if (r.get("_org") or r.get("company_code")) == company]
    return payload


async def get_geofences(
    account_id: int,
    *,
    company: str | None = None,
    samsara_fallback=None,
) -> list[dict[str, Any]]:
    """Return cached geofence definitions, optionally filtered by company.

    Cold-start safety: if the cache is empty and a ``samsara_fallback``
    is provided, fall back to live Samsara so callers never see an
    empty list during ingestor warm-up.
    """
    if not _enabled():
        if samsara_fallback is None:
            return []
        return await samsara_fallback()

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return await samsara_fallback() if samsara_fallback else []
    rows = await tenant.get_geofence_definitions(account_id, company=company)
    if not rows and samsara_fallback is not None:
        logger.info("warehouse cold (geofence_definitions empty) for acct=%d \u2014 using live", account_id)
        return await samsara_fallback()
    return rows
