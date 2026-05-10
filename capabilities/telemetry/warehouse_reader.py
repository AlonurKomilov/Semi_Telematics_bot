"""Phase C — warehouse reader facade.

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


def _warehouse_row_to_overview(row: dict[str, Any]) -> dict[str, Any]:
    """Reshape a ``vehicle_state`` row back into the nested layout the
    live ``client.get_fleet_overview()`` produces, so downstream
    ``_simplify`` / ``_normalize_detail`` helpers in the fleet routes
    continue to work unchanged when the flag flips.

    Lossy on fields the warehouse doesn't track (VIN, make/model,
    license plate, fault details) \u2014 those keep ``"N/A"``-shaped
    placeholders so JSON contracts stay stable.
    """
    odometer_miles = row.get("odometer_mi")
    odometer_time = row.get("odometer_time")
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


async def get_current_vehicles(
    account_id: int,
    *,
    company: str | None = None,
    vehicle_nums: list[str] | None = None,
    samsara_fallback=None,
) -> list[dict[str, Any]]:
    """Return the current per-vehicle snapshot.

    Shape mirrors the live ``client.get_fleet_overview()`` keys the
    callers already handle, so flipping the flag is invisible to them.

    ``samsara_fallback`` is a no-arg async callable that yields the
    legacy live-Samsara response.  Required so this module stays free
    of imports from ``capabilities.telemetry.service`` (which would
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
    return [_warehouse_row_to_overview(r) for r in rows]


async def get_safety_events(
    account_id: int,
    *,
    days: int = 7,
    event_type: str | None = None,
    vehicle_id: str | None = None,
    driver_id: str | None = None,
    samsara_fallback=None,
) -> list[dict[str, Any]]:
    """Return safety events from the warehouse, or fall back to live."""
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
    )
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
    return rows


async def get_vehicle_telemetry_hourly(
    account_id: int,
    *,
    vehicle_id: str | None = None,
    hours: int = 168,
) -> list[dict[str, Any]]:
    """Per-vehicle hourly roll-up window (Phase E25 — VehicleDetail
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
    return await tenant.get_vehicle_telemetry_hourly(
        account_id, vehicle_id=vehicle_id, hours=hours,
    )


# ── Phase 2 — vehicle health snapshot ────────────────────────────────


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
    rows = await tenant.get_vehicle_health_snapshots(
        account_id, company=company, vehicle_nums=vehicle_nums,
    )
    if not rows and samsara_fallback is not None:
        logger.info("warehouse cold (vehicle_health_snapshot empty) for acct=%d \u2014 using live Samsara", account_id)
        return await samsara_fallback()
    return rows


# ── Phase 2 — vehicles with faults / critical faults ────────────────


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


# ── Phase 2 — fleet weather ──────────────────────────────────────────


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
    rows = await tenant.get_fleet_weather_snapshots(account_id, company=company)
    if not rows and samsara_fallback is not None:
        logger.info("warehouse cold (fleet_weather_snapshot empty) for acct=%d \u2014 using live", account_id)
        return await samsara_fallback()
    return rows


# ── Phase 2 — fleet efficiency ───────────────────────────────────────


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
    payload = await tenant.get_fleet_efficiency_snapshot(
        account_id, window_days=days, company_code="",
    )
    if not payload and samsara_fallback is not None:
        logger.info("warehouse cold (fleet_efficiency_snapshot empty) for acct=%d days=%d \u2014 using live", account_id, days)
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
