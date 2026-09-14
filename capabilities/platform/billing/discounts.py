"""Granting a price break, in Stripe's terms.

A discount here is a Stripe Coupon applied to the account's
SUBSCRIPTION.  Not to the Customer: a customer-level discount bleeds
onto every future subscription, and when both exist Stripe silently
prefers the subscription's — two places to look for one number.

When the account has no subscription yet, the coupon rides its next
Checkout Session instead (``discounts=[{"coupon": …}]``) and becomes a
subscription discount the moment the customer pays.  Until then the row
stays ``pending``: granted here, not yet costing anyone anything.

Nothing in this module writes our rows; the caller does, so that what
Stripe answered and what we recorded move together or not at all.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def coupon_params(discount: dict) -> dict:
    """The Coupon to create for one grant.

    ``name`` is what a customer reads on their invoice beside the
    minus sign, so it carries the operator's reason when there is one.
    ``currency`` is required by Stripe for an amount_off coupon and
    forbidden for percent_off.
    """
    months = int(discount.get("months") or 0)
    duration = {"duration": "repeating", "duration_in_months": months} if months > 0 \
        else {"duration": "forever"}
    reason = str(discount.get("reason") or "").strip()
    name = f"Promotion — {reason}" if reason else "Promotion"
    params = {"name": name[:40], **duration,
              "metadata": {"account_id": str(discount.get("account_id") or ""),
                           "discount_id": str(discount.get("id") or "")}}
    if discount.get("kind") == "percent":
        params["percent_off"] = float(int(discount.get("percent_off") or 0))
    else:
        params["amount_off"] = int(discount.get("amount_off_cents") or 0)
        params["currency"] = "usd"
    return params


def _iso(epoch) -> str | None:
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def apply_to_subscription(stripe, *, subscription_id: str, discount: dict) -> dict:
    """Create the coupon and put it on the subscription.

    Returns what Stripe HOLDS afterwards — a fresh read, never the
    modify's own answer, because a cached replay would have us record a
    discount that is not there.  ``{"coupon_id", "discount_id",
    "starts_at", "ends_at"}``.
    """
    from capabilities.platform.billing.stripe_client import _field
    coupon = stripe.Coupon.create(
        **coupon_params(discount),
        idempotency_key=f"discount-coupon:{discount.get('id')}",
    )
    coupon_id = _field(coupon, "id", "")
    stripe.Subscription.modify(
        subscription_id,
        discounts=[{"coupon": coupon_id}],
        # A discount is not a price change; nothing is prorated by it.
        # Stripe applies it from the next invoice onward.
        idempotency_key=f"discount-apply:{discount.get('id')}:{uuid.uuid4().hex}",
    )
    mine = _find_discount(stripe, subscription_id, coupon_id)
    if mine is None:
        raise RuntimeError("Stripe did not put the discount on the subscription")
    return {
        "coupon_id": coupon_id,
        "discount_id": _field(mine, "id", ""),
        "starts_at": _iso(_field(mine, "start")),
        "ends_at": _iso(_field(mine, "end")),
    }


def _coupon_id_of(discount) -> str:
    """The coupon behind a Discount, on this API version.

    A Discount has no ``coupon`` field any more — it carries
    ``source = {"type": "coupon", "coupon": "<id>"}``, and the id is a
    string unless ``discounts.source.coupon`` was expanded.  Both
    shapes are read here so a caller never has to know which expand it
    asked for.  (Probed against a live sandbox subscription, 2026-09-14:
    reading ``discount["coupon"]`` raises KeyError.)
    """
    from capabilities.platform.billing.stripe_client import _field
    source = _field(discount, "source", {}) or {}
    coupon = _field(source, "coupon", "")
    if isinstance(coupon, str):
        return coupon
    return _field(coupon, "id", "") or ""


def _find_discount(stripe, subscription_id: str, coupon_id: str):
    """The discount on *subscription_id* that came from *coupon_id* —
    read back from what Stripe HOLDS, never from a modify's own answer,
    which can be a cached replay."""
    from capabilities.platform.billing.stripe_client import _field
    fresh = stripe.Subscription.retrieve(subscription_id, expand=["discounts"])
    for d in (_field(fresh, "discounts", []) or []):
        if _coupon_id_of(d) == coupon_id:
            return d
    return None


def checkout_discounts(discount: dict | None) -> list[dict]:
    """What a Checkout Session carries for an account whose grant is
    still waiting for its first subscription.  Empty when there is
    none — and never ``[]`` on a Subscription.modify, where an empty
    list CLEARS what is there."""
    coupon = str((discount or {}).get("stripe_coupon_id") or "").strip()
    return [{"coupon": coupon}] if coupon else []


def ensure_coupon(stripe, discount: dict) -> str:
    """The coupon id for a grant, made once and reused.  A pending grant
    needs one before any checkout can carry it."""
    from capabilities.platform.billing.stripe_client import _field
    existing = str(discount.get("stripe_coupon_id") or "").strip()
    if existing:
        return existing
    coupon = stripe.Coupon.create(
        **coupon_params(discount),
        idempotency_key=f"discount-coupon:{discount.get('id')}",
    )
    return _field(coupon, "id", "")


def remove_from_subscription(stripe, *, subscription_id: str, discount_id: str) -> bool:
    """Take it off.  Best-effort: a discount Stripe has already ended is
    gone, and asking again must not turn a revoke into an error."""
    if not subscription_id or not discount_id:
        return False
    try:
        stripe.Subscription.delete_discount(subscription_id)
        return True
    except Exception:
        logger.warning("could not remove discount %s from %s; it may have ended already",
                       discount_id, subscription_id, exc_info=True)
        return False


def read_back(stripe, *, subscription_id: str, discount_id: str) -> dict | None:
    """What Stripe holds for this discount now — the daily sweep's
    question.  None when Stripe no longer has it, which is how a row
    learns its grant ended on Stripe's clock rather than ours."""
    from capabilities.platform.billing.stripe_client import _field
    if not subscription_id:
        return None
    fresh = stripe.Subscription.retrieve(subscription_id, expand=["discounts"])
    for d in (_field(fresh, "discounts", []) or []):
        if _field(d, "id", "") == discount_id:
            return {"starts_at": _iso(_field(d, "start")), "ends_at": _iso(_field(d, "end"))}
    return None


def preview_next_invoice(stripe, subscription_id: str) -> dict | None:
    """What Stripe says the next bill comes to — subtotal, discount,
    total.  Used to SHOW an operator the effect of a grant before they
    confirm it, never on a customer request path: it is a network call
    and a trial or stub account has nothing to preview."""
    from capabilities.platform.billing.stripe_client import _field
    try:
        prev = stripe.Invoice.create_preview(subscription=subscription_id)
    except Exception:
        logger.info("no invoice preview for %s", subscription_id, exc_info=True)
        return None
    off = sum(int(_field(row, "amount", 0) or 0)
              for row in (_field(prev, "total_discount_amounts", []) or []))
    return {"subtotal_cents": int(_field(prev, "subtotal", 0) or 0),
            "discount_cents": off,
            "total_cents": int(_field(prev, "total", 0) or 0)}
