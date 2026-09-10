"""``plans`` — what each plan includes, as DATA the operator edits.

One row per plan (``accounts.tier`` is the key): the label, the registry
ids the plan includes (features and services — ``["*"]`` means every
one, today's and tomorrow's), and the quotas.  The resolver's plan mask
reads it (capabilities/permissions/plans.py); the system console
writes it.  Nothing here knows what an id means — that is the
registry's business, one layer up.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional


def _row(r) -> dict:
    try:
        included = json.loads(r["included"] or "[]")
    except (ValueError, TypeError):
        included = []
    try:
        quotas = json.loads(r["quotas"] or "{}")
    except (ValueError, TypeError):
        quotas = {}
    return {
        "tier": r["tier"], "label": r["label"],
        "included": [str(i) for i in included] if isinstance(included, list) else [],
        "quotas": quotas if isinstance(quotas, dict) else {},
        "updated_at": r["updated_at"], "updated_by": r["updated_by"],
    }


class PlansMixin:
    """Plan entitlements — mixed into the platform DB class."""

    async def list_plans(self) -> list[dict]:
        cur = await self._db.execute("SELECT * FROM plans ORDER BY tier")
        return [_row(r) for r in await cur.fetchall()]

    async def get_plan(self, tier: str) -> Optional[dict]:
        cur = await self._db.execute("SELECT * FROM plans WHERE tier = ?", (tier,))
        r = await cur.fetchone()
        return _row(r) if r else None

    async def upsert_plan(
        self, tier: str, *, label: str, included: list[str],
        quotas: Optional[dict] = None, updated_by: str = "",
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO plans (tier, label, included, quotas, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tier) DO UPDATE SET label = excluded.label, included = excluded.included, "
            "quotas = excluded.quotas, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (tier, label, json.dumps(list(included)), json.dumps(quotas or {}), now, updated_by),
        )
        await self._db.commit()
        return (await self.get_plan(tier)) or {}
