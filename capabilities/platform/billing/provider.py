"""Abstract BillingProvider protocol.

Concrete implementations:
  - StubBillingProvider   (capabilities/platform/billing/stub.py) — dev/no-Stripe
  - StripeBillingProvider (capabilities/platform/billing/stripe_client.py) — production

All methods accept the platform DB instance so providers can read/write
subscription and snapshot rows without needing their own DB connection.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class ProviderError(RuntimeError):
    """The payment provider refused or failed a call the customer
    started (a checkout, a switch, the portal): shown as a 502 with its
    text, never a bare 500."""


@runtime_checkable
class BillingProvider(Protocol):
    """Interface every billing backend must implement."""

    async def get_summary(self, account_id: int, db) -> dict:
        """Return billing summary for the dashboard.

        Returns:
            {
              "tier": str,
              "status": str,             # active | trialing | past_due | canceled
              "vehicle_count": int,
              "base_vehicles": int,
              "monthly_base_cents": int,
              "extra_vehicle_cents": int,
              "extra_vehicles": int,
              "amount_due_cents": int,
              "billing_email": str,
              "provider": str,
              "current_period_start": str | None,
              "current_period_end":   str | None,
              "trial_ends_at":        str | None,
            }
        """
        ...

    async def get_usage_history(self, account_id: int, db, limit: int = 12) -> list[dict]:
        """Return up to *limit* monthly usage snapshots (newest first)."""
        ...

    async def record_monthly_snapshot(
        self,
        account_id: int,
        db,
        period_start: str,
        period_end: str,
        vehicle_count: int,
        user_count: int,
        ai_queries: int,
    ) -> int:
        """Write a usage snapshot row and return its id."""
        ...

    async def create_checkout_session(
        self,
        account_id: int,
        db,
        tier: str,
        success_url: str,
        cancel_url: str,
    ) -> dict:
        """Create a payment/upgrade session.

        Returns:
            {"url": str, "session_id": str}   (Stripe checkout URL or stub)
        """
        ...

    async def create_portal_session(
        self,
        account_id: int,
        db,
        return_url: str,
    ) -> dict:
        """Open the billing management portal for an existing customer.

        Returns:
            {"url": str}
        """
        ...

    async def handle_webhook(self, payload: bytes, sig_header: str, db) -> dict:
        """Process a provider webhook event (e.g. Stripe signature-verified).

        Returns:
            {"handled": bool, "event_type": str}
        """
        ...

    async def sync_billing_quantity(
        self, account_id: int, db, *, force: bool = False,
    ) -> dict:
        """Push the account's billable vehicle count to the provider.

        The count is the vehicle registry's (``count_billable_vehicles``),
        so three callers keep it current: the Samsara ingest after every
        cycle, the daily ``billing_quantity_sync`` job (the one that
        reaches an account whose integration is paused or gone), and the
        operator's console button.  Implementations should be cheap when
        nothing has changed; the stub provider no-ops.

        ``force`` lifts the upward-jump guard (a sudden large increase is
        held for a human) — the console button passes it.

        Returns a status dict for metrics (``skipped`` token, ``before``
        / ``after`` quantities, ``account_id``).
        """
        ...

    async def create_plan_price(self, *, tier: str, label: str, cents: int, before: dict) -> dict:
        """Make the provider hold a price for a plan (the console just
        saved a new monthly amount).  Stripe: a Price on the plan's
        Product; returns ``{"stripe_price_id", "stripe_product_id",
        "archived"}``.  Stub: ``{"skipped": "stub"}`` and nothing touched."""
        ...

    async def apply_discount(self, account_id: int, db, discount: dict) -> dict:
        """Put a granted price break onto the account at the provider.
        Stripe: a Coupon on the subscription, or held for the next
        checkout when there is none.  Returns the fields to write back
        (``status``, the Stripe ids, and Stripe's own start/end)."""
        ...

    async def remove_discount(self, account_id: int, db, discount: dict) -> bool:
        """Take it off.  True when there is nothing left applying."""
        ...

    async def discount_state(self, account_id: int, db, discount: dict) -> dict | None:
        """What the provider holds for this grant now, or None when it
        is gone — the daily sweep's question."""
        ...

    async def preview_discounted_invoice(self, account_id: int, db) -> dict | None:
        """Subtotal, discount and total of the next bill, from the
        provider.  Never on a customer request path."""
        ...

    async def create_extra_price(self, *, tier: str, label: str, cents: int, before: dict) -> dict:
        """The per-extra-truck twin of ``create_plan_price``: a Price on
        the plan's Product for *cents* per truck above the included
        count.  Returns ``{"stripe_extra_price_id", "stripe_product_id",
        "archived"}``; stub: ``{"skipped": "stub"}``."""
        ...

    async def rename_plan_products(self, *, label: str, base_product_id: str, extra_product_id: str) -> list[str]:
        """Carry a plan's new label onto the Stripe Products a customer
        reads on a checkout page and an invoice.  Stub: ``[]``."""
        ...

    async def rollout_preview(self, db, tier: str) -> dict:
        """What a price rollout would do, from our tables alone."""
        ...

    async def rollout_execute(self, db, tier: str, *, actor: str) -> dict:
        """Move up to one batch of live subscriptions on *tier* to the
        plan's price; returns counts and ``remaining``."""
        ...

    async def update_billing_email(self, account_id: int, db, email: str) -> dict:
        """Persist a new billing email and push it to the payment provider.

        Stripe needs the email on the Customer object so receipts /
        recovery emails route correctly; we mirror it locally on the
        subscription row so the dashboard's "send invoices to" field
        can render without hitting Stripe.  The stub provider only
        writes the local row.
        """
        ...
