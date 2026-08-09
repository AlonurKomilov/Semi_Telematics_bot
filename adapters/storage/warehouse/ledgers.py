"""ingest_orphans storage — identities the registry could not resolve.

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


class WarehouseLedgersMixin(_MixinBase):

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
            INSERT INTO ingest_orphans (
                account_id, dataset_key, external_id,
                name, company_code, count, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT (account_id, dataset_key, external_id) DO UPDATE SET
                name=excluded.name,
                company_code=excluded.company_code,
                count=ingest_orphans.count + 1,
                last_seen=excluded.last_seen
            """,
            values,
        )
        await self._db.commit()
        return len(values)

    async def record_device_events(self, account_id: int, events) -> list:
        """Append identity events (vin_change / gateway_swap /
        odo_rebase) — deduped on the exact transition.

        Returns only the events that were NEWLY inserted this call.
        Callers notify from the return value, never from the detected
        list: while an anchor stays changed the watch re-detects the
        same transition every tick (a changed VIN doesn't change back),
        and notifying detections re-pinged every admin per tick until
        the registry caught up (9 duplicate notices per admin,
        2026-08-07..09)."""
        new: list = []
        for e in events:
            cur = await self._db.execute(
                "INSERT INTO device_event_log "
                "(account_id, registry_id, vehicle_id, vehicle_name, "
                " kind, old_value, new_value, observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (account_id, vehicle_id, kind, old_value, "
                "new_value) DO NOTHING",
                (account_id, e.get("registry_id"),
                 str(e.get("vehicle_id") or ""),
                 str(e.get("vehicle_name") or ""),
                 str(e.get("kind") or ""),
                 str(e.get("old_value") or ""),
                 str(e.get("new_value") or ""),
                 str(e.get("observed_at") or "")),
            )
            if getattr(cur, "rowcount", 1) > 0:
                new.append(e)
        await self._db.commit()
        return new

    async def get_device_events(self, account_id: int, *, limit: int = 100):
        cur = await self._db.execute(
            "SELECT vehicle_id, vehicle_name, kind, old_value, new_value, "
            "observed_at FROM device_event_log WHERE account_id = ? "
            "ORDER BY observed_at DESC LIMIT ?",
            (account_id, limit),
        )
        cols = ("vehicle_id", "vehicle_name", "kind", "old_value",
                "new_value", "observed_at")
        return [dict(zip(cols, r)) for r in await cur.fetchall()]

