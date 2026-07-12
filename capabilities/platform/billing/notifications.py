"""Billing-event Telegram notifications.

Sends short, action-oriented messages to billing admins (Owner /
Admin roles) when a billing state transition happens:

  - checkout completed   → "you're on the Pro tier now"
  - payment failed       → "your card was declined, update by X to
                            keep service active"
  - payment recovered    → "thanks, you're back to active"
  - comp granted         → "welcome to your complimentary plan"
  - comp expiring soon   → "your comp expires in N days"
  - comp expired         → "comp window ended, you're billed normally
                            from now"

The bot used is the per-account bot registered in
``infra.bot_registry``; for accounts without a registered bot (rare
edge case during cutover) the function logs and no-ops rather than
raising — billing actions shouldn't fail just because the
notification channel is offline.

Email is currently delivered by Stripe itself via the ``billing_email``
on the Customer object (receipts + dunning + payment-method-update
links) — that's wired in by ``update_billing_email`` in the Stripe
provider.  For 4truck-side events (comp grants), Telegram is the
primary channel and the ``comp_account_history`` table is the formal
record.  A first-class SMTP layer can be added later without touching
this module's call sites.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from adapters.storage.models import Role
from infra import observability as _obs

logger = logging.getLogger(__name__)


_BILLING_ADMIN_ROLES = (Role.OWNER, Role.ADMIN)


async def _billing_admin_telegram_ids(account_id: int) -> list[int]:
    """Return the telegram_ids of users who should receive billing alerts.

    Owner + Admin are the roles allowed to ``can_manage_billing``.  We
    don't include the broader "fleet" / "safety" personas — they don't
    have permission to fix the underlying issue (update a card,
    contact 4truck support), so paging them is just noise.
    """
    from infra import platform as _ip
    try:
        platform_db = _ip.get_router().platform
    except Exception:
        logger.exception("notifications: platform DB unavailable")
        return []
    try:
        users = await platform_db.list_account_users(account_id)
    except Exception:
        logger.exception("notifications: list_account_users failed acct=%d", account_id)
        return []
    return [
        u.telegram_id for u in users
        if u.role in _BILLING_ADMIN_ROLES and u.telegram_id
    ]


async def _send_to_admins(account_id: int, text: str) -> int:
    """Send one HTML-formatted message to every billing admin.

    Returns the number of admins that received the message
    successfully.  Catches per-recipient failures so a single blocked
    chat / unknown_user_id doesn't kill the broadcast.
    """
    from infra.bot_registry import get_app_for_account
    bot_app = get_app_for_account(account_id)
    if not bot_app:
        logger.info(
            "notifications: no bot registered for acct=%d; skipping send",
            account_id,
        )
        return 0
    recipients = await _billing_admin_telegram_ids(account_id)
    if not recipients:
        logger.info(
            "notifications: no billing admins for acct=%d; skipping send",
            account_id,
        )
        return 0
    sent = 0
    for chat_id in recipients:
        try:
            await bot_app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception:
            # Bad chat_id, user blocked the bot, network blip — log
            # and move on.  The action that triggered this notice
            # (Stripe webhook, comp grant) already succeeded; the
            # notification channel is best-effort.
            logger.warning(
                "notifications: send failed acct=%d chat=%d",
                account_id, chat_id, exc_info=True,
            )
    return sent


def _money(cents: int) -> str:
    """Render a cents-int as ``$dd.cc`` for user-facing copy."""
    return f"${cents / 100:.2f}"


def _portal_link(account_id: int) -> str:
    """Customer-portal entry point users tap from the dashboard.

    Kept literal here because the dashboard route is stable; if we ever
    rename it, this single string updates every notification body.
    """
    return "/dashboard/billing"


# ── Payment events ──────────────────────────────────────────────


async def _send_and_record(kind: str, account_id: int, text: str) -> int:
    """Send + record the metric in one call.

    Recording the count (rather than 1 per send) lets dashboards
    answer "how many admins were paged for X in the last week" rather
    than just "how many events happened".  When ``count`` is 0 (no
    bot, no admins) we skip the metric so dashboards don't look like
    we sent something when we silently dropped.
    """
    count = await _send_to_admins(account_id, text)
    _obs.record_billing_notification(kind, "telegram", count)
    return count


async def notify_checkout_complete(account_id: int, tier: str) -> int:
    """Fired by the ``checkout.session.completed`` webhook handler."""
    text = (
        "<b>💳 Subscription active</b>\n\n"
        f"Your account is now on the <b>{tier.capitalize()}</b> plan.\n"
        "We'll send invoice receipts to the email on file.\n\n"
        f"Manage billing: <code>{_portal_link(account_id)}</code>"
    )
    return await _send_and_record("checkout_complete", account_id, text)


async def notify_payment_failed(
    account_id: int, amount_due_cents: int, hosted_invoice_url: str = "",
) -> int:
    """Fired by the ``invoice.payment_failed`` webhook handler."""
    amount = _money(amount_due_cents) if amount_due_cents else ""
    text = (
        "<b>⚠️ Payment failed</b>\n\n"
        f"Your latest invoice{' for ' + amount if amount else ''} "
        "couldn't be charged.  Service stays active during the grace "
        "window, but we'll suspend non-billing API access if the payment "
        "isn't recovered.\n\n"
        "Update your card or pay manually:\n"
    )
    if hosted_invoice_url:
        text += f"• Invoice: {hosted_invoice_url}\n"
    text += f"• Subscription page: <code>{_portal_link(account_id)}</code>"
    return await _send_and_record("payment_failed", account_id, text)


async def notify_payment_recovered(account_id: int, amount_paid_cents: int) -> int:
    """Fired when ``invoice.payment_succeeded`` lifts the past_due flag."""
    amount = _money(amount_paid_cents) if amount_paid_cents else ""
    text = (
        "<b>✅ Payment recovered</b>\n\n"
        f"We received your payment{' of ' + amount if amount else ''} "
        "and your account is back to <b>active</b>.\n"
        "Thanks for keeping your fleet on 4truck!"
    )
    return await _send_and_record("payment_recovered", account_id, text)


# ── Comp lifecycle ──────────────────────────────────────────────


async def notify_comp_granted(
    account_id: int, expires_at: str, reason: str = "",
) -> int:
    """Fired from ``BillingMixin.grant_comp`` after the row is written."""
    nice_date = _human_date(expires_at)
    reason_line = f"\n📝 {reason}" if reason else ""
    text = (
        "<b>🎁 Complimentary access granted</b>\n\n"
        "4truck is covering your subscription at no charge through "
        f"<b>{nice_date}</b>.\n"
        "You'll see the would-be bill on your Subscription page with a "
        "100% Special Discount line — that's what we're covering for "
        f"you.{reason_line}\n\n"
        f"Subscription page: <code>{_portal_link(account_id)}</code>"
    )
    return await _send_and_record("comp_granted", account_id, text)


async def notify_comp_expiring(
    account_id: int, expires_at: str, days_left: int,
) -> int:
    """Reminder fired N / 7 / 3 / 1 days before expiry."""
    nice_date = _human_date(expires_at)
    day_word = "day" if days_left == 1 else "days"
    text = (
        f"<b>⏳ Complimentary plan expires in {days_left} {day_word}</b>\n\n"
        f"Your comp window ends on <b>{nice_date}</b>.  After that you'll "
        "be billed normally based on your tier + active-vehicle count.\n\n"
        "If you'd like to extend or move to a paid plan, reach out to your "
        "4truck contact or pick a plan from the Subscription page:\n"
        f"<code>{_portal_link(account_id)}</code>"
    )
    return await _send_and_record("comp_expiring", account_id, text)


async def notify_comp_expired(account_id: int) -> int:
    """Fired when ``expire_lapsed_comps`` flips an account back to paid."""
    text = (
        "<b>📅 Complimentary period ended</b>\n\n"
        "Your comp window has closed; your account is now billed normally "
        "based on tier + active vehicles in the last 3 days.\n\n"
        "If this was an error or you'd like another comp window, contact "
        "4truck support.\n"
        f"Subscription page: <code>{_portal_link(account_id)}</code>"
    )
    return await _send_and_record("comp_expired", account_id, text)


# ── Helpers ─────────────────────────────────────────────────────


def _human_date(iso: str) -> str:
    """Render an ISO-8601 timestamp as ``Mon DD, YYYY``.

    Tolerant of empty / malformed input so a bad row never crashes a
    notification — falls back to the raw string so the recipient sees
    something rather than nothing.
    """
    if not iso:
        return "(unknown)"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y")
    except (TypeError, ValueError):
        return iso


def days_until(iso: str, now: datetime | None = None) -> int | None:
    """Whole days from ``now`` until ``iso``.  Negative means past.

    Used by the comp-expiry sweep to bucket reminders into 7 / 3 / 1
    day windows.  ``None`` when the input is unparseable.
    """
    if not iso:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        target = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (target - now).days


REMINDER_BUCKETS: tuple[int, ...] = (7, 3, 1)


def reminder_bucket(days_left: int) -> int | None:
    """Map ``days_left`` to the largest reminder bucket it hits.

    A comp with 6 days left → fires the 3-day bucket reminder (not the
    7-day, since we passed that already).  A comp with 9 days left →
    fires no reminder.  Returns None when no bucket matches.
    """
    if days_left is None or days_left < 0:
        return None
    for b in REMINDER_BUCKETS:
        if days_left == b:
            return b
    return None


__all__ = [
    "notify_checkout_complete",
    "notify_payment_failed",
    "notify_payment_recovered",
    "notify_comp_granted",
    "notify_comp_expiring",
    "notify_comp_expired",
    "days_until",
    "reminder_bucket",
    "REMINDER_BUCKETS",
]
