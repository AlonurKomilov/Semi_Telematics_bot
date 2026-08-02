"""Samsara integration \u2014 warehouse sync (ingest).

Pulls Samsara's feeds on a schedule and persists them into the per-tenant
warehouse tables (``vehicle_state``, ``safety_event_log``,
``driver_efficiency_daily``, ``vehicle_health_snapshot``, ``vehicle_fault_*``,
``aggregate_weather/efficiency_snapshot``, ``geofence_definitions``).  Each
ingest is a *pure function* keyed on ``account_id`` so it runs from APScheduler,
the backfill script, or a test fixture without surprises.

Lives WITH the integration (beside ``datatruck/sync.py``), not in the
warehouse: the warehouse only stores + aggregates; each provider owns its own
sync.  The provider-agnostic tier aggregation lives in
``capabilities/warehouse/telemetry/aggregator.py``; the integration-gated fan-out the
jobs use lives in ``capabilities/integrations/shared/helpers.py``.

Each job is wrapped in defensive try/except + structured logging \u2014 warehouse
population must never crash the scheduler thread.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from infra.services import get_tenant_db, get_client

from capabilities.integrations.shared.engine_state import resolve_engine_state
from capabilities.integrations.shared.helpers import (
    _for_each_account_with_capability,
)

logger = logging.getLogger(__name__)


# ── shape adapters ───────────────────────────────────────────────────


def _vehicle_overview_to_state_row(v: dict[str, Any]) -> dict[str, Any]:
    """Reshape a ``client.get_vehicles_overview()`` entry into the columns
    the ``vehicle_state`` table expects.  Kept tiny + pure so it's
    trivially testable without a Samsara stub."""
    loc = v.get("location") or {}
    fuel = v.get("fuel") or {}
    def_lvl = v.get("def_level") or {}
    fc = v.get("fault_codes") or {}
    j1939 = (fc.get("j1939") or {}) if isinstance(fc, dict) else {}
    dtcs = j1939.get("diagnosticTroubleCodes") or []
    cel = j1939.get("checkEngineLights") or {}
    # ``checkEngineLights`` is a dict of {colour: bool/list}; we only
    # want a critical-count signal (red lights = stop-the-truck), so
    # treat the presence of a non-empty "red" entry as 1 critical DTC
    # *family* and let total dtcs cover the rest.
    crit = 0
    if isinstance(cel, dict) and cel.get("red"):
        crit = 1

    fuel_val = fuel.get("value") if isinstance(fuel, dict) else None
    def_val = def_lvl.get("value") if isinstance(def_lvl, dict) else None

    return {
        "vehicle_id":   v.get("id") or "",
        "vehicle_name": v.get("name") or "",
        "company_code": v.get("_org") or v.get("company_code") or "",
        "lat":          loc.get("latitude"),
        "lon":          loc.get("longitude"),
        "speed_mph":    loc.get("speedMilesPerHour") or loc.get("speed"),
        "heading":      loc.get("heading"),
        "address":      loc.get("address") or "",
        # Provider's own word, resolved against road speed before the
        # upsert.  It arrives on the batched stats call, never on the
        # locations payload this row is otherwise built from — reading it
        # from ``loc`` is what kept this column empty for the life of the
        # table, and with it every drive and idle minute.
        "engine_state_raw": v.get("engine_state_raw") or "",
        "fuel_pct":     fuel_val,
        "def_pct":      def_val,
        "odometer_mi":  None,  # not in the overview payload; backfilled by aggregate job if needed
        # Engine hours follows the same overlay pattern — None here,
        # filled in by ``get_current_engine_hours_readings`` before
        # upsert.  Listed explicitly so the shape is self-describing.
        "engine_hours":      None,
        "engine_hours_time": None,
        "fault_count":  len(dtcs),
        "dtc_critical_count": crit,
        "last_driver_id":   "",  # filled by event ingest \u2014 overview lacks driver mapping
        "last_driver_name": "",
        "captured_at":  loc.get("time") or "",
    }


def _safety_event_to_log_row(e: dict[str, Any]) -> dict[str, Any]:
    """Reshape a ``client.get_events()`` entry into ``safety_event_log``
    columns.  Tolerant to upstream key drift (Samsara has shipped at
    least 3 different event-shape variants in 2024-2025).

    Severity heuristic: the legacy events API doesn't expose a discrete
    severity, so we infer from G-force \u2014 >=2.0 hard, >=1.5 medium,
    else low.  This matches what the existing ``capabilities/alerting``
    pipeline does so warehouse rows align with bot alert thresholds.
    """
    g = float(e.get("g_force") or e.get("maxAccelerationGForce") or 0.0)
    if g >= 2.0:
        sev = "high"
    elif g >= 1.5:
        sev = "medium"
    elif g > 0:
        sev = "low"
    else:
        sev = ""

    return {
        "samsara_event_id": e.get("event_id") or e.get("id") or "",
        "vehicle_id":       e.get("vehicle_id") or "",
        "vehicle_name":     e.get("vehicle_name") or "",
        "driver_id":        e.get("driver_id") or "",
        "driver_name":      e.get("driver_name") or "",
        "event_type":       e.get("event_type") or e.get("type") or "",
        "severity":         sev,
        "occurred_at":      e.get("time") or e.get("occurred_at") or "",
        "lat":              e.get("latitude") or e.get("lat"),
        "lon":              e.get("longitude") or e.get("lon"),
        "speed_mph":        e.get("speed_mph") or e.get("speedMilesPerHour"),
        "video_url":        e.get("video_url") or e.get("downloadForwardVideoUrl") or "",
        # Promoted from raw_json to its own column so the dashboard's
        # filter_by_allowed_companies check can skip the per-row JSON
        # decode (~hundreds of ms saved on a 30-day window).
        "company_code":     e.get("_org") or e.get("company") or "",
        "raw":              e,
    }


def _driver_eff_to_daily_row(rec: dict[str, Any], day: str) -> dict[str, Any]:
    """Reshape a single ``client.get_driver_efficiency()`` entry into a
    ``driver_efficiency_daily`` row.

    Note: Samsara's efficiency endpoint returns *windowed* totals, not
    per-day buckets.  We treat each ingest as the bucket for ``day``
    (UTC date).  Hourly cadence + upsert-on-conflict means today's row
    keeps converging on the truth as the day progresses.
    """
    return {
        "driver_id":     rec.get("id") or rec.get("driver_id") or "",
        "driver_name":   rec.get("name") or rec.get("driver_name") or "",
        "day":           day,
        "miles":         rec.get("_miles") or rec.get("miles") or 0,
        "drive_h":       rec.get("_drive_h") or rec.get("_driving_hours") or rec.get("drive_h") or 0,
        "idle_h":        rec.get("_idle_hours") or rec.get("idle_h") or 0,
        "mpg":           rec.get("_mpg") or rec.get("mpg"),
        "antic_pct":     rec.get("_antic_pct") or rec.get("antic_pct"),
        "green_pct":     rec.get("_green_pct") or rec.get("green_pct"),
        "harsh_brake":   rec.get("_harsh_brake") or rec.get("harsh_brake") or 0,
        "harsh_turn":    rec.get("_harsh_turn") or rec.get("harsh_turn") or 0,
        "harsh_accel":   rec.get("_harsh_accel") or rec.get("harsh_accel") or 0,
        "overspeed_min": rec.get("_overspeed_min") or rec.get("overspeed_min") or 0,
        "raw":           rec,
    }


# ── per-account jobs ─────────────────────────────────────────────────


async def ingest_vehicle_state(account_id: int) -> int:
    """Pull the live fleet overview and overwrite ``vehicle_state``.
    Returns the number of vehicles persisted.

    Also fans out to ``get_current_odometer_readings()`` per company
    and stamps ``odometer_mi`` + ``odometer_time`` onto each row before
    upsert.  This is the warehouse-side population so every consumer
    (vehicles list, dashboard, mini-app, AI tools, maintenance
    progress UI) reads odometer from the DB rather than re-querying
    Samsara on the request path.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    try:
        client = await get_client(account_id)
    except Exception:
        logger.warning("ingest_vehicle_state: no Samsara client for acct=%d", account_id)
        return 0

    try:
        fleet = await client.get_vehicles_overview()
    except Exception:
        logger.exception("ingest_vehicle_state: get_vehicles_overview failed acct=%d", account_id)
        return 0

    # Fetch current odometer + cumulative engine-hours for every active
    # company so we can merge the values before persisting.  Failures
    # are non-fatal (some Samsara plans / vehicles without CAN bus
    # gateways don't expose obdOdometerMeters or obdEngineSeconds);
    # affected vehicles keep the corresponding field as None and the
    # readers degrade to "—".
    odometer_by_vehicle_id: dict[str, dict] = {}
    engine_hours_by_vehicle_id: dict[str, dict] = {}
    # MultiCompanyClient exposes `.clients` (per-company SamsaraClient
    # instances); test stubs are flat single-company clients.  Iterate
    # the per-company map when present, otherwise call the flat stub.
    per_company_clients = getattr(client, "clients", None)
    fetch_targets = (
        list(per_company_clients.values()) if per_company_clients else [client]
    )
    for company_client in fetch_targets:
        if hasattr(company_client, "get_current_odometer_readings"):
            try:
                readings = await company_client.get_current_odometer_readings()
            except Exception as e:
                # WARNING (not debug): a persistent odometer-endpoint
                # failure silently freezes every vehicle's mileage while
                # GPS keeps working — exactly the kind of stall that
                # should be visible in logs, not hidden.
                logger.warning(
                    "odometer fetch failed acct=%d: %s", account_id, e,
                )
                readings = []
            for reading in readings:
                vehicle_id = reading.get("id")
                if vehicle_id:
                    odometer_by_vehicle_id[str(vehicle_id)] = {
                        "miles": reading.get("odometer_miles"),
                        "time":  reading.get("time"),
                    }
        if hasattr(company_client, "get_current_engine_hours_readings"):
            try:
                hours_readings = await company_client.get_current_engine_hours_readings()
            except Exception as e:
                logger.warning(
                    "engine-hours fetch failed acct=%d: %s", account_id, e,
                )
                hours_readings = []
            for reading in hours_readings:
                vehicle_id = reading.get("id")
                if vehicle_id:
                    engine_hours_by_vehicle_id[str(vehicle_id)] = {
                        "hours": reading.get("engine_hours"),
                        "time":  reading.get("time"),
                    }

    rows = [_vehicle_overview_to_state_row(v) for v in fleet]
    for row in rows:
        vid = str(row.get("vehicle_id") or "")
        if not vid:
            continue
        odometer = odometer_by_vehicle_id.get(vid)
        if odometer:
            row["odometer_mi"] = odometer.get("miles")
            row["odometer_time"] = odometer.get("time")
        eng = engine_hours_by_vehicle_id.get(vid)
        if eng:
            row["engine_hours"] = eng.get("hours")
            row["engine_hours_time"] = eng.get("time")
        row["engine_state"] = resolve_engine_state(
            row.get("engine_state_raw"), row.get("speed_mph"),
        )
        row.pop("engine_state_raw", None)

    # Stamp OUR identity onto every row before it becomes history.  The
    # registry resolves by the provider's stable external id — never by
    # display name, which is provider-editable free text (a rename is how
    # one truck spent weeks in the warehouse as "229 Idris Ahmed").  A
    # vehicle the registry cannot place keeps registry_id NULL and is
    # quarantined instead of guessed at.
    orphans: list[dict[str, Any]] = []
    try:
        ref_to_id = await tenant.registry_ids_by_telematics_ref(account_id)
    except Exception:
        logger.exception(
            "registry-id resolution unavailable acct=%d — rows keep their "
            "last known registry link", account_id,
        )
        ref_to_id = {}
    if ref_to_id:
        for row in rows:
            vid = str(row.get("vehicle_id") or "")
            rid = ref_to_id.get(vid)
            if rid is not None:
                row["registry_id"] = rid
            else:
                orphans.append({
                    "external_id": vid,
                    "name": row.get("vehicle_name") or "",
                    "company_code": row.get("company_code") or "",
                })
        if orphans:
            try:
                await tenant.record_ingest_orphans(
                    account_id, "vehicles.state", orphans,
                )
            except Exception:
                logger.exception(
                    "orphan quarantine write failed acct=%d", account_id,
                )

    n = await tenant.upsert_vehicle_state(account_id, rows)
    logger.info(
        "ingest_vehicle_state acct=%d persisted=%d with_odometer=%d "
        "with_engine_hours=%d with_engine_state=%d with_registry_id=%d "
        "orphans=%d",
        account_id, n,
        len(odometer_by_vehicle_id),
        len(engine_hours_by_vehicle_id),
        sum(1 for r in rows if r.get("engine_state")),
        sum(1 for r in rows if r.get("registry_id") is not None),
        len(orphans),
    )
    # Surface a whole-fleet odometer stall: we persisted vehicles but got
    # ZERO odometer readings.  Covers both failure modes — the endpoint
    # threw (warned above) AND the quieter case where it returns an empty
    # list with no exception.  Without this, every snapshot from here on
    # carries a NULL odometer and the back-dated work-order reading
    # silently freezes at the last good day, account-wide, for days.
    if n > 0 and not odometer_by_vehicle_id:
        logger.warning(
            "odometer ingestion stalled acct=%d: %d vehicles persisted, "
            "0 odometer readings (Samsara odometer endpoint down or "
            "unauthorized?) — back-dated WO mileage will freeze",
            account_id, n,
        )

    # Keep the Vehicle registry (our SSOT) complete: every Samsara
    # vehicle this tick saw is upserted into ``vehicles`` (source=
    # samsara).  ``upsert_from_integration`` refreshes the spec +
    # telematics_ref but preserves any operator-set vehicle_type /
    # status / notes.  Best-effort — a registry hiccup must never
    # poison the live-state ingest, so we swallow.  This is what makes
    # a newly-added Samsara truck appear in the registry within 60s
    # and is the ongoing guarantee behind the migration-105 backfill.
    try:
        registry_rows = [
            {
                "company_code":   v.get("_org") or "",
                "unit_number":    v.get("name") or "",
                "telematics_ref": str(v.get("id") or ""),
                "vin":            "" if v.get("vin") in (None, "N/A") else v.get("vin"),
                "make":           "" if v.get("make") in (None, "N/A") else v.get("make"),
                "model":          "" if v.get("model") in (None, "N/A") else v.get("model"),
                "year":           None if v.get("year") in (None, "N/A") else v.get("year"),
                "plate_number":   "" if v.get("license_plate") in (None, "N/A") else v.get("license_plate"),
            }
            for v in fleet
        ]
        written = await tenant.upsert_from_integration(
            account_id, registry_rows, source="samsara",
        )
        if registry_rows and not written:
            logger.warning(
                "registry upsert wrote NOTHING acct=%d despite %d vehicles "
                "in the payload — the registry is the identity SSOT, so it "
                "is now drifting from what the provider reports",
                account_id, len(registry_rows),
            )
    except Exception:
        # ERROR, not debug.  The roster upserts as one transaction, so a
        # single unusable value rolls back every vehicle with it — and at
        # debug level that failure stayed invisible for 47 days while the
        # registry silently froze.  Still swallowed: a registry hiccup
        # must not poison live-state ingest.
        logger.exception(
            "registry upsert from ingest FAILED acct=%d (%d vehicles) — "
            "registry now stale", account_id, len(registry_rows),
        )
    # Reconcile billing quantity with the freshly-ingested activity.
    # The provider only PATCHes Stripe when the active-vehicle count
    # actually changed, so most ingests are no-ops; failures here must
    # not poison the ingest result, so we swallow.  Skipped silently
    # for stub-provider accounts and any account without a saved
    # extras subscription_item id.
    try:
        from capabilities.platform.billing import get_provider as _get_billing_provider
        provider = _get_billing_provider()
        await provider.sync_billing_quantity(account_id, tenant)
    except Exception:
        logger.exception(
            "sync_billing_quantity raised during ingest for acct=%d "
            "(non-fatal — vehicle_state still persisted)",
            account_id,
        )
    return n


async def ingest_safety_events(account_id: int, *, days: int = 2) -> int:
    """Pull the last ``days`` of safety events and INSERT OR IGNORE.
    Returns the count of *new* rows persisted (post dedupe).

    A 2-day window with a 5-minute cadence handles late-arriving events
    (Samsara backfills cab-cam clips for up to ~24 h) without flooding
    the dedup index."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    try:
        client = await get_client(account_id)
    except Exception:
        logger.warning("ingest_safety_events: no Samsara client for acct=%d", account_id)
        return 0

    try:
        events = await client.get_events(days=days)
    except Exception:
        logger.exception("ingest_safety_events: get_events failed acct=%d", account_id)
        return 0

    rows = [_safety_event_to_log_row(e) for e in events]
    n = await tenant.insert_safety_events(account_id, rows)
    logger.info("ingest_safety_events acct=%d new=%d total=%d", account_id, n, len(rows))
    return n


async def ingest_driver_efficiency_daily(account_id: int, *, days: int = 1) -> int:
    """Pull driver efficiency and upsert one row per (driver, today).
    Returns rows touched."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    try:
        client = await get_client(account_id)
    except Exception:
        logger.warning("ingest_driver_efficiency: no Samsara client for acct=%d", account_id)
        return 0

    try:
        recs = await client.get_driver_efficiency(days=days)
    except Exception:
        logger.exception("ingest_driver_efficiency: failed acct=%d", account_id)
        return 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = [_driver_eff_to_daily_row(r, today) for r in recs if r.get("id") or r.get("driver_id")]
    n = await tenant.upsert_driver_efficiency_daily(account_id, rows)
    logger.info("ingest_driver_efficiency acct=%d persisted=%d", account_id, n)
    return n


# Import the capability names so each job's wrapper carries the
# canonical id rather than a magic string.  Mismatches between the
# canonical id and the integration-row toggle key would silently
# disable a job — keep them lockstep.
from adapters.telematics.protocol import Capability as _Cap  # noqa: E402


async def job_ingest_vehicle_state(_app=None) -> None:
    await _for_each_account_with_capability(
        _Cap.VEHICLE_STATE, ingest_vehicle_state,
    )


async def job_ingest_safety_events(_app=None) -> None:
    await _for_each_account_with_capability(
        _Cap.SAFETY_EVENTS, ingest_safety_events,
    )


async def job_ingest_driver_efficiency_daily(_app=None) -> None:
    await _for_each_account_with_capability(
        _Cap.DRIVER_EFFICIENCY_DAILY, ingest_driver_efficiency_daily,
    )


# ── vehicle health snapshot ────────────────────────────────


def _vehicle_health_to_snapshot_row(v: dict[str, Any]) -> dict[str, Any]:
    """Reshape a single ``client.get_vehicle_health()`` entry into the
    columns the ``vehicle_health_snapshot`` table expects.  ``raw`` is
    the full live-shape dict so readers can return it unchanged."""
    alerts = v.get("_health_alerts") or []
    return {
        "vehicle_id":   v.get("id") or "",
        "vehicle_name": v.get("name") or "",
        "company_code": v.get("_org") or v.get("company_code") or "",
        "alert_count":  len(alerts) if isinstance(alerts, list) else 0,
        "captured_at":  ((v.get("location") or {}).get("time")
                         if isinstance(v.get("location"), dict) else "") or "",
        "raw":          v,
    }


async def ingest_vehicle_health(account_id: int) -> int:
    """Pull current per-vehicle health from Samsara and overwrite the
    ``vehicle_health_snapshot`` table.  Returns vehicles persisted."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    try:
        client = await get_client(account_id)
    except Exception:
        logger.warning("ingest_vehicle_health: no Samsara client for acct=%d", account_id)
        return 0

    try:
        health = await client.get_vehicle_health()
    except Exception:
        logger.exception("ingest_vehicle_health: get_vehicle_health failed acct=%d", account_id)
        return 0

    rows = [_vehicle_health_to_snapshot_row(v) for v in health]
    n = await tenant.upsert_vehicle_health_snapshots(account_id, rows)
    logger.info("ingest_vehicle_health acct=%d persisted=%d", account_id, n)
    return n


async def job_ingest_vehicle_health(_app=None) -> None:
    await _for_each_account_with_capability(
        _Cap.VEHICLE_HEALTH, ingest_vehicle_health,
    )


# ── vehicle faults (snapshot + per-DTC detail) ────────────


def _faulted_to_snapshot_row(v: dict[str, Any]) -> dict[str, Any]:
    """Reshape a single faulted-vehicle dict into snapshot columns.
    ``raw`` keeps the live shape (incl. ``_dtcs`` / ``_lights``)."""
    return {
        "vehicle_id":   v.get("id") or "",
        "vehicle_name": v.get("name") or "",
        "company_code": v.get("_org") or v.get("company_code") or "",
        "dtc_count":    len(v.get("_dtcs") or []),
        "captured_at":  v.get("_fault_time") or "",
        "raw":          v,
    }


async def ingest_vehicle_faults(account_id: int) -> int:
    """Pull current faulted vehicles + critical list from Samsara and
    refresh both ``vehicle_fault_snapshot`` and ``vehicle_fault_detail``
    for the given account."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    try:
        client = await get_client(account_id)
    except Exception:
        logger.warning("ingest_vehicle_faults: no Samsara client for acct=%d", account_id)
        return 0

    try:
        faulted, _total, _bd = await client.get_vehicles_with_faults()
    except Exception:
        logger.exception("ingest_vehicle_faults: get_vehicles_with_faults failed acct=%d", account_id)
        return 0

    # _severity is stamped on each vehicle by get_vehicles_with_faults()
    critical_ids = {v.get("id") or "" for v in faulted if v.get("_severity") == "critical"}
    rows = [_faulted_to_snapshot_row(v) for v in faulted]
    n = await tenant.upsert_vehicle_fault_snapshot(account_id, rows, critical_ids)

    # Detail / lifecycle: per-vehicle DTC list (only currently-faulted
    # vehicles appear in the input).  Vehicles that were faulted last
    # cycle but no longer faulted are NOT in ``faulted`` — their active
    # DTCs need to be cleared too, so we pass an empty list for them.
    per_vehicle: dict[str, list[dict[str, Any]]] = {}
    for v in faulted:
        vid = v.get("id") or ""
        if not vid:
            continue
        per_vehicle[vid] = list(v.get("_dtcs") or [])

    # Plus all *previously*-active vehicle_ids (so they get cleared if
    # missing from this cycle).  Cheap query against the detail index.
    try:
        prev_active = await _previously_active_vehicle_ids(tenant, account_id)
    except Exception:
        prev_active = set()
    for vid in prev_active - per_vehicle.keys():
        per_vehicle[vid] = []

    try:
        new_obs, new_cleared = await tenant.upsert_vehicle_fault_details(account_id, per_vehicle)
        logger.info(
            "ingest_vehicle_faults acct=%d snapshot=%d new_dtcs=%d cleared=%d",
            account_id, n, new_obs, new_cleared,
        )
    except Exception:
        logger.exception("ingest_vehicle_faults: detail upsert failed acct=%d", account_id)
    return n


async def _previously_active_vehicle_ids(tenant, account_id: int) -> set[str]:
    """Vehicle ids that have at least one ``cleared_at IS NULL`` row.
    Used to clear DTCs for trucks that dropped out of the faulted set."""
    cur = await tenant._db.execute(  # type: ignore[attr-defined]
        """
        SELECT DISTINCT vehicle_id FROM vehicle_fault_detail
        WHERE account_id = ? AND cleared_at IS NULL
        """,
        (account_id,),
    )
    return {row[0] for row in await cur.fetchall()}


async def job_ingest_vehicle_faults(_app=None) -> None:
    await _for_each_account_with_capability(
        _Cap.VEHICLE_FAULTS, ingest_vehicle_faults,
    )


# ── fleet weather ──────────────────────────────────────────


def _weather_to_snapshot_row(v: dict[str, Any]) -> dict[str, Any]:
    w = v.get("_weather") or {}
    return {
        "vehicle_id":   v.get("id") or "",
        "vehicle_name": v.get("name") or "",
        "company_code": v.get("_org") or v.get("company_code") or "",
        "temp_f":       w.get("temp_f") if isinstance(w, dict) else None,
        "captured_at":  (w.get("temp_time") if isinstance(w, dict) else "") or "",
        "raw":          v,
    }


async def ingest_fleet_weather(account_id: int) -> int:
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    try:
        client = await get_client(account_id)
    except Exception:
        logger.warning("ingest_fleet_weather: no Samsara client for acct=%d", account_id)
        return 0
    try:
        weather = await client.get_fleet_weather()
    except Exception:
        logger.exception("ingest_fleet_weather: get_fleet_weather failed acct=%d", account_id)
        return 0
    rows = [_weather_to_snapshot_row(v) for v in weather]
    n = await tenant.upsert_aggregate_weather_snapshots(account_id, rows)
    logger.info("ingest_fleet_weather acct=%d persisted=%d", account_id, n)
    return n


async def job_ingest_fleet_weather(_app=None) -> None:
    await _for_each_account_with_capability(
        _Cap.FLEET_WEATHER, ingest_fleet_weather,
    )


# ── fleet efficiency ───────────────────────────────────────


async def ingest_fleet_efficiency(account_id: int, *, days: int = 7) -> int:
    """Snapshot the windowed combined-efficiency view per company.

    The live response is per-account but the API exposes a ``company``
    filter, so we ingest the unfiltered (``company=None``) variant —
    callers that pass a company filter still hit the warehouse via the
    ``""`` (all-companies) row and the reader filters in Python.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    try:
        client = await get_client(account_id)
    except Exception:
        logger.warning("ingest_fleet_efficiency: no Samsara client for acct=%d", account_id)
        return 0
    try:
        payload = await client.get_fleet_efficiency(days=days)
    except Exception:
        logger.exception("ingest_fleet_efficiency: get_fleet_efficiency failed acct=%d days=%d", account_id, days)
        return 0
    n = await tenant.upsert_aggregate_efficiency_snapshot(
        account_id, window_days=days, company_code="", payload=payload,
    )
    logger.info("ingest_fleet_efficiency acct=%d days=%d rows=%d", account_id, days, n)
    return n


async def job_ingest_fleet_efficiency(_app=None) -> None:
    """Refresh the 7-day window — the most-requested by the dashboard."""
    async def _do(acct_id: int) -> int:
        return await ingest_fleet_efficiency(acct_id, days=7)
    await _for_each_account_with_capability(
        _Cap.FLEET_EFFICIENCY, _do,
    )


# ── geofence definitions cache ─────────────────────────────


async def ingest_geofence_definitions(account_id: int) -> int:
    """Snapshot all geofence definitions for the account.

    Hourly cadence — geofences rarely change.  The MultiCompanyClient
    fans out per-company internally, so we get all companies in one call.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    try:
        client = await get_client(account_id)
    except Exception:
        logger.warning("ingest_geofence_definitions: no client for acct=%d", account_id)
        return 0
    try:
        fences = await client.get_geofences()
    except Exception:
        logger.exception("ingest_geofence_definitions: get_geofences failed acct=%d", account_id)
        return 0
    n = await tenant.upsert_geofence_definitions(account_id, fences or [])
    logger.info("ingest_geofence_definitions acct=%d rows=%d", account_id, n)
    return n


async def job_ingest_geofence_definitions(_app=None) -> None:
    await _for_each_account_with_capability(
        _Cap.GEOFENCE_DEFINITIONS, ingest_geofence_definitions,
    )
