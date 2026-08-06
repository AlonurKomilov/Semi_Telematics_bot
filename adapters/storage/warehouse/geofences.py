"""geofence_definitions storage — the provider's geofence catalog cache.

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


class GeofencesWarehouseMixin(_MixinBase):

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
