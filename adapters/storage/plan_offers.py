"""A hidden plan opened to one account.

After a Contact-Sales conversation the operator and a customer agree on
terms nobody else gets — a flat monthly figure, their own truck
allowance.  That plan is an ordinary row in ``plans``, made in the same
editor as every other, kept hidden from the customer page.  What makes
it THEIRS is a row here: this tier is offered to this account.

The rules that live in this module rather than in a route:

- **An offer opens a door and nothing else.**  It puts the plan on that
  account's Billing page and lets that account check out.  It never
  changes a tier or a subscription — once the customer has paid, the
  subscription row is the truth, and revoking the offer afterwards
  changes nothing about what Stripe bills.
- **One row per (tier, account).**  Offering twice is a no-op, not a
  second row and not an error.
- **Nothing here is readable by a customer.**  The customer list asks
  "which tiers are offered to ME"; who else a plan was offered to is
  operator knowledge, kept out of the customer wire shape by never
  being put on the plan row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


class PlanOffersMixin:
    """Mixed into the platform Database beside the other billing stores."""

    async def offer_plan(
        self, tier: str, account_id: int, *,
        request_id: Optional[int] = None,
        created_by: str = "",
    ) -> dict:
        """Open *tier* to *account_id*; hand back the row either way.

        ``created`` says whether this call made it — an operator pressing
        twice should be told the offer already stood, not shown a fresh
        confirmation for something that did not happen.
        """
        now = datetime.now(timezone.utc).isoformat()
        cur = await self._db.execute(
            "INSERT INTO plan_offers (tier, account_id, request_id, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(tier, account_id) DO NOTHING",
            (tier, int(account_id), request_id, created_by, now),
        )
        # the insert itself says whether it happened — two operators
        # pressing at once both get the truth, not both "created"
        created = bool(getattr(cur, "rowcount", 0))
        row = await self.get_plan_offer(tier, account_id)
        return {**(row or {}), "created": created}

    async def revoke_plan_offer(self, tier: str, account_id: int) -> bool:
        """Close the door.  True when a row was there to remove."""
        cur = await self._db.execute(
            "DELETE FROM plan_offers WHERE tier = ? AND account_id = ?",
            (tier, int(account_id)),
        )
        return bool(getattr(cur, "rowcount", 0))

    async def get_plan_offer(self, tier: str, account_id: int) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM plan_offers WHERE tier = ? AND account_id = ?",
            (tier, int(account_id)),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def plan_offered_to(self, tier: str, account_id: int) -> bool:
        """The question checkout asks."""
        return (await self.get_plan_offer(tier, account_id)) is not None

    async def offered_tiers_for_account(self, account_id: int) -> list[str]:
        """The question the customer's plan list asks — tiers only, never
        the other accounts a tier was offered to."""
        cur = await self._db.execute(
            "SELECT tier FROM plan_offers WHERE account_id = ? ORDER BY tier",
            (int(account_id),),
        )
        return [str(r["tier"]) for r in await cur.fetchall()]

    async def list_plan_offers(self) -> list[dict]:
        """Every offer with the account's name — the operator's Plans
        page, one query for all columns."""
        cur = await self._db.execute(
            "SELECT o.tier, o.account_id, o.request_id, o.created_by, o.created_at, "
            "a.name AS account_name "
            "FROM plan_offers o LEFT JOIN accounts a ON a.id = o.account_id "
            "ORDER BY o.tier, o.created_at",
        )
        return [dict(r) for r in await cur.fetchall()]

    _db: Any
