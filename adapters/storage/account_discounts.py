"""A price break for one account, for a bounded time.

The money itself is Stripe's to compute — each grant becomes a Coupon
on the account's subscription, so the discount reaches the invoice, the
PDF and the receipt without anyone here doing arithmetic on a bill.
These rows are the operator's record of that: who was given what, why,
by whom, and what Stripe answered.

Two rules live here rather than in a route:

- **One live grant per account.**  A partial unique index enforces it,
  and the insert catches the violation rather than racing.  Two coupons
  stacked on one subscription is a bill neither operator can predict.
- **Stripe's dates, not ours.**  ``ends_at`` is whatever Stripe's
  ``discount.end`` says once the coupon is applied; ``duration_in_months``
  runs on the calendar from that moment, so a grant made mid-cycle
  covers two renewals or four depending on the anchor.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

PENDING, ACTIVE, ENDED, REVOKED = "pending", "active", "ended", "revoked"
LIVE_STATUSES = (PENDING, ACTIVE)
#: ``absolute`` is the third and it is not a coupon: 100% off, and
#: Stripe is never asked.  The account's costs are still computed and
#: written down every month — the lines, the total, and the one line
#: that takes it to zero — and an invoice and a receipt go out as they
#: would for anyone.  What it gives up is Stripe: no subscription, no
#: card, and nothing about this customer in Stripe's own reporting.
KINDS = ("amount", "percent", "absolute")
ABSOLUTE = "absolute"


def effective_off(discount: dict | None, subtotal_cents: int) -> int:
    """What this grant takes off a bill of *subtotal_cents*.

    Clamped, because Stripe clamps: an amount larger than the invoice
    makes the invoice zero and the remainder is not carried forward.
    The customer's page shows the same number Stripe will charge by.
    """
    if not discount or discount.get("status") not in LIVE_STATUSES:
        return 0
    subtotal = max(0, int(subtotal_cents or 0))
    if discount.get("kind") == ABSOLUTE:
        return subtotal
    if discount.get("kind") == "percent":
        pct = max(0, min(100, int(discount.get("percent_off") or 0)))
        return subtotal * pct // 100
    return min(subtotal, max(0, int(discount.get("amount_off_cents") or 0)))


def describe(discount: dict | None) -> str:
    """The grant in the words a customer reads on their own bill."""
    if not discount:
        return ""
    if discount.get("kind") == ABSOLUTE:
        # "Absolute 0" is OUR word for the kind of grant, not a phrase a
        # customer's bookkeeper has ever met.  The operator console keeps
        # it (they chose it); the bill says what actually happened.
        return "Covered by 4truck — nothing to pay"
    if discount.get("kind") == "percent":
        return f"{int(discount.get('percent_off') or 0)}% off"
    return f"${int(discount.get('amount_off_cents') or 0) / 100:,.2f} off"


class AccountDiscountsMixin:
    """Mixed into the platform Database beside the other billing stores."""

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    async def create_account_discount(
        self, account_id: int, *, kind: str,
        amount_off_cents: int = 0, percent_off: int = 0, months: int = 0,
        reason: str = "", granted_by: str = "",
    ) -> dict:
        """Record a grant, before Stripe is asked for anything.

        Raises ``ValueError`` when one is already live for the account —
        the caller turns that into the operator's answer, because
        "revoke the one you have first" is a decision, not a retry.
        """
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if kind == "amount" and int(amount_off_cents) <= 0:
            raise ValueError("an amount discount needs an amount above zero")
        if kind == "percent" and not (1 <= int(percent_off) <= 100):
            raise ValueError("a percent discount is between 1 and 100")
        if await self.live_account_discount(account_id):
            raise ValueError("This account already has a discount — revoke it before granting another.")
        now = self._now()
        try:
            await self._db.execute(
                "INSERT INTO account_discounts (account_id, kind, amount_off_cents, percent_off, "
                "months, reason, granted_by, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(account_id), kind, int(amount_off_cents), int(percent_off),
                 int(months), reason.strip(), granted_by, PENDING, now, now),
            )
        except Exception:
            # the unique index refused it: two operators pressed at once
            existing = await self.live_account_discount(account_id)
            if existing:
                raise ValueError("This account already has a discount — revoke it before granting another.")
            raise
        await self._db.commit()
        return await self.live_account_discount(account_id) or {}

    async def live_account_discount(self, account_id: int) -> Optional[dict]:
        """The grant that is on, or waiting to go on — what the customer's
        page and every Stripe write ask for."""
        marks = ",".join("?" for _ in LIVE_STATUSES)
        cur = await self._db.execute(
            f"SELECT * FROM account_discounts WHERE account_id = ? AND status IN ({marks}) "
            "ORDER BY id DESC LIMIT 1",
            (int(account_id), *LIVE_STATUSES),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def account_discount_history(self, account_id: int, limit: int = 20) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM account_discounts WHERE account_id = ? ORDER BY id DESC LIMIT ?",
            (int(account_id), int(limit)),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def mark_account_discount(
        self, discount_id: int, *, status: Optional[str] = None,
        stripe_coupon_id: Optional[str] = None, stripe_discount_id: Optional[str] = None,
        starts_at: Optional[str] = None, ends_at: Optional[str] = None,
    ) -> bool:
        """Write back what Stripe answered.  Only the fields given move,
        so a later reconcile can set ``ends_at`` without disturbing the
        ids the apply recorded."""
        sets, params = ["updated_at = ?"], [self._now()]
        for column, value in (
            ("status", status), ("stripe_coupon_id", stripe_coupon_id),
            ("stripe_discount_id", stripe_discount_id),
            ("starts_at", starts_at), ("ends_at", ends_at),
        ):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(value)
        params.append(int(discount_id))
        cur = await self._db.execute(
            f"UPDATE account_discounts SET {', '.join(sets)} WHERE id = ?", tuple(params))
        await self._db.commit()
        return bool(getattr(cur, "rowcount", 0))

    async def discounts_to_reconcile(self) -> list[dict]:
        """Live grants, for the daily sweep: Stripe ends a coupon on its
        own clock, and a row that still says active after that is a row
        telling the operator something untrue."""
        marks = ",".join("?" for _ in LIVE_STATUSES)
        cur = await self._db.execute(
            f"SELECT * FROM account_discounts WHERE status IN ({marks}) ORDER BY account_id",
            LIVE_STATUSES,
        )
        return [dict(r) for r in await cur.fetchall()]

    _db: Any
