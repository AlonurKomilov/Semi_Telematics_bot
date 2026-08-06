"""safety_event_log storage — the events LOG (full fidelity, append-only).

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


class SafetyWarehouseMixin(_MixinBase):

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
