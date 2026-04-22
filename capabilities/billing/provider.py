"""Abstract BillingProvider protocol.

Concrete implementations:
  - StubBillingProvider   (capabilities/billing/stub.py) — dev/no-Stripe
  - StripeBillingProvider (capabilities/billing/stripe_client.py) — production

All methods accept the platform DB instance so providers can read/write
subscription and snapshot rows without needing their own DB connection.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


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
