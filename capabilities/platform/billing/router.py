"""Billing API routes — the customer-facing surface, labelled "Subscription".

NAMING: billing = the platform charging family (us -> customer); not
carrier invoicing (future features/invoicing), not driver pay (Driver Pay).

Endpoints:
  GET  /billing/summary      — current subscription + vehicle usage
  GET  /billing/usage        — monthly usage history (up to 12 months)
  POST /billing/checkout     — create checkout session (upgrade tier)
  POST /billing/portal       — open Stripe customer portal (manage subscription)
  POST /billing/webhook      — Stripe webhook receiver (no auth — validated by signature)
  POST /billing/update-vehicles — admin: manually sync vehicle count to subscription

All endpoints except /webhook require a valid JWT with role admin or owner.
"""
# router.py is interface-layer code co-located with its hub/domain
# (docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may.


from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel, Field

from interfaces.api.deps import get_platform_db, require_permission

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
    """Refresh ``subscriptions.vehicle_count`` from the warehouse.

    Total known fleet = active + inactive over ``vehicle_state`` — the same
    table billing's pricing math reads, so the summary can never disagree
    with what we charge.  (Deliberately NOT features.vehicles: platform
    domains don't import the customer product — tests/test_layer_boundaries.py.)
    Returns the count, or None if the warehouse read fails.
    """
    try:
        # Registry first (the vehicles-table SSOT — includes manual trucks +
        # trailers, matching the pre-refactor fleet-overview semantics); fall
        # back to the Samsara warehouse for accounts with no registry rows.
        count = await platform_db.count_vehicles(account_id)
        if not count:
            count = (
                await platform_db.count_active_vehicles(account_id)
                + await platform_db.count_inactive_vehicles(account_id)
            )
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
    from capabilities.platform.billing import get_provider
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
    from capabilities.platform.billing import get_provider
    provider = get_provider()
    history = await provider.get_usage_history(user["account_id"], platform_db, limit=limit)
    return {"items": history, "count": len(history)}


# ── Invoices ──────────────────────────────────────────────────────

@router.get("/invoices")
async def billing_invoices(
    limit: int = 24,
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """List recorded invoices for the account (newest first).

    The webhook handler mirrors Stripe invoices into ``billing_invoices``
    as they happen; this endpoint surfaces them to the dashboard so
    operators can download receipts without leaving the app.
    """
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be 1–100")
    items = await platform_db.get_invoices(user["account_id"], limit=limit)
    return {"items": items, "count": len(items)}


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
    from capabilities.platform.billing import get_provider
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
    from capabilities.platform.billing import get_provider
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
    from capabilities.platform.billing import get_provider
    provider = get_provider()
    try:
        result = await provider.handle_webhook(payload, stripe_signature, platform_db)
    except ValueError as e:
        # Signature mismatch — return 400 so Stripe retries
        raise HTTPException(status_code=400, detail=str(e))
    return result


# ── Billing email ─────────────────────────────────────────────────

class BillingEmailRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320,
                       description="Inbox that receives Stripe receipts + dunning.")


@router.patch("/email")
async def update_billing_email(
    body: BillingEmailRequest,
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """Update the billing inbox for the current account.

    Writes the local subscription row immediately so the dashboard's
    "Receipts go to …" field reflects the change without a refresh
    lag, and (on Stripe) PATCHes the Customer object so Stripe routes
    receipts and dunning notices to the new address.  Accounts without
    a Stripe customer yet get the local write only; their first
    checkout will pick up the saved email.
    """
    email = body.email.strip()
    if "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    from capabilities.platform.billing import get_provider
    provider = get_provider()
    result = await provider.update_billing_email(
        user["account_id"], platform_db, email,
    )
    return result


# ── Comp accounts (system-owner only) ────────────────────────────

def _require_system_owner(user: dict = Depends(_billing_admin)) -> dict:
    """Gate comp endpoints to platform operators only.

    A regular account owner can manage billing for their OWN account
    (that's what ``_billing_admin`` already enforces) but they must NOT
    be able to comp themselves into free service.  ``is_system_owner``
    matches the user's Telegram id against the ``SYSTEM_OWNER_IDS`` env
    var — only the 4truck operators on that allowlist may grant comp.
    """
    from capabilities.permissions.roles import is_system_owner
    try:
        tg_id = int(user["sub"])
    except (KeyError, TypeError, ValueError):
        tg_id = 0
    if not is_system_owner(tg_id):
        raise HTTPException(
            status_code=403,
            detail="Comp account management is restricted to platform operators.",
        )
    return user


class CompGrantRequest(BaseModel):
    account_id: int = Field(..., ge=1)
    expires_at: str = Field(..., min_length=10, description="ISO-8601 UTC (e.g. 2026-12-31T23:59:59+00:00)")
    reason: str = Field(default="", max_length=500)


class CompRenewRequest(BaseModel):
    account_id: int = Field(..., ge=1)
    new_expires_at: str = Field(..., min_length=10)
    reason: str = Field(default="", max_length=500)


class CompRevokeRequest(BaseModel):
    account_id: int = Field(..., ge=1)
    reason: str = Field(default="", max_length=500)


@router.post("/comp/grant")
async def comp_grant(
    body: CompGrantRequest,
    user: dict = Depends(_require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Mark an account as comped through ``expires_at``.

    Comp accounts get full functionality at $0/mo for a bounded window.
    ``expires_at`` is required — comp without an expiry is forbidden so
    a free ride doesn't become permanent by accident.
    """
    try:
        actor_id = int(user["sub"])
    except (KeyError, TypeError, ValueError):
        actor_id = None
    try:
        await platform_db.grant_comp(
            account_id=body.account_id,
            expires_at=body.expires_at,
            reason=body.reason,
            actor_user_id=actor_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Tell the recipient.  Best-effort — the comp itself is already
    # persisted, so a notification failure doesn't roll back the grant.
    try:
        from capabilities.platform.billing.notifications import notify_comp_granted
        await notify_comp_granted(body.account_id, body.expires_at, body.reason)
    except Exception:
        logger.exception("notify_comp_granted failed acct=%s", body.account_id)
    return {"ok": True, "account_id": body.account_id, "expires_at": body.expires_at}


@router.post("/comp/renew")
async def comp_renew(
    body: CompRenewRequest,
    user: dict = Depends(_require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Push out the expiration of an existing comp."""
    try:
        actor_id = int(user["sub"])
    except (KeyError, TypeError, ValueError):
        actor_id = None
    try:
        await platform_db.renew_comp(
            account_id=body.account_id,
            new_expires_at=body.new_expires_at,
            reason=body.reason,
            actor_user_id=actor_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Treat a renewal like a fresh grant for the recipient — they care
    # about the new end date, not whether this was technically the
    # first or fifth grant.
    try:
        from capabilities.platform.billing.notifications import notify_comp_granted
        await notify_comp_granted(body.account_id, body.new_expires_at, body.reason)
    except Exception:
        logger.exception("notify_comp_granted (renew) failed acct=%s", body.account_id)
    return {"ok": True, "account_id": body.account_id, "expires_at": body.new_expires_at}


@router.post("/comp/revoke")
async def comp_revoke(
    body: CompRevokeRequest,
    user: dict = Depends(_require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Clear comp flags — the account is billed normally going forward.

    The historical comp record stays in ``comp_account_history`` for
    audit; tier/status are not changed (downgrading is a separate call).
    """
    try:
        actor_id = int(user["sub"])
    except (KeyError, TypeError, ValueError):
        actor_id = None
    await platform_db.revoke_comp(
        account_id=body.account_id,
        reason=body.reason,
        actor_user_id=actor_id,
    )
    return {"ok": True, "account_id": body.account_id}


@router.get("/comp/history")
async def comp_history(
    account_id: int,
    limit: int = 50,
    user: dict = Depends(_require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Return the comp audit trail for an account (system-owner only)."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1–200")
    items = await platform_db.get_comp_history(account_id, limit=limit)
    return {"items": items, "count": len(items)}


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
