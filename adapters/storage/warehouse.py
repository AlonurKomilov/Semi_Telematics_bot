"""telemetry warehouse mixin.

Read + upsert helpers for the four warehouse tables created in
``schema.py``::

  - vehicle_state          (one row per vehicle, overwritten)
  - safety_event_log       (append-only, idempotent on samsara_event_id)
  - driver_efficiency_daily (one row per driver per day)
  - vehicle_telemetry_hourly (hourly roll-up)

Pure SQL — no Samsara client calls happen here.  The ingestor
(``capabilities/telemetry/ingestor.py``) is responsible for fetching
upstream data and shaping it before calling these helpers.

API routes never import this module directly; they go through the
reader functions in ``capabilities/telemetry/warehouse_reader.py``
which adds the ``WAREHOUSE_READS_ENABLED`` feature-flag check and
graceful fallback to the live Samsara client (so an empty warehouse
during the cutover window doesn't 500 the dashboard).
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import date, datetime, timezone
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
                    captured_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    raw_json, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                int(r.get("harsh_event_count") or 0),
                ts,
            ))
        if values:
            await self._db.executemany(
                """
                INSERT INTO vehicle_telemetry_hourly (
                    account_id, vehicle_id, hour_utc,
                    miles, drive_min, idle_min,
                    max_speed_mph, harsh_event_count, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, vehicle_id, hour_utc) DO UPDATE SET
                    miles=excluded.miles,
                    drive_min=excluded.drive_min,
                    idle_min=excluded.idle_min,
                    max_speed_mph=excluded.max_speed_mph,
                    harsh_event_count=excluded.harsh_event_count,
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
        where = ["account_id = ?", "hour_utc >= ?"]
        args: list[Any] = [account_id, since]
        if vehicle_id:
            where.append("vehicle_id = ?")
            args.append(vehicle_id)
        # Hard-coded column list — see comment in get_current_vehicles
        # for the asyncpg compatibility reason.
        cols = [
            "vehicle_id", "hour_utc", "miles", "drive_min", "idle_min",
            "max_speed_mph", "harsh_event_count",
        ]
        cur = await self._db.execute(
            f"""
            SELECT {', '.join(cols)}
            FROM vehicle_telemetry_hourly
            WHERE {' AND '.join(where)}
            ORDER BY hour_utc DESC
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
              FROM vehicle_telemetry_hourly
             WHERE account_id = ? AND hour_utc >= ?
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
                    engine_load_pct, rpm
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (account_id, vehicle_id, captured_at) DO NOTHING
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
            SELECT vehicle_id, day_utc, COALESCE(miles, 0) AS miles
              FROM vehicle_metrics_daily
             WHERE account_id = ? AND day_utc >= ?
             ORDER BY vehicle_id, day_utc
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
        — fleet-wide history queries are intentionally not supported
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
                ts,
            ))
        if values:
            await self._db.executemany(
                """
                INSERT INTO vehicle_metrics_daily (
                    account_id, vehicle_id, day_utc,
                    miles, drive_min, idle_min,
                    max_speed_mph, avg_fuel_pct,
                    harsh_event_count, fault_count_eod, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (account_id, vehicle_id, day_utc) DO UPDATE SET
                    miles=excluded.miles,
                    drive_min=excluded.drive_min,
                    idle_min=excluded.idle_min,
                    max_speed_mph=excluded.max_speed_mph,
                    avg_fuel_pct=excluded.avg_fuel_pct,
                    harsh_event_count=excluded.harsh_event_count,
                    fault_count_eod=excluded.fault_count_eod,
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
            "DELETE FROM vehicle_metrics_daily "
            "WHERE account_id = ? AND day_utc < ?",
            (account_id, cutoff_day),
        )
        await self._db.commit()
        return getattr(cur, "rowcount", 0) or 0

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
        where = ["account_id = ?", "day_utc >= ?"]
        args: list[Any] = [account_id, since_day]
        if vehicle_id:
            where.append("vehicle_id = ?")
            args.append(vehicle_id)
        cols = [
            "vehicle_id", "day_utc", "miles", "drive_min", "idle_min",
            "max_speed_mph", "avg_fuel_pct", "harsh_event_count",
        ]
        cur = await self._db.execute(
            f"""
            SELECT {', '.join(cols)}
              FROM vehicle_metrics_daily
             WHERE {' AND '.join(where)}
             ORDER BY day_utc ASC
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
                   COALESCE(AVG(avg_fuel_pct), 0)       AS avg_fuel_pct,
                   COALESCE(SUM(harsh_event_count), 0)  AS harsh_events,
                   COUNT(*)                             AS days_with_data
              FROM vehicle_metrics_daily
             WHERE account_id = ? AND vehicle_id = ?
               AND day_utc >= ?
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
               AND status != 'draft'
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
            "avg_fuel_pct":      round(float(d.get("avg_fuel_pct") or 0), 1),
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
              FROM vehicle_metrics_daily
             WHERE account_id = ? AND day_utc >= ?
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
            "DELETE FROM vehicle_telemetry_hourly "
            "WHERE account_id = ? AND hour_utc < ?",
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
                ts,
            ))
        if values:
            await self._db.executemany(
                """
                INSERT INTO vehicle_health_snapshot (
                    vehicle_id, account_id, vehicle_name, company_code,
                    alert_count, raw_json, captured_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vehicle_id) DO UPDATE SET
                    account_id=excluded.account_id,
                    vehicle_name=excluded.vehicle_name,
                    company_code=excluded.company_code,
                    alert_count=excluded.alert_count,
                    raw_json=excluded.raw_json,
                    captured_at=excluded.captured_at,
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
                ts,
            ))
        if values:
            await self._db.executemany(
                """
                INSERT INTO vehicle_fault_snapshot (
                    vehicle_id, account_id, vehicle_name, company_code,
                    dtc_count, has_critical, raw_json,
                    captured_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vehicle_id) DO UPDATE SET
                    account_id=excluded.account_id,
                    vehicle_name=excluded.vehicle_name,
                    company_code=excluded.company_code,
                    dtc_count=excluded.dtc_count,
                    has_critical=excluded.has_critical,
                    raw_json=excluded.raw_json,
                    captured_at=excluded.captured_at,
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
                        dtc.get("spn"),
                        dtc.get("fmi"),
                        str(dtc.get("description") or dtc.get("fmiDescription") or ""),
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
                ts,
            ))
        if values:
            await self._db.executemany(
                """
                INSERT INTO aggregate_weather_snapshot (
                    vehicle_id, account_id, vehicle_name, company_code,
                    temp_f, raw_json, captured_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vehicle_id) DO UPDATE SET
                    account_id=excluded.account_id,
                    vehicle_name=excluded.vehicle_name,
                    company_code=excluded.company_code,
                    temp_f=excluded.temp_f,
                    raw_json=excluded.raw_json,
                    captured_at=excluded.captured_at,
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
    J1939 trouble code."""
    sid = str(dtc.get("id") or dtc.get("samsara_id") or "").strip()
    if sid:
        return sid
    spn = dtc.get("spn")
    fmi = dtc.get("fmi")
    if spn is not None and fmi is not None:
        return f"spn:{spn}-fmi:{fmi}"
    return ""
