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
