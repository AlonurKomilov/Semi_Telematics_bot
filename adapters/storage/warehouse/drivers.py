"""driver_efficiency storage — per-driver daily stats from the provider.

Split from the 3,283-line warehouse.py monolith — method names and
bodies byte-identical (Phase 5, Stage 1).  Composed into ``Database``
via the package ``__init__``.
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable, Optional

from adapters.storage.warehouse._util import (
    VELOCITY_DRIVE_DAY_MIN_MILES,
    VELOCITY_MIN_COVERAGE_DAYS,
    VELOCITY_MIN_DRIVE_DAYS,
    _MixinBase,
    _dtc_id,
    _now_iso,
    _opt_float,
)

logger = logging.getLogger(__name__)


class DriversWarehouseMixin(_MixinBase):

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
            "SELECT COUNT(*) AS c FROM driver_efficiency "
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


    # ── driver_efficiency ──────────────────────────────────────

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
                INSERT INTO driver_efficiency (
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
            FROM driver_efficiency
            WHERE {' AND '.join(where)}
            GROUP BY driver_id
            ORDER BY miles DESC
            """,
            tuple(args),
        )
        return [dict(zip(cols, row)) for row in await cur.fetchall()]


    async def prune_driver_efficiency_daily(
        self, account_id: int, *, days_keep: int = 730,
    ) -> int:
        """Drop per-driver daily efficiency rows older than the window
        (the Driver-feature analogue of ``prune_vehicle_state_day``)."""
        from datetime import timedelta
        cutoff_day = (datetime.now(timezone.utc) - timedelta(days=days_keep)).date().isoformat()
        cur = await self._db.execute(
            "DELETE FROM driver_efficiency "
            "WHERE account_id = ? AND day < ?",
            (account_id, cutoff_day),
        )
        await self._db.commit()
        return getattr(cur, "rowcount", 0) or 0
