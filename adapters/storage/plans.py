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
    keys = r.keys() if hasattr(r, "keys") else ()
    def _int(k):
        try:
            return int(r[k] or 0) if k in keys else 0
        except (TypeError, ValueError):
            return 0
    return {
        "tier": r["tier"], "label": r["label"],
        "included": [str(i) for i in included] if isinstance(included, list) else [],
        "quotas": quotas if isinstance(quotas, dict) else {},
        # the price catalog: what the customer's page shows and checkout charges
        "price_monthly_cents": _int("price_monthly_cents"),
        "base_vehicles": _int("base_vehicles"),
        "extra_vehicle_cents": _int("extra_vehicle_cents"),
        "stripe_price_id": str(r["stripe_price_id"] or "") if "stripe_price_id" in keys else "",
        "public": bool(_int("public")),
        "sort": _int("sort"),
        # the plan a self-serve signup's trial starts on (one row carries it)
        "trial_default": bool(_int("trial_default")),
        # the Stripe Product this plan's Prices hang off
        "stripe_product_id": str(r["stripe_product_id"] or "") if "stripe_product_id" in keys else "",
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

    async def count_accounts_by_tier(self) -> dict[str, int]:
        """Active accounts per ``tier`` — the blast radius of a plan edit,
        and the tiers that have accounts but no plan row."""
        cur = await self._db.execute(
            "SELECT tier, COUNT(*) AS n FROM accounts WHERE is_active = 1 GROUP BY tier")
        return {(r["tier"] or "free"): int(r["n"]) for r in await cur.fetchall()}

    async def upsert_plan(
        self, tier: str, *, label: str, included: list[str],
        quotas: Optional[dict] = None, updated_by: str = "",
        price_monthly_cents: Optional[int] = None, base_vehicles: Optional[int] = None,
        extra_vehicle_cents: Optional[int] = None, stripe_price_id: Optional[str] = None,
        public: Optional[bool] = None, sort: Optional[int] = None,
        trial_default: Optional[bool] = None, stripe_product_id: Optional[str] = None,
    ) -> dict:
        """Create or replace a plan.  Label, included and quotas are always
        written; a catalog field left ``None`` keeps the row's value (or the
        column default for a new row) — the operator edits prices from one
        panel and the seed never has to know them."""
        now = datetime.now(timezone.utc).isoformat()
        cur = await self.get_plan(tier) or {}
        pick = lambda v, k, d: (cur.get(k, d) if v is None else v)  # noqa: E731
        await self._db.execute(
            "INSERT INTO plans (tier, label, included, quotas, price_monthly_cents, base_vehicles, "
            "extra_vehicle_cents, stripe_price_id, public, sort, trial_default, stripe_product_id, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tier) DO UPDATE SET label = excluded.label, included = excluded.included, "
            "quotas = excluded.quotas, price_monthly_cents = excluded.price_monthly_cents, "
            "base_vehicles = excluded.base_vehicles, extra_vehicle_cents = excluded.extra_vehicle_cents, "
            "stripe_price_id = excluded.stripe_price_id, public = excluded.public, sort = excluded.sort, "
            "trial_default = excluded.trial_default, stripe_product_id = excluded.stripe_product_id, "
            "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (tier, label, json.dumps(list(included)), json.dumps(quotas or {}),
             int(pick(price_monthly_cents, "price_monthly_cents", 0)),
             int(pick(base_vehicles, "base_vehicles", 0)),
             int(pick(extra_vehicle_cents, "extra_vehicle_cents", 0)),
             str(pick(stripe_price_id, "stripe_price_id", "") or ""),
             1 if pick(public, "public", False) else 0,
             int(pick(sort, "sort", 0)),
             1 if pick(trial_default, "trial_default", False) else 0,
             str(pick(stripe_product_id, "stripe_product_id", "") or ""),
             now, updated_by),
        )
        # one plan carries the trial: flagging this one un-flags the rest
        if trial_default:
            await self._db.execute("UPDATE plans SET trial_default = 0 WHERE tier != ?", (tier,))
        await self._db.commit()
        return (await self.get_plan(tier)) or {}

    async def plan_by_stripe_price(self, price_id: str) -> Optional[str]:
        """The tier whose row carries this Stripe price id — how a webhook
        re-derives what plan a subscription is on from the Price it sees."""
        if not price_id:
            return None
        cur = await self._db.execute("SELECT tier FROM plans WHERE stripe_price_id = ? LIMIT 1", (price_id,))
        r = await cur.fetchone()
        return r["tier"] if r else None

    async def trial_plan(self) -> Optional[str]:
        """The plan a self-serve signup's trial starts on — the one row
        flagged ``trial_default`` — or ``None`` when the operator has
        flagged none (then no trial starts: an account signs up on Free)."""
        cur = await self._db.execute(
            "SELECT tier FROM plans WHERE trial_default = 1 ORDER BY sort, tier LIMIT 1")
        r = await cur.fetchone()
        return r["tier"] if r else None

    async def pricing_for(self, tier: str) -> dict:
        """What a checkout charges and a subscription records for *tier*:
        the plan row's numbers when the operator has set any, else the
        code table (``BillingMixin.tier_pricing``) that seeded them."""
        row = await self.get_plan(tier)
        if row and (row["price_monthly_cents"] or row["base_vehicles"] or row["extra_vehicle_cents"]):
            return {
                "tier": tier,
                "base_vehicles": row["base_vehicles"],
                "monthly_base_cents": row["price_monthly_cents"],
                "extra_vehicle_cents": row["extra_vehicle_cents"],
            }
        from .billing import BillingMixin
        return BillingMixin.tier_pricing(tier)
