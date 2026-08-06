"""weather_snapshot + efficiency_snapshot storage — per-truck weather and per-company rollup caches.

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


class AggregatesWarehouseMixin(_MixinBase):

    # ── weather_snapshot ─────────────────────────────

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
                INSERT INTO weather_snapshot (
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
                    source_ts=COALESCE(excluded.source_ts, weather_snapshot.source_ts),
                    updated_at=excluded.updated_at
                """,
                values,
            )
        # Same fail-safe as fault snapshot: only DELETE if the writer
        # actually saw vehicles this cycle.
        if seen:
            placeholders = ",".join("?" * len(seen))
            await self._db.execute(
                f"DELETE FROM weather_snapshot "
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
            FROM weather_snapshot
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


    # ── efficiency_snapshot ──────────────────────────

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
            INSERT INTO efficiency_snapshot (
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
            SELECT payload_json FROM efficiency_snapshot
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
