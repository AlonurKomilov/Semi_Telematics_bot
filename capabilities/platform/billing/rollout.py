"""A plan's price reaching Stripe — the Price behind the row, and the
accounts already on the plan.

Two doors, both driven by the system console:

* ``ensure_plan_price`` — on a console save that changes the monthly
  price, the API creates the Stripe Price itself (one Product per
  plan, made on first use and remembered on the row), so the row's
  cents and the Price Stripe charges are written by the same hand.
  The old Price is archived, never deleted.

* ``execute`` — the rollout: every live Stripe subscription on the
  plan has its BASE item swapped to the new Price with
  ``proration_behavior="none"``, so the new amount bills from each
  account's next period.  Previewed first, capped per call, resumable
  (an item with a terminal outcome is never re-run), never enumerated
  from Stripe, and it stops itself after five errors in a row.  The
  extras item is never touched.

The stub provider does none of this; ``system.py`` asks the provider,
and the provider asks here only in Stripe mode.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

#: a Stripe subscription that has stopped (or never started) billing
STRIPE_DEAD = ("canceled", "unpaid", "incomplete", "incomplete_expired")
#: how many accounts one POST works through before handing back `remaining`
BATCH = 50
#: consecutive failures after which a rollout marks itself aborted
MAX_CONSECUTIVE_ERRORS = 5


class RolloutRefused(ValueError):
    """The rollout must not start: the reason is the message."""


class RolloutBusy(RolloutRefused):
    """Another rollout for this plan is still running."""


def _slots(stripe_sub: Any) -> dict:
    from capabilities.platform.billing.stripe_client import StripeBillingProvider
    return StripeBillingProvider._extract_items(stripe_sub)


def _monthly_usd(slot: dict):
    from capabilities.platform.billing.stripe_client import StripeBillingProvider
    return StripeBillingProvider._stripe_monthly_usd(slot)


def _period_end(stripe_sub: Any) -> str:
    end = stripe_sub.get("current_period_end") if hasattr(stripe_sub, "get") else None
    return str(end) if end else ""


# ── the Price behind the row ──────────────────────────────────────

def ensure_plan_price(stripe, *, tier: str, label: str, cents: int, before: dict) -> dict:
    """Make Stripe hold a Price for *cents* on this plan's Product.

    Returns ``{"stripe_price_id", "stripe_product_id", "archived"}``.
    Zero cents means no Price at all — the id is cleared and checkout
    refuses the plan by its own rule.  Idempotency keys carry the
    row's ``updated_at`` so a 49→59→49 flip within a day does not hand
    back the archived first Price.
    """
    product_id = (before or {}).get("stripe_product_id") or ""
    old_price = (before or {}).get("stripe_price_id") or ""
    if int(cents) <= 0:
        # free: no Price to hold; the one it had is handed back to be archived
        return {"stripe_price_id": "", "stripe_product_id": product_id, "archived": old_price}
    if not product_id:
        product = stripe.Product.create(
            name=label or tier.title(), metadata={"tier": tier},
            idempotency_key=f"plan-product:{tier}",
        )
        product_id = product["id"]
    stamp = str((before or {}).get("updated_at") or "")
    price = stripe.Price.create(
        product=product_id, unit_amount=int(cents), currency="usd",
        recurring={"interval": "month"}, nickname=f"{label or tier} monthly",
        lookup_key=f"4truck_{tier}_monthly", transfer_lookup_key=True,
        metadata={"tier": tier},
        idempotency_key=f"plan-price:{tier}:{int(cents)}:{stamp}",
    )
    return {"stripe_price_id": price["id"], "stripe_product_id": product_id, "archived": old_price}


def archive_price(stripe, price_id: str) -> bool:
    """Best-effort: an old Price is archived, never deleted (invoices
    reference it).  A failure here is logged and changes nothing."""
    if not price_id:
        return False
    try:
        stripe.Price.modify(price_id, active=False)
        return True
    except Exception:
        logger.warning("could not archive Stripe price %s; it stays active", price_id, exc_info=True)
        return False


# ── the rollout ───────────────────────────────────────────────────

async def preview(db, tier: str) -> dict:
    """What a rollout would do, from our tables alone: the plan's price,
    the live Stripe subscriptions on it, and any rollout still open."""
    plan = await db.get_plan(tier)
    if plan is None:
        raise RolloutRefused(f"No plan named '{tier}'")
    subs = await db.subscriptions_on_tier(tier)
    to_price = plan.get("stripe_price_id") or ""
    on_new = sum(1 for s in subs if (s.get("provider_base_price_id") or "") == to_price)
    open_rollout = await db.open_price_rollout(tier)
    items = await db.rollout_items(open_rollout["id"]) if open_rollout else []
    return {
        "tier": tier,
        "to_price_id": to_price,
        "to_cents": int(plan.get("price_monthly_cents") or 0),
        "candidates": len(subs),
        "already_on_new_price": on_new,
        "to_move": len(subs) - on_new,
        "open_rollout": {**open_rollout, "items": len(items)} if open_rollout else None,
        "recent": await db.list_price_rollouts(tier, limit=5),
    }


def _check_price(stripe, price_id: str, cents: int) -> None:
    p = stripe.Price.retrieve(price_id)
    rec = p.get("recurring") or {}
    if not p.get("active", True):
        raise RolloutRefused("The plan's Stripe price is archived — save the plan again to create a fresh one")
    if rec.get("interval") != "month" or int(rec.get("interval_count") or 1) != 1 \
            or str(p.get("currency") or "").lower() != "usd":
        raise RolloutRefused("The plan's Stripe price is not a plain monthly USD price")
    if int(p.get("unit_amount") or -1) != int(cents):
        raise RolloutRefused(
            f"The plan says ${cents / 100:.2f} but its Stripe price charges "
            f"${int(p.get('unit_amount') or 0) / 100:.2f} — save the plan again")


async def execute(stripe, db, tier: str, *, actor: str, limit: int = BATCH) -> dict:
    """Move up to *limit* live subscriptions on *tier* to the plan's
    Price.  Returns counts and ``remaining``; the console calls again
    while ``remaining`` is not zero.  Resumes an open rollout for the
    same target price; refuses while one younger than fifteen minutes
    is still being worked."""
    plan = await db.get_plan(tier)
    if plan is None:
        raise RolloutRefused(f"No plan named '{tier}'")
    to_price = plan.get("stripe_price_id") or ""
    to_cents = int(plan.get("price_monthly_cents") or 0)
    if not to_price or to_cents <= 0:
        raise RolloutRefused("The plan has no Stripe price to roll out — set a price and save it first")
    _check_price(stripe, to_price, to_cents)

    await db.abort_stale_rollouts(tier)
    open_rollout = await db.open_price_rollout(tier)
    if open_rollout and (open_rollout.get("to_price_id") or "") != to_price:
        # a rollout toward a price that is no longer the plan's: close it
        await db.finish_price_rollout(open_rollout["id"], aborted=True, summary={"reason": "price changed"})
        open_rollout = None
    if open_rollout:
        rollout_id = int(open_rollout["id"])
    else:
        subs_now = await db.subscriptions_on_tier(tier)
        from_prices = {s.get("provider_base_price_id") or "" for s in subs_now} - {to_price}
        rollout_id = await db.create_price_rollout(
            tier, from_price_id=",".join(sorted(p for p in from_prices if p))[:200], to_price_id=to_price,
            from_cents=int(subs_now[0]["monthly_base_usd"]) if subs_now else 0, to_cents=to_cents, actor=actor)

    # one batch at a time: a second operator pressing the button while this
    # batch runs is told so, instead of both walking the same accounts
    token = uuid.uuid4().hex
    if not await db.claim_rollout(rollout_id, token):
        raise RolloutBusy("A batch of this rollout is running right now — try again in a moment")
    try:
        return await _run_batch(stripe, db, tier, rollout_id, to_price, to_cents, limit=limit)
    finally:
        await db.release_rollout(rollout_id, token)


async def _run_batch(stripe, db, tier: str, rollout_id: int, to_price: str, to_cents: int, *, limit: int) -> dict:
    done = {int(i["account_id"]) for i in await db.rollout_items(rollout_id)}
    candidates = await db.subscriptions_on_tier(tier)
    counts: dict[str, int] = {}
    consecutive = 0
    processed = 0
    aborted = False
    for sub in candidates:
        account_id = int(sub["account_id"])
        if account_id in done:
            continue
        if processed >= limit:
            break
        sub_id = sub.get("provider_subscription_id") or ""
        try:
            outcome, effective_at = await _move_one(stripe, db, account_id, sub_id, to_price)
            await db.record_rollout_item(rollout_id, account_id, subscription_id=sub_id,
                                         outcome=outcome, effective_at=effective_at)
            consecutive = 0
        except RolloutRefused:
            raise
        except Exception as e:  # one account's failure is recorded, the next is tried
            await db.record_rollout_item(rollout_id, account_id, subscription_id=sub_id,
                                         outcome="error", error=f"{type(e).__name__}: {e}")
            outcome = "error"
            consecutive += 1
            logger.warning("rollout %s: account %s failed (%s)", rollout_id, account_id, e)
        counts[outcome] = counts.get(outcome, 0) + 1
        done.add(account_id)
        processed += 1
        if consecutive >= MAX_CONSECUTIVE_ERRORS:
            aborted = True
            break
    remaining = sum(1 for s in candidates if int(s["account_id"]) not in done)
    if aborted or remaining == 0:
        totals = {i["outcome"]: 0 for i in await db.rollout_items(rollout_id)}
        for i in await db.rollout_items(rollout_id):
            totals[i["outcome"]] += 1
        await db.finish_price_rollout(rollout_id, aborted=aborted, summary=totals)
    return {"rollout_id": rollout_id, "tier": tier, "to_price_id": to_price, "to_cents": to_cents,
            "processed": processed, "counts": counts, "remaining": remaining, "aborted": aborted,
            "finished": aborted or remaining == 0}


async def _move_one(stripe, db, account_id: int, sub_id: str, to_price: str) -> tuple[str, str]:
    """One subscription: retrieve, decide, modify only the base item."""
    s = stripe.Subscription.retrieve(sub_id, expand=["items"])
    status = str(s.get("status") or "")
    if status in STRIPE_DEAD:
        # reconcile: our row said live, Stripe says not
        await db.update_subscription(account_id, status="canceled" if status == "canceled" else status)
        return "skipped_status", ""
    if s.get("schedule"):
        return "has_schedule", ""
    slots = _slots(s)
    base = slots["base"]
    if not base.get("id"):
        return "no_base_item", ""
    if (base.get("price_id") or "") == to_price:
        amt = _monthly_usd(base)
        await db.update_subscription(
            account_id, provider_base_item_id=base["id"], provider_base_price_id=to_price,
            **({"monthly_base_usd": amt} if amt is not None else {}))
        return "already", _period_end(s)
    modified = stripe.Subscription.modify(
        sub_id,
        items=[{"id": base["id"], "price": to_price, "quantity": 1}],
        proration_behavior="none",
        idempotency_key=f"rollout:{account_id}:{to_price}",
    )
    after = _slots(modified)["base"]
    if (after.get("price_id") or "") != to_price:
        raise RuntimeError("Stripe returned the subscription still on the old price")
    amt = _monthly_usd(after)
    await db.update_subscription(
        account_id, provider_base_item_id=after["id"] or base["id"], provider_base_price_id=to_price,
        **({"monthly_base_usd": amt} if amt is not None else {}))
    return "changed", _period_end(modified)
