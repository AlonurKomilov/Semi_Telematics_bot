"""Billing API routes — the customer-facing surface, labelled "Subscription".

NAMING: billing = the platform charging family (us -> customer); not
carrier invoicing (future features/invoicing), not driver pay (Driver Pay).

Endpoints:
  GET  /billing/summary      — current subscription + vehicle usage
  GET  /billing/usage        — monthly usage history (up to 12 months)
  POST /billing/checkout     — create checkout session (upgrade tier)
  POST /billing/portal       — open Stripe customer portal (manage subscription)
  POST /billing/stripe/webhook — Stripe webhook receiver (no auth — validated by signature;
                               /billing/webhook is kept as an alias)
  POST /billing/update-vehicles — admin: manually sync vehicle count to subscription

All endpoints except /webhook require a valid JWT with role admin or owner.
"""
# router.py is interface-layer code co-located with its hub/domain
# (docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may.


from __future__ import annotations

import asyncio
import html
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field

from interfaces.api.deps import get_platform_db, require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

def _dashboard_url() -> str:
    """The origin Stripe returns the customer to — see billing/urls.py
    for which variable wins and why."""
    from capabilities.platform.billing.urls import return_origin
    return return_origin()


_billing_admin = require_permission("can_manage_billing")


async def _count_companies(account_id: int, platform_db) -> int:
    """How many companies the account holds — the plan cards compare it
    against each plan's quota so a customer meets a limit BEFORE paying,
    not after. A count we cannot read warns about nothing, which is the
    safe way to be wrong."""
    try:
        return await platform_db.count_account_companies(account_id)
    except Exception:
        logger.warning("billing summary: company count unavailable for %s", account_id)
        return 0


async def _count_users(account_id: int, platform_db) -> int:
    try:
        return await platform_db.count_account_users(account_id)
    except Exception:
        return 0


# ── Summary ───────────────────────────────────────────────────────

async def _sync_vehicle_count(account_id: int, platform_db) -> int | None:
    """Refresh ``subscriptions.vehicle_count`` from the vehicle registry.

    The same count billing's pricing math reads
    (``count_billable_vehicles``), so the summary can never disagree
    with what we charge.  (Deliberately NOT features.vehicles: platform
    domains don't import the customer product —
    tests/test_layer_boundaries.py.)  Returns the count, or None if the
    read fails.
    """
    try:
        count = await platform_db.count_billable_vehicles(account_id)
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

    # Refresh the fleet size from the registry before computing the summary
    await _sync_vehicle_count(account_id, platform_db)

    summary = await provider.get_summary(account_id, platform_db)
    # Enrich with AI usage stats (last 30 days) and account info
    ai_stats = await platform_db.get_ai_usage_stats(account_id, days=30)
    account = await platform_db.get_account(account_id)
    summary["ai_usage"] = ai_stats
    summary["account_name"] = account.name if account else ""
    summary["user_count"] = await _count_users(account_id, platform_db)
    summary["company_count"] = await _count_companies(account_id, platform_db)
    summary["can_edit_contact"] = owner_may_edit_contact(user)
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


@router.get("/invoices/{invoice_number}/pdf")
async def billing_invoice_pdf(
    invoice_number: str,
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """The PDF of an invoice 4truck wrote itself.

    A Stripe invoice is downloaded from Stripe, which hosts the file;
    an Absolute 0 invoice has no Stripe behind it, so the file is built
    here from the row — the lines it was written with, not today's
    prices, because a bill is a record of what was charged at the time.

    Scoped to the caller's own account: an invoice number names a
    customer's money, and the id is guessable by construction.
    """
    from capabilities.platform.billing import local_invoice as _inv
    rows = await platform_db.get_invoices(user["account_id"], limit=200)
    row = next((r for r in rows if str(r.get("provider_invoice_id")) == invoice_number), None)
    if row is None:
        raise HTTPException(status_code=404, detail="No such invoice on this account.")
    hosted = str(row.get("invoice_pdf_url") or "").strip()
    if hosted.startswith("https://"):
        # a Stripe invoice: its own PDF is the document
        return RedirectResponse(url=hosted, status_code=302)
    lines = row.get("lines_json") or ""
    if not lines:
        raise HTTPException(status_code=404, detail="This invoice has no PDF.")
    account = await platform_db.get_account(user["account_id"])
    invoice = {
        "account_id": user["account_id"],
        "account_name": getattr(account, "name", "") or "",
        "number": invoice_number,
        "period_start": str(row.get("period_start") or ""),
        "period_end": str(row.get("period_end") or ""),
        "lines": json.loads(lines),
        "subtotal_cents": int(row.get("subtotal_cents") or 0),
        "discount_cents": int(row.get("discount_cents") or 0),
        "total_cents": int(row.get("amount_due_cents") or 0),
        "currency": str(row.get("currency") or "usd"),
    }
    from capabilities.platform.billing.receipt_email import reply_to
    issuer = {"name": (os.getenv("SMTP_FROM_NAME") or "4truck"), "support": reply_to()}
    pdf = await asyncio.to_thread(lambda: _inv.render_pdf(invoice, issuer=issuer))
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="4truck-invoice-{invoice_number}.pdf"'},
    )


# ── Asking for a plan that is not sold self-serve ────────────────


class PlanRequestBody(BaseModel):
    tier: str = Field(..., min_length=2, max_length=32)
    note: str = Field("", max_length=2000)
    contact_email: str = Field("", max_length=320)


@router.post("/plan-request", status_code=201)
async def billing_plan_request(
    body: PlanRequestBody,
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """Leave a request for a plan whose button is not a checkout.

    A plan offered at no price is a "talk to us" plan — Enterprise is
    the case this exists for — and its button used to do nothing at all.
    Now it leaves a row with a case number the customer can quote, pings
    the operators on Telegram, and emails the sales inbox when one is
    configured.

    Pressing it twice joins the request already open rather than making
    a second one; the answer says so.
    """
    tier = body.tier.strip().lower()
    plan = await platform_db.get_plan(tier)
    if not plan or not plan["public"]:
        raise HTTPException(status_code=404, detail=f"Plan '{tier}' is not on offer.")
    if int(plan["price_monthly_cents"] or 0) > 0:
        # A priced plan has a checkout; sending it here instead would
        # hide a purchase behind a conversation.
        raise HTTPException(
            status_code=400,
            detail=f"'{plan['label']}' can be bought directly — use Upgrade.")

    account_id = user["account_id"]
    try:
        req = await platform_db.create_plan_request(
            account_id, tier,
            requested_by=user.get("user_id"),
            contact_email=body.contact_email.strip(),
            note=body.note.strip(),
        )
    except Exception:
        logger.exception("plan request could not be recorded for account=%s tier=%s",
                         account_id, tier)
        raise HTTPException(
            status_code=503,
            detail="We could not record that just now — please email us instead.")

    account = await platform_db.get_account(account_id)
    account_name = (account.name if account else "") or f"account {account_id}"
    from capabilities.platform.billing import plan_requests as _notify
    if not req.get("joined"):
        # Only a NEW request is announced: joining one already open must
        # not ping the operator twice about the same conversation.
        try:
            await _notify.notify_operators(account_id, req, account_name)
        except Exception:
            logger.exception("plan request %s: operator notice failed", req.get("case_number"))
        try:
            _notify.email_sales(req, account_name)
        except Exception:
            logger.exception("plan request %s: sales email failed", req.get("case_number"))
        try:
            # The asker hears back immediately, with the number to quote.
            # A request that vanishes into a form looks exactly like one
            # that was never sent.
            _notify.email_customer(req, account_name)
        except Exception:
            logger.exception("plan request %s: acknowledgement failed", req.get("case_number"))
    joined = bool(req.get("joined"))
    return {
        "case_number": req.get("case_number", ""),
        "status": req.get("status", "open"),
        "joined": joined,
        "tier": tier,
        # Said here rather than assembled in the page, so the bot, the
        # dashboard and anything else answer the same way.
        "message": (
            f"You have already asked about {plan['label']} — "
            f"case {req.get('case_number', '')} is open and someone will reply to it."
            if joined else
            f"Request {req.get('case_number', '')} is with us. "
            "We have emailed you a copy."
        ),
    }


@router.get("/plan-requests")
async def billing_plan_requests(
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """This account's own requests that are not closed, so a plan card
    can show its case number instead of offering the button again."""
    try:
        items = await platform_db.plan_requests_for_account(user["account_id"])
    except Exception:
        logger.warning("plan requests unavailable for account=%s", user["account_id"])
        items = []
    return {"items": [
        {"tier": r["tier"], "case_number": r["case_number"], "status": r["status"],
         "created_at": r["created_at"]}
        for r in items
    ]}


# ── Checkout (upgrade) ───────────────────────────────────────────

class CheckoutRequest(BaseModel):
    # any plan key the operator can make (capabilities/permissions/plans.py
    # PLAN_KEY_RE) — three names were hard-coded here, so a plan made on
    # the console could be shown and priced but never bought
    tier: str = Field(..., pattern=r"^[a-z][a-z0-9_]{1,31}$")


@router.get("/plans")
async def billing_plans(
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """The plans a customer may pick, from the plan table: every public
    plan, the account's current one (public or not), and any hidden plan
    offered to this account after a Contact-Sales conversation — each
    with its price, the ids it includes (``["*"]`` = everything) and its
    quotas.  The dashboard names the ids from its own catalog, in the
    reader's language; Billing never reads a feature flag."""
    from capabilities.permissions.plans import EVERYTHING, EXCLUDABLE, quota_defaults, quota_for
    account = await platform_db.get_account(user["account_id"])
    current = (account.tier if account else None) or "free"
    rows = await platform_db.list_plans()
    offered = set(await platform_db.offered_tiers_for_account(user["account_id"]))
    out = []
    for r in sorted(rows, key=lambda x: (x["sort"], x["tier"])):
        # A retired plan is closed to new accounts, so it is not a choice
        # — but it stays on the page of the account already ON it, which
        # is their own plan and the only place they read what they pay
        # for.  Their card shows "Current Plan" and offers no upgrade to
        # itself, so nothing here invites them onto it.
        if r.get("retired") and r["tier"] != current:
            continue
        if not r["public"] and r["tier"] != current and r["tier"] not in offered:
            continue
        inc = r["included"]
        everything = EVERYTHING in inc
        out.append({
            "tier": r["tier"], "label": r["label"],
            "price_monthly_cents": r["price_monthly_cents"],
            "base_vehicles": r["base_vehicles"],
            "extra_vehicle_cents": r["extra_vehicle_cents"],
            "everything": everything,
            "included": list(EXCLUDABLE) if everything else [i for i in EXCLUDABLE if i in inc],
            # the number the API enforces: the row's, else the config table's
            "quotas": {k: quota_for(r["tier"], k, d) for k, d in quota_defaults(r["tier"]).items()},
            "public": bool(r["public"]),
            # hidden from everyone else; on this page because it was offered here
            "offered": r["tier"] in offered,
            "current": r["tier"] == current,
        })
    return {"plans": out, "current_tier": current}


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
    success_url = f"{_dashboard_url()}/billing?success=1"
    cancel_url  = f"{_dashboard_url()}/billing?canceled=1"
    from capabilities.platform.billing.provider import ProviderError
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
    except ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))
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
    return_url = f"{_dashboard_url()}/billing"
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

# The provider-named path is the one Stripe is configured with: a second
# provider tomorrow gets its own (/billing/<provider>/webhook) instead of
# a switch inside one handler.  The bare /billing/webhook stays as an
# alias so an endpoint registered against it keeps delivering.
@router.post("/stripe/webhook")
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


# ── Billing contact: the address every bill goes to ───────────────
#
# An edit control lived here once and was removed, because a system
# write from a customer surface mixes operator and customer roles.
# What comes back is not that write: it is a REQUEST, and it moves
# nothing by itself.  Two proofs stand between it and the address —
# a code typed back by the owner who asked (a stolen session cannot
# read the owner's inbox) and a link opened at the NEW address (a typo
# never confirms, and the old address keeps working).  The operator
# console keeps its own direct write for the cases support must fix.


def owner_may_edit_contact(user: dict) -> bool:
    """Only the owner moves the billing address.

    ``can_manage_billing`` reaches admins too, and an admin should be
    able to READ the bills without being able to redirect where they
    are sent — that redirect is the first move of an account takeover.
    One predicate for the door and for the page: the summary carries
    this answer as ``can_edit_contact`` so the Edit control is drawn
    from the same rule that refuses the request.
    """
    return user.get("role") == "owner"


def _require_account_owner(user: dict) -> None:
    if not owner_may_edit_contact(user):
        raise HTTPException(
            status_code=403,
            detail="Only the account owner can change the billing contact.")


class BillingEmailRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320,
                       description="Inbox that should receive invoices + receipts.")


class BillingEmailCodeRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


async def _pending_with_link_flag(platform_db, user: dict) -> dict | None:
    """The waiting change, plus whether a link will actually be needed.

    Only the server knows both halves: the address being moved to, and
    whether it is the requester's own VERIFIED sign-in address.  The page
    cannot work it out — it is shown the code's destination masked — so
    it would have to guess, and a guess here would promise the customer
    an email that never arrives.
    """
    pending = await platform_db.pending_billing_email_change(user["account_id"])
    if not pending:
        return None
    from interfaces.api.deps import get_current_db_user
    db_user = await get_current_db_user(user, platform_db)
    same = bool(db_user) and pending["new_email"].strip().lower() == \
        (db_user.email or "").strip().lower()
    verified = bool(db_user) and same and await platform_db.is_email_verified(int(db_user.id))
    return {**pending, "needs_link": not (same and verified)}


@router.get("/email/change")
async def billing_email_change_state(
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """The change in flight, if any — so the page can resume its step.

    Carries neither the code nor the token: the page needs to know
    WHICH step it is on and where each message went, and an answer
    holding the second proof would hand it to whoever holds the first.
    """
    return {"pending": await _pending_with_link_flag(platform_db, user)}


@router.post("/email/change")
async def billing_email_change_start(
    body: BillingEmailRequest,
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """Step 1 — ask, and send the owner a code to prove it was them."""
    _require_account_owner(user)
    from interfaces.api.deps import get_current_db_user
    db_user = await get_current_db_user(user, platform_db)
    if not db_user or not (db_user.email or "").strip():
        raise HTTPException(
            status_code=422,
            detail="Your profile has no email, so there is nowhere to send the "
                   "confirmation code. Add one in Profile settings first.")
    sub = await platform_db.get_subscription(user["account_id"]) or {}
    new_email = body.email.strip()
    if new_email.lower() == str(sub.get("billing_email") or "").strip().lower():
        raise HTTPException(
            status_code=409, detail="That is already the billing contact.")
    try:
        code = await platform_db.start_billing_email_change(
            user["account_id"], new_email=new_email,
            requested_by=int(db_user.id), requested_email=db_user.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    acct = await platform_db.get_account(user["account_id"])
    from capabilities.platform.billing.contact_email import send_change_code
    sent = await asyncio.to_thread(
        lambda: send_change_code(
            to=db_user.email, code=code, new_email=new_email,
            account_name=getattr(acct, "name", "") or "",
            recipient_name=getattr(db_user, "display_name", "") or ""))
    logger.info("billing contact change requested acct=%s by user=%s (code emailed=%s)",
                user["account_id"], db_user.id, sent)
    return {"pending": await _pending_with_link_flag(platform_db, user),
            "email_sent": sent}


@router.post("/email/change/verify")
async def billing_email_change_verify(
    body: BillingEmailCodeRequest,
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """Step 2 — the code checks out; now ask the NEW address itself."""
    _require_account_owner(user)
    from interfaces.api.deps import get_current_db_user
    db_user = await get_current_db_user(user, platform_db)
    pending = await platform_db.pending_billing_email_change(user["account_id"])
    if not pending:
        raise HTTPException(status_code=404, detail="There is no change waiting.")
    token = await platform_db.verify_billing_email_code(
        user["account_id"], body.code,
        requested_by=int(db_user.id) if db_user else None)
    if not token:
        # One answer for wrong, expired and not-yours: which one it is
        # tells an attacker where they are.
        raise HTTPException(
            status_code=400,
            detail="That code is wrong or has expired. Ask for a new one.")
    sub = await platform_db.get_subscription(user["account_id"]) or {}
    acct = await platform_db.get_account(user["account_id"])

    # The second proof exists to show the NEW address is real and is
    # theirs.  When the new address IS the requester's own verified
    # sign-in address, that is already proven — this account signed in
    # from it — and the link would be mailed to the very inbox that just
    # supplied the code.  Asking twice there does not add security, it
    # only makes the first answer look like it did not count.
    same_as_signin = (
        pending["new_email"].strip().lower() == (db_user.email or "").strip().lower()
        if db_user else False)
    if same_as_signin and await platform_db.is_email_verified(int(db_user.id)):
        from capabilities.platform.billing import get_provider
        try:
            await get_provider().update_billing_email(
                user["account_id"], platform_db, pending["new_email"])
        except Exception as e:
            # Leave no half-finished request behind: the owner starts
            # over rather than facing a step that can no longer complete.
            await platform_db.cancel_billing_email_change(user["account_id"])
            logger.exception("billing contact could not be applied acct=%s",
                             user["account_id"])
            raise HTTPException(
                status_code=502,
                detail=f"The billing provider refused the change: {e}")
        await platform_db.confirm_billing_email_change(token)
        try:
            await platform_db.add_platform_audit(
                "billing_email_changed", account_id=user["account_id"],
                actor=f"user:{db_user.id}",
                details=json.dumps({"new_email": pending["new_email"],
                                    "via": "own_verified_signin_address"}))
        except Exception:
            logger.exception("platform audit write failed for billing email change on %s",
                             user["account_id"])
        logger.info("billing contact changed acct=%s (owner's own verified address)",
                    user["account_id"])
        return {"pending": None, "applied": True, "email": pending["new_email"]}

    from capabilities.platform.billing.contact_email import send_confirm_link
    sent = await asyncio.to_thread(
        lambda: send_confirm_link(
            to=pending["new_email"], token=token,
            account_name=getattr(acct, "name", "") or "",
            old_email=str(sub.get("billing_email") or "")))
    logger.info("billing contact change confirmed by owner acct=%s (link emailed=%s)",
                user["account_id"], sent)
    return {"pending": await _pending_with_link_flag(platform_db, user),
            "applied": False, "email_sent": sent}


@router.delete("/email/change")
async def billing_email_change_cancel(
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """Drop the request.  A link already sent stops working with it."""
    _require_account_owner(user)
    dropped = await platform_db.cancel_billing_email_change(user["account_id"])
    if not dropped:
        raise HTTPException(status_code=404, detail="There is no change waiting.")
    return {"ok": True}


@router.get("/email/confirm")
async def billing_email_confirm(token: str = ""):
    """Step 3 — opened at the NEW address, by whoever reads that inbox.

    Deliberately unauthenticated: the person who must prove the address
    is theirs may have no login here at all — an accountant, a
    bookkeeper — and a link that lands on a sign-in wall proves nothing.
    The token IS the credential: one use, 48 hours, and it names one
    account and one address.

    Answers HTML rather than JSON because a human opens it from their
    mail client, and an unknown or spent token gets the same page as an
    expired one — telling a stranger which is telling them something.
    """
    from fastapi.responses import HTMLResponse
    from infra.platform import get_platform_db as _get_db
    platform_db = _get_db()
    change = await platform_db.confirm_billing_email_change(token)
    if not change:
        return HTMLResponse(_confirm_page(
            "This link is no longer valid",
            "It may have been used already, cancelled, or it expired. Ask for "
            "a new one from the Billing page — nothing has been changed."),
            status_code=400)
    from capabilities.platform.billing import get_provider
    try:
        await get_provider().update_billing_email(
            change["account_id"], platform_db, change["new_email"])
    except Exception:
        logger.exception("billing contact confirmed but not applied acct=%s",
                         change["account_id"])
        return HTMLResponse(_confirm_page(
            "We could not finish that just now",
            "Your address is confirmed but something on our side failed. "
            "Please try the change again from the Billing page, or contact "
            "support."), status_code=502)
    try:
        await platform_db.add_platform_audit(
            "billing_email_changed", account_id=change["account_id"],
            actor=f"user:{change['requested_by']}",
            details=json.dumps({"new_email": change["new_email"]}))
    except Exception:
        logger.exception("platform audit write failed for billing email change on %s",
                         change["account_id"])
    logger.info("billing contact changed acct=%s", change["account_id"])
    return HTMLResponse(_confirm_page(
        "Address confirmed",
        f"Invoices and payment receipts will be sent to "
        f"{html.escape(change['new_email'])} from now on. You can close this page."))


def _confirm_page(title: str, body: str) -> str:
    """A page for someone who may have never seen this product before."""
    return (
        '<!doctype html>\n'
        f'<html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{html.escape(title)}</title></head>'
        '<body style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
        'sans-serif;max-width:480px;margin:48px auto;padding:24px;color:#1f2937">'
        f'<h1 style="font-size:18px">{html.escape(title)}</h1>'
        f'<p style="font-size:14px;color:#6b7280;line-height:1.6">{body}</p>'
        '</body></html>')


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

@router.post("/update-vehicles")
async def update_vehicle_count(
    user: dict = Depends(_billing_admin),
    platform_db=Depends(get_platform_db),
):
    """Recompute the billed vehicle count from our own records.

    The quantity we charge for is not something the payer may state.
    This endpoint used to take a ``vehicle_count`` in the request body
    and write it straight to the subscription, so anyone holding
    ``can_manage_billing`` — owner, admin and accounting by default —
    could set their own billed fleet to zero, with no trace anywhere.
    Its docstring said the scheduler called it after a Samsara sync;
    nothing called it at all. The monthly snapshot job only READS
    ``vehicle_count``, and ``GET /summary`` already refreshes it.

    So the route stays (an unknown caller keeps working rather than
    meeting a 404) and the number now comes from
    ``_sync_vehicle_count`` — the vehicle registry, the same source the
    pricing math reads. A request body, if one is still sent, is
    ignored.

    Account-wide by design: the caller's Team-Management vehicle scope
    does NOT narrow the count. A subscription is billed for the whole
    fleet, so an accountant assigned two trucks must not be able to
    reduce the invoice to two.
    """
    count = await _sync_vehicle_count(user["account_id"], platform_db)
    if count is None:
        # The registry read failed; the stored count is left untouched
        # rather than replaced with a guess.
        raise HTTPException(
            status_code=503,
            detail="Could not read the vehicle registry just now — the "
                   "billed count is unchanged. Try again shortly.",
        )
    return {"ok": True, "vehicle_count": count}
