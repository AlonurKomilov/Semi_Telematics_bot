"""Phase C — telemetry warehouse ingestor.

Pulls from Samsara on a schedule and persists the result into the
per-tenant warehouse tables.  All four jobs are *pure functions* keyed
on ``account_id`` so they can be invoked from APScheduler, a one-shot
backfill script (``scripts/backfill_warehouse.py``), or a test fixture
without surprises.

Operational contract (per plan.md C2):

  | Job                               | Cadence    |
  |-----------------------------------|------------|
  | ``ingest_vehicle_state``          | every 60 s |
  | ``ingest_safety_events``          | every 5 m  |
  | ``ingest_driver_efficiency_daily``| hourly     |
  | ``aggregate_telemetry_hourly``    | HH:05      |
  | ``ingest_vehicle_health``         | every 5 m  |

Each job is wrapped in defensive try/except + structured logging \u2014
warehouse population must never crash the scheduler thread (the
fallback path in ``warehouse_reader`` keeps the dashboard alive on
ingestor failure, but a crashed scheduler stops *every* job).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from infra.services import get_platform_db, get_tenant_db, get_client

logger = logging.getLogger(__name__)


# ── shape adapters ───────────────────────────────────────────────────


def _vehicle_overview_to_state_row(v: dict[str, Any]) -> dict[str, Any]:
    """Reshape a ``client.get_fleet_overview()`` entry into the columns
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
        "engine_state": (loc.get("engineStates") or {}).get("value", "") if isinstance(loc.get("engineStates"), dict) else "",
        "fuel_pct":     fuel_val,
        "def_pct":      def_val,
        "odometer_mi":  None,  # not in the overview payload; backfilled by aggregate job if needed
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
        fleet = await client.get_fleet_overview()
    except Exception:
        logger.exception("ingest_vehicle_state: get_fleet_overview failed acct=%d", account_id)
        return 0

    # Fetch current odometer for every active company in this account
    # so we can merge the value before persisting.  Failures are
    # non-fatal (some Samsara plans / vehicles without CAN bus gateways
    # don't expose obdOdometerMeters); affected vehicles keep
    # odometer_mi=None and the readers degrade to "—".
    odometer_by_vehicle_id: dict[str, dict] = {}
    # MultiCompanyClient exposes `.clients` (per-company SamsaraClient
    # instances); test stubs are flat single-company clients.  Iterate
    # the per-company map when present, otherwise call the flat stub.
    per_company_clients = getattr(client, "clients", None)
    fetch_targets = (
        list(per_company_clients.values()) if per_company_clients else [client]
    )
    for company_client in fetch_targets:
        if not hasattr(company_client, "get_current_odometer_readings"):
            continue
        try:
            readings = await company_client.get_current_odometer_readings()
        except Exception as e:
            logger.debug("odometer fetch failed: %s", e)
            continue
        for reading in readings:
            vehicle_id = reading.get("id")
            if vehicle_id:
                odometer_by_vehicle_id[str(vehicle_id)] = {
                    "miles": reading.get("odometer_miles"),
                    "time": reading.get("time"),
                }

    rows = [_vehicle_overview_to_state_row(v) for v in fleet]
    if odometer_by_vehicle_id:
        for row in rows:
            odometer = odometer_by_vehicle_id.get(str(row.get("vehicle_id") or ""))
            if odometer:
                row["odometer_mi"] = odometer.get("miles")
                row["odometer_time"] = odometer.get("time")

    n = await tenant.upsert_vehicle_state(account_id, rows)
    logger.info(
        "ingest_vehicle_state acct=%d persisted=%d with_odometer=%d",
        account_id, n, len(odometer_by_vehicle_id),
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


async def aggregate_telemetry_hourly(account_id: int) -> int:
    """Derive last hour's ``vehicle_telemetry_hourly`` row per vehicle
    from ``safety_event_log`` (harsh count) and ``vehicle_state``
    (max speed snapshot).  This is a placeholder aggregation \u2014 it
    counts safety events per vehicle in the trailing hour and snaps
    the current speed/odometer.  A richer roll-up (drive vs idle
    minutes by GPS sampling) is deferred to E3."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    now = datetime.now(timezone.utc)
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    prev_hour_start = hour_start - timedelta(hours=1)
    hour_label = prev_hour_start.strftime("%Y-%m-%dT%H:00:00")

    # Count harsh events per vehicle in the just-closed hour.
    cur = await tenant._db.execute(
        """
        SELECT vehicle_id,
               COUNT(*) AS harsh_event_count,
               MAX(COALESCE(speed_mph, 0)) AS max_speed_mph
        FROM safety_event_log
        WHERE account_id = ?
          AND occurred_at >= ?
          AND occurred_at <  ?
        GROUP BY vehicle_id
        """,
        (account_id, prev_hour_start.isoformat(), hour_start.isoformat()),
    )
    cols = [d[0] for d in cur.description]
    event_rows = [dict(zip(cols, r)) for r in await cur.fetchall()]

    rows: list[dict[str, Any]] = []
    for er in event_rows:
        if not er.get("vehicle_id"):
            continue
        rows.append({
            "vehicle_id":        er["vehicle_id"],
            "hour_utc":          hour_label,
            "miles":             0,  # filled by future per-vehicle history sampler
            "drive_min":         0,
            "idle_min":          0,
            "max_speed_mph":     er.get("max_speed_mph") or 0,
            "harsh_event_count": int(er.get("harsh_event_count") or 0),
        })
    if not rows:
        return 0
    n = await tenant.upsert_vehicle_telemetry_hourly(account_id, rows)
    logger.info("aggregate_telemetry_hourly acct=%d hour=%s rows=%d", account_id, hour_label, n)
    return n


# ── scheduler entry points (iterate active accounts) ─────────────────


_INGEST_MAX_CONCURRENT_ACCOUNTS = int(
    os.getenv("INGEST_MAX_CONCURRENT_ACCOUNTS", "5")
)


async def _for_each_active_account(coro_factory) -> None:
    """Invoke ``coro_factory(account_id)`` for every active account in
    parallel (bounded concurrency), catching per-account errors so one
    bad tenant doesn't block the rest of the fleet.

    Each per-account job is dominated by Samsara API round-trips
    (~3–5 s); running them serially caused the 60 s cadence jobs to
    overrun once a deployment passed ~10 accounts. Bounded by env var
    ``INGEST_MAX_CONCURRENT_ACCOUNTS`` (default 5) so we don't burst
    the Samsara API or starve the SQLite single-writer lane.
    """
    import time as _time
    job_name = getattr(coro_factory, "__name__", "anon")
    t0 = _time.perf_counter()
    try:
        accounts = await get_platform_db().list_accounts(active_only=True)
    except Exception:
        logger.exception("ingestor: list_accounts failed")
        return
    if not accounts:
        return

    sem = asyncio.Semaphore(_INGEST_MAX_CONCURRENT_ACCOUNTS)
    per_acct_ms: dict[int, float] = {}

    async def _run(acc):
        async with sem:
            t_acct = _time.perf_counter()
            try:
                await coro_factory(acc.id)
            except Exception:
                logger.exception("ingestor: per-account job failed acct=%d", acc.id)
            finally:
                per_acct_ms[acc.id] = round(
                    (_time.perf_counter() - t_acct) * 1000, 1,
                )

    await asyncio.gather(*(_run(a) for a in accounts))
    total_ms = round((_time.perf_counter() - t0) * 1000, 1)
    if per_acct_ms:
        slowest_aid = max(per_acct_ms, key=per_acct_ms.get)
        logger.info(
            "ingestor job=%s accounts=%d total_ms=%s slowest_acct=%d slowest_ms=%s",
            job_name, len(accounts), total_ms,
            slowest_aid, per_acct_ms[slowest_aid],
        )


async def job_ingest_vehicle_state(_app=None) -> None:
    await _for_each_active_account(ingest_vehicle_state)


async def job_ingest_safety_events(_app=None) -> None:
    await _for_each_active_account(ingest_safety_events)


async def job_ingest_driver_efficiency_daily(_app=None) -> None:
    await _for_each_active_account(ingest_driver_efficiency_daily)


async def job_aggregate_telemetry_hourly(_app=None) -> None:
    await _for_each_active_account(aggregate_telemetry_hourly)


# ── Phase 2 — vehicle health snapshot ────────────────────────────────


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
    await _for_each_active_account(ingest_vehicle_health)


# ── Phase 2 — vehicle faults (snapshot + per-DTC detail) ────────────


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
    await _for_each_active_account(ingest_vehicle_faults)


# ── Phase 2 — fleet weather ──────────────────────────────────────────


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
    n = await tenant.upsert_fleet_weather_snapshots(account_id, rows)
    logger.info("ingest_fleet_weather acct=%d persisted=%d", account_id, n)
    return n


async def job_ingest_fleet_weather(_app=None) -> None:
    await _for_each_active_account(ingest_fleet_weather)


# ── Phase 2 — fleet efficiency ───────────────────────────────────────


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
    n = await tenant.upsert_fleet_efficiency_snapshot(
        account_id, window_days=days, company_code="", payload=payload,
    )
    logger.info("ingest_fleet_efficiency acct=%d days=%d rows=%d", account_id, days, n)
    return n


async def job_ingest_fleet_efficiency(_app=None) -> None:
    """Refresh the 7-day window — the most-requested by the dashboard."""
    async def _do(acct_id: int) -> int:
        return await ingest_fleet_efficiency(acct_id, days=7)
    await _for_each_active_account(_do)


# ── Phase 4 — geofence definitions cache ─────────────────────────────


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
    await _for_each_active_account(ingest_geofence_definitions)
