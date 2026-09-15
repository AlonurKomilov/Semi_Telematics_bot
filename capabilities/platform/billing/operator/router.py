"""The operator's half of billing — plans, offers, discounts, invoices.

The customer's half is [capabilities/platform/billing/router.py]: their own
card page, their own checkout.  This is the other audience for the same
domain — what 4truck staff do to an account's money from the operator
console, behind ``require_system_owner``.

It lives HERE rather than in the operator router because the repo's rule
is that a router lives with what it serves (docs/FEATURES.md, "Routers
live with their features").  For most of a year these twenty endpoints
sat in ``interfaces/api/routes/system.py`` alongside accounts, security,
knowledge and health — six domains in one 3,100-line file, of which
money was the largest single tenant.  Nothing was wrong with the code;
what was wrong was that changing a plan price meant opening a file where
most of what you can break has nothing to do with billing.

The URLs did not move with it.  Every path here is still ``/system/…``
exactly as the console has always called it — the prefix on this router
says so, and a route-parity test pins the whole set.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from interfaces.api.deps import get_platform_db, require_system_owner

logger = logging.getLogger(__name__)

#: Same prefix the operator router carries, so the move changed no URL.
router = APIRouter(prefix="/system", tags=["system"])


@router.post("/accounts/{account_id}/sync-quantity")
async def force_sync_quantity(
    account_id: int,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Manually reconcile Stripe's extras-line qty against the registry.

    The same logic runs after every Samsara ingest cycle and once a day
    for every account, so this endpoint is for the cases a human must
    decide: the bill looks wrong and the operator wants a sync now, or
    the unattended sync held a large upward jump (``jump_guard``) and
    the operator, having looked at the fleet, releases it — this call
    passes ``force=True``.  Returns the same status dict
    ``sync_billing_quantity`` would have returned from the scheduler.
    """
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    from capabilities.platform.billing import get_provider
    provider = get_provider()
    result = await provider.sync_billing_quantity(account_id, platform_db, force=True)
    logger.info(
        "system: sync_billing_quantity manual run acct=%s operator_tg=%s result=%s",
        account_id, user.get("sub"), result,
    )
    return result


@router.post("/accounts/{account_id}/invoice")
async def system_issue_local_invoice(
    account_id: int,
    month: str = Query(default="", description="YYYY-MM; default is the month just closed"),
    send: bool = Query(default=True,
                       description="Email the invoice + receipt. Turn OFF when "
                                   "backfilling months that closed long ago."),
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Write and send one Absolute 0 account's invoice for a month, now.

    The monthly job does this on the 1st, which is right for the month
    that just closed and useless for the months an account spent
    unbilled before Absolute 0 was granted.  This is the operator
    saying which month, and getting the same invoice, the same PDF and
    the same email the job would have produced.

    Idempotent through the invoice number: asking twice for one month
    returns the one invoice, never a second.  Refuses an account that
    has no Absolute 0 grant — every other account is Stripe's to bill,
    and a local invoice beside a Stripe one bills the customer twice on
    paper.
    """
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    from capabilities.platform.billing.jobs import issue_local_invoice, month_window
    period = None
    if month.strip():
        try:
            period = month_window(month.strip())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    try:
        row = await issue_local_invoice(account_id, period=period, send=send)
    except Exception as e:
        logger.exception("system: local invoice failed acct=%s", account_id)
        raise HTTPException(status_code=502, detail=f"The invoice could not be written: {e}")
    if row is None:
        raise HTTPException(
            status_code=409,
            detail="This account has no Absolute 0 discount — its bills are Stripe's to issue. "
                   "Grant Absolute 0 first if 4truck is covering the cost.")
    logger.info("system: local invoice %s issued acct=%s by operator_tg=%s (emailed=%s)",
                row.get("number"), account_id, user.get("sub"), send)
    return {"invoice": row}


class BillingEmailOverride(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)


@router.patch("/accounts/{account_id}/billing-email")
async def operator_set_billing_email(
    account_id: int,
    body: BillingEmailOverride,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Operator-side email override for any account.

    The customer-facing dashboard intentionally has no email-edit
    surface (info-only).  This is the operator escape hatch — useful
    when a customer-side admin contact is unreachable but the receipts
    need to start landing somewhere else.  Calls
    ``provider.update_billing_email`` which both writes the local row
    AND pushes the change to Stripe's Customer object if one exists.
    """
    email = body.email.strip()
    if "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    from capabilities.platform.billing import get_provider
    provider = get_provider()
    result = await provider.update_billing_email(account_id, platform_db, email)
    logger.info(
        "system: billing-email override acct=%s operator_tg=%s synced_to_provider=%s",
        account_id, user.get("sub"), result.get("synced_to_provider"),
    )
    return result


# ── Alert routing config ─────────────────────────────────────────
#
# Operator surface for switching an account between the legacy
# ``single_group`` (one forum chat + topic per alert_type) and the
# ``per_persona_groups`` mode (flat group per persona).  Customers
# self-serve the same config from Settings → Bot (features/settings/
# account/router.py /admin/alert-routing); these operator endpoints
# remain for support work on any account.


# Persona keys the operator UI can register.  Mirrors
# capabilities.alerting.persona_mapping.PERSONAS but listed here as
# a constant the request validator can rely on without a cross-package
# import (and so a typo in the dashboard form gets a 400 immediately
# instead of writing a bad row).


@router.get("/audit/comp-actions")
async def audit_comp_actions(
    limit: int = Query(default=50, ge=1, le=500),
    action: str = Query(default="", description="Filter to one action token"),
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Recent comp grant/renew/revoke/expire/reminder rows, all accounts."""
    items = await platform_db.list_recent_comp_actions(
        limit=limit, action=action or None,
    )
    return {"items": items, "count": len(items)}


@router.get("/invoices")
async def invoices_search(
    status: str = Query(default="", description="paid | open | uncollectible | void"),
    from_date: str = Query(default="", description="ISO-8601 lower bound on created_at"),
    to_date: str = Query(default="", description="ISO-8601 upper bound on created_at"),
    account_search: str = Query(default="", description="Substring match on account name"),
    limit: int = Query(default=100, ge=1, le=500),
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Cross-account invoice search for the operator console."""
    items = await platform_db.list_recent_invoices_cross_account(
        status=status or None,
        from_date=from_date or None,
        to_date=to_date or None,
        account_search=account_search or None,
        limit=limit,
    )
    return {"items": items, "count": len(items)}


# ── Helpers ─────────────────────────────────────────────────────


class PlanBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=60)
    included: list[str] = Field(..., max_length=200, description='registry ids, or ["*"] for everything')
    quotas: dict[str, int] = Field(default_factory=dict, max_length=20)
    # the price catalog — omitted = keep the row's value
    price_monthly_cents: int | None = Field(default=None, ge=0, le=10_000_000)
    base_vehicles: int | None = Field(default=None, ge=0, le=100_000)
    extra_vehicle_cents: int | None = Field(default=None, ge=0, le=1_000_000)
    stripe_price_id: str | None = Field(default=None, max_length=120)
    public: bool | None = None
    sort: int | None = Field(default=None, ge=0, le=1000)
    # the plan a self-serve signup's trial starts on; True moves the flag here
    trial_default: bool | None = None


class NewPlanBody(BaseModel):
    #: No ``tier``.  The key is derived from the label and returned —
    #: it is the plan's permanent identity, and typing it by hand is how
    #: a plan called Gold came to be keyed "costum".
    label: str = Field(..., min_length=1, max_length=60)


_AUDITED_PLAN_FIELDS = ("label", "included", "quotas", "price_monthly_cents", "base_vehicles",
                        "extra_vehicle_cents", "stripe_price_id", "stripe_product_id", "public", "sort", "trial_default")


def _stripe_setup() -> dict:
    """Which pieces of the Stripe wiring exist, by env presence.  Read
    by the Plans page so an operator sees what is missing BEFORE
    BILLING_PROVIDER is switched, not from the first failed checkout."""
    import os as _os
    has = lambda k: bool((_os.getenv(k) or "").strip())  # noqa: E731
    return {
        "secret_key": has("STRIPE_SECRET_KEY"),
        "webhook_secret": has("STRIPE_WEBHOOK_SECRET"),
        "extras_price": has("STRIPE_PRICE_EXTRA_VEHICLE"),
        "return_url": any(has(k) for k in ("AUTH_BASE_URL", "DASHBOARD_BASE_URL", "APP_BASE_URL")),
    }


def _billing_provider_name() -> str:
    import os as _os
    return (_os.getenv("BILLING_PROVIDER", "stub") or "stub").lower()


def _label_of(raw: str) -> str:
    label = raw.strip()
    if not label:
        raise HTTPException(status_code=400, detail="Label is required")
    return label


def _price_creation_failed(tier: str, what: str, e: Exception) -> HTTPException:
    """The answer when Stripe would not make a plan's Price.

    One cause has a name: the row still carries the OTHER mode's Product
    or Price ids — a dry run's, after the keys were swapped without the
    runbook's SQL step — and live Stripe answers "No such product".  That
    is the operator's state to fix, so it is a 409 that says how, not a
    502 that nginx may replace with its own page before the console
    reads it.  Anything else stays what it is: the provider failed.
    """
    from capabilities.platform.billing.setup_check import OTHER_MODE_HINT
    msg = str(e)
    if "No such product" in msg or "No such price" in msg:
        return HTTPException(
            status_code=409,
            detail=f"Stripe does not know the ids on plan '{tier}' — they {OTHER_MODE_HINT}. ({msg})")
    return HTTPException(status_code=502, detail=f"The billing provider could not create the {what}: {msg}")


def _plan_view(row: dict, counts: dict[str, int], offers: dict[str, list] | None = None) -> dict:
    from capabilities.permissions.plans import EVERYTHING, normalize_included, quota_defaults
    # normalized on read too: a row that names an id no longer for sale
    # (edited in the DB, or saved before the sellable set narrowed) is
    # shown as the mask reads it
    inc, _ = normalize_included(row.get("included") or [])
    return {
        **row,
        "included": inc,
        "everything": EVERYTHING in inc,
        "accounts": counts.get(row["tier"], 0),
        "quota_defaults": quota_defaults(row["tier"]),
        # the accounts a HIDDEN plan is open to — operator knowledge, never
        # on the customer wire (adapters/storage/plan_offers.py)
        "offered_to": (offers or {}).get(row["tier"], []),
    }


async def _offers_by_tier(platform_db) -> dict[str, list]:
    out: dict[str, list] = {}
    for o in await platform_db.list_plan_offers():
        out.setdefault(o["tier"], []).append({
            "account_id": int(o["account_id"]),
            "account_name": o.get("account_name") or f"#{o['account_id']}",
            "request_id": o.get("request_id"),
            "created_at": o.get("created_at") or "",
        })
    return out


def _check_quotas(quotas: dict[str, int]) -> None:
    from capabilities.permissions.plans import QUOTA_KEYS
    bad_keys = sorted(set(quotas) - set(QUOTA_KEYS))
    if bad_keys:
        raise HTTPException(status_code=400, detail=f"Unknown quota: {', '.join(bad_keys)}")
    if any(v < 0 for v in quotas.values()):
        raise HTTPException(status_code=400, detail="A quota is 0 (unlimited) or a positive number")


async def _audit_plan(platform_db, event: str, *, tier: str, actor: str, before, row: dict, accounts: int) -> None:
    try:
        await platform_db.add_platform_audit(
            event, actor=actor,
            details=json.dumps({
                "tier": tier, "accounts": accounts,
                "before": {k: before.get(k) for k in _AUDITED_PLAN_FIELDS} if before else None,
                "after": {k: row.get(k) for k in _AUDITED_PLAN_FIELDS},
            }),
        )
    except Exception:
        logger.exception("plan audit write failed tier=%s", tier)


@router.get("/plans")
async def system_plans(
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Every plan with what it includes, the sellable catalog to draw
    the grid from, and the tiers that have accounts but no plan row —
    those accounts hold nothing sellable until a row exists."""
    from capabilities.permissions.plans import QUOTA_KEYS, catalog
    from capabilities.permissions.plans import PLAN_KEY_RE
    rows = await platform_db.list_plans()
    counts = await platform_db.count_accounts_by_tier()
    offers = await _offers_by_tier(platform_db)
    known = {r["tier"] for r in rows}
    return {
        "plans": [_plan_view(r, counts, offers) for r in rows],
        "catalog": catalog(),
        "quota_keys": list(QUOTA_KEYS),
        "plan_key_pattern": PLAN_KEY_RE.pattern,
        "accounts_without_plan": {t: n for t, n in sorted(counts.items()) if t not in known},
        # stripe = a price change creates the Stripe Price and can be rolled out;
        # stub = prices are numbers on a page
        "billing_provider": _billing_provider_name(),
        # what a switch to Stripe still needs — env presence only, no Stripe call
        "stripe_setup": _stripe_setup(),
    }


@router.get("/plan-requests")
async def system_plan_requests(
    status: str = "",
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Customers who asked about a plan that is not sold self-serve.

    The queue an operator works: a request carries the account, the
    plan, what they wrote, where to reply, and a case number they can
    quote back.
    """
    try:
        items = await platform_db.list_plan_requests(status=status.strip())
    except Exception:
        logger.exception("system: plan requests unavailable")
        raise HTTPException(status_code=503, detail="The request queue is unavailable.")
    names: dict[int, str] = {}
    for r in items:
        aid = int(r["account_id"])
        if aid not in names:
            acc = await platform_db.get_account(aid)
            names[aid] = (acc.name if acc else "") or f"account {aid}"
    return {"items": [{**r, "account_name": names[int(r["account_id"])]} for r in items],
            "open": await platform_db.count_open_plan_requests()}


class PlanRequestStatusBody(BaseModel):
    status: str = Field(..., pattern="^(open|contacted|closed)$")


@router.post("/plan-requests/{request_id}/status")
async def system_set_plan_request_status(
    request_id: int,
    body: PlanRequestStatusBody,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Move a request along. Closing it frees the (account, plan) pair,
    so the customer can ask again later."""
    moved = await platform_db.set_plan_request_status(
        request_id, body.status, actor=f"tg:{user.get('sub')}")
    if not moved:
        raise HTTPException(status_code=404, detail="No such request.")
    logger.info("system: plan request %s -> %s by %s", request_id, body.status, user.get("sub"))
    return {"ok": True, "status": body.status}


class PlanOfferBody(BaseModel):
    account_id: int = Field(..., gt=0)
    # the Contact-Sales case this answers, when there is one
    request_id: int | None = Field(default=None, gt=0)


@router.post("/plans/{tier}/offers", status_code=201)
async def system_offer_plan(
    tier: str,
    body: PlanOfferBody,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Open a hidden plan to one account: the terms agreed after a
    Contact-Sales conversation.

    The plan goes on that account's Billing page and that account may
    check out; nothing else moves — no tier, no subscription.  When the
    offer answers a case, the case moves to *contacted* and the email
    on it gets the terms; the account's billing admins hear on Telegram
    either way.  Offering twice is reported, not repeated.
    """
    from capabilities.platform.billing.offers import email_offer, offer_refusal, telegram_offer
    plan = await platform_db.get_plan(tier)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"No plan named '{tier}' — create it on the Plans page first")
    why_not = offer_refusal(plan, await platform_db.list_plans(), provider=_billing_provider_name())
    if why_not:
        raise HTTPException(status_code=409, detail=why_not)
    acc = await platform_db.get_account(body.account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    req = None
    if body.request_id is not None:
        req = await platform_db.get_plan_request(body.request_id)
        # a case belongs to the account that opened it; answering one
        # account's case with an offer to another is a mix-up at best
        if req is None or int(req["account_id"]) != int(body.account_id):
            raise HTTPException(status_code=400, detail="That case does not belong to this account")
    actor = f"operator:{user.get('sub') or user.get('telegram_id')}"
    offer = await platform_db.offer_plan(
        tier, body.account_id, request_id=body.request_id, created_by=actor)
    if not offer.get("created"):
        return {"created": False, "tier": tier, "account_id": body.account_id,
                "message": f"{acc.name} already has this offer."}
    if req is not None and req.get("status") == "open":
        await platform_db.set_plan_request_status(int(req["id"]), "contacted", actor=actor)
    try:
        await platform_db.add_platform_audit(
            "plan_offer", account_id=body.account_id, actor=actor,
            details=json.dumps({"tier": tier, "request_id": body.request_id}),
        )
    except Exception:
        logger.exception("platform audit write failed for plan offer %s/%s", tier, body.account_id)
    case_number = (req or {}).get("case_number") or ""
    emailed = email_offer((req or {}).get("contact_email") or "", plan=plan,
                          account_name=acc.name, case_number=case_number)
    try:
        reached = await telegram_offer(body.account_id, plan=plan, case_number=case_number)
    except Exception:
        logger.exception("plan offer %s/%s: Telegram notice failed", tier, body.account_id)
        reached = 0
    logger.info("system: plan %s offered to acct=%s by %s (email=%s, telegram=%s)",
                tier, body.account_id, actor, emailed, reached)
    return {"created": True, "tier": tier, "account_id": body.account_id,
            "case_number": case_number, "emailed": emailed, "telegram": reached}


@router.delete("/plans/{tier}/offers/{account_id}")
async def system_revoke_plan_offer(
    tier: str,
    account_id: int,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Close the door again.  A subscription the account already made
    on this plan is untouched — the offer only ever gated the page and
    the checkout."""
    if not await platform_db.revoke_plan_offer(tier, account_id):
        raise HTTPException(status_code=404, detail="No such offer")
    actor = f"operator:{user.get('sub') or user.get('telegram_id')}"
    try:
        await platform_db.add_platform_audit(
            "plan_offer_revoked", account_id=account_id, actor=actor,
            details=json.dumps({"tier": tier}),
        )
    except Exception:
        logger.exception("platform audit write failed for plan offer revoke %s/%s", tier, account_id)
    logger.info("system: plan %s offer to acct=%s revoked by %s", tier, account_id, actor)
    return {"ok": True}


class DiscountBody(BaseModel):
    account_id: int = Field(..., gt=0)
    kind: str = Field(..., pattern="^(amount|percent|absolute)$")
    #: cents off each bill, for kind=amount
    amount_off_cents: int = Field(default=0, ge=0, le=10_000_000)
    #: 1..100, for kind=percent
    percent_off: int = Field(default=0, ge=0, le=100)
    #: how many bills it covers; 0 = until revoked
    months: int = Field(default=0, ge=0, le=60)
    reason: str = Field(default="", max_length=200)


@router.get("/accounts/{account_id}/discount")
async def system_account_discount(
    account_id: int,
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """The account's live price break and what came before it, plus what
    the provider says the next bill comes to.

    The preview is the operator's answer to "what will they actually be
    charged" — Stripe's own arithmetic rather than ours, and only here,
    never on a customer request path.
    """
    live = await platform_db.live_account_discount(account_id)
    history = await platform_db.account_discount_history(account_id)
    preview = None
    from capabilities.platform.billing import get_provider
    provider = get_provider()
    if hasattr(provider, "preview_discounted_invoice"):
        try:
            preview = await provider.preview_discounted_invoice(account_id, platform_db)
        except Exception:
            logger.exception("discount preview failed for account %s", account_id)
    return {"discount": live, "history": history, "next_invoice": preview}


@router.post("/accounts/{account_id}/discount", status_code=201)
async def system_grant_discount(
    account_id: int,
    body: DiscountBody,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Give one account a price break for a bounded time.

    The row is written first and the provider is asked second, so a
    grant that Stripe refuses leaves a record of what was attempted
    rather than silence.  A provider failure rolls the row back to
    ``revoked`` — a pending grant nobody can see is worse than none.
    """
    if body.account_id != account_id:
        raise HTTPException(status_code=400, detail="The body's account does not match the URL's.")
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    sub = await platform_db.get_subscription(account_id) or {}
    if sub.get("is_comped"):
        raise HTTPException(
            status_code=409,
            detail="This account is comped — it already pays nothing. Revoke the comp first "
                   "if it should pay a reduced amount instead.")
    actor = f"operator:{user.get('sub') or user.get('telegram_id')}"
    try:
        row = await platform_db.create_account_discount(
            account_id, kind=body.kind, amount_off_cents=body.amount_off_cents,
            percent_off=body.percent_off, months=body.months,
            reason=body.reason.strip(), granted_by=actor)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    from adapters.storage.account_discounts import ABSOLUTE
    if body.kind == ABSOLUTE:
        # Absolute 0 is billed by us, not by Stripe: no coupon, no
        # subscription, no card.  The monthly job writes the invoice and
        # sends it; the provider is never asked anything.
        from datetime import datetime as _dt, timezone as _tz
        await platform_db.mark_account_discount(
            int(row["id"]), status="active",
            starts_at=_dt.now(_tz.utc).isoformat())
        try:
            await platform_db.add_platform_audit(
                "discount_granted", account_id=account_id, actor=actor,
                details=json.dumps({"kind": ABSOLUTE, "months": body.months,
                                    "reason": body.reason.strip()}),
            )
        except Exception:
            logger.exception("platform audit write failed for absolute grant on %s", account_id)
        logger.info("system: Absolute 0 granted acct=%s by %s", account_id, actor)
        return {"discount": await platform_db.live_account_discount(account_id)}
    from capabilities.platform.billing import get_provider
    try:
        written = await get_provider().apply_discount(account_id, platform_db, row)
    except Exception as e:
        logger.exception("discount %s: the provider refused for account %s", row.get("id"), account_id)
        await platform_db.mark_account_discount(int(row["id"]), status="revoked")
        raise HTTPException(status_code=502, detail=f"The billing provider refused the discount: {e}")
    await platform_db.mark_account_discount(int(row["id"]), **written)
    try:
        await platform_db.add_platform_audit(
            "discount_granted", account_id=account_id, actor=actor,
            details=json.dumps({"kind": body.kind, "amount_off_cents": body.amount_off_cents,
                                "percent_off": body.percent_off, "months": body.months,
                                "reason": body.reason.strip()}),
        )
    except Exception:
        logger.exception("platform audit write failed for discount on %s", account_id)
    logger.info("system: discount granted acct=%s by %s (%s)", account_id, actor, written.get("status"))
    return {"discount": await platform_db.live_account_discount(account_id)}


@router.delete("/accounts/{account_id}/discount")
async def system_revoke_discount(
    account_id: int,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Take the price break off.  Bills already issued keep it — this
    changes what the NEXT one comes to."""
    row = await platform_db.live_account_discount(account_id)
    if not row:
        raise HTTPException(status_code=404, detail="This account has no discount to revoke.")
    from adapters.storage.account_discounts import ABSOLUTE
    from capabilities.platform.billing import get_provider
    try:
        if row.get("kind") != ABSOLUTE:      # nothing was ever put on Stripe
            await get_provider().remove_discount(account_id, platform_db, row)
    except Exception:
        # Stripe may have ended it already; the row must still close, or
        # the operator cannot grant a replacement.
        logger.exception("discount %s: the provider could not remove it", row.get("id"))
    await platform_db.mark_account_discount(int(row["id"]), status="revoked")
    actor = f"operator:{user.get('sub') or user.get('telegram_id')}"
    try:
        await platform_db.add_platform_audit(
            "discount_revoked", account_id=account_id, actor=actor,
            details=json.dumps({"discount_id": row.get("id")}),
        )
    except Exception:
        logger.exception("platform audit write failed for discount revoke on %s", account_id)
    logger.info("system: discount revoked acct=%s by %s", account_id, actor)
    return {"ok": True}


@router.get("/billing-mode")
async def system_billing_mode(_user: dict = Depends(require_system_owner)):
    """Which billing provider and Stripe mode this API is running in.

    Environment only — no Stripe call — because the console asks it on
    every navigation: the operator must not have to remember, on the
    Accounts page, that the platform is in test mode and every
    customer's Upgrade button opens a test checkout.  The Plans page's
    wiring check is the thorough answer; this is the one that travels.
    """
    from capabilities.platform.billing.setup_check import stripe_mode
    return {"provider": _billing_provider_name(), "mode": stripe_mode()}


@router.get("/plans/stripe-check")
async def system_plans_stripe_check(
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Ask Stripe whether the payment wiring is real, not just present.

    ``/system/plans.stripe_setup`` answers from environment variables,
    which cannot see the two failures that cost money: a Price id from
    the other Stripe mode, and a Product id pasted where a Price id
    belongs (that one bills no extra trucks at all, silently). This
    route asks Stripe itself. It only reads.

    Its own route rather than a field on ``GET /plans`` so the grid still
    paints instantly when Stripe is slow. "stripe-check" can never be
    mistaken for a plan key: ``PLAN_KEY_RE`` has no dash.
    """
    from capabilities.platform.billing.setup_check import check_stripe_setup
    rows = await platform_db.list_plans()
    try:
        latest = await platform_db.latest_invoice()
    except Exception:
        # the receipt row degrades to "nothing billed yet" rather than
        # taking the whole card down with it
        logger.exception("stripe-check: could not read the latest invoice")
        latest = None
    return await check_stripe_setup(rows, provider=_billing_provider_name(), latest_invoice=latest)


@router.post("/plans", status_code=201)
async def system_create_plan(
    body: NewPlanBody,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """A NEW plan, with everything included.  Narrow it with PUT after.

    The key is DERIVED from the label, never sent: it is the identity
    that keys five tables and rides into Stripe's lookup_key and
    metadata, and it cannot be corrected afterwards without replacing
    the plan.  A label whose key is taken gets ``_2`` rather than a
    refusal — two plans may legitimately be called the same thing at
    different times, and the operator should not have to invent a key to
    get past us.
    """
    from capabilities.permissions.plans import EVERYTHING, invalidate_plans, slug_from_label
    label = _label_of(body.label)
    existing = {str(p["tier"]) for p in (await platform_db.list_plans() or [])}
    try:
        tier = slug_from_label(label, existing)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="That name has no letters or digits to build a key from — "
                   "give the plan a name a person would read.")
    actor = f"tg:{user.get('sub')}"
    row = await platform_db.upsert_plan(tier, label=_label_of(body.label), included=[EVERYTHING], quotas={}, updated_by=actor)
    invalidate_plans()
    counts = await platform_db.count_accounts_by_tier()
    await _audit_plan(platform_db, "plan.created", tier=tier, actor=actor, before=None, row=row, accounts=counts.get(tier, 0))
    logger.info("system: plan %s created by %s", tier, actor)
    return {"plan": _plan_view(row, counts)}


@router.put("/plans/{tier}")
async def system_put_plan(
    tier: str,
    body: PlanBody,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Replace what a plan includes.  Validated against the sellable set —
    an id that is not for sale (Billing, Overview, the administration
    tier) or that rides another entry is refused, not silently dropped.
    Audited as ``plan.updated`` with the before/after.  A key with no
    plan is refused: creating one is POST's job."""
    from capabilities.permissions.plans import PLAN_KEY_RE, invalidate_plans, normalize_included
    if not PLAN_KEY_RE.match(tier):
        raise HTTPException(status_code=400, detail="Plan key: a-z, 0-9, _ — 2 to 32 chars, starting with a letter")
    label = _label_of(body.label)
    included, unknown = normalize_included(body.included)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Not a plan line: {', '.join(unknown)}")
    _check_quotas(body.quotas)
    before = await platform_db.get_plan(tier)
    if not before:
        raise HTTPException(status_code=404, detail=f"No plan named '{tier}' — create it first")
    actor = f"tg:{user.get('sub')}"
    # A changed monthly price makes the provider hold a Price for it BEFORE
    # the row is written — the row's cents and the Price Stripe charges by
    # the same hand; a provider failure leaves the row untouched.  The stub
    # skips; the old Price is archived after the row commits.
    stripe_price_id = body.stripe_price_id.strip() if body.stripe_price_id is not None else None
    if stripe_price_id is not None and _billing_provider_name() == "stripe" \
            and stripe_price_id != (before.get("stripe_price_id") or ""):
        # Stripe is the source of the Price: it is made on save from the
        # price, never pasted, so a checkout can never be pointed at a
        # Price the row's cents do not describe
        raise HTTPException(status_code=400, detail="The Stripe price is created from the price on save; it cannot be set by hand")
    stripe_product_id = None
    archived = ""
    new_cents = body.price_monthly_cents
    if new_cents is not None and (new_cents != int(before.get("price_monthly_cents") or 0)
                                  or (new_cents > 0 and not before.get("stripe_price_id"))):
        from capabilities.platform.billing import get_provider
        try:
            made = await get_provider().create_plan_price(tier=tier, label=label, cents=int(new_cents), before=before)
        except Exception as e:
            logger.exception("plan %s: the provider could not create a price for %s cents", tier, new_cents)
            raise _price_creation_failed(tier, "price", e)
        if not made.get("skipped"):
            stripe_price_id = made.get("stripe_price_id", "")
            stripe_product_id = made.get("stripe_product_id") or None
            archived = made.get("archived") or ""
    # The per-extra-truck Price, by the same rule: a changed amount, or an
    # amount with no Price yet, makes one on the plan's Product before the
    # row is written.  One env-wide Price used to serve every plan.
    stripe_extra_price_id = None
    stripe_extra_product_id = None
    archived_extra = ""
    extra_cents = body.extra_vehicle_cents
    # Remade when the amount changed, when there is no Price yet, and when
    # the Price predates the extras Product: a Price on the plan's own
    # Product made a bill print the plan's name twice, and the only way
    # off it is a new Price on the new Product.  So a Save heals a plan
    # carrying the old shape, rather than asking for SQL.
    needs_extra_price = extra_cents is not None and extra_cents > 0 and (
        extra_cents != int(before.get("extra_vehicle_cents") or 0)
        or not before.get("stripe_extra_price_id")
        or not before.get("stripe_extra_product_id"))
    if needs_extra_price or (extra_cents == 0 and before.get("stripe_extra_price_id")):
        from capabilities.platform.billing import get_provider
        # the base call above may have just made the Product: hand it on
        before_for_extra = {**before, **({"stripe_product_id": stripe_product_id} if stripe_product_id else {})}
        try:
            made_x = await get_provider().create_extra_price(
                tier=tier, label=label, cents=int(extra_cents), before=before_for_extra)
        except Exception as e:
            logger.exception("plan %s: the provider could not create an extras price for %s cents", tier, extra_cents)
            raise _price_creation_failed(tier, "extras price", e)
        if not made_x.get("skipped"):
            stripe_extra_price_id = made_x.get("stripe_extra_price_id", "")
            stripe_extra_product_id = made_x.get("stripe_extra_product_id") or None
            archived_extra = made_x.get("archived") or ""
    # The fifth door, and the quietest: a retired plan made the trial
    # default, or made public, would be back on sale without anyone
    # saying "un-retire".  The state has to hold on the save path too.
    if before.get("retired") and (body.trial_default or body.public):
        raise HTTPException(
            status_code=409,
            detail=f"'{tier}' is retired — it is closed to new accounts, so it cannot be made "
                   "public or the trial default. Un-retire it first.")
    row = await platform_db.upsert_plan(
        tier, label=label, included=included, quotas=dict(body.quotas), updated_by=actor,
        price_monthly_cents=body.price_monthly_cents, base_vehicles=body.base_vehicles,
        extra_vehicle_cents=body.extra_vehicle_cents,
        stripe_price_id=stripe_price_id, stripe_product_id=stripe_product_id,
        stripe_extra_price_id=stripe_extra_price_id, stripe_extra_product_id=stripe_extra_product_id,
        public=body.public, sort=body.sort, trial_default=body.trial_default)
    invalidate_plans()
    # A plan that becomes public has no private offers left to keep:
    # "only this account" says nothing once every account has it.  The
    # rows go HERE, at the moment the operator decides it, rather than
    # lingering to re-open the plan to those accounts the day someone
    # hides it again — a door nobody would remember opening.
    withdrawn = 0
    if row.get("public") and not before.get("public"):
        withdrawn = await platform_db.revoke_all_plan_offers(tier)
        if withdrawn:
            try:
                await platform_db.add_platform_audit(
                    "plan_offers_withdrawn", account_id=0, actor=actor,
                    details=json.dumps({"tier": tier, "offers": withdrawn, "why": "plan made public"}),
                )
            except Exception:
                logger.exception("platform audit write failed for offers withdrawn on %s", tier)
            logger.info("system: plan %s made public — %d offer(s) withdrawn by %s", tier, withdrawn, actor)
    from capabilities.platform.billing import get_provider
    provider = get_provider()
    for gone, kept in ((archived, row.get("stripe_price_id")), (archived_extra, row.get("stripe_extra_price_id"))):
        if gone and gone != kept and hasattr(provider, "archive_plan_price"):
            await provider.archive_plan_price(gone)
    # The name a customer reads on a checkout page and an invoice lives on
    # the Stripe Product, not on our row: a renamed plan kept its old name
    # there forever.  Best-effort — the row is already saved, and a rename
    # that fails changes no money.
    if label != (before.get("label") or "") and hasattr(provider, "rename_plan_products"):
        try:
            await provider.rename_plan_products(
                label=label,
                base_product_id=row.get("stripe_product_id") or "",
                extra_product_id=row.get("stripe_extra_product_id") or "")
        except Exception:
            logger.exception("plan %s: renamed here but not on the provider's products", tier)
    counts = await platform_db.count_accounts_by_tier()
    await _audit_plan(platform_db, "plan.updated", tier=tier, actor=actor, before=before, row=row, accounts=counts.get(tier, 0))
    logger.info("system: plan %s saved by %s (%d account(s))", tier, actor, counts.get(tier, 0))
    on_old = 0
    if row.get("stripe_price_id"):
        on_old = sum(1 for s_ in await platform_db.subscriptions_on_tier(tier)
                     if (s_.get("provider_base_price_id") or "") != row["stripe_price_id"])
    return {"plan": _plan_view(row, counts, await _offers_by_tier(platform_db)),
            "subscribers_on_old_price": on_old, "offers_withdrawn": withdrawn}


# ── The operator moves an account to a plan ────────────────────────
#
# The one door for "this account is on that plan" outside a checkout:
# an enterprise deal, a comp, a downgrade by hand.  It refuses an
# account Stripe is billing — Stripe would keep charging the old price,
# and a plan that the page says one thing about while the invoice says
# another is the lie this whole table exists to end; those accounts
# move through the price rollout, not here.


class AccountPlanBody(BaseModel):
    tier: str = Field(..., min_length=2, max_length=32)
    reason: str = Field(default="", max_length=300)


# Stripe is still charging (or about to): active, a trial that will
# convert, and past_due with an open invoice.  ``unpaid`` is left out on
# purpose — Stripe has stopped billing it — as are canceled/incomplete.
# One tuple, shared with the rollout's candidate query.
from adapters.storage.plan_rollouts import LIVE_STATUSES as _STRIPE_LIVE  # noqa: E402


@router.patch("/accounts/{account_id}/plan")
async def operator_set_account_plan(
    account_id: int,
    body: AccountPlanBody,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    from capabilities.permissions.plans import PLAN_KEY_RE, account_plan_changed
    tier = body.tier.strip()
    if not PLAN_KEY_RE.match(tier):
        raise HTTPException(status_code=400, detail="Plan key: a-z, 0-9, _ — 2 to 32 chars, starting with a letter")
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    plan = await platform_db.get_plan(tier)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"No plan named '{tier}' — create it on the Plans page first")
    if plan.get("retired"):
        # The fourth door onto a plan, and the only one a person opens by
        # hand.  Retired means closed to new accounts by EVERY route, or
        # the state means nothing the day someone moves an account here
        # out of habit.
        raise HTTPException(
            status_code=409,
            detail=f"'{tier}' is retired — it is closed to new accounts. Move this account to a "
                   "plan still being sold, or un-retire that one first.")
    previous = acc.tier or "free"
    if previous == tier:
        return {"id": account_id, "tier": tier, "changed": False}
    sub = await platform_db.get_subscription(account_id)
    if sub and sub.get("provider") == "stripe" and sub.get("provider_subscription_id") \
            and (sub.get("status") or "") in _STRIPE_LIVE and not sub.get("is_comped"):
        raise HTTPException(
            status_code=409,
            detail="Stripe bills this account; moving it here would not change what Stripe charges. "
                   "Change its plan through the price rollout, or cancel the Stripe subscription first.")
    pricing = await platform_db.pricing_for(tier)
    # one transaction: the account and its subscription row change plan
    # together or not at all — the Billing page reads the row, the
    # resolver reads the account, and they must never disagree
    async with platform_db.transaction():
        await platform_db.update_account_tier(account_id, tier)
        if sub:
            await platform_db.update_subscription(
                account_id, tier=tier,
                base_vehicles=pricing["base_vehicles"],
                monthly_base_usd=pricing["monthly_base_cents"],
                extra_vehicle_cents=pricing["extra_vehicle_cents"],
            )
    account_plan_changed(account_id)
    actor = f"operator:{user.get('sub')}"
    try:
        await platform_db.add_platform_audit(
            "account_plan", account_id=account_id, actor=actor,
            details=json.dumps({"from": previous, "to": tier, "reason": body.reason.strip()}),
        )
    except Exception:
        logger.exception("platform audit write failed for account %s", account_id)
    logger.info("system: account plan acct=%s %s -> %s by %s", account_id, previous, tier, actor)
    return {"id": account_id, "tier": tier, "changed": True, "previous": previous}


# ── The price rollout ───────────────────────────────────────────────
#
# A saved price reaches new checkouts at once and existing subscribers
# only through this: previewed, capped per call (the console calls again
# while ``remaining`` is not zero), resumable, audited, and refused by
# the engine when the plan's Stripe price does not say what the row says.


class PlanRetireBody(BaseModel):
    retired: bool


@router.post("/plans/{tier}/retire")
async def system_retire_plan(
    tier: str,
    body: PlanRetireBody,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Close a plan to new accounts without touching the ones on it.

    The state between "we have stopped selling this" and "nobody is left
    on it".  Deleting a plan an account sits on is not a downgrade — the
    tier resolves to nothing and every sellable feature answers False —
    so a plan with customers can only be sunset, never removed, and this
    is how the selling stops while they migrate at their own pace.

    Closed by all four doors at once: checkout and the customer's plan
    list through ``offers.purchasable_plan``, an operator's account move,
    a new offer, and a trial.  Nothing about an existing subscription
    asks any of them, which is the point.

    Reversible — ``retired: false`` puts it back on sale.  Refused for
    the trial default, because new signups land there and closing it
    would leave them nowhere; make another plan the trial default first.
    """
    plan = await platform_db.get_plan(tier)
    if not plan:
        raise HTTPException(status_code=404, detail="No such plan.")
    if body.retired and plan.get("trial_default"):
        raise HTTPException(
            status_code=409,
            detail="This is the trial default — new signups land on it, so it cannot be "
                   "closed to new accounts. Make another plan the trial default first.")
    if bool(plan.get("retired")) == bool(body.retired):
        return {"tier": tier, "retired": bool(body.retired), "changed": False}

    actor = f"tg:{user.get('sub')}"
    from datetime import datetime as _dt, timezone as _tz
    stamp = _dt.now(_tz.utc).isoformat() if body.retired else ""
    row = await platform_db.upsert_plan(
        tier, label=plan["label"], included=plan.get("included") or [],
        quotas=plan.get("quotas") or {}, retired_at=stamp, updated_by=actor)
    # Offers are doors onto the plan; retiring it and leaving them open
    # would be the state contradicting itself on the one surface a
    # customer actually sees.
    withdrawn = 0
    if body.retired:
        withdrawn = await platform_db.revoke_all_plan_offers(tier)
    from capabilities.permissions.plans import invalidate_plans
    invalidate_plans()
    counts = await platform_db.count_accounts_by_tier()
    left = counts.get(tier, 0)
    try:
        await platform_db.add_platform_audit(
            "plan.retired" if body.retired else "plan.unretired",
            account_id=0, actor=actor,
            details=json.dumps({"tier": tier, "accounts_still_on_it": left,
                                "offers_withdrawn": withdrawn}))
    except Exception:
        logger.exception("platform audit write failed for retire on %s", tier)
    logger.info("system: plan %s %s by %s (%s accounts still on it)",
                tier, "retired" if body.retired else "un-retired", actor, left)
    return {"tier": tier, "retired": bool(body.retired), "changed": True,
            "accounts_still_on_it": left, "offers_withdrawn": withdrawn,
            "plan": _plan_view(row, counts)}


@router.delete("/plans/{tier}")
async def system_delete_plan(
    tier: str,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Remove a plan nobody is on.

    Refused, never cascaded.  A tier with no plan row resolves to None
    in the permission layer, and every sellable feature then answers
    False — so deleting a plan an account sits on does not downgrade
    them, it closes the product on them.  Move them first; the answer
    says how many and where.

    Three plans are refused whatever their counts: ``free`` is the floor
    every account falls back to, the trial default is where new signups
    land, and a plan with an OPEN customer case is a conversation in
    progress.  Live offers are withdrawn with the plan and named back.

    Stripe is tidied best-effort: the base and extras Prices are
    archived, and if Stripe refuses the plan still goes and the ids come
    back so an operator can archive them by hand.  An orphaned Price
    charges nobody; a plan we could not delete because Stripe was down
    would strand the operator.
    """
    plan = await platform_db.get_plan(tier)
    if not plan:
        raise HTTPException(status_code=404, detail="No such plan.")
    if tier == "free":
        raise HTTPException(
            status_code=409,
            detail="'free' is the plan every account falls back to — it cannot be deleted.")
    if plan.get("trial_default"):
        raise HTTPException(
            status_code=409,
            detail="This is the trial default — new signups land on it. Make another "
                   "plan the trial default first.")

    counts = await platform_db.plan_dependents(tier)
    unreadable = [k for k, v in counts.items() if v < 0]
    if unreadable:
        raise HTTPException(
            status_code=503,
            detail=f"Could not check what still points at this plan ({', '.join(unreadable)}) "
                   "— refusing rather than guessing.")
    blocking = {k: v for k, v in counts.items()
                if v > 0 and k in ("accounts", "subscriptions", "open_requests")}
    if blocking:
        says = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in blocking.items())
        raise HTTPException(
            status_code=409,
            detail=f"{says} still point at this plan. Move them to another plan (or close "
                   "the cases) first — deleting it now would take every service away "
                   "from them, not move them down a tier.")

    actor = f"tg:{user.get('sub')}"
    withdrawn = 0
    if counts.get("offers"):
        withdrawn = await platform_db.revoke_all_plan_offers(tier)

    archived: list[str] = []
    failed: list[str] = []
    from capabilities.platform.billing import get_provider
    provider = get_provider()
    for key in ("stripe_price_id", "stripe_extra_price_id"):
        price_id = str(plan.get(key) or "").strip()
        if not price_id:
            continue
        try:
            await provider.archive_plan_price(price_id)
            archived.append(price_id)
        except Exception:
            logger.exception("plan %s: could not archive %s", tier, price_id)
            failed.append(price_id)

    await platform_db.delete_plan(tier)
    from capabilities.permissions.plans import invalidate_plans
    invalidate_plans()
    try:
        await platform_db.add_platform_audit(
            "plan.deleted", account_id=0, actor=actor,
            details=json.dumps({"tier": tier, "label": plan.get("label") or "",
                                "offers_withdrawn": withdrawn,
                                "prices_archived": archived,
                                "prices_left_live": failed}))
    except Exception:
        logger.exception("platform audit write failed for plan delete on %s", tier)
    logger.info("system: plan %s deleted by %s (offers withdrawn=%s, archived=%s, left=%s)",
                tier, actor, withdrawn, archived, failed)
    return {"deleted": tier, "offers_withdrawn": withdrawn,
            "prices_archived": archived, "prices_left_live": failed}


@router.get("/plans/{tier}/rollout")
async def system_plan_rollout_preview(
    tier: str,
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    from capabilities.platform.billing import get_provider
    from capabilities.platform.billing.rollout import RolloutRefused
    try:
        return await get_provider().rollout_preview(platform_db, tier)
    except RolloutRefused as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/plans/{tier}/rollout")
async def system_plan_rollout(
    tier: str,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    from capabilities.platform.billing import get_provider
    from capabilities.platform.billing.rollout import RolloutBusy, RolloutRefused
    actor = f"tg:{user.get('sub')}"
    try:
        out = await get_provider().rollout_execute(platform_db, tier, actor=actor)
    except RolloutBusy as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (RolloutRefused, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        await platform_db.add_platform_audit(
            "plan.rollout", actor=actor,
            details=json.dumps({"tier": tier, "rollout_id": out.get("rollout_id"), "to_price_id": out.get("to_price_id"),
                                "to_cents": out.get("to_cents"), "processed": out.get("processed"),
                                "counts": out.get("counts"), "remaining": out.get("remaining"),
                                "finished": out.get("finished"), "aborted": out.get("aborted")}),
        )
    except Exception:
        logger.exception("plan rollout audit write failed tier=%s", tier)
    logger.info("system: plan %s price rollout by %s: %s", tier, actor, out)
    return out
