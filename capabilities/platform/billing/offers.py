"""The one rule for who may buy a hidden plan, and what an offer sends.

A plan is on the customer page because it is public, or because it was
offered to that account after a Contact-Sales conversation.  Both
providers' checkouts, the customer's plan list and the plan-switch path
ask this module the same question, so "hidden" can never mean
"unbuyable" in one place and "buyable" in another.

An offer opens a door.  It does not move a tier or touch a subscription;
what the customer pays is decided when they check out, and from then on
the subscription row is the truth (adapters/storage/plan_offers.py).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


async def purchasable_plan(db, tier: str, account_id: int) -> Optional[dict]:
    """The plan row when *account_id* may buy *tier*, else None.

    Public plans are for everyone.  A hidden plan is for the accounts it
    was offered to and nobody else — an account asking for a hidden
    plan by name, without an offer, gets the same answer as for a plan
    that does not exist, so a customer cannot learn what else is sold.
    """
    plan = await db.get_plan(tier)
    if plan is None:
        return None
    if plan.get("retired"):
        # Retired means closed to NEW accounts by every route.  Answered
        # here rather than at each caller because this is the one
        # question checkout, the customer's plan list and the switch
        # path all ask — a plan that were buyable in one of them and not
        # the others is the bug this module exists to prevent.  An
        # account already on it is untouched: nothing about their
        # subscription asks this.
        return None
    if plan["public"]:
        return plan
    if await db.plan_offered_to(tier, int(account_id)):
        return plan
    return None


def offer_refusal(plan: dict, all_plans: list[dict], *, provider: str) -> str:
    """Why *plan* cannot be offered right now — '' when it can.

    Two things must be true before a customer is sent to pay for it.
    It must be hidden: offering a public plan is meaningless, and an
    operator who tries is usually looking for the visibility switch.
    And under Stripe it must have a Stripe Price of its own: checkout
    needs one to charge, and the webhook reads the tier BACK from the
    Price, so a Price shared with another plan would land the customer
    on that other plan's terms.
    """
    if plan.get("retired"):
        return ("This plan is retired — it is closed to new accounts. Un-retire it first "
                "if you mean to sell it again.")
    if plan.get("public"):
        return "This plan is already on every customer's Billing page — nothing to offer."
    if int(plan.get("price_monthly_cents") or 0) <= 0:
        # an offer is a thing to PAY for; a plan given away is a comp,
        # granted on the Accounts page — sending someone to checkout
        # for $0 would say "Create the Stripe price" for a price that
        # does not exist
        return "This plan has no price. Set one first — a plan given at no charge is a comp, granted from the Accounts page."
    if provider != "stripe":
        return ""
    price = (plan.get("stripe_price_id") or "").strip()
    if not price:
        return "Create the Stripe price on this plan first, so checkout has something to charge."
    shared = [p["tier"] for p in all_plans
              if p["tier"] != plan["tier"] and (p.get("stripe_price_id") or "").strip() == price]
    if shared:
        return (f"This plan shares its Stripe price with '{shared[0]}'; a private plan needs "
                "a price of its own, or a payment would land on the other plan.")
    return ""


# ── telling the customer ────────────────────────────────────────────


def _dashboard_url() -> str:
    from capabilities.platform.billing.urls import return_origin
    return f"{return_origin()}/billing"


def email_offer(to: str, *, plan: dict, account_name: str, case_number: str = "") -> bool:
    """The email that says the terms are ready to pay for.

    Sent to the address on the case when there is one, because that is
    the person we spoke to.  Best-effort: the offer is already recorded
    by the time this runs, and a mail system that is down must not undo
    it.
    """
    to = (to or "").strip()
    if not to:
        return False
    from capabilities.platform.billing.plan_requests import sales_inbox
    reply_to = sales_inbox() or (os.getenv("SMTP_FROM_REPLY_TO") or "").strip()
    cents = int(plan.get("price_monthly_cents") or 0)
    price = f"${cents // 100}" if cents % 100 == 0 else f"${cents / 100:.2f}"
    base = int(plan.get("base_vehicles") or 0)
    extra = int(plan.get("extra_vehicle_cents") or 0)
    terms = [f"{plan.get('label')}: {price} a month"]
    if base:
        terms.append(f"includes {base} truck{'' if base == 1 else 's'}")
    if extra:
        terms.append(f"each truck beyond that ${extra / 100:.2f} a month")
    case_line = f"This is case {case_number}." if case_number else ""
    body = "\n".join(line for line in [
        f"Hello {account_name},",
        "",
        f"The plan we discussed is ready for you on 4truck. {case_line}".strip(),
        "",
        "  " + " · ".join(terms),
        "",
        "It is on your Billing page and only your account can see it:",
        f"  {_dashboard_url()}",
        "",
        "Open it and press Upgrade to subscribe. Reply to this email if",
        "anything about the terms is not what you expected.",
    ])
    try:
        from capabilities.email.smtp import send_email
        return bool(send_email(
            to=to,
            subject=(f"[{case_number}] " if case_number else "") + f"Your {plan.get('label')} plan is ready",
            body=body,
            reply_to=reply_to or None,
        ))
    except Exception:
        logger.exception("plan offer %s could not be emailed to %s", plan.get("tier"), to)
        return False


async def telegram_offer(account_id: int, *, plan: dict, case_number: str = "") -> int:
    """Telegram to the account's billing admins, through the same path
    every other billing notice takes (and the same in-app record)."""
    from capabilities.platform.billing.notifications import _send_and_record
    cents = int(plan.get("price_monthly_cents") or 0)
    price = f"${cents // 100}" if cents % 100 == 0 else f"${cents / 100:.2f}"
    case = f" (case <code>{case_number}</code>)" if case_number else ""
    text = (
        "<b>📄 Your plan is ready</b>\n\n"
        f"The <b>{plan.get('label')}</b> plan we discussed{case} is on your Billing page "
        f"at <b>{price}/month</b>. Only your account can see it.\n\n"
        "Open Billing and press Upgrade to subscribe."
    )
    return await _send_and_record("plan_offered", account_id, text)
