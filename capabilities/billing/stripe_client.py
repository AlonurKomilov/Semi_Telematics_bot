"""Stripe billing provider.

Requires:
  BILLING_PROVIDER=stripe
  STRIPE_SECRET_KEY=sk_live_...
  STRIPE_WEBHOOK_SECRET=whsec_...

Set BILLING_PROVIDER=stub (the default) to disable Stripe entirely.
"""

from __future__ import annotations

import json
import logging
import os

from adapters.storage.billing import BillingMixin

logger = logging.getLogger(__name__)

_STRIPE_PRICE_IDS: dict[str, str] = {
    # Set these in your Stripe dashboard, then export as env vars
    "starter": os.getenv("STRIPE_PRICE_STARTER", ""),
    "pro":     os.getenv("STRIPE_PRICE_PRO",     ""),
}


def _stripe():
    """Import stripe lazily so the module loads without the package installed."""
    try:
        import stripe as _s
        _s.api_key = os.environ["STRIPE_SECRET_KEY"]
        return _s
    except ImportError:
        raise RuntimeError(
            "stripe package not installed. Run: pip install stripe>=8.0"
        )
    except KeyError:
        raise RuntimeError(
            "STRIPE_SECRET_KEY env var not set. "
            "Set BILLING_PROVIDER=stub to use the stub provider."
        )


class StripeBillingProvider:
    """Stripe-backed billing provider."""

    # ── Summary / info ───────────────────────────────────────────

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
            "provider":             "stripe",
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

    # ── Checkout / portal ────────────────────────────────────────

    async def create_checkout_session(
        self,
        account_id: int,
        db,
        tier: str,
        success_url: str,
        cancel_url: str,
    ) -> dict:
        stripe = _stripe()
        sub = await db.get_or_create_subscription(account_id)

        price_id = _STRIPE_PRICE_IDS.get(tier, "")
        if not price_id:
            raise ValueError(
                f"No Stripe price ID configured for tier '{tier}'. "
                f"Set STRIPE_PRICE_{tier.upper()} env var."
            )

        # Get or create Stripe customer
        customer_id = sub.get("provider_customer_id", "")
        if not customer_id:
            customer = stripe.Customer.create(
                email=sub.get("billing_email") or None,
                metadata={"account_id": str(account_id)},
            )
            customer_id = customer["id"]
            await db.update_subscription(
                account_id,
                provider="stripe",
                provider_customer_id=customer_id,
            )

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"account_id": str(account_id), "tier": tier},
        )
        return {"url": session["url"], "session_id": session["id"]}

    async def create_portal_session(
        self,
        account_id: int,
        db,
        return_url: str,
    ) -> dict:
        stripe = _stripe()
        sub = await db.get_or_create_subscription(account_id)
        customer_id = sub.get("provider_customer_id", "")
        if not customer_id:
            raise ValueError(
                "No Stripe customer ID for account — "
                "customer must complete checkout before accessing the portal."
            )
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return {"url": session["url"]}

    # ── Webhook ──────────────────────────────────────────────────

    async def handle_webhook(self, payload: bytes, sig_header: str, db) -> dict:
        stripe = _stripe()
        webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
        if not webhook_secret:
            logger.warning("STRIPE_WEBHOOK_SECRET not set — skipping signature verification")
            event = json.loads(payload)
        else:
            try:
                event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
            except stripe.error.SignatureVerificationError as e:
                logger.warning("Stripe webhook signature verification failed: %s", e)
                raise ValueError("Invalid webhook signature") from e

        event_type = event.get("type", "")
        data = event.get("data", {}).get("object", {})
        account_id_str = (data.get("metadata") or {}).get("account_id", "")

        if not account_id_str:
            return {"handled": False, "event_type": event_type}

        account_id = int(account_id_str)

        if event_type == "checkout.session.completed":
            tier = (data.get("metadata") or {}).get("tier", "starter")
            sub_id = data.get("subscription", "")
            pricing = BillingMixin.tier_pricing(tier)
            await db.update_subscription(
                account_id,
                tier=tier,
                status="active",
                provider_subscription_id=sub_id,
                base_vehicles=pricing["base_vehicles"],
                monthly_base_usd=pricing["monthly_base_cents"],
                extra_vehicle_cents=pricing["extra_vehicle_cents"],
            )
            await db.update_account_tier(account_id, tier)
            logger.info("Checkout complete: account=%s tier=%s", account_id, tier)

        elif event_type == "customer.subscription.updated":
            status = data.get("status", "active")
            period_start = data.get("current_period_start")
            period_end   = data.get("current_period_end")
            await db.update_subscription(
                account_id,
                status=status,
                current_period_start=str(period_start) if period_start else None,
                current_period_end=str(period_end)   if period_end   else None,
            )

        elif event_type == "customer.subscription.deleted":
            await db.update_subscription(account_id, status="canceled")
            await db.update_account_tier(account_id, "free")
            logger.info("Subscription canceled: account=%s", account_id)

        elif event_type == "invoice.payment_failed":
            await db.update_subscription(account_id, status="past_due")

        return {"handled": True, "event_type": event_type}
