"""Stub billing provider — no external calls, safe for dev/testing.

Returns realistic-looking data derived from the DB subscription row.
All checkout/portal URLs point back to the dashboard settings page.
"""

from __future__ import annotations

import logging

from adapters.storage.billing import BillingMixin

logger = logging.getLogger(__name__)


class StubBillingProvider:
    """No-op billing provider.  No Stripe account or key required."""

    async def get_summary(self, account_id: int, db) -> dict:
        return await db.get_billing_summary(account_id, provider="stub")

    async def get_usage_history(self, account_id: int, db, limit: int = 12) -> list[dict]:
        return await db.get_usage_snapshots(account_id, limit=limit)

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
        sub = await db.get_or_create_subscription(account_id)
        return await db.record_usage_snapshot(
            account_id=account_id,
            period_start=period_start,
            period_end=period_end,
            vehicle_count=vehicle_count,
            user_count=user_count,
            ai_queries=ai_queries,
            base_vehicles=sub["base_vehicles"],
            monthly_base_cents=sub["monthly_base_usd"],
            extra_vehicle_cents=sub["extra_vehicle_cents"],
        )

    async def create_checkout_session(
        self,
        account_id: int,
        db,
        tier: str,
        success_url: str,
        cancel_url: str,
    ) -> dict:
        logger.info(
            "Stub checkout: account=%s tier=%s (no payment processed)",
            account_id, tier,
        )
        # Stub: immediately upgrade the tier in DB so the UI reflects the change
        pricing = BillingMixin.tier_pricing(tier)
        await db.update_subscription(
            account_id,
            tier=tier,
            status="active",
            base_vehicles=pricing["base_vehicles"],
            monthly_base_usd=pricing["monthly_base_cents"],
            extra_vehicle_cents=pricing["extra_vehicle_cents"],
        )
        # Also bump the account tier
        await db.update_account_tier(account_id, tier)
        return {"url": success_url, "session_id": "stub_session"}

    async def create_portal_session(
        self,
        account_id: int,
        db,
        return_url: str,
    ) -> dict:
        return {"url": return_url}

    async def handle_webhook(self, payload: bytes, sig_header: str, db) -> dict:
        return {"handled": False, "event_type": "stub.noop"}

    async def sync_billing_quantity(self, account_id: int, db) -> dict:
        """No-op for the stub provider — there's no Stripe to PATCH."""
        return {"skipped": "stub_provider", "account_id": account_id}

    async def update_billing_email(self, account_id: int, db, email: str) -> dict:
        """Persist the email locally only; no external system to sync."""
        await db.get_or_create_subscription(account_id)
        await db.update_subscription(account_id, billing_email=email)
        return {"account_id": account_id, "email": email, "synced_to_provider": False}
