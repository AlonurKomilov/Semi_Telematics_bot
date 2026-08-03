"""telemetry warehouse mixin.

Read + upsert helpers for the four warehouse tables created in
``schema.py``::

  - vehicle_state          (one row per vehicle, overwritten)
  - safety_event_log       (append-only, idempotent on samsara_event_id)
  - driver_efficiency_daily (one row per driver per day)
  - vehicle_telemetry_hourly (hourly roll-up)

Pure SQL — no Samsara client calls happen here.  The ingestor
(``capabilities/integrations/samsara/sync.py``) is responsible for fetching
upstream data and shaping it before calling these helpers.

API routes never import this module directly; they go through the
reader functions in ``capabilities/warehouse/telemetry/warehouse_reader.py``
which adds the ``WAREHOUSE_READS_ENABLED`` feature-flag check and
graceful fallback to the live Samsara client (so an empty warehouse
during the cutover window doesn't 500 the dashboard).
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable, Optional

logger = logging.getLogger(__name__)


# ── Velocity tuning constants ─────────────────────────────────────
#
# A "drive day" is a day where the vehicle moved meaningfully —
# 5 mi keeps the threshold robust against parking-lot crawl, fuel-
# island repositioning, and GPS jitter that can register as a
# fractional mile.  Below this we treat the day as idle (shop /
# weekend / spare) and exclude it from the median.
VELOCITY_DRIVE_DAY_MIN_MILES = 5.0

# Minimum days of OBSERVED data (regardless of whether they were
# drive or idle) before we'll surface a velocity at all.  Below this
# coverage threshold the projection is noise, and we'd rather show
# nothing than guess.  7 days = at least one full week including
# whatever the truck's weekly cycle is.
VELOCITY_MIN_COVERAGE_DAYS = 7

# Minimum DRIVE days (the median's actual sample size) before we
# trust the result.  3 drive days catches a typical Mon-Wed-Fri
# pattern; below this the median is too dependent on a single point.
VELOCITY_MIN_DRIVE_DAYS = 3

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class at runtime."""
        _db: Any

        def acquire(self) -> Any: ...
        def transaction(self) -> Any: ...
        async def read_all(self, sql: str, params: tuple = ()) -> list: ...
        async def read_one(self, sql: str, params: tuple = ()) -> Any: ...
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _opt_float(value: Any) -> float | None:
    """Coerce a possibly-None / possibly-strings input into ``float | None``.

    Lets snapshot writers pass raw dict-from-JSON values without
    pre-cleaning every column.  Returns None for None, empty strings,
    or unparseable inputs so the column stays NULL rather than turning
    into 0.0 (which would silently corrupt downstream delta math).
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class WarehouseMixin(_MixinBase):
    # ── vehicle_state ────────────────────────────────────────────────

    async def upsert_vehicle_state(
        self,
        account_id: int,
        rows: Iterable[dict[str, Any]],
    ) -> int:
        """Bulk upsert (full overwrite) of current vehicle snapshot.

        Caller passes the ``get_vehicles_overview()`` result already
        reshaped into the warehouse columns (the ingestor does the
        mapping; keeping the SQL dumb makes it trivially testable).
        Returns the number of rows touched.

        We use INSERT OR REPLACE rather than DELETE+INSERT so a transient
        Samsara hiccup (returns 0 vehicles) doesn't blank the table \u2014
        callers should pass the *full* fleet every cycle, but if they
        don't we fail-safe to "stale row beats no row".
        """
        ts = _now_iso()
        # Aggregating in a single transaction keeps the hot path cheap;
        # batches of ~80 vehicles complete in <30 ms locally.
        values: list[tuple] = []
        for r in rows:
            vid = (r.get("vehicle_id") or r.get("id") or "").strip()
            if not vid:
                continue
            values.append((
                vid, account_id,
                str(r.get("vehicle_name") or r.get("name") or ""),
                str(r.get("company_code") or ""),
                r.get("lat"), r.get("lon"),
                r.get("speed_mph"), r.get("heading"),
                str(r.get("address") or ""),
                str(r.get("engine_state") or ""),
                r.get("fuel_pct"), r.get("def_pct"),
                r.get("odometer_mi"),
                r.get("odometer_time"),
                r.get("engine_hours"),
                r.get("engine_hours_time"),
                int(r.get("fault_count") or 0),
                int(r.get("dtc_critical_count") or 0),
                str(r.get("last_driver_id") or ""),
                str(r.get("last_driver_name") or ""),
                r.get("registry_id"),
                r.get("source_ts"),
                # Preserve an empty ``captured_at`` instead of substituting
                # the upsert timestamp.  Billing's activity-window query
                # reads this column to decide whether a vehicle has had
                # any Samsara signal in the last N days; substituting NOW
                # would silently bill vehicles that were added to Samsara
                # but haven't transmitted location yet.
                str(r.get("captured_at") or ""),
                ts,
            ))
        if values:
            await self._db.executemany(
                """
                INSERT INTO vehicle_state (
                    vehicle_id, account_id, vehicle_name, company_code,
                    lat, lon, speed_mph, heading, address,
                    engine_state, fuel_pct, def_pct,
                    odometer_mi, odometer_time,
                    engine_hours, engine_hours_time,
                    fault_count, dtc_critical_count,
                    last_driver_id, last_driver_name,
                    registry_id, source_ts,
                    captured_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vehicle_id) DO UPDATE SET
                    account_id=excluded.account_id,
                    vehicle_name=excluded.vehicle_name,
                    company_code=excluded.company_code,
                    lat=excluded.lat, lon=excluded.lon,
                    speed_mph=excluded.speed_mph, heading=excluded.heading,
                    address=excluded.address,
                    engine_state=excluded.engine_state,
                    fuel_pct=excluded.fuel_pct, def_pct=excluded.def_pct,
                    odometer_mi=COALESCE(excluded.odometer_mi, vehicle_state.odometer_mi),
                    odometer_time=COALESCE(excluded.odometer_time, vehicle_state.odometer_time),
                    engine_hours=COALESCE(excluded.engine_hours, vehicle_state.engine_hours),
                    engine_hours_time=COALESCE(excluded.engine_hours_time, vehicle_state.engine_hours_time),
                    fault_count=excluded.fault_count,
                    dtc_critical_count=excluded.dtc_critical_count,
                    last_driver_id=excluded.last_driver_id,
                    last_driver_name=excluded.last_driver_name,
                    -- The identity WE resolved outranks its absence: a
                    -- tick where the resolver missed keeps the last
                    -- known registry link rather than unlinking history.
                    registry_id=COALESCE(excluded.registry_id, vehicle_state.registry_id),
                    -- A known world-time outranks its absence: a tick
                    -- where every provider marker went missing keeps
                    -- the last honest age instead of erasing it.
                    source_ts=COALESCE(excluded.source_ts, vehicle_state.source_ts),
                    -- Keep the most recent non-empty Samsara location
                    -- timestamp.  If Samsara hiccups and returns the
                    -- vehicle without a location ``time``, we shouldn't
                    -- blank out the previous-good value — billing reads
                    -- this column for activity detection.
                    captured_at=CASE
                        WHEN COALESCE(excluded.captured_at, '') = ''
                            THEN vehicle_state.captured_at
                        ELSE excluded.captured_at
                    END,
                    updated_at=excluded.updated_at
                """,
                values,
            )
        await self._db.commit()
        return len(values)

    async def get_vehicle_state(
        self,
        account_id: int,
        *,
        vehicle_id: str | None = None,
        company: str | None = None,
        vehicle_nums: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Read current vehicle snapshots; filterable for the common
        access patterns the API exposes.  Returns dicts ordered by
        vehicle_name for stable UI rendering."""
        where = ["account_id = ?"]
        args: list[Any] = [account_id]
        if vehicle_id:
            where.append("vehicle_id = ?")
            args.append(vehicle_id)
        if company:
            where.append("company_code = ?")
            args.append(company)
        if vehicle_nums:
            placeholders = ",".join("?" * len(vehicle_nums))
            where.append(f"vehicle_name IN ({placeholders})")
            args.extend(vehicle_nums)

        # Hard-coded column list rather than ``cur.description`` so this
        # works on both SQLite (aiosqlite cursors expose .description)
        # and Postgres (asyncpg adapter cursor doesn't).
        cols = [
            "vehicle_id", "vehicle_name", "company_code",
            "lat", "lon", "speed_mph", "heading", "address",
            "engine_state", "fuel_pct", "def_pct",
            "odometer_mi", "odometer_time",
            "engine_hours", "engine_hours_time",
            "fault_count", "dtc_critical_count",
            "last_driver_id", "last_driver_name",
            "registry_id", "source_ts",
            "captured_at", "updated_at",
        ]
        cur = await self._db.execute(
            f"""
            SELECT {', '.join(cols)}
            FROM vehicle_state
            WHERE {' AND '.join(where)}
            ORDER BY vehicle_name
            """,
            tuple(args),
        )
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    # ── safety_event_log ─────────────────────────────────────────────

    async def insert_safety_events(
        self,
        account_id: int,
        events: Iterable[dict[str, Any]],
    ) -> int:
        """Idempotent insert of Samsara safety events.

        Dedupes on the unique ``samsara_event_id`` index \u2014 INSERT OR
        IGNORE so the periodic ingestor can safely re-pull a 48 h
        sliding window without producing duplicates.  Returns the
        number of *new* rows persisted (post-dedupe).
        """
        ts = _now_iso()
        values: list[tuple] = []
        for e in events:
            evt_id = str(e.get("samsara_event_id") or e.get("id") or "").strip()
            if not evt_id:
                continue
            values.append((
                account_id, evt_id,
                str(e.get("vehicle_id") or ""),
                str(e.get("vehicle_name") or ""),
                str(e.get("driver_id") or ""),
                str(e.get("driver_name") or ""),
                str(e.get("event_type") or ""),
                str(e.get("severity") or ""),
                str(e.get("occurred_at") or ""),
                e.get("lat"), e.get("lon"),
                e.get("speed_mph"),
                str(e.get("video_url") or ""),
                str(e.get("company_code") or ""),
                json.dumps(e.get("raw") or e, default=str),
                # The event's own occurrence time IS its world-time.
                e.get("occurred_at") or None,
                ts,
            ))
        if not values:
            return 0
        # Pre-filter against existing samsara_event_ids so the count of
        # new rows stays accurate after the executemany. Doing this in
        # a single SELECT (vs. trusting executemany rowcount, which is
        # unreliable for OR IGNORE across SQLite/Postgres) is still
        # cheaper than per-row execute().
        evt_ids = [v[1] for v in values]
        existing_ids: set[str] = set()
        # Chunk the IN clause to stay under SQLite's 999-parameter cap.
        for i in range(0, len(evt_ids), 500):
            chunk = evt_ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = await self._db.execute(
                f"SELECT samsara_event_id FROM safety_event_log "
                f"WHERE account_id = ? AND samsara_event_id IN ({placeholders})",
                (account_id, *chunk),
            )
            for row in await cur.fetchall():
                existing_ids.add(str(row[0]))
        new_values = [v for v in values if v[1] not in existing_ids]
        if new_values:
            await self._db.executemany(
                """
                INSERT OR IGNORE INTO safety_event_log (
                    account_id, samsara_event_id,
                    vehicle_id, vehicle_name, driver_id, driver_name,
                    event_type, severity, occurred_at,
                    lat, lon, speed_mph, video_url,
                    company_code,
                    raw_json, source_ts, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                new_values,
            )
        await self._db.commit()
        return len(new_values)

    async def count_safety_events_in_window(
        self,
        account_id: int,
        days: int = 90,
    ) -> int:
        """Return how many safety events the warehouse already has in
        the trailing *days* window — used by the on-connect backfill to
        decide whether to skip the Samsara call (data already there).
        """
        from datetime import timedelta
        since = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        cur = await self._db.execute(
            "SELECT COUNT(*) AS c FROM safety_event_log "
            "WHERE account_id = ? AND occurred_at >= ?",
            (account_id, since),
        )
        row = await cur.fetchone()
        if row is None:
            return 0
        try:
            return int(row["c"])
        except (KeyError, TypeError):
            return int(row[0])

    async def count_driver_efficiency_in_window(
        self,
        account_id: int,
        days: int = 90,
    ) -> int:
        """How many (driver, day) rows the warehouse already has within
        the trailing window.  Used by the on-connect backfill to skip
        the Samsara call when the scoreboard period is already populated.
        """
        from datetime import timedelta
        since = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%d")
        cur = await self._db.execute(
            "SELECT COUNT(*) AS c FROM driver_efficiency_daily "
            "WHERE account_id = ? AND day >= ?",
            (account_id, since),
        )
        row = await cur.fetchone()
        if row is None:
            return 0
        try:
            return int(row["c"])
        except (KeyError, TypeError):
            return int(row[0])

    async def get_safety_events_warehouse(
        self,
        account_id: int,
        *,
        days: int = 7,
        event_type: str | None = None,
        vehicle_id: str | None = None,
        vehicle_name: str | None = None,
        driver_id: str | None = None,
        limit: int = 5000,
        include_raw: bool = True,
    ) -> list[dict[str, Any]]:
        """Read safety events from the warehouse, ordered most-recent
        first.  ``days`` filters on ``occurred_at`` lexicographically
        (ISO-8601 ordering, UTC).

        *include_raw* — when True (default, used by alerting + reporting
        flows that need the full live-Samsara event shape), every row
        decodes its ``raw_json`` blob into a ``raw`` dict.  Set False
        from list-view callers (the dashboard's /safety/events route)
        that only need the SQL columns; skipping the per-row
        ``json.loads`` saves hundreds of ms on a 30-day window.
        """
        from datetime import timedelta
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        where = ["account_id = ?", "occurred_at >= ?"]
        args: list[Any] = [account_id, since]
        if event_type:
            where.append("event_type = ?")
            args.append(event_type)
        if vehicle_id:
            where.append("vehicle_id = ?")
            args.append(vehicle_id)
        if vehicle_name:
            where.append("vehicle_name = ?")
            args.append(vehicle_name)
        if driver_id:
            where.append("driver_id = ?")
            args.append(driver_id)

        # Hard-coded column list rather than ``cur.description`` so this
        # works on both SQLite (aiosqlite cursors expose .description)
        # and Postgres (asyncpg adapter cursor doesn't).  When the caller
        # didn't ask for the raw blob, we skip the column entirely so the
        # 2KB-per-row payload never crosses the DB connection.
        cols = [
            "samsara_event_id", "vehicle_id", "vehicle_name",
            "driver_id", "driver_name",
            "event_type", "severity", "occurred_at",
            "lat", "lon", "speed_mph", "video_url", "company_code",
        ]
        if include_raw:
            cols.append("raw_json")
        cur = await self._db.execute(
            f"""
            SELECT {', '.join(cols)}
            FROM safety_event_log
            WHERE {' AND '.join(where)}
            ORDER BY occurred_at DESC
            LIMIT ?
            """,
            (*args, limit),
        )
        out: list[dict[str, Any]] = []
        for row in await cur.fetchall():
            d = dict(zip(cols, row))
            if include_raw:
                # Decode raw_json on the way out so callers can treat
                # rows like the live-Samsara shape.
                try:
                    d["raw"] = json.loads(d.pop("raw_json") or "{}")
                except Exception:
                    d["raw"] = {}
            out.append(d)
        return out

    async def get_safety_event_counts_grouped(
        self,
        account_id: int,
        *,
        days: int,
        event_types: list[str] | None = None,
        driver_ids: list[str] | None = None,
    ) -> dict[tuple[str, str], int]:
        """Single grouped query → ``{(driver_id, event_type): count}``.

        Replaces the per-(driver × event_type) N+1 in coaching/engine.py.
        Empty ``event_types`` or ``driver_ids`` filters fold the GROUP BY
        across all values for that dimension. Drivers with zero events
        simply won't appear in the result — caller defaults to 0.
        """
        from datetime import timedelta
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        where = ["account_id = ?", "occurred_at >= ?"]
        args: list[Any] = [account_id, since]
        if event_types:
            placeholders = ",".join("?" * len(event_types))
            where.append(f"event_type IN ({placeholders})")
            args.extend(event_types)
        if driver_ids:
            placeholders = ",".join("?" * len(driver_ids))
            where.append(f"driver_id IN ({placeholders})")
            args.extend(driver_ids)

        cur = await self._db.execute(
            f"""
            SELECT driver_id, event_type, COUNT(*) AS cnt
            FROM safety_event_log
            WHERE {' AND '.join(where)}
            GROUP BY driver_id, event_type
            """,
            tuple(args),
        )
        out: dict[tuple[str, str], int] = {}
        for row in await cur.fetchall():
            did = str(row[0] or "")
            et = str(row[1] or "")
            out[(did, et)] = int(row[2] or 0)
        return out

    # ── driver_efficiency_daily ──────────────────────────────────────

    async def upsert_driver_efficiency_daily(
        self,
        account_id: int,
        rows: Iterable[dict[str, Any]],
    ) -> int:
        """Upsert one row per (driver, day).  Caller (ingestor) passes
        Samsara's per-day breakdown shaped to the warehouse columns.
        Counts how many rows were touched."""
        ts = _now_iso()
        values: list[tuple] = []
        for r in rows:
            did = str(r.get("driver_id") or "").strip()
            day = str(r.get("day") or "").strip()
            if not did or not day:
                continue
            values.append((
                account_id, did,
                str(r.get("driver_name") or ""),
                day,
                float(r.get("miles") or 0),
                float(r.get("drive_h") or 0),
                float(r.get("idle_h") or 0),
                r.get("mpg"),
                r.get("antic_pct"),
                r.get("green_pct"),
                int(r.get("harsh_brake") or 0),
                int(r.get("harsh_turn") or 0),
                int(r.get("harsh_accel") or 0),
                float(r.get("overspeed_min") or 0),
                json.dumps(r.get("raw") or {}, default=str),
                ts,
            ))
        if values:
            await self._db.executemany(
                """
                INSERT INTO driver_efficiency_daily (
                    account_id, driver_id, driver_name, day,
                    miles, drive_h, idle_h, mpg, antic_pct, green_pct,
                    harsh_brake, harsh_turn, harsh_accel,
                    overspeed_min, raw_json, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, driver_id, day) DO UPDATE SET
                    driver_name=excluded.driver_name,
                    miles=excluded.miles, drive_h=excluded.drive_h,
                    idle_h=excluded.idle_h, mpg=excluded.mpg,
                    antic_pct=excluded.antic_pct, green_pct=excluded.green_pct,
                    harsh_brake=excluded.harsh_brake,
                    harsh_turn=excluded.harsh_turn,
                    harsh_accel=excluded.harsh_accel,
                    overspeed_min=excluded.overspeed_min,
                    raw_json=excluded.raw_json,
                    ingested_at=excluded.ingested_at
                """,
                values,
            )
        await self._db.commit()
        return len(values)

    async def get_driver_efficiency_window(
        self,
        account_id: int,
        *,
        days: int = 7,
        driver_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate per-driver efficiency over a trailing N-day window.
        Returns the same shape the live Samsara reader emits so callers
        don't need to branch on data source."""
        from datetime import timedelta
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        where = ["account_id = ?", "day >= ?"]
        args: list[Any] = [account_id, since]
        if driver_id:
            where.append("driver_id = ?")
            args.append(driver_id)

        # Hard-coded column list — see comment in get_current_vehicles
        # for the asyncpg compatibility reason.
        cols = [
            "driver_id", "driver_name", "miles", "drive_h", "idle_h",
            "mpg", "antic_pct", "green_pct",
            "harsh_brake", "harsh_turn", "harsh_accel", "overspeed_min",
        ]
        cur = await self._db.execute(
            f"""
            SELECT driver_id,
                   MAX(driver_name)            AS driver_name,
                   SUM(miles)                  AS miles,
                   SUM(drive_h)                AS drive_h,
                   SUM(idle_h)                 AS idle_h,
                   AVG(mpg)                    AS mpg,
                   AVG(antic_pct)              AS antic_pct,
                   AVG(green_pct)              AS green_pct,
                   SUM(harsh_brake)            AS harsh_brake,
                   SUM(harsh_turn)             AS harsh_turn,
                   SUM(harsh_accel)            AS harsh_accel,
                   SUM(overspeed_min)          AS overspeed_min
            FROM driver_efficiency_daily
            WHERE {' AND '.join(where)}
            GROUP BY driver_id
            ORDER BY miles DESC
            """,
            tuple(args),
        )
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    # ── vehicle_telemetry_hourly ────────────────────────────────────

    async def upsert_vehicle_telemetry_hourly(
        self,
        account_id: int,
        rows: Iterable[dict[str, Any]],
    ) -> int:
        """Upsert hourly roll-up rows.  Aggregator computes these from
        ``vehicle_state`` history + safety events; this mixin just
        persists them."""
        ts = _now_iso()
        values: list[tuple] = []
        for r in rows:
            vid = str(r.get("vehicle_id") or "").strip()
            hour = str(r.get("hour_utc") or "").strip()
            if not vid or not hour:
                continue
            values.append((
                account_id, vid, hour,
                float(r.get("miles") or 0),
                float(r.get("drive_min") or 0),
                float(r.get("idle_min") or 0),
                float(r.get("max_speed_mph") or 0),
                _opt_float(r.get("avg_fuel_pct")),
                int(r.get("harsh_event_count") or 0),
                (float(r["odometer_eod"])
                 if r.get("odometer_eod") is not None else None),
                (float(r["engine_hours_eod"])
                 if r.get("engine_hours_eod") is not None else None),
                r.get("source_ts"),
                r.get("registry_id"),
                ts,
            ))
        if values:
            # granularity='hourly' bucket in the unified vehicle_telemetry
            # table; bucket_start carries the old hour_utc label.
            await self._db.executemany(
                """
                INSERT INTO vehicle_telemetry (
                    account_id, vehicle_id, granularity, bucket_start,
                    miles, drive_min, idle_min,
                    max_speed_mph, avg_fuel_pct, harsh_event_count,
                    odometer_eod, engine_hours_eod, source_ts, registry_id,
                    ingested_at
                ) VALUES (?, ?, 'hourly', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, vehicle_id, granularity, bucket_start) DO UPDATE SET
                    miles=excluded.miles,
                    drive_min=excluded.drive_min,
                    idle_min=excluded.idle_min,
                    max_speed_mph=excluded.max_speed_mph,
                    avg_fuel_pct=excluded.avg_fuel_pct,
                    harsh_event_count=excluded.harsh_event_count,
                    -- Keep a prior non-null banked reading when a re-run
                    -- can't recompute one: replaying an hour whose 5-min
                    -- snapshots have since been pruned yields a row only
                    -- from safety events, carrying no odometer at all.
                    odometer_eod=COALESCE(excluded.odometer_eod, vehicle_telemetry.odometer_eod),
                    engine_hours_eod=COALESCE(excluded.engine_hours_eod, vehicle_telemetry.engine_hours_eod),
                    source_ts=COALESCE(excluded.source_ts, vehicle_telemetry.source_ts),
                    registry_id=COALESCE(excluded.registry_id, vehicle_telemetry.registry_id),
                    ingested_at=excluded.ingested_at
                """,
                values,
            )
        await self._db.commit()
        return len(values)

    async def get_vehicle_telemetry_hourly(
        self,
        account_id: int,
        *,
        vehicle_id: str | None = None,
        hours: int = 168,
    ) -> list[dict[str, Any]]:
        """Read the hourly roll-up; default window is 7 days (168 h)
        which covers both the dashboard's vehicle-detail timeline and
        the weekly bot scorecard."""
        from datetime import timedelta
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:00:00")
        where = ["account_id = ?", "granularity = 'hourly'", "bucket_start >= ?"]
        args: list[Any] = [account_id, since]
        if vehicle_id:
            where.append("vehicle_id = ?")
            args.append(vehicle_id)
        # Output keys (positional zip — see get_current_vehicles for the
        # asyncpg compatibility reason); ``hour_utc`` is the unified table's
        # ``bucket_start`` for the hourly grain, kept as the public key.
        cols = [
            "vehicle_id", "hour_utc", "miles", "drive_min", "idle_min",
            "max_speed_mph", "harsh_event_count",
        ]
        cur = await self._db.execute(
            f"""
            SELECT vehicle_id, bucket_start, miles, drive_min, idle_min,
                   max_speed_mph, harsh_event_count
            FROM vehicle_telemetry
            WHERE {' AND '.join(where)}
            ORDER BY bucket_start DESC
            """,
            tuple(args),
        )
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    async def get_vehicle_avg_daily_miles(
        self,
        account_id: int,
        *,
        days: int = 30,
    ) -> dict[str, float]:
        """Average daily miles per vehicle over the last ``days``.

        Aggregates ``vehicle_telemetry_hourly.miles`` per vehicle_id and
        divides by ``days``.  Drives the maintenance-calendar's mileage
        projection: a truck that does 120 mi/day with 600 mi to go on
        the next oil change projects to ~5 days out.

        Returned dict is keyed by ``vehicle_id`` because that's the
        column the warehouse populates; callers that join by
        ``vehicle_name`` should map through the live state table.

        Vehicles with no telemetry in the window are simply absent from
        the returned dict (not present-but-zero) so the caller can tell
        "0 mi/day driven" from "no signal at all" and decide whether to
        project or not.
        """
        from datetime import timedelta
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:00:00",
        )
        cur = await self._db.execute(
            """
            SELECT vehicle_id, SUM(miles) AS total_miles
              FROM vehicle_telemetry
             WHERE account_id = ? AND granularity = 'hourly' AND bucket_start >= ?
             GROUP BY vehicle_id
            """,
            (account_id, since),
        )
        out: dict[str, float] = {}
        for row in await cur.fetchall():
            d = dict(zip(("vehicle_id", "total_miles"), row))
            vid = str(d.get("vehicle_id") or "")
            total = d.get("total_miles") or 0
            if vid and total:
                out[vid] = float(total) / float(days)
        return out


    # ── vehicle_state_snapshot (5-min history) ──────────────────────
    #
    # Persistence layer for the per-vehicle state-over-time history
    # that backs the maintenance calendar projection, safety event
    # correlation, dispatch trip playback, accounting fuel-burn
    # reports, HR hours-of-service reconciliation, and predictive
    # maintenance.  The snapshot job copies vehicle_state rows in;
    # the aggregator job reads them to compute hourly deltas; the
    # retention prune drops rows past the configured window.

    async def upsert_vehicle_state_snapshots(
        self,
        account_id: int,
        rows: Iterable[dict[str, Any]],
    ) -> int:
        """Bulk-insert snapshot rows.  ``ON CONFLICT DO NOTHING`` keeps
        re-runs idempotent — if the scheduler fires twice within the
        same minute the second insert is a no-op rather than a duplicate
        key error."""
        values: list[tuple] = []
        for r in rows:
            vid = str(r.get("vehicle_id") or "").strip()
            ts = str(r.get("captured_at") or "").strip()
            if not vid or not ts:
                continue
            values.append((
                account_id, vid, ts,
                _opt_float(r.get("lat")),
                _opt_float(r.get("lon")),
                _opt_float(r.get("speed_mph")),
                str(r.get("engine_state") or ""),
                _opt_float(r.get("fuel_pct")),
                _opt_float(r.get("def_pct")),
                _opt_float(r.get("odometer_mi")),
                _opt_float(r.get("engine_hours")),
                int(r.get("fault_count") or 0),
                int(r.get("dtc_critical_count") or 0),
                str(r.get("last_driver_id") or ""),
                _opt_float(r.get("battery_v")),
                _opt_float(r.get("oil_psi")),
                _opt_float(r.get("coolant_c")),
                _opt_float(r.get("engine_load_pct")),
                _opt_float(r.get("rpm")),
                r.get("registry_id"),
                r.get("source_ts"),
            ))
        if values:
            await self._db.executemany(
                """
                INSERT INTO vehicle_state_snapshot (
                    account_id, vehicle_id, captured_at,
                    lat, lon, speed_mph, engine_state,
                    fuel_pct, def_pct, odometer_mi, engine_hours,
                    fault_count, dtc_critical_count, last_driver_id,
                    battery_v, oil_psi, coolant_c,
                    engine_load_pct, rpm, registry_id, source_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (account_id, vehicle_id, captured_at) DO NOTHING
                """,
                values,
            )
        await self._db.commit()
        return len(values)

    async def record_ingest_orphans(
        self,
        account_id: int,
        dataset_key: str,
        orphans: list[dict[str, Any]],
    ) -> int:
        """Quarantine provider identities the registry could not place.

        One row per (dataset, account, external id); a re-sighting bumps
        ``count`` and ``last_seen`` instead of inserting again, so the
        table stays the size of the PROBLEM, not of time.  Recording is
        the whole point: an identity that fails to resolve must become
        visible somewhere, or it becomes a phantom vehicle nobody can
        explain — that is how "229 Idris Ahmed" lived in the warehouse
        for weeks.
        """
        if not orphans:
            return 0
        ts = _now_iso()
        values = [
            (
                account_id, dataset_key,
                str(o.get("external_id") or ""),
                str(o.get("name") or ""),
                str(o.get("company_code") or ""),
                ts, ts,
            )
            for o in orphans
            if str(o.get("external_id") or "").strip()
        ]
        if not values:
            return 0
        await self._db.executemany(
            """
            INSERT INTO warehouse_ingest_orphans (
                account_id, dataset_key, external_id,
                name, company_code, count, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT (account_id, dataset_key, external_id) DO UPDATE SET
                name=excluded.name,
                company_code=excluded.company_code,
                count=warehouse_ingest_orphans.count + 1,
                last_seen=excluded.last_seen
            """,
            values,
        )
        await self._db.commit()
        return len(values)

    async def get_vehicle_state_at_or_before(
        self,
        account_id: int,
        vehicle_id: str,
        captured_before: str,
    ) -> Optional[dict[str, Any]]:
        """Latest snapshot for a vehicle at-or-before ``captured_before``.

        Used by the hourly aggregator to compute miles delta over each
        hour window: end_snapshot.odometer - start_snapshot.odometer.
        Returns None when no snapshot exists in the retention window —
        in that case the aggregator skips this hour for this vehicle
        and the miles total stays at zero rather than guessing.
        """
        cur = await self._db.execute(
            """
            SELECT odometer_mi, engine_hours, fuel_pct, captured_at,
                   engine_state, speed_mph
              FROM vehicle_state_snapshot
             WHERE account_id = ? AND vehicle_id = ?
               AND captured_at <= ?
             ORDER BY captured_at DESC
             LIMIT 1
            """,
            (account_id, vehicle_id, captured_before),
        )
        row = await cur.fetchone()
        if not row:
            return None
        return dict(zip(
            ("odometer_mi", "engine_hours", "fuel_pct", "captured_at",
             "engine_state", "speed_mph"),
            row,
        ))

    async def get_undriven_vehicles(
        self,
        account_id: int,
        *,
        min_days: float = 1.0,
        move_threshold_mph: float = 1.0,
        lookback_days: int = 30,
    ) -> list[dict[str, Any]]:
        """Vehicles that have NOT moved (speed above threshold) in at least
        ``min_days``.

        Movement-based — the authoritative answer to "which vehicle hasn't
        driven in N days" — as opposed to parking-location events.  Only
        vehicles still reporting (recent ``vehicle_state_snapshot`` rows)
        are considered, so a truck that simply fell offline isn't reported
        as "stopped".  Snapshots are keyed by ``vehicle_id``, so we join the
        current ``vehicle_state`` for name + company.

        Tiered so the answer reaches past the 7-day snapshot retention:
          * Recent, precise movement comes from ``vehicle_state_snapshot``
            (5-min cadence, ≤7 days) — this also decides which vehicles are
            online enough to consider.
          * For an online truck with NO movement inside the snapshot window,
            the last day it actually drove is read from the long-retention
            ``vehicle_metrics_daily`` roll-up (≤730 days; a "drive day" =
            ``drive_min > 0``).  Without this, "undriven for 14 days" could
            never be answered once snapshots are pruned at 7 days.

        Each returned dict: ``vehicle_id``, ``vehicle_name``,
        ``company_code``, ``last_moved`` (None if it never drove within
        ``lookback_days``), ``last_seen``, ``days_stopped`` (None when
        last_moved is unknown).  Sorted longest-stopped first.
        """
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=lookback_days)).isoformat()
        cutoff = (now - timedelta(days=min_days)).isoformat()

        # Online trucks + recent precise movement (5-min snapshot tier).
        cur = await self._db.execute(
            """
            SELECT vehicle_id,
                   MAX(CASE WHEN speed_mph > ? THEN captured_at END) AS last_moved,
                   MAX(captured_at) AS last_seen
              FROM vehicle_state_snapshot
             WHERE account_id = ? AND captured_at >= ?
             GROUP BY vehicle_id
            """,
            (move_threshold_mph, account_id, since),
        )
        agg: dict[str, dict] = {}
        for row in await cur.fetchall():
            d = dict(zip(("vehicle_id", "last_moved", "last_seen"), row))
            vid = str(d.get("vehicle_id") or "")
            if vid:
                agg[vid] = d

        # Extend the lookback past the 7-day snapshot horizon: for online
        # trucks showing no movement inside the snapshot window, read the
        # last day they actually drove from the long-retention daily tier.
        stalled = [vid for vid, d in agg.items() if not d.get("last_moved")]
        last_drive_day: dict[str, str] = {}
        if stalled:
            since_day = (now - timedelta(days=lookback_days)).date().isoformat()
            cur = await self._db.execute(
                """
                SELECT vehicle_id, MAX(bucket_start) AS last_drive_day
                  FROM vehicle_telemetry
                 WHERE account_id = ? AND granularity = 'daily'
                   AND bucket_start >= ? AND drive_min > 0
                 GROUP BY vehicle_id
                """,
                (account_id, since_day),
            )
            for row in await cur.fetchall():
                d = dict(zip(("vehicle_id", "last_drive_day"), row))
                vid = str(d.get("vehicle_id") or "")
                if vid and d.get("last_drive_day"):
                    last_drive_day[vid] = str(d["last_drive_day"])

        # vehicle_id → name/company from the current state table.
        meta_by_id = {
            str(s.get("vehicle_id")): s
            for s in await self.get_vehicle_state(account_id)
        }

        out: list[dict[str, Any]] = []
        for vid, d in agg.items():
            last_moved = d.get("last_moved")
            # No movement in the snapshot window → fall back to the daily
            # tier's last drive day, anchored at end-of-day so the coarser
            # tier never OVER-states days_stopped (no false "undriven" flags).
            if not last_moved and vid in last_drive_day:
                last_moved = last_drive_day[vid] + "T23:59:59+00:00"
            # Still driving recently → not undriven.
            if last_moved and last_moved >= cutoff:
                continue
            meta = meta_by_id.get(vid, {})
            days_stopped: Optional[float] = None
            if last_moved:
                try:
                    lm = datetime.fromisoformat(str(last_moved).replace("Z", "+00:00"))
                    if lm.tzinfo is None:
                        lm = lm.replace(tzinfo=timezone.utc)
                    days_stopped = round((now - lm).total_seconds() / 86400.0, 1)
                except Exception:
                    days_stopped = None
            out.append({
                "vehicle_id": vid,
                "vehicle_name": meta.get("vehicle_name") or vid,
                "company_code": meta.get("company_code") or "",
                "last_moved": last_moved,
                "last_seen": d.get("last_seen"),
                "days_stopped": days_stopped,
            })

        # Longest-stopped first; "never moved in window" (None) sorts to top.
        out.sort(
            key=lambda x: x["days_stopped"] if x["days_stopped"] is not None else float("inf"),
            reverse=True,
        )
        return out

    async def compute_vehicle_velocity_window(
        self,
        account_id: int,
        *,
        days: int = 30,
        min_days_required: int = 3,
    ) -> dict[str, float]:
        """Average daily miles per vehicle over the recent history.

        Reads from ``vehicle_state_snapshot`` directly so the answer
        reflects the genuine odometer-delta over the window, regardless
        of whether the hourly aggregator has run for every hour in the
        window.

        ``min_days_required`` skips vehicles whose first observed
        snapshot is too recent for the window to be meaningful — a
        truck onboarded yesterday with one snapshot would otherwise
        report 0 mi/day and trigger no projection.  3 days is the
        empirical threshold below which Samsara odometer noise can
        outweigh signal.

        Returned dict is keyed by ``vehicle_id``; vehicles with no
        data in the window are absent rather than present-with-zero so
        the caller can distinguish "no signal" from "zero miles".
        """
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=days)).isoformat()
        cur = await self._db.execute(
            """
            SELECT vehicle_id,
                   MIN(odometer_mi) AS first_odo,
                   MAX(odometer_mi) AS last_odo,
                   MIN(captured_at) AS first_at,
                   MAX(captured_at) AS last_at
              FROM vehicle_state_snapshot
             WHERE account_id = ?
               AND captured_at >= ?
               AND odometer_mi IS NOT NULL
             GROUP BY vehicle_id
            """,
            (account_id, since),
        )
        out: dict[str, float] = {}
        cols = ("vehicle_id", "first_odo", "last_odo", "first_at", "last_at")
        for row in await cur.fetchall():
            d = dict(zip(cols, row))
            vid = str(d.get("vehicle_id") or "")
            if not vid:
                continue
            first_odo = d.get("first_odo")
            last_odo = d.get("last_odo")
            first_at = d.get("first_at")
            last_at = d.get("last_at")
            if first_odo is None or last_odo is None or not first_at or not last_at:
                continue
            try:
                t0 = datetime.fromisoformat(first_at.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(last_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            actual_span_days = (t1 - t0).total_seconds() / 86400.0
            if actual_span_days < float(min_days_required):
                continue
            miles_driven = max(0.0, float(last_odo) - float(first_odo))
            out[vid] = miles_driven / actual_span_days
        return out

    async def compute_vehicle_velocity_daily(
        self,
        account_id: int,
        *,
        window_days: int = 30,
    ) -> dict[str, dict[str, Any]]:
        """Per-vehicle daily-miles velocity, robust to weekly cycles.

        Returns the **median** of per-day miles over the requested
        window, computed from ``vehicle_metrics_daily`` — which
        retains 730 days, so the requested window is honoured for
        real (unlike the snapshot-based helper, where the 7-day
        retention silently truncates a 30-day query).

        Why median, not mean
        --------------------
        Fleet trucks have bimodal day distributions: a drive day
        does 200-400 mi, an idle day (weekend / shop / spare) does
        0.  A simple mean of [0, 0, 0, 300, 350, 280, 0] (Mon-Sun)
        is 133 mi/day, which is wrong — the truck does 300 on
        operating days; the zeros are absences, not slowness.
        Projecting against the mean over-estimates how long it'll
        take to hit a maintenance threshold and pushes due dates
        too far into the future.

        Why "operational days only"
        ---------------------------
        Days with ``miles < VELOCITY_DRIVE_DAY_MIN_MILES`` are
        excluded from the median.  This is what makes yard / backup
        / shop-held trucks project accurately — they have legitimate
        idle weeks the calendar shouldn't smooth into the velocity.

        Coverage guards
        ---------------
        Two guards together prevent low-data projections:
          * ``window_observed_days >= VELOCITY_MIN_COVERAGE_DAYS``
            (any kind of day, drive or idle) — captures cold-start
            and onboarding cases.
          * ``drive_day_count >= VELOCITY_MIN_DRIVE_DAYS`` — the
            median's sample size must be meaningful.

        Vehicles failing either guard are absent from the result so
        the caller can distinguish "no signal" from "signal present
        but zero".

        Return shape
        ------------
        Each value is a dict so the caller can surface the provenance
        in the operator-facing tooltip ("projected from 187 mi/day,
        last 30 days, 22 drive days observed"):

            {
              "velocity":       187.0,   # median of drive-day miles
              "window_days":    30,      # the requested window
              "days_observed":  30,      # any-kind-of-day rows seen
              "drive_days":     22,      # rows with miles >= threshold
              "min_drive_mi":   60.0,    # robustness: floor of drive-day miles
              "max_drive_mi":   410.0,   # robustness: ceiling
            }
        """
        from datetime import timedelta
        # ``window_days - 1`` so a 30-day window covers exactly 30
        # calendar days inclusive of today.  Without this offset the
        # ``>= since_day`` filter returns 31 days (today and the 30
        # before it), which makes ``days_observed > window_days``
        # — confusing in the tooltip and noted as a reporting
        # mismatch in code review.
        since_day = (
            datetime.now(timezone.utc) - timedelta(days=max(0, window_days - 1))
        ).date().isoformat()
        cur = await self._db.execute(
            """
            SELECT vehicle_id, bucket_start, COALESCE(miles, 0) AS miles
              FROM vehicle_telemetry
             WHERE account_id = ? AND granularity = 'daily' AND bucket_start >= ?
             ORDER BY vehicle_id, bucket_start
            """,
            (account_id, since_day),
        )
        rows = await cur.fetchall()

        # Group per vehicle on the python side — the daily table is
        # bounded (~30 rows × N vehicles for the default window), so
        # this is cheaper than running per-vehicle SQL with window
        # functions.
        per_vehicle: dict[str, list[float]] = {}
        for row in rows:
            d = dict(zip(("vehicle_id", "day_utc", "miles"), row))
            vid = str(d.get("vehicle_id") or "")
            if not vid:
                continue
            try:
                miles = float(d.get("miles") or 0)
            except (TypeError, ValueError):
                continue
            per_vehicle.setdefault(vid, []).append(miles)

        out: dict[str, dict[str, Any]] = {}
        for vid, daily_miles in per_vehicle.items():
            if len(daily_miles) < VELOCITY_MIN_COVERAGE_DAYS:
                continue
            drive_days = [
                m for m in daily_miles
                if m >= VELOCITY_DRIVE_DAY_MIN_MILES
            ]
            if len(drive_days) < VELOCITY_MIN_DRIVE_DAYS:
                continue
            out[vid] = {
                "velocity":       round(statistics.median(drive_days), 1),
                "window_days":    int(window_days),
                "days_observed":  len(daily_miles),
                "drive_days":     len(drive_days),
                "min_drive_mi":   round(min(drive_days), 1),
                "max_drive_mi":   round(max(drive_days), 1),
            }
        return out

    async def prune_vehicle_state_snapshots(
        self, account_id: int, *, days_keep: int = 7,
    ) -> int:
        """Drop snapshot rows older than the retention window."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_keep)).isoformat()
        cur = await self._db.execute(
            "DELETE FROM vehicle_state_snapshot "
            "WHERE account_id = ? AND captured_at < ?",
            (account_id, cutoff),
        )
        await self._db.commit()
        return getattr(cur, "rowcount", 0) or 0

    async def get_reading_as_of(
        self, account_id: int, vehicle_name: str, on_date: str,
    ) -> Optional[dict]:
        """Odometer + engine-hours for a vehicle AS OF a date.

        Powers back-dated work orders: a WO dated last month should show
        the mileage the truck had then, not today's.  ``vehicle_name`` is
        the unit name; resolved to the telematics id via ``vehicle_state``.

        Tiered lookup, precise-first:
          1. The 5-minute ``vehicle_state_snapshot`` (kept 7 days) — exact
             reading at/-before end of ``on_date``.
          2. Fallback to the end-of-day reading in ``vehicle_metrics_daily``
             (kept 730 days) for dates older than the snapshot window.

        Returns ``{odometer_miles, engine_hours, as_of}`` or ``None`` when
        the vehicle isn't telematics-linked or neither tier has a reading
        at/-before that date (caller falls back to the current reading).
        """
        name = (vehicle_name or "").strip()
        on_date = (on_date or "").strip()[:10]
        if not name or len(on_date) != 10:
            return None
        # name → telematics vehicle_id (the snapshot's key).
        rc = await self._db.execute(
            "SELECT vehicle_id FROM vehicle_state "
            "WHERE account_id = ? AND lower(vehicle_name) = lower(?) LIMIT 1",
            (account_id, name),
        )
        vrow = await rc.fetchone()
        if not vrow:
            return None
        vehicle_id = dict(vrow)["vehicle_id"]
        # captured_at < next-day midnight → "<= end of on_date".  String
        # compare is safe for both "YYYY-MM-DDTHH:MM:SSZ" and "… HH:MM:SS"
        # because a bare date sorts before any same-day timestamp.
        from datetime import date as _date, timedelta as _td
        try:
            next_day = (_date.fromisoformat(on_date) + _td(days=1)).isoformat()
        except ValueError:
            return None
        # Tier 1 — precise 5-minute snapshot.
        cur = await self._db.execute(
            "SELECT odometer_mi, engine_hours, captured_at "
            "FROM vehicle_state_snapshot "
            "WHERE account_id = ? AND vehicle_id = ? AND captured_at < ? "
            "AND odometer_mi IS NOT NULL "
            "ORDER BY captured_at DESC LIMIT 1",
            (account_id, vehicle_id, next_day),
        )
        row = await cur.fetchone()
        if row:
            d = dict(row)
            return {
                "odometer_miles": d.get("odometer_mi"),
                "engine_hours": d.get("engine_hours"),
                "as_of": d.get("captured_at"),
            }
        # Tier 2 — end-of-day reading from the long-retention daily table.
        cur = await self._db.execute(
            "SELECT odometer_eod, engine_hours_eod, bucket_start AS day_utc "
            "FROM vehicle_telemetry "
            "WHERE account_id = ? AND vehicle_id = ? AND granularity = 'daily' "
            "AND bucket_start <= ? AND odometer_eod IS NOT NULL "
            "ORDER BY bucket_start DESC LIMIT 1",
            (account_id, vehicle_id, on_date),
        )
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        return {
            "odometer_miles": d.get("odometer_eod"),
            "engine_hours": d.get("engine_hours_eod"),
            "as_of": d.get("day_utc"),
        }

    @staticmethod
    def _days_between(a: str, b: str) -> int:
        """Whole days from ISO day ``a`` to ISO day ``b`` (0 when equal,
        negative when ``b`` precedes ``a``)."""
        from datetime import date as _d
        try:
            return (_d.fromisoformat(str(b)[:10]) - _d.fromisoformat(str(a)[:10])).days
        except ValueError:
            return 0

    # A team (two drivers, 24h) tops out around 1,400 mi/day; anything
    # above this in ONE daily bucket is a reporting backlog, not driving.
    _CATCHUP_DAY_MILES = 1_500.0

    async def get_period_mileage(
        self, account_id: int, start: str, end: str,
        *, start_ts: str | None = None, end_ts: str | None = None,
    ) -> list[dict]:
        """Miles driven per vehicle in [start, end] (inclusive dates).

        ``start_ts`` / ``end_ts`` (optional, naive-UTC ISO
        "YYYY-MM-DDTHH:MM:SS") request EXACT-TIME boundaries.  Each is
        resolved down a precision ladder: the 5-minute snapshot (kept
        7 days) → the hourly tier's banked end-of-hour odometer (kept
        90 days, populated from 2026-07-30 forward) → the day logic
        below as the fallback.  Rows report which sides were exact via
        ``start_precise`` / ``end_precise`` so the API can say "time
        of day was not applied for these vehicles" instead of quietly
        answering a different question than was asked.

        The authoritative number is an ODOMETER DELTA — Samsara's own
        mileage guide computes distance this way — not a sum of daily
        buckets (summed buckets silently under-count when a day's sync
        was missed):

            miles = odometer_eod(last day ≤ end)
                  − odometer_eod(last day < start)

        The baseline reach-back is unbounded on purpose: a truck idle
        for weeks before the range still has the correct odometer at
        its last reading — no driving means no drift.

        TIERED like ``get_reading_as_of``: both boundaries prefer the
        5-minute ``vehicle_state_snapshot`` (kept 7 days) when it is a
        later day than the daily bucket, else the daily end-of-day
        reading (kept 730 days).  Without the snapshot tier the newest
        answer was always "yesterday" - the daily roll-up only lands at
        00:05 UTC for the day before.

        Per-vehicle degraded shapes, flagged, never silent:
          * ``estimated`` -- a boundary fell inside a data gap, so the
            odometer at that boundary was INTERPOLATED between the
            readings bracketing it.  Reaching back to a stale reading
            instead would import driving from outside the window.
          * ``partial`` — no reading before the range (vehicle joined
            telematics mid-range): counted from its FIRST in-range
            reading; real miles are ≥ the number shown.
          * ``reset`` — negative delta (odometer reset / device swap):
            clamped to the sum of the daily ``miles`` buckets.
          * ``catchup`` — some in-range day shows more miles than a
            truck can physically drive (> ``_CATCHUP_DAY_MILES``):
            the odometer feed went silent and then reported the
            backlog in one reading (production shows correlated
            multi-truck jump days).  The range TOTAL is still the
            real odometer growth, but a boundary inside such a gap
            can attribute some earlier driving into the range — the
            flag keeps that honest.
          * no end reading at all → the vehicle has no usable odometer
            history for the range and is NOT returned; the caller
            reports those from the registry as "no odometer data"
            (omitted ≠ zero).

        Returns one dict per vehicle with a reading at/-before ``end``:
        ``{vehicle_id, vehicle_name, miles, start_odo, end_odo,
        start_read_on, end_read_on, days_covered, flag}`` — miles
        rounded to 1 decimal, ``flag`` in {'', 'partial', 'reset'}.
        """
        start = (start or "").strip()[:10]
        end = (end or "").strip()[:10]
        if len(start) != 10 or len(end) != 10 or start > end:
            return []

        async def _last_reading_per_vehicle(upper: str, strict: bool):
            op = "<" if strict else "<="
            cur = await self._db.execute(
                f"SELECT t.vehicle_id, t.vehicle_name, t.odometer_eod, "
                f"       t.bucket_start "
                f"FROM vehicle_telemetry t "
                f"JOIN (SELECT vehicle_id, MAX(bucket_start) AS mb "
                f"      FROM vehicle_telemetry "
                f"      WHERE account_id = ? AND granularity = 'daily' "
                f"      AND bucket_start {op} ? AND odometer_eod IS NOT NULL "
                f"      GROUP BY vehicle_id) m "
                f"  ON m.vehicle_id = t.vehicle_id "
                f" AND m.mb = t.bucket_start "
                f"WHERE t.account_id = ? AND t.granularity = 'daily' "
                f"AND t.odometer_eod IS NOT NULL",
                (account_id, upper, account_id),
            )
            return {str(d["vehicle_id"]): d
                    for r in await cur.fetchall() for d in (dict(r),)}

        # -- Tiered read, precise-first (mirrors get_reading_as_of) --
        #
        # The daily tier only gains a day at 00:05 UTC for YESTERDAY, so
        # reading it alone made "today" invisible: the board reported
        # "history reaches <yesterday>" while the 5-minute snapshot tier
        # held readings from minutes ago.  Both boundaries now take the
        # snapshot when it is genuinely FRESHER (a later calendar day)
        # and fall back to the daily end-of-day for anything older than
        # the 7-day snapshot window.
        from datetime import date as _date2, timedelta as _td2

        async def _last_snapshot_per_vehicle(before_iso: str):
            cur = await self._db.execute(
                "SELECT s.vehicle_id, s.odometer_mi, s.captured_at "
                "FROM vehicle_state_snapshot s "
                "JOIN (SELECT vehicle_id, MAX(captured_at) AS mc "
                "      FROM vehicle_state_snapshot "
                "      WHERE account_id = ? AND captured_at < ? "
                "      AND odometer_mi IS NOT NULL "
                "      GROUP BY vehicle_id) m "
                "  ON m.vehicle_id = s.vehicle_id AND m.mc = s.captured_at "
                "WHERE s.account_id = ? AND s.odometer_mi IS NOT NULL",
                (account_id, before_iso, account_id),
            )
            return {str(d["vehicle_id"]): d
                    for r in await cur.fetchall() for d in (dict(r),)}

        def _merge(daily: dict, snaps: dict) -> dict:
            # Normalise both tiers to {odo, read_on, vehicle_name}.  The
            # snapshot wins only when its DAY is later than the daily
            # bucket: within one day the daily EOD is the max-of-day and
            # is therefore already the better (later) reading.
            out: dict[str, dict] = {}
            for vid, d in daily.items():
                out[vid] = {"odo": float(d["odometer_eod"]),
                            "read_on": d["bucket_start"],
                            "vehicle_name": d.get("vehicle_name") or ""}
            for vid, sn in snaps.items():
                day = str(sn["captured_at"])[:10]
                prev = out.get(vid)
                if prev is None or day > prev["read_on"]:
                    out[vid] = {"odo": float(sn["odometer_mi"]),
                                "read_on": day,
                                "vehicle_name": (prev or {}).get("vehicle_name", "")}
            return out

        try:
            _end_excl = (_date2.fromisoformat(end) + _td2(days=1)).isoformat()
        except ValueError:
            return []
        ends = _merge(await _last_reading_per_vehicle(end, strict=False),
                      await _last_snapshot_per_vehicle(_end_excl))
        if not ends:
            return []
        baselines = _merge(await _last_reading_per_vehicle(start, strict=True),
                           await _last_snapshot_per_vehicle(start))

        # ── Exact-time boundaries (precision ladder) ────────────────
        async def _last_hourly_reading_per_vehicle(le_hour_label: str):
            cur = await self._db.execute(
                "SELECT t.vehicle_id, t.odometer_eod, t.bucket_start "
                "FROM vehicle_telemetry t "
                "JOIN (SELECT vehicle_id, MAX(bucket_start) AS mb "
                "      FROM vehicle_telemetry "
                "      WHERE account_id = ? AND granularity = 'hourly' "
                "      AND bucket_start <= ? AND odometer_eod IS NOT NULL "
                "      GROUP BY vehicle_id) m "
                "  ON m.vehicle_id = t.vehicle_id AND m.mb = t.bucket_start "
                "WHERE t.account_id = ? AND t.granularity = 'hourly' "
                "AND t.odometer_eod IS NOT NULL",
                (account_id, le_hour_label, account_id),
            )
            return {str(d["vehicle_id"]): d
                    for r in await cur.fetchall() for d in (dict(r),)}

        async def _precise_boundary(ts: str) -> dict:
            """{vid: {odo, read_at}} — the reading AT ``ts`` where any
            tier can actually answer; absent vids fall back to days."""
            out2: dict[str, dict] = {}
            # Hourly first (coarser), snapshots overwrite (finer).  An
            # hourly bucket is only usable when the WHOLE hour closed
            # at/-before ts — its odometer is the max over the hour.
            try:
                _t = datetime.fromisoformat(ts)
                closed = (_t - timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00")
            except ValueError:
                return out2
            for vid, h in (await _last_hourly_reading_per_vehicle(closed)).items():
                out2[vid] = {"odo": float(h["odometer_eod"]),
                             "read_at": str(h["bucket_start"])}
            cur = await self._db.execute(
                "SELECT s.vehicle_id, s.odometer_mi, s.captured_at "
                "FROM vehicle_state_snapshot s "
                "JOIN (SELECT vehicle_id, MAX(captured_at) AS mc "
                "      FROM vehicle_state_snapshot "
                "      WHERE account_id = ? AND captured_at <= ? "
                "      AND odometer_mi IS NOT NULL "
                "      GROUP BY vehicle_id) m "
                "  ON m.vehicle_id = s.vehicle_id AND m.mc = s.captured_at "
                "WHERE s.account_id = ? AND s.odometer_mi IS NOT NULL",
                (account_id, ts, account_id),
            )
            for r in await cur.fetchall():
                d = dict(r)
                out2[str(d["vehicle_id"])] = {
                    "odo": float(d["odometer_mi"]),
                    "read_at": str(d["captured_at"]),
                }
            return out2

        # A time-tier reading is only the TRUE boundary when the tier
        # actually reaches ts — a reading days older than ts means the
        # tiers have been pruned past it; the day logic (with its gap
        # interpolation) is more honest there.
        def _fresh_enough(read_at: str, ts: str, hours: float = 26.0) -> bool:
            try:
                a = datetime.fromisoformat(str(read_at)[:19])
                b = datetime.fromisoformat(str(ts)[:19])
            except ValueError:
                return False
            return (b - a) <= timedelta(hours=hours)

        precise_starts: dict[str, dict] = {}
        precise_ends: dict[str, dict] = {}
        if start_ts:
            precise_starts = {
                vid: v for vid, v in (await _precise_boundary(start_ts)).items()
                if _fresh_enough(v["read_at"], start_ts)
            }
        if end_ts:
            precise_ends = {
                vid: v for vid, v in (await _precise_boundary(end_ts)).items()
                if _fresh_enough(v["read_at"], end_ts)
            }

        # First IN-RANGE reading — the 'partial' fallback baseline —
        # and the in-range daily aggregates in one pass.
        cur = await self._db.execute(
            "SELECT vehicle_id, MIN(bucket_start) AS first_day, "
            "       SUM(miles) AS sum_miles, COUNT(*) AS days_covered, "
            "       MAX(miles) AS max_day_miles "
            "FROM vehicle_telemetry "
            "WHERE account_id = ? AND granularity = 'daily' "
            "AND bucket_start >= ? AND bucket_start <= ? "
            "AND odometer_eod IS NOT NULL "
            "GROUP BY vehicle_id",
            (account_id, start, end),
        )
        in_range = {str(d["vehicle_id"]): d
                    for r in await cur.fetchall() for d in (dict(r),)}
        # The reading on the OTHER side of each boundary, so a boundary
        # that falls inside a data gap can be interpolated instead of
        # reached-back across (see the boundary block below).
        async def _first_reading_per_vehicle(lower: str, inclusive: bool):
            op = ">=" if inclusive else ">"
            cur2 = await self._db.execute(
                f"SELECT t.vehicle_id, t.odometer_eod, t.bucket_start "
                f"FROM vehicle_telemetry t "
                f"JOIN (SELECT vehicle_id, MIN(bucket_start) AS mb "
                f"      FROM vehicle_telemetry "
                f"      WHERE account_id = ? AND granularity = 'daily' "
                f"      AND bucket_start {op} ? AND odometer_eod IS NOT NULL "
                f"      GROUP BY vehicle_id) m "
                f"  ON m.vehicle_id = t.vehicle_id AND m.mb = t.bucket_start "
                f"WHERE t.account_id = ? AND t.granularity = 'daily' "
                f"AND t.odometer_eod IS NOT NULL",
                (account_id, lower, account_id),
            )
            return {str(d["vehicle_id"]): {"odo": float(d["odometer_eod"]),
                                           "read_on": d["bucket_start"]}
                    for r in await cur2.fetchall() for d in (dict(r),)}

        after_start = await _first_reading_per_vehicle(start, inclusive=True)
        after_end = await _first_reading_per_vehicle(end, inclusive=False)
        firsts = {vid: v["odo"] for vid, v in after_start.items()}

        out: list[dict] = []
        for vid, e in ends.items():
            end_odo = e["odo"]
            agg = in_range.get(vid, {})
            b = baselines.get(vid)
            nxt = after_start.get(vid)
            flag = ""
            start_precise = False
            end_precise = False
            _ps = precise_starts.get(vid)
            if _ps is not None:
                b = {"odo": _ps["odo"], "read_on": str(_ps["read_at"])[:10]}
                nxt = None            # exact reading — no interpolation
                start_precise = True
            _pe = precise_ends.get(vid)
            if _pe is not None:
                end_odo = _pe["odo"]
                e = {**e, "read_on": str(_pe["read_at"])[:10]}
                end_precise = True
            # -- START boundary -------------------------------------
            # Reaching back to "the last reading whenever it was" is only
            # safe when that reading sits ON the boundary.  With days of
            # gap it imports driving from BEFORE the window: production
            # truck 245 measured from a reading 7 days early and reported
            # 9,932 mi where Samsara's own odometer said 5,997.  So a
            # stale boundary is INTERPOLATED between the readings that
            # bracket it, and the row says it is estimated.
            if start_precise:
                start_odo = b["odo"]
                start_read_on = b["read_on"]
            elif b is not None and self._days_between(b["read_on"], start) <= 1:
                start_odo = b["odo"]
                start_read_on = b["read_on"]
            elif (b is not None and nxt is not None
                    and nxt["odo"] >= b["odo"]
                    and self._days_between(b["read_on"], nxt["read_on"]) > 0):
                # A day's reading is the odometer at the END of that day,
                # so the window opens at the end of the day BEFORE start.
                span = self._days_between(b["read_on"], nxt["read_on"])
                pos = max(0, self._days_between(b["read_on"], start) - 1)
                start_odo = b["odo"] + (nxt["odo"] - b["odo"]) * (pos / span)
                start_read_on = start
                flag = "estimated"
            elif nxt is not None:
                start_odo = nxt["odo"]
                start_read_on = nxt["read_on"]
                flag = "partial"
            elif b is not None:
                start_odo = b["odo"]
                start_read_on = b["read_on"]
            else:
                # A reading before/at end but nothing before the range
                # and nothing inside it → no in-range driving evidence.
                continue

            # -- END boundary ---------------------------------------
            # Symmetric: a last reading days before the window end loses
            # the tail.  Interpolate toward the next reading after the
            # window when one exists.
            end_read_on = e["read_on"]
            nxt_e = after_end.get(vid)
            if end_precise:
                nxt_e = None          # exact reading — no interpolation
            if (nxt_e is not None
                    and self._days_between(end_read_on, end) > 0
                    and nxt_e["odo"] >= end_odo
                    and self._days_between(end_read_on, nxt_e["read_on"]) > 0):
                span_e = self._days_between(end_read_on, nxt_e["read_on"])
                pos_e = min(self._days_between(end_read_on, end), span_e)
                end_odo = end_odo + (nxt_e["odo"] - end_odo) * (pos_e / span_e)
                end_read_on = end
                if flag in ("", "catchup"):
                    flag = "estimated"
            miles = end_odo - start_odo
            if miles < 0:
                miles = float(agg.get("sum_miles") or 0.0)
                flag = "reset"
            elif not flag and \
                    float(agg.get("max_day_miles") or 0.0) > self._CATCHUP_DAY_MILES:
                flag = "catchup"
            out.append({
                "vehicle_id": vid,
                "vehicle_name": e.get("vehicle_name") or vid,
                "miles": round(miles, 1),
                "start_odo": round(start_odo, 1),
                "end_odo": round(end_odo, 1),
                "start_read_on": start_read_on,
                "end_read_on": end_read_on,
                "days_covered": int(agg.get("days_covered") or 0),
                "flag": flag,
                "start_precise": start_precise,
                "end_precise": end_precise,
            })
        out.sort(key=lambda r: -r["miles"])
        return out

    async def get_vehicle_period_mileage(
        self, account_id: int, vehicle_name: str, start: str, end: str,
        *, start_ts: str | None = None, end_ts: str | None = None,
    ) -> Optional[dict]:
        """Single-vehicle period mileage + the per-day breakdown.

        Same odometer-delta rule as :meth:`get_period_mileage`;
        ``vehicle_name`` resolves to the telematics id via
        ``vehicle_state`` (the same door ``get_reading_as_of`` uses).
        Returns ``None`` when the vehicle isn't telematics-linked or has
        no usable reading for the range; otherwise the summary dict plus
        ``days``: ``[{day, miles}]`` for the range (bars in the UI).
        """
        name = (vehicle_name or "").strip()
        if not name:
            return None
        rc = await self._db.execute(
            "SELECT vehicle_id FROM vehicle_state "
            "WHERE account_id = ? AND lower(vehicle_name) = lower(?) LIMIT 1",
            (account_id, name),
        )
        vrow = await rc.fetchone()
        if not vrow:
            return None
        vid = str(dict(vrow)["vehicle_id"])
        rows = await self.get_period_mileage(
            account_id, start, end, start_ts=start_ts, end_ts=end_ts)
        summary = next((r for r in rows if r["vehicle_id"] == vid), None)
        if summary is None:
            return None
        cur = await self._db.execute(
            "SELECT bucket_start AS day, miles FROM vehicle_telemetry "
            "WHERE account_id = ? AND vehicle_id = ? "
            "AND granularity = 'daily' "
            "AND bucket_start >= ? AND bucket_start <= ? "
            "ORDER BY bucket_start",
            (account_id, vid, (start or "")[:10], (end or "")[:10]),
        )
        summary["days"] = [
            {"day": d["day"], "miles": round(float(d["miles"] or 0.0), 1)}
            for r in await cur.fetchall() for d in (dict(r),)
        ]
        return summary

    async def get_snapshot_coverage(
        self, account_id: int, vehicle_name: str,
    ) -> dict:
        """How far back our odometer history reaches for a vehicle.

        Lets the work-order form explain *why* a back-dated date has no
        reading: the vehicle has no telematics link at all, or it's linked
        but our stored history doesn't reach that far.  Mirrors the tiered
        read in ``get_reading_as_of`` — the window is the UNION of the
        5-minute ``vehicle_state_snapshot`` (≤7 days) and the end-of-day
        ``vehicle_metrics_daily`` (≤730 days), so the reported reach
        matches what the as-of lookup can actually answer.

        Returns ``{telematics_linked, coverage_start, coverage_end}`` —
        the earliest/latest dates with an odometer (None when the vehicle
        isn't linked or has no odometer history yet).
        """
        name = (vehicle_name or "").strip()
        if not name:
            return {"telematics_linked": False,
                    "coverage_start": None, "coverage_end": None}
        rc = await self._db.execute(
            "SELECT vehicle_id FROM vehicle_state "
            "WHERE account_id = ? AND lower(vehicle_name) = lower(?) LIMIT 1",
            (account_id, name),
        )
        vrow = await rc.fetchone()
        if not vrow:
            return {"telematics_linked": False,
                    "coverage_start": None, "coverage_end": None}
        vehicle_id = dict(vrow)["vehicle_id"]
        # Snapshot tier (precise, recent) — full ISO timestamps.
        cur = await self._db.execute(
            "SELECT MIN(captured_at) AS first_at, MAX(captured_at) AS last_at "
            "FROM vehicle_state_snapshot "
            "WHERE account_id = ? AND vehicle_id = ? AND odometer_mi IS NOT NULL",
            (account_id, vehicle_id),
        )
        snap = dict(await cur.fetchone() or {})
        # Daily EOD tier (long-retention) — bare YYYY-MM-DD day labels.
        cur = await self._db.execute(
            "SELECT MIN(bucket_start) AS first_day, MAX(bucket_start) AS last_day "
            "FROM vehicle_telemetry "
            "WHERE account_id = ? AND vehicle_id = ? AND granularity = 'daily' "
            "AND odometer_eod IS NOT NULL",
            (account_id, vehicle_id),
        )
        daily = dict(await cur.fetchone() or {})

        # Earliest/latest across both tiers.  Compare on the date prefix
        # so a daily "2025-01-01" and a snapshot "2025-01-01T..Z" order
        # correctly; return the daily label for the start (it reaches
        # furthest back) and the snapshot timestamp for the end (freshest).
        starts = [s for s in (daily.get("first_day"), snap.get("first_at")) if s]
        ends = [e for e in (snap.get("last_at"), daily.get("last_day")) if e]
        coverage_start = min(starts, key=lambda s: s[:10]) if starts else None
        coverage_end = max(ends, key=lambda e: e[:10]) if ends else None
        return {
            "telematics_linked": True,
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
        }

    # Identifier guard for the feed-freshness query.  ``specs`` come
    # from the provider catalog (developer-controlled, not user input),
    # but we still validate table/column names against this pattern so a
    # typo can never become a SQL-injection surface.
    _IDENT_RE = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    async def get_feed_freshness(
        self, account_id: int, specs,
    ) -> dict[str, dict]:
        """Per-table ``{count, last_at}`` for a provider's declared data
        feeds — powers the Integration card's shared "Synced data" table.

        ``specs`` is an iterable of ``(table, ts_col)`` from the provider
        catalog's ``FeedSpec`` list.  Each table is queried independently
        so an odd/missing table degrades to ``{count: 0, last_at: None}``
        rather than failing the whole card.  Returns a dict keyed by
        table name.
        """
        out: dict[str, dict] = {}
        for table, ts_col in specs:
            if not (self._IDENT_RE.match(table) and self._IDENT_RE.match(ts_col)):
                out[table] = {"count": 0, "last_at": None}
                continue
            try:
                cur = await self._db.execute(
                    f"SELECT COUNT(*) AS n, MAX({ts_col}) AS last_at "
                    f"FROM {table} WHERE account_id = ?",
                    (account_id,),
                )
                row = dict(await cur.fetchone() or {})
                out[table] = {
                    "count": int(row.get("n") or 0),
                    "last_at": row.get("last_at"),
                }
            except Exception:
                out[table] = {"count": 0, "last_at": None}
        return out

    async def get_telemetry_tier_freshness(
        self, account_id: int,
    ) -> dict[str, dict]:
        """Per-granularity ``{count, last_at}`` for the unified
        ``vehicle_telemetry`` table — powers the operator telemetry-cascade
        diagnostic, where hourly/daily/weekly share one physical table and
        ``get_feed_freshness`` (one row per table) can't tell them apart."""
        out: dict[str, dict] = {
            g: {"count": 0, "last_at": None}
            for g in ("hourly", "daily", "weekly")
        }
        try:
            cur = await self._db.execute(
                "SELECT granularity, COUNT(*) AS n, MAX(ingested_at) AS last_at "
                "FROM vehicle_telemetry WHERE account_id = ? GROUP BY granularity",
                (account_id,),
            )
            for row in await cur.fetchall():
                d = dict(zip(("granularity", "n", "last_at"), row))
                g = str(d.get("granularity") or "")
                if g:
                    out[g] = {
                        "count": int(d.get("n") or 0),
                        "last_at": d.get("last_at"),
                    }
        except Exception:
            pass
        return out

    async def vehicle_state_snapshot_has_day(
        self, account_id: int, day_utc: date,
    ) -> bool:
        """Cheap existence check used by the backfill day cursor.

        A day "exists" if ANY snapshot row sits inside its UTC window.
        We don't try to detect partial-day coverage — that would be
        expensive and the backfill is idempotent (``ON CONFLICT DO
        NOTHING`` at the row level), so re-fetching a partially-
        populated day is safe even if wasteful.  In practice partial
        days only happen when a backfill is interrupted mid-day, and
        the next run finishes the day cheaply.
        """
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        start = _dt.combine(day_utc, _dt.min.time(), tzinfo=_tz.utc).isoformat()
        end = (_dt.combine(day_utc, _dt.min.time(), tzinfo=_tz.utc) + _td(days=1)).isoformat()
        cur = await self._db.execute(
            "SELECT 1 FROM vehicle_state_snapshot "
            "WHERE account_id = ? AND captured_at >= ? AND captured_at < ? "
            "LIMIT 1",
            (account_id, start, end),
        )
        row = await cur.fetchone()
        return row is not None

    async def vehicle_state_snapshot_day_summary(
        self, account_id: int, *, days_back: int = 30,
    ) -> list[dict[str, Any]]:
        """Per-day row count for the recent window — backs the
        backfill progress card on the dashboard.

        Returns ``[{"day_utc": "YYYY-MM-DD", "row_count": N}, ...]``
        ordered newest-first.  Days with zero rows are present in the
        list with ``row_count=0`` so callers can render gaps.
        """
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        since = (_dt.now(_tz.utc) - _td(days=days_back)).date().isoformat()
        cur = await self._db.execute(
            """
            SELECT substr(captured_at, 1, 10) AS day_utc,
                   COUNT(*) AS row_count
              FROM vehicle_state_snapshot
             WHERE account_id = ? AND captured_at >= ?
             GROUP BY day_utc
             ORDER BY day_utc DESC
            """,
            (account_id, since),
        )
        rows = await cur.fetchall()
        cols = ("day_utc", "row_count")
        existing = {
            str(dict(zip(cols, row)).get("day_utc") or ""): int(
                dict(zip(cols, row)).get("row_count") or 0
            )
            for row in rows
        }
        # Pad missing days so the dashboard can render gaps.
        out: list[dict[str, Any]] = []
        today = _dt.now(_tz.utc).date()
        for offset in range(days_back):
            d = (today - _td(days=offset)).isoformat()
            out.append({"day_utc": d, "row_count": existing.get(d, 0)})
        return out


    async def query_vehicle_state_history(
        self,
        account_id: int,
        *,
        vehicle_id: str | None = None,
        vehicle_name: str | None = None,
        days: int = 7,
        max_rows: int = 200,
    ) -> list[dict]:
        """Read raw snapshot rows for one vehicle (or all) over a window.

        Surfaces the ``vehicle_state_snapshot`` 5-min history to the AI
        agent so it can answer trend / utilization / "when did X last
        move" / idle-streak questions that the point-in-time tools
        can't.  Caps at ``max_rows`` so a careless 30-day query against
        a busy fleet doesn't blow the agent's context window — the
        caller should narrow ``days`` or pick a specific vehicle when
        they need finer resolution.

        Requires either ``vehicle_id`` (Samsara ID) or ``vehicle_name``
        — account-wide history queries are intentionally not supported
        because the row count explodes quadratically with fleet size.
        ``vehicle_name`` is resolved via the ``vehicle_state`` table
        which carries the canonical name→id mapping.
        """
        from datetime import timedelta as _td
        if not vehicle_id and not vehicle_name:
            return []

        # Resolve vehicle_name → vehicle_id if needed.
        if not vehicle_id and vehicle_name:
            cur = await self._db.execute(
                "SELECT vehicle_id FROM vehicle_state "
                "WHERE account_id = ? AND vehicle_name = ? LIMIT 1",
                (account_id, vehicle_name),
            )
            r = await cur.fetchone()
            if not r:
                return []
            vehicle_id = r[0]

        since = (datetime.now(timezone.utc) - _td(days=days)).isoformat()
        cur = await self._db.execute(
            """
            SELECT captured_at, lat, lon, speed_mph, engine_state,
                   fuel_pct, def_pct, odometer_mi, engine_hours,
                   fault_count, dtc_critical_count, last_driver_id,
                   battery_v, oil_psi, coolant_c, engine_load_pct, rpm
              FROM vehicle_state_snapshot
             WHERE account_id = ? AND vehicle_id = ?
               AND captured_at >= ?
             ORDER BY captured_at DESC
             LIMIT ?
            """,
            (account_id, vehicle_id, since, max_rows),
        )
        rows = await cur.fetchall()
        cols = (
            "captured_at", "lat", "lon", "speed_mph", "engine_state",
            "fuel_pct", "def_pct", "odometer_mi", "engine_hours",
            "fault_count", "dtc_critical_count", "last_driver_id",
            "battery_v", "oil_psi", "coolant_c", "engine_load_pct", "rpm",
        )
        return [dict(zip(cols, row)) for row in rows]


    # ── vehicle_metrics_daily (day roll-up) ─────────────────────────

    async def upsert_vehicle_metrics_daily(
        self,
        account_id: int,
        rows: Iterable[dict[str, Any]],
    ) -> int:
        """Bulk-upsert daily roll-up rows.  Re-running today's
        aggregator overwrites today's row in place, so a partial
        day's metrics converge as more hours close out."""
        ts = _now_iso()
        values: list[tuple] = []
        for r in rows:
            vid = str(r.get("vehicle_id") or "").strip()
            day = str(r.get("day_utc") or "").strip()
            if not vid or not day:
                continue
            values.append((
                account_id, vid, day,
                float(r.get("miles") or 0),
                float(r.get("drive_min") or 0),
                float(r.get("idle_min") or 0),
                _opt_float(r.get("max_speed_mph")),
                _opt_float(r.get("avg_fuel_pct")),
                int(r.get("harsh_event_count") or 0),
                int(r.get("fault_count_eod") or 0),
                _opt_float(r.get("odometer_eod")),
                _opt_float(r.get("engine_hours_eod")),
                r.get("source_ts"),
                r.get("registry_id"),
                ts,
            ))
        if values:
            # granularity='daily' bucket in the unified vehicle_telemetry
            # table; bucket_start carries the old day_utc label.
            await self._db.executemany(
                """
                INSERT INTO vehicle_telemetry (
                    account_id, vehicle_id, granularity, bucket_start,
                    miles, drive_min, idle_min,
                    max_speed_mph, avg_fuel_pct,
                    harsh_event_count, fault_count_eod,
                    odometer_eod, engine_hours_eod, source_ts, registry_id,
                    ingested_at
                ) VALUES (?, ?, 'daily', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (account_id, vehicle_id, granularity, bucket_start) DO UPDATE SET
                    miles=excluded.miles,
                    drive_min=excluded.drive_min,
                    idle_min=excluded.idle_min,
                    max_speed_mph=excluded.max_speed_mph,
                    avg_fuel_pct=excluded.avg_fuel_pct,
                    harsh_event_count=excluded.harsh_event_count,
                    fault_count_eod=excluded.fault_count_eod,
                    -- Keep a prior non-null EOD reading if a later re-run
                    -- (e.g. after snapshot pruning) can't recompute it.
                    odometer_eod=COALESCE(excluded.odometer_eod, vehicle_telemetry.odometer_eod),
                    engine_hours_eod=COALESCE(excluded.engine_hours_eod, vehicle_telemetry.engine_hours_eod),
                    source_ts=COALESCE(excluded.source_ts, vehicle_telemetry.source_ts),
                    registry_id=COALESCE(excluded.registry_id, vehicle_telemetry.registry_id),
                    ingested_at=excluded.ingested_at
                """,
                values,
            )
        await self._db.commit()
        return len(values)

    async def prune_vehicle_metrics_daily(
        self, account_id: int, *, days_keep: int = 730,
    ) -> int:
        """Drop daily roll-up rows older than the retention window."""
        from datetime import timedelta
        cutoff_day = (datetime.now(timezone.utc) - timedelta(days=days_keep)).date().isoformat()
        cur = await self._db.execute(
            "DELETE FROM vehicle_telemetry "
            "WHERE account_id = ? AND granularity = 'daily' AND bucket_start < ?",
            (account_id, cutoff_day),
        )
        await self._db.commit()
        return getattr(cur, "rowcount", 0) or 0

    async def upsert_vehicle_metrics_weekly(
        self,
        account_id: int,
        rows: Iterable[dict[str, Any]],
    ) -> int:
        """Upsert weekly roll-up rows (granularity='weekly' in the unified
        ``vehicle_telemetry`` table).  ``bucket_start`` is the ISO-week
        Monday (YYYY-MM-DD, UTC).  Same columns as the daily tier; the
        long-horizon store for multi-year trends without keeping 730 daily
        rows per vehicle."""
        ts = _now_iso()
        values: list[tuple] = []
        for r in rows:
            vid = str(r.get("vehicle_id") or "").strip()
            week = str(r.get("week_utc") or r.get("bucket_start") or "").strip()
            if not vid or not week:
                continue
            values.append((
                account_id, vid, week,
                float(r.get("miles") or 0),
                float(r.get("drive_min") or 0),
                float(r.get("idle_min") or 0),
                _opt_float(r.get("max_speed_mph")),
                _opt_float(r.get("avg_fuel_pct")),
                int(r.get("harsh_event_count") or 0),
                int(r.get("fault_count_eod") or 0),
                _opt_float(r.get("odometer_eod")),
                _opt_float(r.get("engine_hours_eod")),
                r.get("source_ts"),
                r.get("registry_id"),
                ts,
            ))
        if values:
            await self._db.executemany(
                """
                INSERT INTO vehicle_telemetry (
                    account_id, vehicle_id, granularity, bucket_start,
                    miles, drive_min, idle_min,
                    max_speed_mph, avg_fuel_pct,
                    harsh_event_count, fault_count_eod,
                    odometer_eod, engine_hours_eod, source_ts, registry_id,
                    ingested_at
                ) VALUES (?, ?, 'weekly', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (account_id, vehicle_id, granularity, bucket_start) DO UPDATE SET
                    miles=excluded.miles,
                    drive_min=excluded.drive_min,
                    idle_min=excluded.idle_min,
                    max_speed_mph=excluded.max_speed_mph,
                    avg_fuel_pct=excluded.avg_fuel_pct,
                    harsh_event_count=excluded.harsh_event_count,
                    fault_count_eod=excluded.fault_count_eod,
                    odometer_eod=COALESCE(excluded.odometer_eod, vehicle_telemetry.odometer_eod),
                    engine_hours_eod=COALESCE(excluded.engine_hours_eod, vehicle_telemetry.engine_hours_eod),
                    source_ts=COALESCE(excluded.source_ts, vehicle_telemetry.source_ts),
                    registry_id=COALESCE(excluded.registry_id, vehicle_telemetry.registry_id),
                    ingested_at=excluded.ingested_at
                """,
                values,
            )
        await self._db.commit()
        return len(values)

    async def prune_vehicle_telemetry_weekly(
        self, account_id: int, *, days_keep: int = 1825,
    ) -> int:
        """Drop weekly roll-up rows older than the retention window
        (default ~5 years — the tier is tiny, so the long window is cheap
        and backs multi-year year-over-year trends)."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_keep)).date().isoformat()
        cur = await self._db.execute(
            "DELETE FROM vehicle_telemetry "
            "WHERE account_id = ? AND granularity = 'weekly' AND bucket_start < ?",
            (account_id, cutoff),
        )
        await self._db.commit()
        return getattr(cur, "rowcount", 0) or 0

    async def prune_driver_efficiency_daily(
        self, account_id: int, *, days_keep: int = 730,
    ) -> int:
        """Drop per-driver daily efficiency rows older than the window
        (the Driver-feature analogue of ``prune_vehicle_metrics_daily``)."""
        from datetime import timedelta
        cutoff_day = (datetime.now(timezone.utc) - timedelta(days=days_keep)).date().isoformat()
        cur = await self._db.execute(
            "DELETE FROM driver_efficiency_daily "
            "WHERE account_id = ? AND day < ?",
            (account_id, cutoff_day),
        )
        await self._db.commit()
        return getattr(cur, "rowcount", 0) or 0

    async def prune_safety_event_log(
        self, account_id: int, *, days_keep: int = 1095,
    ) -> int:
        """Drop safety / harsh-event rows older than the window.

        Compliance-sensitive (FMCSA / litigation / insurance), so the
        window is deliberately long (3 years by default).  Owned by the
        Safety Events feature.
        """
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_keep)).isoformat()
        cur = await self._db.execute(
            "DELETE FROM safety_event_log "
            "WHERE account_id = ? AND occurred_at < ?",
            (account_id, cutoff),
        )
        await self._db.commit()
        return getattr(cur, "rowcount", 0) or 0

    async def prune_vehicle_fault_detail(
        self, account_id: int, *, days_keep: int = 365,
    ) -> int:
        """Drop CLEARED fault rows older than the window.

        Active faults (``cleared_at IS NULL``) are live state and are NEVER
        pruned — only resolved DTCs aged past the window are removed, so the
        diagnostic history stays bounded without ever losing a live fault.
        """
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_keep)).isoformat()
        cur = await self._db.execute(
            "DELETE FROM vehicle_fault_detail "
            "WHERE account_id = ? AND cleared_at IS NOT NULL AND cleared_at < ?",
            (account_id, cutoff),
        )
        await self._db.commit()
        return getattr(cur, "rowcount", 0) or 0

    # ── Retention-run telemetry ──────────────────────────────────
    # One summary row per target per nightly retention run, so the
    # operator console can show "last run + rows deleted".  Global
    # (no account_id) — the run is aggregated across all accounts.
    # The table self-creates on first write so this stays a
    # behaviour-preserving, self-contained addition.

    async def _ensure_retention_runs_table(self) -> None:
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS retention_runs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                target_key    TEXT    NOT NULL,
                scope         TEXT    NOT NULL DEFAULT '',
                keep_days     INTEGER NOT NULL DEFAULT 0,
                rows_deleted  INTEGER NOT NULL DEFAULT 0,
                accounts      INTEGER NOT NULL DEFAULT 0,
                ran_at        TEXT    NOT NULL
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_retention_runs_target_ran "
            "ON retention_runs(target_key, ran_at DESC)"
        )

    async def record_retention_runs(self, ran_at: str, rows) -> int:
        """Persist one summary row per target for a completed run.

        ``rows`` is an iterable of dicts with ``target_key`` plus optional
        ``scope`` / ``keep_days`` / ``rows_deleted`` / ``accounts``.
        """
        await self._ensure_retention_runs_table()
        values = [
            (
                str(r.get("target_key") or ""),
                str(r.get("scope") or ""),
                int(r.get("keep_days") or 0),
                int(r.get("rows_deleted") or 0),
                int(r.get("accounts") or 0),
                ran_at,
            )
            for r in rows
            if r.get("target_key")
        ]
        if not values:
            return 0
        await self._db.executemany(
            "INSERT INTO retention_runs "
            "(target_key, scope, keep_days, rows_deleted, accounts, ran_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            values,
        )
        await self._db.commit()
        return len(values)

    async def get_latest_retention_runs(self) -> list[dict[str, Any]]:
        """The most recent run row per target (newest ``ran_at`` wins).

        Returns ``[]`` when no run has been recorded yet (table absent)."""
        try:
            cur = await self._db.execute(
                """
                SELECT DISTINCT ON (target_key)
                       target_key, scope, keep_days, rows_deleted, accounts, ran_at
                  FROM retention_runs
                 ORDER BY target_key, ran_at DESC
                """
            )
            return [dict(r) for r in await cur.fetchall()]
        except Exception:
            return []

    # ── Scheduler-job snapshot ───────────────────────────────────
    # The bot process snapshots its registered APScheduler jobs here so
    # the (separate) API process can show them on the operator console —
    # it can't read the bot's in-memory scheduler directly.  Self-creating
    # table, full-replace on each snapshot so removed jobs drop out.

    async def _ensure_scheduler_jobs_table(self) -> None:
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduler_jobs (
                job_id       TEXT    PRIMARY KEY,
                trigger      TEXT    NOT NULL DEFAULT '',
                next_run_at  TEXT,
                category     TEXT    NOT NULL DEFAULT '',
                description  TEXT    NOT NULL DEFAULT '',
                updated_at   TEXT    NOT NULL
            )
            """
        )
        # Upgrade path for a table created before ``category`` existed.
        await self._db.execute(
            "ALTER TABLE scheduler_jobs ADD COLUMN IF NOT EXISTS "
            "category TEXT NOT NULL DEFAULT ''"
        )

    async def record_scheduler_jobs(self, rows) -> int:
        """Replace the snapshot of currently-registered scheduler jobs.

        ``rows`` is an iterable of dicts with ``job_id`` plus optional
        ``trigger`` / ``next_run_at`` / ``category`` / ``description``."""
        await self._ensure_scheduler_jobs_table()
        now = self._now()
        values = [
            (
                str(r.get("job_id") or ""),
                str(r.get("trigger") or ""),
                r.get("next_run_at"),
                str(r.get("category") or ""),
                str(r.get("description") or ""),
                now,
            )
            for r in rows
            if r.get("job_id")
        ]
        await self._db.execute("DELETE FROM scheduler_jobs")
        if values:
            await self._db.executemany(
                "INSERT INTO scheduler_jobs "
                "(job_id, trigger, next_run_at, category, description, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                values,
            )
        await self._db.commit()
        return len(values)

    async def get_scheduler_jobs(self) -> list[dict[str, Any]]:
        """The last snapshot of registered scheduler jobs, soonest-next
        first.  Returns ``[]`` when no snapshot exists yet."""
        try:
            cur = await self._db.execute(
                "SELECT job_id, trigger, next_run_at, category, description, updated_at "
                "FROM scheduler_jobs ORDER BY next_run_at ASC NULLS LAST, job_id"
            )
            return [dict(r) for r in await cur.fetchall()]
        except Exception:
            return []

    async def get_vehicle_metrics_daily(
        self,
        account_id: int,
        *,
        vehicle_id: str | None = None,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Read daily roll-up rows for the window, oldest-first.

        Backs the vehicle-detail usage chart (per-day miles, drive
        hours, idle hours over 30/90/365 day windows), the fleet
        utilization summary, and the predictive-maintenance trend
        lines.  Default window is 30 days to keep the dashboard
        payload cheap; routes can override.
        """
        from datetime import timedelta
        since_day = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        where = ["account_id = ?", "granularity = 'daily'", "bucket_start >= ?"]
        args: list[Any] = [account_id, since_day]
        if vehicle_id:
            where.append("vehicle_id = ?")
            args.append(vehicle_id)
        # Output keys (positional zip); ``day_utc`` is the unified table's
        # ``bucket_start`` for the daily grain, kept as the public key.
        cols = [
            "vehicle_id", "day_utc", "miles", "drive_min", "idle_min",
            "max_speed_mph", "avg_fuel_pct", "harsh_event_count",
        ]
        cur = await self._db.execute(
            f"""
            SELECT vehicle_id, bucket_start, miles, drive_min, idle_min,
                   max_speed_mph, avg_fuel_pct, harsh_event_count
              FROM vehicle_telemetry
             WHERE {' AND '.join(where)}
             ORDER BY bucket_start ASC
            """,
            tuple(args),
        )
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    async def get_vehicle_usage_summary(
        self,
        account_id: int,
        vehicle_id: str,
        *,
        days: int = 30,
    ) -> dict[str, Any]:
        """One-row totals over the window: miles, drive_h, idle_h,
        utilization %, average fuel %, harsh-event count, plus the
        cost-per-mile aggregate joined from ``work_orders``.

        Returns zeros when no data exists — callers render that as
        "no recent activity" rather than as an error.  Utilization is
        drive / (drive + idle); parked time isn't captured by sample
        counts (only moving + idle states populate samples in the
        hourly aggregator), so the ratio reads as "duty time" not
        "calendar time".
        """
        from datetime import timedelta
        since_day = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        cur = await self._db.execute(
            """
            SELECT COALESCE(SUM(miles), 0)              AS total_miles,
                   COALESCE(SUM(drive_min), 0)          AS total_drive_min,
                   COALESCE(SUM(idle_min), 0)           AS total_idle_min,
                   COALESCE(MAX(max_speed_mph), 0)      AS max_speed_mph,
                   AVG(avg_fuel_pct)                    AS avg_fuel_pct,
                   COALESCE(SUM(harsh_event_count), 0)  AS harsh_events,
                   COUNT(*)                             AS days_with_data
              FROM vehicle_telemetry
             WHERE account_id = ? AND vehicle_id = ?
               AND granularity = 'daily' AND bucket_start >= ?
            """,
            (account_id, vehicle_id, since_day),
        )
        row = await cur.fetchone()
        cols = (
            "total_miles", "total_drive_min", "total_idle_min",
            "max_speed_mph", "avg_fuel_pct", "harsh_events",
            "days_with_data",
        )
        d = dict(zip(cols, row)) if row else {k: 0 for k in cols}

        # Cost over the same window from work_orders.  Match by both
        # vehicle_id (preferred — set on new uploads) and vehicle_name
        # (legacy rows) so older work orders still count.  Service
        # date is the canonical "when did the cost occur" timestamp.
        cur = await self._db.execute(
            """
            SELECT vehicle_id, vehicle_name FROM vehicle_state
             WHERE account_id = ? AND vehicle_id = ?
             LIMIT 1
            """,
            (account_id, vehicle_id),
        )
        vrow = await cur.fetchone()
        vehicle_name = (vrow[1] if vrow else "") or ""

        cur = await self._db.execute(
            """
            SELECT COALESCE(SUM(total_cost), 0) AS total_cost,
                   COUNT(*)                     AS work_order_count
              FROM work_orders
             WHERE account_id = ?
               AND service_date >= ?
               AND status NOT IN ('open', 'void')
               AND payment_status != 'void'
               AND (vehicle_id = ? OR (vehicle_id = '' AND vehicle_name = ?))
            """,
            (account_id, since_day, vehicle_id, vehicle_name),
        )
        crow = await cur.fetchone()
        cost_cols = ("total_cost", "work_order_count")
        cd = dict(zip(cost_cols, crow)) if crow else {"total_cost": 0, "work_order_count": 0}

        total_miles = float(d.get("total_miles") or 0)
        drive_min = float(d.get("total_drive_min") or 0)
        idle_min = float(d.get("total_idle_min") or 0)
        duty_min = drive_min + idle_min
        utilization_pct = (drive_min / duty_min * 100.0) if duty_min > 0 else 0.0
        total_cost = float(cd.get("total_cost") or 0)
        cost_per_mile = (total_cost / total_miles) if total_miles > 0 else None

        return {
            "vehicle_id":        vehicle_id,
            "vehicle_name":      vehicle_name,
            "days":              int(days),
            "days_with_data":    int(d.get("days_with_data") or 0),
            "total_miles":       round(total_miles, 1),
            "drive_hours":       round(drive_min / 60.0, 2),
            "idle_hours":        round(idle_min / 60.0, 2),
            "utilization_pct":   round(utilization_pct, 1),
            "max_speed_mph":     round(float(d.get("max_speed_mph") or 0), 1),
            # NULL, not 0: "no fuel reading" and "tank empty" are
            # different answers, and only one of them is alarming.
            "avg_fuel_pct":      (round(float(d["avg_fuel_pct"]), 1)
                                  if d.get("avg_fuel_pct") is not None else None),
            "harsh_events":      int(d.get("harsh_events") or 0),
            "total_cost":        round(total_cost, 2),
            "work_order_count":  int(cd.get("work_order_count") or 0),
            "cost_per_mile":     round(cost_per_mile, 3) if cost_per_mile is not None else None,
        }

    async def get_account_utilization_summary(
        self,
        account_id: int,
        *,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Per-vehicle utilization summary across the entire fleet.

        Backs the Vehicles list "Utilization (30d)" column and the
        fleet executive scorecard.  Vehicles with no data in the
        window are absent — the caller can render them as "—" rather
        than as zero.
        """
        from datetime import timedelta
        since_day = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        cur = await self._db.execute(
            """
            SELECT vehicle_id,
                   COALESCE(SUM(miles), 0)     AS total_miles,
                   COALESCE(SUM(drive_min), 0) AS drive_min,
                   COALESCE(SUM(idle_min), 0)  AS idle_min,
                   COUNT(*)                    AS days_with_data
              FROM vehicle_telemetry
             WHERE account_id = ? AND granularity = 'daily' AND bucket_start >= ?
             GROUP BY vehicle_id
            """,
            (account_id, since_day),
        )
        out: list[dict[str, Any]] = []
        cols = ("vehicle_id", "total_miles", "drive_min", "idle_min", "days_with_data")
        for row in await cur.fetchall():
            d = dict(zip(cols, row))
            vid = str(d.get("vehicle_id") or "")
            if not vid:
                continue
            drive_min = float(d.get("drive_min") or 0)
            idle_min = float(d.get("idle_min") or 0)
            duty_min = drive_min + idle_min
            utilization_pct = (drive_min / duty_min * 100.0) if duty_min > 0 else 0.0
            out.append({
                "vehicle_id":      vid,
                "total_miles":     round(float(d.get("total_miles") or 0), 1),
                "drive_hours":     round(drive_min / 60.0, 2),
                "idle_hours":      round(idle_min / 60.0, 2),
                "utilization_pct": round(utilization_pct, 1),
                "days_with_data":  int(d.get("days_with_data") or 0),
            })
        return out

    async def prune_vehicle_telemetry_hourly(
        self, account_id: int, *, days_keep: int = 90,
    ) -> int:
        """Drop hourly bucket rows older than the retention window.

        Lives here next to its siblings so all three retention windows
        are tuned in one place.  90 days matches the predictive-
        maintenance lookback used by the engine-wear analyzer."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_keep)).strftime(
            "%Y-%m-%dT%H:00:00",
        )
        cur = await self._db.execute(
            "DELETE FROM vehicle_telemetry "
            "WHERE account_id = ? AND granularity = 'hourly' AND bucket_start < ?",
            (account_id, cutoff),
        )
        await self._db.commit()
        return getattr(cur, "rowcount", 0) or 0


    # ── vehicle_health_snapshot ────────────────────────────

    async def upsert_vehicle_health_snapshots(
        self,
        account_id: int,
        rows: Iterable[dict[str, Any]],
    ) -> int:
        """Bulk upsert (full overwrite) of current vehicle-health snapshot.

        Caller passes pre-shaped rows: ``{vehicle_id, vehicle_name,
        company_code, alert_count, raw, captured_at}`` where ``raw`` is
        the live-shape per-vehicle dict from ``client.get_vehicle_health()``.
        ``raw`` is JSON-serialised on the way in and decoded on the way
        out so readers return the live shape unchanged.
        """
        ts = _now_iso()
        values: list[tuple] = []
        for r in rows:
            vid = (r.get("vehicle_id") or r.get("id") or "").strip()
            if not vid:
                continue
            values.append((
                vid, account_id,
                str(r.get("vehicle_name") or r.get("name") or ""),
                str(r.get("company_code") or r.get("_org") or ""),
                int(r.get("alert_count") or 0),
                json.dumps(r.get("raw") or {}, default=str),
                str(r.get("captured_at") or ts),
                r.get("captured_at") or None,
                ts,
            ))
        if values:
            await self._db.executemany(
                """
                INSERT INTO vehicle_health_snapshot (
                    vehicle_id, account_id, vehicle_name, company_code,
                    alert_count, raw_json, captured_at, source_ts,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vehicle_id) DO UPDATE SET
                    account_id=excluded.account_id,
                    vehicle_name=excluded.vehicle_name,
                    company_code=excluded.company_code,
                    alert_count=excluded.alert_count,
                    raw_json=excluded.raw_json,
                    captured_at=excluded.captured_at,
                    source_ts=COALESCE(excluded.source_ts, source_ts),
                    updated_at=excluded.updated_at
                """,
                values,
            )
        await self._db.commit()
        return len(values)

    async def get_vehicle_health_snapshots(
        self,
        account_id: int,
        *,
        company: str | None = None,
        vehicle_nums: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Read current vehicle-health snapshot rows.

        Returns the original live-shape per-vehicle dicts (decoded from
        ``raw_json``), so callers stay agnostic to whether the data
        came from Samsara or the warehouse.
        """
        where = ["account_id = ?"]
        args: list[Any] = [account_id]
        if company:
            where.append("company_code = ?")
            args.append(company)
        if vehicle_nums:
            placeholders = ",".join("?" * len(vehicle_nums))
            where.append(f"vehicle_name IN ({placeholders})")
            args.extend(vehicle_nums)
        cur = await self._db.execute(
            f"""
            SELECT raw_json, vehicle_name, company_code, alert_count
            FROM vehicle_health_snapshot
            WHERE {' AND '.join(where)}
            ORDER BY alert_count DESC, company_code, vehicle_name
            """,
            tuple(args),
        )
        out: list[dict[str, Any]] = []
        for row in await cur.fetchall():
            raw_json, vname, ccode, _alert = row
            try:
                d = json.loads(raw_json or "{}")
            except Exception:
                d = {}
            # Defensive: ensure live-shape keys are present even if a
            # writer ever stored an empty/legacy blob.
            d.setdefault("name", vname)
            d.setdefault("_org", ccode)
            out.append(d)
        return out

    # ── vehicle_fault_snapshot + vehicle_fault_detail ──────

    async def upsert_vehicle_fault_snapshot(
        self,
        account_id: int,
        faulted_rows: Iterable[dict[str, Any]],
        critical_vehicle_ids: set[str] | None = None,
    ) -> int:
        """Replace the per-vehicle fault snapshot for *account_id*.

        ``faulted_rows`` is the iterable of pre-shaped per-vehicle dicts
        (the ingestor wraps the live ``client.get_vehicles_with_faults()``
        response).  Vehicles no longer in the input are removed so a
        cleared truck disappears from the snapshot in a single cycle.
        """
        ts = _now_iso()
        critical_ids = set(critical_vehicle_ids or ())
        seen: set[str] = set()
        values: list[tuple] = []
        for r in faulted_rows:
            vid = (r.get("vehicle_id") or "").strip()
            if not vid:
                continue
            seen.add(vid)
            values.append((
                vid, account_id,
                str(r.get("vehicle_name") or ""),
                str(r.get("company_code") or ""),
                int(r.get("dtc_count") or 0),
                1 if vid in critical_ids else 0,
                json.dumps(r.get("raw") or {}, default=str),
                str(r.get("captured_at") or ts),
                r.get("captured_at") or None,
                ts,
            ))
        if values:
            await self._db.executemany(
                """
                INSERT INTO vehicle_fault_snapshot (
                    vehicle_id, account_id, vehicle_name, company_code,
                    dtc_count, has_critical, raw_json,
                    captured_at, source_ts, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vehicle_id) DO UPDATE SET
                    account_id=excluded.account_id,
                    vehicle_name=excluded.vehicle_name,
                    company_code=excluded.company_code,
                    dtc_count=excluded.dtc_count,
                    has_critical=excluded.has_critical,
                    raw_json=excluded.raw_json,
                    captured_at=excluded.captured_at,
                    source_ts=COALESCE(excluded.source_ts, source_ts),
                    updated_at=excluded.updated_at
                """,
                values,
            )
        # Drop rows for vehicles no longer faulted in this cycle.  Done
        # last so a transient empty pull (samsara hiccup → seen=∅) only
        # blanks the snapshot once we got past the writer step.
        if seen:
            placeholders = ",".join("?" * len(seen))
            await self._db.execute(
                f"DELETE FROM vehicle_fault_snapshot "
                f"WHERE account_id = ? AND vehicle_id NOT IN ({placeholders})",
                (account_id, *seen),
            )
        else:
            await self._db.execute(
                "DELETE FROM vehicle_fault_snapshot WHERE account_id = ?",
                (account_id,),
            )
        await self._db.commit()
        return len(values)

    async def upsert_vehicle_fault_details(
        self,
        account_id: int,
        per_vehicle_dtcs: dict[str, list[dict[str, Any]]],
    ) -> tuple[int, int]:
        """Lifecycle-aware upsert of per-DTC rows.

        ``per_vehicle_dtcs`` maps ``vehicle_id`` → list of DTC dicts
        observed in the current cycle.  For each vehicle:

          * any DTC currently active (``cleared_at IS NULL``) but absent
            from the new list gets ``cleared_at = now``;
          * any DTC in the new list is INSERT-OR-IGNORE'd (preserves the
            original ``observed_at``); a re-appearance after a clear
            opens a fresh row when the dtc_id differs (the SPN/FMI pair
            generates the same id, but the lifecycle column gives us the
            audit trail).

        Returns ``(newly_observed, newly_cleared)``.
        """
        ts = _now_iso()
        new_obs = 0
        new_cleared = 0

        # Normalize input: drop empty vehicle_ids and build the set of
        # (vid, dtc_id) currently observed.
        normalized: dict[str, list[dict[str, Any]]] = {}
        current: set[tuple[str, str]] = set()
        for vid, dtcs in per_vehicle_dtcs.items():
            vid = (vid or "").strip()
            if not vid:
                continue
            normalized[vid] = dtcs
            for dtc in dtcs:
                did = _dtc_id(dtc)
                if did:
                    current.add((vid, did))

        if not normalized:
            await self._db.commit()
            return 0, 0

        # ── ONE bulk SELECT for all (vehicle_id, dtc_id, cleared_at)
        #    rows across every vehicle in this cycle. Replaces the
        #    O(V) + O(V·D) per-vehicle SELECTs with O(V/500) chunks.
        existing_state: dict[tuple[str, str], Any] = {}
        active_by_vid: dict[str, set[str]] = {v: set() for v in normalized}
        vids = list(normalized.keys())
        for i in range(0, len(vids), 500):
            chunk = vids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = await self._db.execute(
                f"""
                SELECT vehicle_id, dtc_id, cleared_at
                FROM vehicle_fault_detail
                WHERE account_id = ? AND vehicle_id IN ({placeholders})
                """,
                (account_id, *chunk),
            )
            for row in await cur.fetchall():
                row_vid = str(row[0] or "")
                row_did = str(row[1] or "")
                cleared_at = row[2]
                existing_state[(row_vid, row_did)] = cleared_at
                if cleared_at is None:
                    active_by_vid.setdefault(row_vid, set()).add(row_did)

        # ── Plan three bulk operations from the in-memory diff
        clear_params: list[tuple] = []   # (ts, account_id, vid, did)
        insert_params: list[tuple] = []  # full INSERT row tuple
        reopen_params: list[tuple] = []  # (ts, raw_json, account_id, vid, did)

        for vid, dtcs in normalized.items():
            current_ids = {_dtc_id(d) for d in dtcs if _dtc_id(d)}
            # Rows that were active before this cycle but are no longer
            # observed → mark them cleared.
            for did in (active_by_vid.get(vid, set()) - current_ids):
                clear_params.append((ts, account_id, vid, did))
                new_cleared += 1
            # Rows observed in this cycle: insert if brand-new, reopen
            # if a previously cleared row exists for the same dtc_id.
            for dtc in dtcs:
                did = _dtc_id(dtc)
                if not did:
                    continue
                key = (vid, did)
                if key not in existing_state:
                    insert_params.append((
                        account_id, vid, did,
                        dtc.get("spn") if dtc.get("spn") is not None else dtc.get("spnId"),
                        dtc.get("fmi") if dtc.get("fmi") is not None else dtc.get("fmiId"),
                        # spnDescription names the fault ("Engine Exhaust
                        # Gas Recirculation 1 Mass Flow Rate"); the FMI
                        # text only grades it ("Low-moderate severity").
                        str(dtc.get("description") or dtc.get("spnDescription")
                            or dtc.get("fmiDescription") or ""),
                        str(dtc.get("severity") or ""),
                        ts,
                        json.dumps(dtc, default=str),
                    ))
                    new_obs += 1
                elif existing_state[key] is not None:
                    # Previously cleared → reopen.
                    reopen_params.append((
                        ts, json.dumps(dtc, default=str),
                        account_id, vid, did,
                    ))
                    new_obs += 1

        if clear_params:
            await self._db.executemany(
                """
                UPDATE vehicle_fault_detail
                SET cleared_at = ?
                WHERE account_id = ? AND vehicle_id = ? AND dtc_id = ?
                  AND cleared_at IS NULL
                """,
                clear_params,
            )
        if insert_params:
            await self._db.executemany(
                """
                INSERT INTO vehicle_fault_detail (
                    account_id, vehicle_id, dtc_id,
                    spn, fmi, description, severity,
                    observed_at, cleared_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                insert_params,
            )
        if reopen_params:
            await self._db.executemany(
                """
                UPDATE vehicle_fault_detail
                SET cleared_at = NULL, observed_at = ?, raw_json = ?
                WHERE account_id = ? AND vehicle_id = ? AND dtc_id = ?
                """,
                reopen_params,
            )
        await self._db.commit()
        return new_obs, new_cleared

    async def get_vehicles_with_faults_warehouse(
        self,
        account_id: int,
        *,
        company: str | None = None,
    ) -> tuple[list[dict[str, Any]], int, dict[str, dict[str, int]]]:
        """Return ``(faulted, total, breakdown)`` mirroring the live shape.

        * ``faulted``   — decoded raw_json from ``vehicle_fault_snapshot``
        * ``total``     — vehicle count from ``vehicle_state`` (filtered)
        * ``breakdown`` — per-company ``{total, faulted, dtcs}``
        """
        # faulted list
        where = ["account_id = ?"]
        args: list[Any] = [account_id]
        if company:
            where.append("company_code = ?")
            args.append(company)
        cur = await self._db.execute(
            f"""
            SELECT raw_json, has_critical FROM vehicle_fault_snapshot
            WHERE {' AND '.join(where)}
            ORDER BY has_critical DESC, dtc_count DESC, vehicle_name
            """,
            tuple(args),
        )
        faulted: list[dict[str, Any]] = []
        for raw_json, has_critical in await cur.fetchall():
            try:
                v = json.loads(raw_json or "{}")
                v["_severity"] = "critical" if has_critical else "warning"
                faulted.append(v)
            except Exception:
                pass

        # totals + breakdown from vehicle_state
        where2 = ["account_id = ?"]
        args2: list[Any] = [account_id]
        if company:
            where2.append("company_code = ?")
            args2.append(company)
        cur2 = await self._db.execute(
            f"""
            SELECT company_code, COUNT(*) AS total,
                   SUM(CASE WHEN fault_count > 0 THEN 1 ELSE 0 END) AS faulted,
                   SUM(fault_count) AS dtcs
            FROM vehicle_state
            WHERE {' AND '.join(where2)}
            GROUP BY company_code
            """,
            tuple(args2),
        )
        breakdown: dict[str, dict[str, int]] = {}
        grand_total = 0
        for code, total, fcount, dtcs in await cur2.fetchall():
            grand_total += int(total or 0)
            breakdown[str(code or "")] = {
                "total": int(total or 0),
                "faulted": int(fcount or 0),
                "dtcs": int(dtcs or 0),
            }
        return faulted, grand_total, breakdown

    async def get_vehicle_fault_snapshot_by_name(
        self,
        account_id: int,
        vehicle_name: str,
    ) -> dict[str, Any] | None:
        """Return the decoded raw_json (full DTC details) for one vehicle.

        ``vehicle_fault_snapshot.raw_json`` stores the live Samsara
        shape — including ``fault_codes.j1939.diagnosticTroubleCodes``
        with ``spnDescription`` / ``fmiDescription`` / ``sourceAddressName``.
        Used by /api/vehicles/{name}/faults so the dashboard renders the
        actual fault descriptions instead of the empty-dict placeholders
        that ``_warehouse_row_to_overview`` produces from vehicle_state's
        fault_count alone.

        Returns None when no fault snapshot exists for the vehicle.
        """
        cur = await self._db.execute(
            "SELECT raw_json FROM vehicle_fault_snapshot "
            "WHERE account_id = ? AND vehicle_name = ? "
            "LIMIT 1",
            (account_id, vehicle_name),
        )
        row = await cur.fetchone()
        if not row:
            return None
        raw = row[0] if not isinstance(row, dict) else row.get("raw_json")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def get_active_fault_dtc_ids(
        self,
        account_id: int,
        *,
        vehicle_id: str | None = None,
    ) -> set[str]:
        """Return active (``cleared_at IS NULL``) ``dtc_id``s.

        alerting uses this in place of the Redis ``_known_faults``
        set so the DB becomes the dedup SSOT.  Currently unused; the
        method is here so lands as a behaviour change only.
        """
        where = ["account_id = ?", "cleared_at IS NULL"]
        args: list[Any] = [account_id]
        if vehicle_id:
            where.append("vehicle_id = ?")
            args.append(vehicle_id)
        cur = await self._db.execute(
            f"SELECT dtc_id FROM vehicle_fault_detail WHERE {' AND '.join(where)}",
            tuple(args),
        )
        return {row[0] for row in await cur.fetchall()}

    # ── aggregate_weather_snapshot ─────────────────────────────

    async def upsert_aggregate_weather_snapshots(
        self,
        account_id: int,
        rows: Iterable[dict[str, Any]],
    ) -> int:
        ts = _now_iso()
        seen: set[str] = set()
        values: list[tuple] = []
        for r in rows:
            vid = (r.get("vehicle_id") or "").strip()
            if not vid:
                continue
            seen.add(vid)
            values.append((
                vid, account_id,
                str(r.get("vehicle_name") or ""),
                str(r.get("company_code") or ""),
                r.get("temp_f"),
                json.dumps(r.get("raw") or {}, default=str),
                str(r.get("captured_at") or ts),
                r.get("captured_at") or None,
                ts,
            ))
        if values:
            await self._db.executemany(
                """
                INSERT INTO aggregate_weather_snapshot (
                    vehicle_id, account_id, vehicle_name, company_code,
                    temp_f, raw_json, captured_at, source_ts, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vehicle_id) DO UPDATE SET
                    account_id=excluded.account_id,
                    vehicle_name=excluded.vehicle_name,
                    company_code=excluded.company_code,
                    temp_f=excluded.temp_f,
                    raw_json=excluded.raw_json,
                    captured_at=excluded.captured_at,
                    source_ts=COALESCE(excluded.source_ts, source_ts),
                    updated_at=excluded.updated_at
                """,
                values,
            )
        # Same fail-safe as fault snapshot: only DELETE if the writer
        # actually saw vehicles this cycle.
        if seen:
            placeholders = ",".join("?" * len(seen))
            await self._db.execute(
                f"DELETE FROM aggregate_weather_snapshot "
                f"WHERE account_id = ? AND vehicle_id NOT IN ({placeholders})",
                (account_id, *seen),
            )
        await self._db.commit()
        return len(values)

    async def get_aggregate_weather_snapshots(
        self,
        account_id: int,
        *,
        company: str | None = None,
    ) -> list[dict[str, Any]]:
        where = ["account_id = ?"]
        args: list[Any] = [account_id]
        if company:
            where.append("company_code = ?")
            args.append(company)
        cur = await self._db.execute(
            f"""
            SELECT raw_json, vehicle_name, company_code, temp_f
            FROM aggregate_weather_snapshot
            WHERE {' AND '.join(where)}
            ORDER BY temp_f ASC
            """,
            tuple(args),
        )
        out: list[dict[str, Any]] = []
        for row in await cur.fetchall():
            raw_json, vname, ccode, _t = row
            try:
                d = json.loads(raw_json or "{}")
            except Exception:
                d = {}
            d.setdefault("name", vname)
            d.setdefault("_org", ccode)
            out.append(d)
        return out

    # ── aggregate_efficiency_snapshot ──────────────────────────

    async def upsert_aggregate_efficiency_snapshot(
        self,
        account_id: int,
        *,
        window_days: int,
        company_code: str,
        payload: list[dict[str, Any]],
    ) -> int:
        ts = _now_iso()
        await self._db.execute(
            """
            INSERT INTO aggregate_efficiency_snapshot (
                account_id, window_days, company_code,
                payload_json, captured_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, window_days, company_code) DO UPDATE SET
                payload_json=excluded.payload_json,
                captured_at=excluded.captured_at,
                updated_at=excluded.updated_at
            """,
            (
                account_id, int(window_days), str(company_code or ""),
                json.dumps(payload, default=str), ts, ts,
            ),
        )
        await self._db.commit()
        return len(payload)

    async def get_aggregate_efficiency_snapshot(
        self,
        account_id: int,
        *,
        window_days: int,
        company_code: str,
    ) -> list[dict[str, Any]]:
        cur = await self._db.execute(
            """
            SELECT payload_json FROM aggregate_efficiency_snapshot
            WHERE account_id = ? AND window_days = ? AND company_code = ?
            """,
            (account_id, int(window_days), str(company_code or "")),
        )
        row = await cur.fetchone()
        if not row:
            return []
        try:
            return json.loads(row[0] or "[]")
        except Exception:
            return []

    # ── geofence definitions cache ─────────────────────

    async def upsert_geofence_definitions(
        self,
        account_id: int,
        items: list[dict[str, Any]],
        *,
        captured_at: str | None = None,
    ) -> int:
        """Bulk upsert geofence definitions.  Snapshot semantics: rows
        for this account whose ``geofence_id`` is missing from ``items``
        are deleted (so renamed/removed zones drop out).  No-op when
        ``items`` is empty (avoid wiping on a transient empty pull).
        """
        if not items:
            return 0
        ts = captured_at or _now_iso()
        rows: list[tuple[Any, ...]] = []
        seen_ids: list[str] = []
        for it in items:
            gid = str(it.get("id") or it.get("geofence_id") or "").strip()
            if not gid:
                continue
            seen_ids.append(gid)
            rows.append((
                gid,
                int(account_id),
                str(it.get("_org") or it.get("company") or ""),
                str(it.get("name") or ""),
                str(it.get("geofence_type")
                    or ("circle" if it.get("circularGeofence")
                        or (it.get("geofence", {}) or {}).get("circle")
                        else "polygon")),
                json.dumps(it, default=str),
                ts,
                ts,
            ))
        if not rows:
            return 0
        await self._db.executemany(
            """
            INSERT INTO geofence_definitions
                (geofence_id, account_id, company_code, name,
                 geofence_type, raw_json, captured_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(geofence_id) DO UPDATE SET
                account_id    = excluded.account_id,
                company_code  = excluded.company_code,
                name          = excluded.name,
                geofence_type = excluded.geofence_type,
                raw_json      = excluded.raw_json,
                captured_at   = excluded.captured_at,
                updated_at    = excluded.updated_at
            """,
            rows,
        )
        # Drop stale rows for this account.
        placeholders = ",".join("?" for _ in seen_ids)
        await self._db.execute(
            f"""
            DELETE FROM geofence_definitions
            WHERE account_id = ?
              AND geofence_id NOT IN ({placeholders})
            """,
            (int(account_id), *seen_ids),
        )
        await self._db.commit()
        return len(rows)

    async def get_geofence_definitions(
        self,
        account_id: int,
        *,
        company: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return cached geofences for an account, optionally filtered
        by company_code.  Each row is the full ``raw_json`` payload."""
        if company:
            cur = await self._db.execute(
                """
                SELECT raw_json FROM geofence_definitions
                WHERE account_id = ? AND company_code = ?
                """,
                (int(account_id), str(company)),
            )
        else:
            cur = await self._db.execute(
                """
                SELECT raw_json FROM geofence_definitions
                WHERE account_id = ?
                """,
                (int(account_id),),
            )
        out: list[dict[str, Any]] = []
        for row in await cur.fetchall():
            try:
                out.append(json.loads(row[0] or "{}"))
            except Exception:
                continue
        return out


def _dtc_id(dtc: dict[str, Any]) -> str:
    """Stable ID for a Samsara DTC.  Prefer the API's own id when
    present; otherwise hash on (spn, fmi) which uniquely identifies a
    J1939 trouble code.

    The live payload spells the pair ``spnId`` / ``fmiId`` (and carries
    no ``id`` at all) — reading only the bare spellings returned "" for
    every real DTC, so every one was skipped and the table stayed empty
    for its whole life.  Both spellings are read, like fuel and engine
    state before it: the provider's naming has drifted once per field.
    """
    sid = str(dtc.get("id") or dtc.get("samsara_id") or "").strip()
    if sid:
        return sid
    spn = dtc.get("spn") if dtc.get("spn") is not None else dtc.get("spnId")
    fmi = dtc.get("fmi") if dtc.get("fmi") is not None else dtc.get("fmiId")
    if spn is not None and fmi is not None:
        return f"spn:{spn}-fmi:{fmi}"
    return ""
