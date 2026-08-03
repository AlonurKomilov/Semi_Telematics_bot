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

# A single jump between consecutive odometer readings this large is the
# device re-baselining, not a truck driving.  Deliberately far above any
# real bucket: readings arrive in coarse quanta and in catch-up bursts
# after a quiet stretch, so the largest legitimate one-bucket absorption
# measured across 126k production buckets is ~5,165 miles.  This sits at
# roughly twice that, low enough to catch the re-baselines (the worst
# real one booked 24,352 miles) and high enough that no honest reading
# has ever approached it.  Distance is dropped, never clamped — a
# clamped value would look like miles somebody drove.
IMPLAUSIBLE_STEP_MILES = 10_000.0

# A sample's state persists until the NEXT sample — that gap is the
# duty time it earns.  Capped, because a gap is only believable while
# the feed is alive: when a truck goes silent mid-hour, its last known
# state may claim at most this much, not the rest of the hour.  The
# cap also makes duty math CADENCE-INDEPENDENT — history sampled every
# 5 minutes and new data sampled every minute compute correctly in the
# same query, because nothing multiplies by an assumed interval.
DUTY_GAP_CAP_MIN = 10.0


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
            "registry_id":        sr.get("registry_id"),
            "source_ts":          sr.get("source_ts"),
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
    cols = ["vehicle_id", "harsh_event_count", "max_speed_mph", "registry_id"]
    cur = await tenant._db.execute(
        """
        SELECT vehicle_id,
               COUNT(*) AS harsh_event_count,
               MAX(COALESCE(speed_mph, 0)) AS max_speed_mph,
               MAX(registry_id) AS registry_id
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
    #
    # Miles sum the POSITIVE, plausible steps between consecutive
    # readings rather than taking MAX-MIN across the window.  For a
    # healthy odometer the two are identical (the steps telescope), but
    # they part company exactly where the odometer misbehaves: a gateway
    # that re-baselines mid-window makes MAX-MIN report the whole old
    # reading as distance driven (one truck booked 24,352 miles in a
    # single hour that way), and a reading that resumes after a silent
    # stretch dumps the entire catch-up into whichever bucket saw both
    # values.  Steps beyond ``IMPLAUSIBLE_STEP_MILES`` are dropped, not
    # clamped: they are an artefact of the device, not distance the
    # truck covered.  Negative steps are resets and contribute nothing.
    dist_cols = [
        "vehicle_id", "miles_in_window", "reset_steps", "jump_steps",
        "odometer_eoh", "engine_hours_eoh",
    ]
    cur = await tenant._db.execute(
        """
        SELECT vehicle_id,
               SUM(CASE WHEN step > 0 AND step <= ? THEN step ELSE 0 END)
                                                   AS miles_in_window,
               SUM(CASE WHEN step < 0 THEN 1 ELSE 0 END) AS reset_steps,
               SUM(CASE WHEN step > ? THEN 1 ELSE 0 END) AS jump_steps,
               MAX(odometer_mi)                    AS odometer_eoh,
               MAX(engine_hours)                   AS engine_hours_eoh
          FROM (
              SELECT vehicle_id, odometer_mi, engine_hours,
                     odometer_mi - LAG(odometer_mi) OVER (
                         PARTITION BY vehicle_id ORDER BY captured_at
                     ) AS step
                FROM vehicle_state_snapshot
               WHERE account_id = ?
                 AND captured_at >= ?
                 AND captured_at <  ?
                 AND odometer_mi IS NOT NULL
          ) ordered
         GROUP BY vehicle_id
        """,
        # Placeholder order follows the SQL text: the two ceilings sit in
        # the select list, ahead of the derived table's own filters.
        (IMPLAUSIBLE_STEP_MILES, IMPLAUSIBLE_STEP_MILES,
         account_id, hour_start.isoformat(), hour_end.isoformat()),
    )
    dist_by_vid = {
        str(d["vehicle_id"]): d
        for d in (dict(zip(dist_cols, r)) for r in await cur.fetchall())
        if d.get("vehicle_id")
    }

    # Duty time, speed and fuel come from EVERY snapshot in the window,
    # not just the ones carrying an odometer.  Sharing the query above
    # would have made a working odometer a precondition for counting
    # drive minutes — and a truck whose CAN bus reports engine state but
    # no mileage is common enough that it would leave a real chunk of the
    # fleet at zero duty forever.
    duty_cols = [
        "vehicle_id", "max_speed", "avg_fuel_pct",
        "drive_min", "idle_min", "source_ts", "registry_id",
    ]
    cur = await tenant._db.execute(
        """
        SELECT vehicle_id,
               MAX(COALESCE(speed_mph, 0))         AS max_speed,
               AVG(fuel_pct)                       AS avg_fuel_pct,
               SUM(CASE WHEN engine_state = 'moving' THEN duty_min ELSE 0 END)
                                                   AS drive_min,
               SUM(CASE WHEN engine_state = 'idle'   THEN duty_min ELSE 0 END)
                                                   AS idle_min,
               MAX(source_ts)                      AS source_ts,
               MAX(registry_id)                    AS registry_id
          FROM (
              SELECT vehicle_id, speed_mph, fuel_pct, engine_state,
                     source_ts,
                     registry_id,
                     LEAST(
                         EXTRACT(EPOCH FROM (
                             COALESCE(
                                 LEAD(captured_at::timestamptz) OVER (
                                     PARTITION BY vehicle_id
                                     ORDER BY captured_at
                                 ),
                                 ?::timestamptz
                             ) - captured_at::timestamptz
                         )) / 60.0,
                         ?
                     ) AS duty_min
                FROM vehicle_state_snapshot
               WHERE account_id = ?
                 AND captured_at >= ?
                 AND captured_at <  ?
          ) samples
         GROUP BY vehicle_id
        """,
        # Placeholder order follows the SQL text: the window-end bound
        # and the gap cap sit in the derived table's select list, ahead
        # of its filters.  The bound is passed as a real datetime — the
        # ::timestamptz cast makes the driver expect one.
        (hour_end, DUTY_GAP_CAP_MIN,
         account_id, hour_start.isoformat(), hour_end.isoformat()),
    )
    snap_rows = []
    for r in await cur.fetchall():
        d = dict(zip(duty_cols, r))
        vid = str(d.get("vehicle_id") or "")
        if not vid:
            continue
        d.update({k: v for k, v in dist_by_vid.pop(vid, {}).items()
                  if k != "vehicle_id"})
        snap_rows.append(d)
    # A vehicle can report an odometer in a window it reported nothing
    # else in; its distance still counts.
    snap_rows.extend(dist_by_vid.values())

    rows: list[dict[str, Any]] = []
    seen_vids: set[str] = set()
    for sr in snap_rows:
        vid = str(sr.get("vehicle_id") or "")
        if not vid:
            continue
        seen_vids.add(vid)
        miles = max(0.0, float(sr.get("miles_in_window") or 0))
        dropped = int(sr.get("reset_steps") or 0) + int(sr.get("jump_steps") or 0)
        if dropped:
            logger.warning(
                "odometer discontinuity acct=%d vehicle=%s hour=%s "
                "resets=%s jumps=%s — those steps excluded from miles",
                account_id, vid, hour_label,
                sr.get("reset_steps"), sr.get("jump_steps"),
            )
        max_speed = float(sr.get("max_speed") or 0)
        raw_fuel = sr.get("avg_fuel_pct")
        avg_fuel = float(raw_fuel) if raw_fuel is not None else None
        drive_min = round(float(sr.get("drive_min") or 0), 1)
        idle_min = round(float(sr.get("idle_min") or 0), 1)
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
            # End-of-hour odometer/engine-hours (max over the hour's
            # samples — both monotonic).  Banked so hour-precision
            # mileage boundaries survive the 7-day snapshot prune:
            # the hourly tier keeps 90 days.
            "odometer_eod":      sr.get("odometer_eoh"),
            "engine_hours_eod":  sr.get("engine_hours_eoh"),
            # Propagated, never minted: the hour's world-time is the
            # freshest its samples carried — and its identity is the one
            # the samples already resolved.
            "source_ts":         sr.get("source_ts"),
            "registry_id":       sr.get("registry_id"),
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
            "avg_fuel_pct":      None,
            "harsh_event_count": int(evt.get("harsh_event_count") or 0),
            "registry_id":       evt.get("registry_id"),
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
        "source_ts", "registry_id",
    ]
    cur = await tenant._db.execute(
        """
        SELECT vehicle_id,
               SUM(COALESCE(miles, 0))             AS miles,
               SUM(COALESCE(drive_min, 0))         AS drive_min,
               SUM(COALESCE(idle_min, 0))          AS idle_min,
               MAX(COALESCE(max_speed_mph, 0))     AS max_speed_mph,
               AVG(avg_fuel_pct)                   AS avg_fuel_pct,
               SUM(COALESCE(harsh_event_count, 0)) AS harsh_event_count,
               MAX(source_ts)                      AS source_ts,
               MAX(registry_id)                    AS registry_id
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
            "source_ts":         d.get("source_ts"),
            "registry_id":       d.get("registry_id"),
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

    # Close the day's LAST hour ourselves before summing it.  Both crons
    # fire at :05, so this one used to read the hourly tier while the
    # 23:00 bucket was still being written by the other — every day lost
    # its final hour, and with it 6-9% of the day's miles.  Ordering now
    # comes from the code rather than from which job happens to win.
    await _aggregate_hour_window(
        tenant, account_id, day_start + timedelta(hours=23),
    )

    n = await _aggregate_day_window(tenant, account_id, day_start)
    day_label = day_start.strftime("%Y-%m-%d")
    logger.info(
        "aggregate_metrics_daily acct=%d day=%s rows=%d",
        account_id, day_label, n,
    )

    # Re-roll the recent past unconditionally.  Filling only ABSENT days
    # left every day that was written WRONG — a hole heals itself, a bad
    # number never did.  The daily tier sums the hourly one, which keeps
    # 90 days, so re-summing is cheap, idempotent, and picks up whatever
    # later corrected the hours underneath (a history backfill, the hour
    # -23 fix above).  Bounded by _DAILY_HEAL_DAYS: past the hourly
    # tier's reach there is nothing new to learn.
    #
    # Anything repaired by hand must be written to the HOURLY tier, not
    # straight into a daily row — the next pass rebuilds daily rows from
    # hours and would quietly undo it.
    healed = 0
    rolled = 0
    for i in range(1, _DAILY_HEAL_DAYS + 1):
        past = day_start - timedelta(days=i)
        # Only days the 5-min tier still covers.  Past that edge the
        # hours can no longer be rebuilt, so re-summing would republish
        # the hourly tier over a daily row that may be the better copy.
        if not await _day_has_snapshots(tenant, account_id, past):
            continue
        healed += await _aggregate_day_window(tenant, account_id, past)
        rolled += 1
    logger.info(
        "aggregate_metrics_daily RE-ROLLED acct=%d days=%d rows=%d",
        account_id, rolled, healed,
    )
    return n + healed


# How far back each pass re-rolls.  Daily is capped by the HOURLY tier's
# own retention (a day can only be rebuilt while its hourly rows survive);
# weekly reads the 730-day daily tier, so it can afford a longer memory.
_DAILY_HEAL_DAYS = 7
_WEEKLY_HEAL_WEEKS = 4


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
        "source_ts", "registry_id",
    ]
    cur = await tenant._db.execute(
        """
        SELECT vehicle_id,
               SUM(COALESCE(miles, 0))             AS miles,
               SUM(COALESCE(drive_min, 0))         AS drive_min,
               SUM(COALESCE(idle_min, 0))          AS idle_min,
               MAX(COALESCE(max_speed_mph, 0))     AS max_speed_mph,
               AVG(avg_fuel_pct)                   AS avg_fuel_pct,
               SUM(COALESCE(harsh_event_count, 0)) AS harsh_event_count,
               MAX(COALESCE(fault_count_eod, 0))   AS fault_count_eod,
               MAX(odometer_eod)                   AS odometer_eod,
               MAX(engine_hours_eod)               AS engine_hours_eod,
               MAX(source_ts)                      AS source_ts,
               MAX(registry_id)                    AS registry_id
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
            "source_ts":         d.get("source_ts"),
            "registry_id":       d.get("registry_id"),
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

    # Re-roll recent weeks unconditionally, for the same reason the
    # daily pass does: a skipped Monday used to cost a whole week
    # forever, and a week rolled from a daily tier that was later
    # repaired kept its stale total just as permanently (production
    # carried weeks disagreeing with their own days by up to 85%).  The
    # daily tier is kept 730 days, so re-summing a few weeks is cheap.
    healed = 0
    for i in range(1, _WEEKLY_HEAL_WEEKS + 1):
        past = last_week_monday - timedelta(days=7 * i)
        rows = await _aggregate_week_window(tenant, account_id, past)
        healed += rows
    logger.info(
        "aggregate_metrics_weekly RE-ROLLED acct=%d weeks=%d rows=%d",
        account_id, _WEEKLY_HEAL_WEEKS, healed,
    )
    return n + healed


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


async def _day_has_snapshots(tenant, account_id: int, day_start: datetime) -> bool:
    """Whether the 5-min tier still covers this UTC day."""
    cur = await tenant._db.execute(
        "SELECT 1 FROM vehicle_state_snapshot WHERE account_id = ? "
        "AND captured_at >= ? AND captured_at < ? LIMIT 1",
        (account_id,
         day_start.strftime("%Y-%m-%d"),
         (day_start + timedelta(days=1)).strftime("%Y-%m-%d")),
    )
    return await cur.fetchone() is not None


async def reaggregate_window(
    account_id: int,
    *,
    first_day: datetime,
    last_day: datetime,
    rebuild_unbacked_days: bool = False,
) -> dict[str, int]:
    """Rebuild the hourly + daily tiers over an EXPLICIT UTC day range.

    ``backfill_aggregations`` walks back from *now* by a day count, which
    suits a fresh history backfill but not a repair: a damaged or missing
    day has to be rebuilt without disturbing the days around it.  This
    states the window instead — inclusive on both ends — so the blast
    radius is exactly what the caller named.

    Both tiers upsert, so calls are idempotent and the hourly pass
    completes before the daily one sums it.  What the window CANNOT
    rebuild it leaves alone: an hour whose 5-min snapshots have aged out
    produces no row, and the daily upsert keeps the reading it already
    banked.

    Days the 5-min tier no longer covers are left ALONE by default.
    Their hours can no longer be rebuilt, so re-summing a daily row
    there does not refresh it — it merely republishes whatever the hourly
    tier happens to hold, and if anything damaged those hours the daily
    row was the last copy of the better number.  Pass
    ``rebuild_unbacked_days`` only right after a history backfill has put
    real readings back underneath them.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return {"hours": 0, "days": 0, "hourly_rows": 0, "daily_rows": 0}

    day_start = first_day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_last = last_day.replace(hour=0, minute=0, second=0, microsecond=0)
    if day_last < day_start:
        return {"hours": 0, "days": 0, "hourly_rows": 0, "daily_rows": 0}

    end_exclusive = day_last + timedelta(days=1)

    # Decide per day BEFORE touching either tier.  A day the 5-min tier
    # no longer covers cannot be rebuilt from anything local, and running
    # the passes anyway is not merely useless: the hourly pass still
    # emits a zero-mile row for any vehicle that logged a safety event
    # that hour, overwriting the real mileage the tier was holding.
    buildable: list[datetime] = []
    skipped: list[str] = []
    day_cursor = day_start
    while day_cursor < end_exclusive:
        if rebuild_unbacked_days or await _day_has_snapshots(
            tenant, account_id, day_cursor,
        ):
            buildable.append(day_cursor)
        else:
            skipped.append(day_cursor.strftime("%Y-%m-%d"))
        day_cursor += timedelta(days=1)

    hourly_rows = 0
    hours_done = 0
    for day in buildable:
        for hour in range(24):
            hourly_rows += await _aggregate_hour_window(
                tenant, account_id, day + timedelta(hours=hour),
            )
            hours_done += 1

    daily_rows = 0
    days_done = 0
    for day in buildable:
        daily_rows += await _aggregate_day_window(tenant, account_id, day)
        days_done += 1

    if skipped:
        logger.warning(
            "reaggregate_window acct=%d left %d day(s) alone — no 5-min "
            "coverage left to rebuild them from: %s",
            account_id, len(skipped), ", ".join(skipped),
        )

    logger.info(
        "reaggregate_window acct=%d %s..%s hours=%d days=%d "
        "hourly_rows=%d daily_rows=%d",
        account_id, day_start.strftime("%Y-%m-%d"), day_last.strftime("%Y-%m-%d"),
        hours_done, days_done, hourly_rows, daily_rows,
    )
    return {
        "hours": hours_done,
        "days": days_done,
        "hourly_rows": hourly_rows,
        "daily_rows": daily_rows,
    }
