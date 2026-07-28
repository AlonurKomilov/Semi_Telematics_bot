"""Vehicle telemetry warehouse — aggregation (the roll-up tier builders).

These functions downsample data ALREADY in the warehouse into the tiered
history: the raw 5-min ``vehicle_state_snapshot`` rolls up into the unified
``vehicle_telemetry`` aggregate table (``granularity`` hourly → daily →
weekly).  They are PROVIDER-AGNOSTIC — they read local tables and make no
integration call — and are registered as the vehicle roll-up cascade with
the data-lifecycle hub (see ``features/vehicles/lifecycle.py``).

The provider-specific INGEST that fills the raw tables lives WITH each
integration (``capabilities/integrations/samsara/sync.py``), not here — the
warehouse only stores + aggregates.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from infra.services import get_tenant_db

logger = logging.getLogger(__name__)


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
    """Aggregate ONE hour into the hourly tier of ``vehicle_telemetry``.

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
    """Roll the last closed hour into one row per vehicle in the
    hourly tier of ``vehicle_telemetry``.

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
    """Aggregate ONE UTC day into the daily tier of ``vehicle_telemetry``.

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
          FROM vehicle_telemetry
         WHERE account_id = ?
           AND granularity = 'hourly'
           AND bucket_start >= ?
           AND bucket_start <  ?
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
    per vehicle in the daily tier of ``vehicle_telemetry``.

    Live-path wrapper around ``_aggregate_day_window`` — computes
    "yesterday UTC" and delegates.  Runs at 00:05 UTC daily.

    Daily storage is the right tier for year-over-year comparisons,
    account-wide executive scorecards, and long-horizon trend lines:
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


async def _aggregate_week_window(
    tenant,
    account_id: int,
    week_start: datetime,
) -> int:
    """Aggregate ONE ISO week (Mon..Sun) into the weekly tier.

    Rolls the up-to-7 daily rows in ``[week_start, week_start + 7d)`` into
    one weekly row per vehicle.  ``week_start`` must be a Monday 00:00 UTC.
    End-of-day odometer / engine-hours carry the LATEST daily reading in
    the week (both monotonic → MAX == last reading), so the weekly tier
    stays usable for long-horizon back-dated reads.
    """
    week_end = week_start + timedelta(days=7)
    week_label = week_start.strftime("%Y-%m-%d")
    day_start_label = week_start.strftime("%Y-%m-%d")
    day_end_label = week_end.strftime("%Y-%m-%d")

    cols = [
        "vehicle_id", "miles", "drive_min", "idle_min",
        "max_speed_mph", "avg_fuel_pct", "harsh_event_count",
        "fault_count_eod", "odometer_eod", "engine_hours_eod",
    ]
    cur = await tenant._db.execute(
        """
        SELECT vehicle_id,
               SUM(COALESCE(miles, 0))             AS miles,
               SUM(COALESCE(drive_min, 0))         AS drive_min,
               SUM(COALESCE(idle_min, 0))          AS idle_min,
               MAX(COALESCE(max_speed_mph, 0))     AS max_speed_mph,
               AVG(COALESCE(avg_fuel_pct, 0))      AS avg_fuel_pct,
               SUM(COALESCE(harsh_event_count, 0)) AS harsh_event_count,
               MAX(COALESCE(fault_count_eod, 0))   AS fault_count_eod,
               MAX(odometer_eod)                   AS odometer_eod,
               MAX(engine_hours_eod)               AS engine_hours_eod
          FROM vehicle_telemetry
         WHERE account_id = ?
           AND granularity = 'daily'
           AND bucket_start >= ?
           AND bucket_start <  ?
         GROUP BY vehicle_id
        """,
        (account_id, day_start_label, day_end_label),
    )
    rows: list[dict[str, Any]] = []
    for r in await cur.fetchall():
        d = dict(zip(cols, r))
        vid = str(d.get("vehicle_id") or "")
        if not vid:
            continue
        rows.append({
            "vehicle_id":        vid,
            "week_utc":          week_label,
            "miles":             float(d.get("miles") or 0),
            "drive_min":         float(d.get("drive_min") or 0),
            "idle_min":          float(d.get("idle_min") or 0),
            "max_speed_mph":     d.get("max_speed_mph"),
            "avg_fuel_pct":      d.get("avg_fuel_pct"),
            "harsh_event_count": int(d.get("harsh_event_count") or 0),
            "fault_count_eod":   int(d.get("fault_count_eod") or 0),
            "odometer_eod":      d.get("odometer_eod"),
            "engine_hours_eod":  d.get("engine_hours_eod"),
        })
    if not rows:
        return 0
    return await tenant.upsert_vehicle_metrics_weekly(account_id, rows)


async def aggregate_metrics_weekly(account_id: int) -> int:
    """Roll the just-completed ISO week's daily rows into the weekly tier.

    Live-path wrapper around ``_aggregate_week_window`` — runs Mondays at
    00:10 UTC (after the daily roll-up) and targets the previous Mon..Sun
    week.  Aligns to Monday so a backfill call on any weekday still targets
    whole weeks.

    Weekly is the long-horizon tier (~5-year retention): multi-year
    year-over-year trends without scanning 730 daily rows per vehicle.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    this_week_monday = today - timedelta(days=today.weekday())
    last_week_monday = this_week_monday - timedelta(days=7)
    n = await _aggregate_week_window(tenant, account_id, last_week_monday)
    logger.info(
        "aggregate_metrics_weekly acct=%d week=%s rows=%d",
        account_id, last_week_monday.strftime("%Y-%m-%d"), n,
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
         from ``vehicle_state_snapshot`` into the hourly tier.
      2. For each UTC day in the last ``days`` days: roll the hourly
         buckets into one row per vehicle in the daily tier.

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
