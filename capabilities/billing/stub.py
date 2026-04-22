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
        sub = await db.get_or_create_subscription(account_id)
        extra, amount = BillingMixin.compute_amount_due(
            vehicle_count=sub["vehicle_count"],
            base_vehicles=sub["base_vehicles"],
            monthly_base_cents=sub["monthly_base_usd"],
            extra_vehicle_cents=sub["extra_vehicle_cents"],
        )
        return {
            "tier":                 sub["tier"],
            "status":               sub["status"],
            "vehicle_count":        sub["vehicle_count"],
            "base_vehicles":        sub["base_vehicles"],
            "monthly_base_cents":   sub["monthly_base_usd"],
            "extra_vehicle_cents":  sub["extra_vehicle_cents"],
            "extra_vehicles":       extra,
            "amount_due_cents":     amount,
            "billing_email":        sub["billing_email"],
            "provider":             "stub",
            "current_period_start": sub.get("current_period_start"),
            "current_period_end":   sub.get("current_period_end"),
            "trial_ends_at":        sub.get("trial_ends_at"),
        }

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
