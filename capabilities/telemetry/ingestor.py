"""telemetry warehouse ingestor.

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
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from infra.services import get_platform_db, get_tenant_db, get_client

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
        "engine_state": (loc.get("engineStates") or {}).get("value", "") if isinstance(loc.get("engineStates"), dict) else "",
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

    n = await tenant.upsert_vehicle_state(account_id, rows)
    logger.info(
        "ingest_vehicle_state acct=%d persisted=%d with_odometer=%d with_engine_hours=%d",
        account_id, n,
        len(odometer_by_vehicle_id),
        len(engine_hours_by_vehicle_id),
    )
    # Surface a whole-fleet odometer stall: we persisted vehicles but got
    # ZERO odometer readings.  Covers both failure modes — the endpoint
    # threw (warned above) AND the quieter case where it returns an empty
    # list with no exception.  Without this, every snapshot from here on
    # carries a NULL odometer and the back-dated work-order reading
    # silently freezes at the last good day, fleet-wide, for days.
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
        await tenant.upsert_from_integration(
            account_id, registry_rows, source="samsara",
        )
    except Exception as e:
        logger.debug("registry upsert from ingest skipped acct=%d: %s", account_id, e)
    # Reconcile billing quantity with the freshly-ingested activity.
    # The provider only PATCHes Stripe when the active-vehicle count
    # actually changed, so most ingests are no-ops; failures here must
    # not poison the ingest result, so we swallow.  Skipped silently
    # for stub-provider accounts and any account without a saved
    # extras subscription_item id.
    try:
        from capabilities.billing import get_provider as _get_billing_provider
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


async def snapshot_vehicle_state(account_id: int) -> int:
    """Copy the current ``vehicle_state`` row for every active vehicle
    into ``vehicle_state_snapshot`` with the current timestamp.

    Forms the raw history layer that other features build on:
      * Maintenance - odometer deltas drive the calendar projection.
      * Safety - speed deltas + RPM patterns drive hard-event
        correlation and idle scoring.
      * Dispatch - lat/lon over time backs trip playback.
      * Accounting - fuel-pct deltas + miles-per-day feed fuel-burn
        and cost-per-mile reports.
      * Mechanics - battery / oil / coolant / RPM trend lines for the
        predictive-maintenance composite.

    Merges health metrics (battery_v, oil_psi, coolant_c, load_pct,
    rpm) from ``vehicle_health_snapshot.raw_json`` so a single row
    carries everything the consumers need.  Soft-fails on missing
    health data: the row still gets written with the state fields
    populated; health columns stay NULL.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    state_rows = await tenant.get_vehicle_state(account_id)
    if not state_rows:
        return 0

    health_by_vid: dict[str, dict[str, Any]] = {}
    try:
        cur = await tenant._db.execute(
            "SELECT vehicle_id, raw_json FROM vehicle_health_snapshot "
            "WHERE account_id = ?",
            (account_id,),
        )
        for row in await cur.fetchall():
            d = dict(zip(("vehicle_id", "raw_json"), row))
            vid = str(d.get("vehicle_id") or "")
            blob = d.get("raw_json")
            if not vid or not blob:
                continue
            try:
                obj = json.loads(blob) if isinstance(blob, str) else blob
            except (TypeError, ValueError):
                continue
            health = (obj or {}).get("_health") or {}
            if health:
                health_by_vid[vid] = health
    except Exception as e:
        logger.debug("snapshot_vehicle_state: health fetch skipped (%s)", e)

    rows: list[dict[str, Any]] = []
    for sr in state_rows:
        vid = str(sr.get("vehicle_id") or "").strip()
        if not vid:
            continue
        health = health_by_vid.get(vid, {})
        rows.append({
            "vehicle_id":         vid,
            "captured_at":        captured_at,
            "lat":                sr.get("lat"),
            "lon":                sr.get("lon"),
            "speed_mph":          sr.get("speed_mph"),
            "engine_state":       sr.get("engine_state") or "",
            "fuel_pct":           sr.get("fuel_pct"),
            "def_pct":            sr.get("def_pct"),
            "odometer_mi":        sr.get("odometer_mi"),
            "engine_hours":       sr.get("engine_hours"),
            "fault_count":        sr.get("fault_count") or 0,
            "dtc_critical_count": sr.get("dtc_critical_count") or 0,
            "last_driver_id":     sr.get("last_driver_id") or "",
            "battery_v":          health.get("battery_v"),
            "oil_psi":            health.get("oil_psi"),
            "coolant_c":          health.get("coolant_c"),
            "engine_load_pct":    health.get("load_pct"),
            "rpm":                health.get("rpm"),
        })
    if not rows:
        return 0
    n = await tenant.upsert_vehicle_state_snapshots(account_id, rows)
    logger.info(
        "snapshot_vehicle_state acct=%d at=%s rows=%d with_health=%d",
        account_id, captured_at, n, sum(1 for v in health_by_vid.values() if v),
    )
    return n


async def _aggregate_hour_window(
    tenant,
    account_id: int,
    hour_start: datetime,
) -> int:
    """Aggregate ONE hour into ``vehicle_telemetry_hourly``.

    Shared body of the live cron path and the backfill path.  Window
    is ``[hour_start, hour_start + 1h)`` so the caller controls
    exactly which hour gets aggregated.  Used by:

      * ``aggregate_telemetry_hourly`` (just-closed hour, every hour)
      * ``backfill_aggregations`` (one call per hour for 30 days)
    """
    hour_end = hour_start + timedelta(hours=1)
    hour_label = hour_start.strftime("%Y-%m-%dT%H:00:00")

    # Harsh-event counts + window max-speed from safety_event_log.
    # Hard-coded column list - asyncpg cursors don't expose .description,
    # see adapters/storage/warehouse.py:get_current_vehicles for the
    # original incident.
    cols = ["vehicle_id", "harsh_event_count", "max_speed_mph"]
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
        (account_id, hour_start.isoformat(), hour_end.isoformat()),
    )
    event_rows = [dict(zip(cols, r)) for r in await cur.fetchall()]
    event_by_vid: dict[str, dict] = {
        str(er.get("vehicle_id") or ""): er for er in event_rows if er.get("vehicle_id")
    }

    # Window snapshot rollup - one query per account, grouped by
    # vehicle.  Vehicles with zero snapshots in the window simply
    # don't get a row this hour.
    snap_cols = [
        "vehicle_id", "miles_in_window",
        "max_speed", "avg_fuel_pct",
        "drive_samples", "idle_samples",
    ]
    cur = await tenant._db.execute(
        """
        SELECT vehicle_id,
               MAX(odometer_mi) - MIN(odometer_mi) AS miles_in_window,
               MAX(COALESCE(speed_mph, 0))         AS max_speed,
               AVG(COALESCE(fuel_pct, 0))          AS avg_fuel_pct,
               SUM(CASE WHEN engine_state = 'moving' THEN 1 ELSE 0 END) AS drive_samples,
               SUM(CASE WHEN engine_state = 'idle'   THEN 1 ELSE 0 END) AS idle_samples
          FROM vehicle_state_snapshot
         WHERE account_id = ?
           AND captured_at >= ?
           AND captured_at <  ?
           AND odometer_mi IS NOT NULL
         GROUP BY vehicle_id
        """,
        (account_id, hour_start.isoformat(), hour_end.isoformat()),
    )
    snap_rows = [dict(zip(snap_cols, r)) for r in await cur.fetchall()]

    # 5-minute snapshot cadence: each sample represents 5 minutes of
    # duty time.  drive_min / idle_min derive from sample counts.
    SAMPLE_INTERVAL_MIN = 5

    rows: list[dict[str, Any]] = []
    seen_vids: set[str] = set()
    for sr in snap_rows:
        vid = str(sr.get("vehicle_id") or "")
        if not vid:
            continue
        seen_vids.add(vid)
        miles = max(0.0, float(sr.get("miles_in_window") or 0))
        max_speed = float(sr.get("max_speed") or 0)
        avg_fuel = float(sr.get("avg_fuel_pct") or 0)
        drive_min = int(sr.get("drive_samples") or 0) * SAMPLE_INTERVAL_MIN
        idle_min = int(sr.get("idle_samples") or 0) * SAMPLE_INTERVAL_MIN
        evt = event_by_vid.get(vid, {})
        if not max_speed and evt:
            max_speed = float(evt.get("max_speed_mph") or 0)
        rows.append({
            "vehicle_id":        vid,
            "hour_utc":          hour_label,
            "miles":             miles,
            "drive_min":         drive_min,
            "idle_min":          idle_min,
            "max_speed_mph":     max_speed,
            "avg_fuel_pct":      avg_fuel,
            "harsh_event_count": int(evt.get("harsh_event_count") or 0),
        })

    # Vehicles that had safety events but no snapshot in the window
    # still get a row - keeps the harsh-event count visible even
    # when snapshots were missed for that hour.
    for vid, evt in event_by_vid.items():
        if vid in seen_vids:
            continue
        rows.append({
            "vehicle_id":        vid,
            "hour_utc":          hour_label,
            "miles":             0,
            "drive_min":         0,
            "idle_min":          0,
            "max_speed_mph":     float(evt.get("max_speed_mph") or 0),
            "avg_fuel_pct":      0,
            "harsh_event_count": int(evt.get("harsh_event_count") or 0),
        })

    if not rows:
        return 0
    return await tenant.upsert_vehicle_telemetry_hourly(account_id, rows)


async def aggregate_telemetry_hourly(account_id: int) -> int:
    """Roll the last closed hour into one row per vehicle in
    ``vehicle_telemetry_hourly``.

    Live-path wrapper around ``_aggregate_hour_window`` — computes
    the just-closed hour and delegates.  Miles come from snapshot
    deltas; drive_min / idle_min come from counting samples whose
    engine_state was moving / idle, multiplied by the 5-minute
    snapshot cadence.  Harsh events come from safety_event_log.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    now = datetime.now(timezone.utc)
    prev_hour_start = (
        now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    )
    n = await _aggregate_hour_window(tenant, account_id, prev_hour_start)
    hour_label = prev_hour_start.strftime("%Y-%m-%dT%H:00:00")
    logger.info(
        "aggregate_telemetry_hourly acct=%d hour=%s rows=%d",
        account_id, hour_label, n,
    )
    return n


async def _aggregate_day_window(
    tenant,
    account_id: int,
    day_start: datetime,
) -> int:
    """Aggregate ONE UTC day into ``vehicle_metrics_daily``.

    Shared body of the live cron path and the backfill path.  Sums
    the 24 hourly buckets in ``[day_start, day_start + 1d)`` and
    UPSERTs one row per vehicle.  Idempotent — the daily upsert
    overwrites in place.
    """
    day_end = day_start + timedelta(days=1)
    day_label = day_start.strftime("%Y-%m-%d")
    hour_start_label = day_start.strftime("%Y-%m-%dT%H:00:00")
    hour_end_label = day_end.strftime("%Y-%m-%dT%H:00:00")

    cols = [
        "vehicle_id", "miles", "drive_min", "idle_min",
        "max_speed_mph", "avg_fuel_pct", "harsh_event_count",
    ]
    cur = await tenant._db.execute(
        """
        SELECT vehicle_id,
               SUM(COALESCE(miles, 0))             AS miles,
               SUM(COALESCE(drive_min, 0))         AS drive_min,
               SUM(COALESCE(idle_min, 0))          AS idle_min,
               MAX(COALESCE(max_speed_mph, 0))     AS max_speed_mph,
               AVG(COALESCE(avg_fuel_pct, 0))      AS avg_fuel_pct,
               SUM(COALESCE(harsh_event_count, 0)) AS harsh_event_count
          FROM vehicle_telemetry_hourly
         WHERE account_id = ?
           AND hour_utc >= ?
           AND hour_utc <  ?
         GROUP BY vehicle_id
        """,
        (account_id, hour_start_label, hour_end_label),
    )
    hourly_agg = await cur.fetchall()

    # End-of-day odometer + engine-hours, read straight from the 5-min
    # snapshots for this day.  Both readings are monotonic (cumulative),
    # so MAX over the day == the last reading of the day — no correlated
    # subquery needed.  This carries the long-lived odometer into the
    # 730-day daily tier so back-dated work orders resolve far past the
    # 7-day snapshot retention.  Window matches the hourly labels above
    # (the snapshot ``captured_at`` may be ISO "…T..Z" or "… ..", and a
    # bare-hour label sorts correctly against both).
    eod_by_vid: dict[str, dict] = {}
    cur = await tenant._db.execute(
        """
        SELECT vehicle_id,
               MAX(odometer_mi)  AS odometer_eod,
               MAX(engine_hours) AS engine_hours_eod
          FROM vehicle_state_snapshot
         WHERE account_id = ?
           AND captured_at >= ?
           AND captured_at <  ?
           AND odometer_mi IS NOT NULL
         GROUP BY vehicle_id
        """,
        (account_id, hour_start_label, hour_end_label),
    )
    for r in await cur.fetchall():
        d = dict(zip(("vehicle_id", "odometer_eod", "engine_hours_eod"), r))
        vid = str(d.get("vehicle_id") or "")
        if vid:
            eod_by_vid[vid] = d

    rows = []
    for r in hourly_agg:
        d = dict(zip(cols, r))
        vid = str(d.get("vehicle_id") or "")
        if not vid:
            continue
        eod = eod_by_vid.get(vid, {})
        rows.append({
            "vehicle_id":        vid,
            "day_utc":           day_label,
            "miles":             float(d.get("miles") or 0),
            "drive_min":         float(d.get("drive_min") or 0),
            "idle_min":          float(d.get("idle_min") or 0),
            "max_speed_mph":     d.get("max_speed_mph"),
            "avg_fuel_pct":      d.get("avg_fuel_pct"),
            "harsh_event_count": int(d.get("harsh_event_count") or 0),
            # End-of-day fault count would need a snapshot lookup at
            # day-end; left at 0 until the predictive-maintenance
            # consumer needs it (column reserved so adding it later
            # doesn't require another migration).
            "fault_count_eod":   0,
            "odometer_eod":      eod.get("odometer_eod"),
            "engine_hours_eod":  eod.get("engine_hours_eod"),
        })
    if not rows:
        return 0
    return await tenant.upsert_vehicle_metrics_daily(account_id, rows)


async def aggregate_metrics_daily(account_id: int) -> int:
    """Roll the previous UTC day's hourly buckets into one daily row
    per vehicle in ``vehicle_metrics_daily``.

    Live-path wrapper around ``_aggregate_day_window`` — computes
    "yesterday UTC" and delegates.  Runs at 00:05 UTC daily.

    Daily storage is the right tier for year-over-year comparisons,
    fleet-wide executive scorecards, and long-horizon trend lines:
    querying 730 rows beats querying 17,520 hourly rows on the same
    horizon.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    now = datetime.now(timezone.utc)
    day_start = (now - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    n = await _aggregate_day_window(tenant, account_id, day_start)
    day_label = day_start.strftime("%Y-%m-%d")
    logger.info(
        "aggregate_metrics_daily acct=%d day=%s rows=%d",
        account_id, day_label, n,
    )
    return n


async def backfill_aggregations(
    account_id: int,
    *,
    days: int = 30,
) -> dict[str, int]:
    """Replay hourly + daily aggregators for the last ``days`` days.

    Designed to run right after a fresh ``backfill_vehicle_history``
    completes, so the calendar projection has data the moment the
    Samsara backfill finishes.

    Pipeline:
      1. For each hour in the last ``days * 24`` hours: re-aggregate
         from ``vehicle_state_snapshot`` into ``vehicle_telemetry_hourly``.
      2. For each UTC day in the last ``days`` days: roll the hourly
         buckets into one row per vehicle in ``vehicle_metrics_daily``.

    Both steps are idempotent (UPSERT semantics) so running this
    alongside the live cron jobs is safe — the cron writes the same
    rows the backfill writes; whoever writes second wins, both
    answers are identical.

    Returns ``{"hours": N, "days": M, "hourly_rows": ..., "daily_rows": ...}``
    so the caller can log progress.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return {"hours": 0, "days": 0, "hourly_rows": 0, "daily_rows": 0}

    now = datetime.now(timezone.utc)
    # Re-aggregate hours back to the start of (now - days days), then
    # forward to the just-closed hour.  Most accounts have ~720 hours
    # for a 30-day backfill — each query is a bounded GROUP BY on an
    # indexed column so total wall-clock is typically under a minute.
    hourly_rows_total = 0
    daily_rows_total = 0
    hours_done = 0
    days_done = 0

    current_hour_start = now.replace(minute=0, second=0, microsecond=0)
    earliest_hour_start = current_hour_start - timedelta(hours=days * 24)
    cursor = earliest_hour_start
    # ``<=`` so the in-progress hour gets aggregated too — operators
    # were told "calendar works the moment this backfill finishes",
    # so leaving the current hour unaggregated until the next cron
    # tick would make that promise off by up to one hour.  The hour
    # is idempotent — the next cron pass overwrites with the
    # complete value when the hour closes.
    while cursor <= current_hour_start:
        try:
            hourly_rows_total += await _aggregate_hour_window(
                tenant, account_id, cursor,
            )
        except Exception as e:
            logger.warning(
                "backfill_aggregations acct=%d hour=%s failed: %s",
                account_id, cursor.isoformat(), e,
            )
        hours_done += 1
        cursor += timedelta(hours=1)

    # Daily roll-up — once the hourly table is fully populated, each
    # day's aggregation is a fast SUM over 24 rows.
    today_utc_start = now.replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    earliest_day_start = today_utc_start - timedelta(days=days)
    day_cursor = earliest_day_start
    while day_cursor < today_utc_start:
        try:
            daily_rows_total += await _aggregate_day_window(
                tenant, account_id, day_cursor,
            )
        except Exception as e:
            logger.warning(
                "backfill_aggregations acct=%d day=%s failed: %s",
                account_id, day_cursor.date().isoformat(), e,
            )
        days_done += 1
        day_cursor += timedelta(days=1)

    logger.info(
        "backfill_aggregations acct=%d hours=%d days=%d "
        "hourly_rows=%d daily_rows=%d",
        account_id, hours_done, days_done,
        hourly_rows_total, daily_rows_total,
    )
    return {
        "hours": hours_done,
        "days": days_done,
        "hourly_rows": hourly_rows_total,
        "daily_rows": daily_rows_total,
    }


# (Telemetry-history pruning — snapshot 7d / hourly 90d / daily 730d — now
# runs through the cross-cutting Retention hub; the Vehicle feature owns
# those targets in features/vehicles/retention.py.  See capabilities/retention.)


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
    the Samsara API or saturate the Postgres connection pool.
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
                # Stamp the RLS GUC for this account so every
                # ``tenant._db.execute(...)`` inside the ingestor sees
                # ``app.account_id`` set.  Without this, RLS-enforced
                # mode (ENABLE_RLS=1, application user NOBYPASSRLS)
                # makes every aggregation query return zero rows —
                # silently breaking the telemetry pipeline.
                tenant_db = await get_tenant_db(acc.id)
                async with tenant_db.with_account(acc.id):
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


# ── Capability-aware fan-out ─────────────────────────────────────
#
# Wraps the bare ``_for_each_active_account`` with per-account
# integration-toggle checks so jobs only run for accounts whose
# feature_toggles enable the capability AND whose integration row
# status is ``connected``.
#
# Default-on semantics: when an account has no integration row at all
# (legacy / unmigrated accounts that pre-date M1), the job runs
# unconditionally — preserves existing production behaviour during
# rollout.  Once M1's data-backfill migration runs in production,
# every existing account has a row and toggles take effect.

async def _for_each_account_with_capability(
    capability: str,
    coro_factory,
    *,
    provider_id: str = "samsara",
) -> None:
    """Fan out a job to accounts that have ``capability`` enabled.

    For each active account:
      1. Look up its integration row for ``provider_id``.
      2. Skip if status is anything other than ``connected``.
      3. Skip if ``feature_toggles[capability]["enabled"]`` is False.
      4. Run ``coro_factory(account_id)`` under the same RLS / error-
         containment harness as the bare fan-out helper.

    When an account has no integration row yet (mid-rollout, legacy
    account, account created via the old code path), the job runs.
    This keeps every existing pipeline working while rollout
    happens; once production runs migration 084, every active
    account has a row.
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
    skipped_disabled = 0
    skipped_paused = 0
    ran = 0
    lock = asyncio.Lock()

    async def _run(acc):
        nonlocal skipped_disabled, skipped_paused, ran
        async with sem:
            t_acct = _time.perf_counter()
            try:
                tenant_db = await get_tenant_db(acc.id)
                async with tenant_db.with_account(acc.id):
                    db = get_platform_db()
                    ai = await db.get_account_integration(acc.id, provider_id)
                    if ai is not None:
                        if ai.status != "connected":
                            async with lock:
                                skipped_paused += 1
                            return
                        cap_cfg = ai.feature_toggles.get(capability) or {}
                        # Default-on for accounts that exist but
                        # whose toggle map predates this capability
                        # (e.g. a new capability the catalog adds
                        # after the row was first written).  Operators
                        # turn it off explicitly via the dashboard.
                        if cap_cfg.get("enabled", True) is False:
                            async with lock:
                                skipped_disabled += 1
                            return
                    await coro_factory(acc.id)
                    async with lock:
                        ran += 1
            except Exception:
                logger.exception(
                    "ingestor: per-account job failed acct=%d cap=%s",
                    acc.id, capability,
                )
            finally:
                per_acct_ms[acc.id] = round(
                    (_time.perf_counter() - t_acct) * 1000, 1,
                )

    await asyncio.gather(*(_run(a) for a in accounts))
    total_ms = round((_time.perf_counter() - t0) * 1000, 1)
    if per_acct_ms:
        slowest_aid = max(per_acct_ms, key=per_acct_ms.get)
        logger.info(
            "ingestor job=%s cap=%s accounts=%d ran=%d "
            "skipped_disabled=%d skipped_paused=%d total_ms=%s "
            "slowest_acct=%d slowest_ms=%s",
            job_name, capability, len(accounts),
            ran, skipped_disabled, skipped_paused, total_ms,
            slowest_aid, per_acct_ms[slowest_aid],
        )


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


# The downsampling roll-ups (snapshot_vehicle_state → aggregate_telemetry_hourly
# → aggregate_metrics_daily, defined above) are PROVIDER-AGNOSTIC warehouse
# plumbing: they downsample whatever raw vehicle state already sits in the
# warehouse (vehicle_state → vehicle_state_snapshot → hourly → daily),
# regardless of which provider filled it (Samsara today, Motive/Geotab later).
# They no longer have scheduler wrappers here — they're registered as a cascade
# with the Roll-up hub (features/vehicles/rollups.py → capabilities/rollups),
# which fans each stage across EVERY active account on its cadence (no provider
# gate: an account with no telemetry is a harmless no-op).  A second high-
# frequency stream registers its own cascade the same way.


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
