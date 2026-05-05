"""Billing API routes.

Endpoints:
  GET  /billing/summary      — current subscription + vehicle usage
  GET  /billing/usage        — monthly usage history (up to 12 months)
  POST /billing/checkout     — create checkout session (upgrade tier)
  POST /billing/portal       — open Stripe customer portal (manage subscription)
  POST /billing/webhook      — Stripe webhook receiver (no auth — validated by signature)
  POST /billing/update-vehicles — admin: manually sync vehicle count to subscription

All endpoints except /webhook require a valid JWT with role admin or owner.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel, Field

from interfaces.api.deps import get_current_user, get_platform_db, require_permission
from adapters.storage.models import Role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

_BASE_URL = os.getenv("APP_BASE_URL", "https://4truck.us")

_billing_admin = require_permission("can_manage_billing")


async def _count_users(account_id: int, platform_db) -> int:
    try:
        return await platform_db.count_account_users(account_id)
    except Exception:
        return 0


# ── Summary ───────────────────────────────────────────────────────

async def _sync_vehicle_count(account_id: int, platform_db) -> int | None:
    """Query live vehicle count from Samsara and sync it to the subscription row.

    Returns the live count, or None if Samsara is unavailable.
    """
    try:
        from capabilities.vehicles.service import get_fleet_overview
        vehicles = await get_fleet_overview(account_id)
        count = len(vehicles)
        await platform_db.update_subscription(account_id, vehicle_count=count)
        return count
    except Exception as exc:
        logger.warning("Could not sync vehicle count for account %s: %s", account_id, exc)
        return None


@router.get("/summary")
async def billing_summary(
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """Return the current billing tier, vehicle count, and next invoice estimate."""
    from capabilities.billing import get_provider
    provider = get_provider()
    account_id = user["account_id"]

    # Sync live vehicle count from Samsara before computing the summary
    await _sync_vehicle_count(account_id, platform_db)

    summary = await provider.get_summary(account_id, platform_db)
    # Enrich with AI usage stats (last 30 days) and account info
    ai_stats = await platform_db.get_ai_usage_stats(account_id, days=30)
    account = await platform_db.get_account(account_id)
    summary["ai_usage"] = ai_stats
    summary["account_name"] = account.name if account else ""
    summary["user_count"] = await _count_users(account_id, platform_db)
    return summary


# ── Usage history ─────────────────────────────────────────────────

@router.get("/usage")
async def billing_usage(
    limit: int = 12,
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """Return monthly usage snapshots (newest first)."""
    if limit < 1 or limit > 36:
        raise HTTPException(status_code=400, detail="limit must be 1–36")
    from capabilities.billing import get_provider
    provider = get_provider()
    history = await provider.get_usage_history(user["account_id"], platform_db, limit=limit)
    return {"items": history, "count": len(history)}


# ── Checkout (upgrade) ───────────────────────────────────────────

class CheckoutRequest(BaseModel):
    tier: str = Field(..., pattern="^(starter|pro|enterprise)$")


@router.post("/checkout")
async def billing_checkout(
    body: CheckoutRequest,
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """Create a checkout/upgrade session.  Returns a redirect URL."""
    from capabilities.billing import get_provider
    provider = get_provider()
    account_id = user["account_id"]
    success_url = f"{_BASE_URL}/dashboard/billing?success=1"
    cancel_url  = f"{_BASE_URL}/dashboard/billing?canceled=1"
    try:
        result = await provider.create_checkout_session(
            account_id=account_id,
            db=platform_db,
            tier=body.tier,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


# ── Billing portal (manage / cancel) ─────────────────────────────

@router.post("/portal")
async def billing_portal(
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """Create a Stripe billing portal session.  Returns a redirect URL."""
    from capabilities.billing import get_provider
    provider = get_provider()
    return_url = f"{_BASE_URL}/dashboard/billing"
    try:
        result = await provider.create_portal_session(
            account_id=user["account_id"],
            db=platform_db,
            return_url=return_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


# ── Webhook (no JWT auth — signature-verified by provider) ───────

@router.post("/webhook")
async def billing_webhook(
    request: Request,
    stripe_signature: str = Header(default=""),
    platform_db=Depends(get_platform_db),
):
    """Receive and process provider webhooks (e.g. Stripe).

    The payload is raw bytes so the Stripe SDK can verify the signature.
    """
    payload = await request.body()
    from capabilities.billing import get_provider
    provider = get_provider()
    try:
        result = await provider.handle_webhook(payload, stripe_signature, platform_db)
    except ValueError as e:
        # Signature mismatch — return 400 so Stripe retries
        raise HTTPException(status_code=400, detail=str(e))
    return result


# ── Manual vehicle count sync ────────────────────────────────────

class VehicleCountRequest(BaseModel):
    vehicle_count: int = Field(..., ge=0, le=100_000)


@router.post("/update-vehicles")
async def update_vehicle_count(
    body: VehicleCountRequest,
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """Manually update the vehicle count used for billing.

    In production this is called automatically by the scheduler after
    syncing the Samsara vehicle list.  Use this endpoint to correct the
    count without waiting for the next sync.
    """
    await platform_db.update_subscription(
        user["account_id"],
        vehicle_count=body.vehicle_count,
    )
    return {"ok": True, "vehicle_count": body.vehicle_count}
