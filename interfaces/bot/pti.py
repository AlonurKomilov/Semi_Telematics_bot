"""Bot-side PTI surface.

Two responsibilities:

  1. ``/pti`` command — deep-link to the Mini App.  Drivers complete
     inspections in the Mini App (per the locked design), the bot
     command just opens that page with the right context.

  2. Notification helpers — called BY the PTI cron jobs in
     ``capabilities/pti/jobs.py`` to fan out reminders + digests.
     The cron module lazy-imports these so the cron loop still runs
     if this module is missing during a phased rollout.

Cadence (refined from the original plan to cut driver noise):

  * Driver gets 24h-before reminder           (one ping)
  * Driver gets "due today" + "overdue 1 day" merged into a single
    daily ping with throttle, so a long-overdue PTI doesn't spam the
    driver every hour.
  * Fleet gets an *instant* ping when a driver submits — catches
    OOS / urgent defects in real time instead of waiting for the
    daily 09:00 digest.
  * Fleet still gets the 09:00 digest (now 09:05 to avoid colliding
    with maintenance + doc-expiry digests that also fire at 09:00).
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from adapters.storage import Role
from infra.bot_registry import get_app_for_account
from infra.services import get_tenant_db, get_platform_db
from interfaces.bot.config import WEBAPP_URL
from features.pti.formatter import (
    inspection_summary,
    reminder_body,
)

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────


def _pti_deeplink_kb(label: str = "Open PTI") -> InlineKeyboardMarkup:
    """Inline keyboard with a single Web App button that opens the
    Mini App at the ``#pti`` hash route.  When ``WEBAPP_URL`` is unset
    (dev / staging without a hosted miniapp), returns an empty
    keyboard — caller decides whether to send the message anyway."""
    if not WEBAPP_URL:
        return InlineKeyboardMarkup([])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, web_app=WebAppInfo(url=f"{WEBAPP_URL}#pti")),
    ]])


def _dashboard_link_kb(label: str, path: str) -> InlineKeyboardMarkup:
    """For fleet pings — link to the dashboard rather than the Mini
    App (Mini App is driver-only)."""
    from infra.config import APP_BASE_URL
    url = f"{APP_BASE_URL.rstrip('/')}/dashboard{path}"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, url=url),
    ]])


async def _send_to_telegram(account_id: int, telegram_id: int, text: str,
                            keyboard: Optional[InlineKeyboardMarkup] = None) -> bool:
    """Best-effort Telegram send.  Returns False on error (no raise)
    so a single bad chat doesn't sink the rest of a fan-out loop."""
    bot_app = get_app_for_account(account_id)
    if not bot_app:
        logger.debug("pti: no bot for acct=%d, skipping send to %d",
                     account_id, telegram_id)
        return False
    try:
        await bot_app.bot.send_message(
            chat_id=telegram_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
        return True
    except Exception as e:
        # Common cases: user blocked the bot, chat doesn't exist,
        # rate-limited.  Log + move on.
        logger.debug("pti send failed acct=%d uid=%d: %s",
                     account_id, telegram_id, e)
        return False


# ── /pti command ──────────────────────────────────────────────────


async def cmd_pti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Driver-facing ``/pti`` command — deep-link button to the Mini
    App.  Fleet/admin users see the same button but it points them at
    the dashboard instead, since the Mini App's PTI tab is the
    driver capture flow.
    """
    if not update.effective_chat or update.effective_chat.type != "private":
        # PTI is personal — don't surface it in group chats.
        return

    tg_id = update.effective_user.id if update.effective_user else None
    if tg_id is None:
        return

    platform_db = get_platform_db()
    user = await platform_db.get_user_by_telegram_id(tg_id)
    if user is None or not user.is_active:
        # Not registered — the global "/start to register" message
        # already covers this; silent return keeps the bot tidy.
        return

    if user.role == Role.DRIVER:
        text = (
            "📋 <b>Pre-Trip Inspection</b>\n\n"
            "Tap the button below to open your inspection in the Mini App."
        )
        await update.effective_chat.send_message(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_pti_deeplink_kb("Open PTI"),
        )
    else:
        # Fleet/safety/owner/admin — point them at the dashboard
        # review page (fleet doesn't do driver-side PTI).
        text = (
            "📋 <b>Inspections (PTI)</b>\n\n"
            "Review submitted PTIs in the dashboard."
        )
        await update.effective_chat.send_message(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_dashboard_link_kb("Open Inspections", "/inspections"),
        )


# ── Driver-facing pings ───────────────────────────────────────────


async def notify_due_soon(account_id: int, inspections: list[dict]) -> int:
    """Fan out a 24h-before reminder to each driver whose inspection
    is in the due-soon window.

    Throttle: skips rows that already have ``alerted_at`` set so the
    hourly cron doesn't re-ping the same driver every tick.  Stamps
    ``alerted_at`` after a successful send.

    Returns the count of drivers actually pinged.
    """
    tenant_db = await get_tenant_db(account_id)
    if tenant_db is None:
        return 0

    sent = 0
    for ins in inspections:
        # ``alerted_at`` lives on driver_inspections (column was
        # carried over from the maintenance throttle pattern).  When
        # present, we've already pinged this row in this cycle.
        if ins.get("alerted_at"):
            continue
        user = await get_platform_db().get_user_by_id(int(ins["user_id"]))
        if user is None or not user.telegram_id:
            continue
        text = (
            "📋 <b>Weekly PTI reminder</b>\n\n"
            f"{reminder_body(ins)}"
        )
        ok = await _send_to_telegram(
            account_id, int(user.telegram_id), text,
            keyboard=_pti_deeplink_kb("Start PTI"),
        )
        if ok:
            # Reuse the existing storage helper from maintenance —
            # both modules share the same ``alerted_at`` semantics
            # (one ping per cycle until the row's status changes).
            await tenant_db.mark_inspection_alerted(int(ins["id"]))
            sent += 1
    logger.info("pti notify_due_soon acct=%d pinged=%d / candidates=%d",
                account_id, sent, len(inspections))
    return sent


# ── Fleet-facing pings ────────────────────────────────────────────


async def notify_overdue_to_admin(account_id: int,
                                   overdue: list[dict]) -> int:
    """Daily admin escalation — sends ONE message per account listing
    every overdue PTI rather than one ping per row.  Cuts noise when
    a fleet has 10 drivers all overdue at once."""
    if not overdue:
        return 0
    lines: list[str] = []
    for ins in overdue[:25]:  # cap so the message stays under TG limit
        lines.append(f"  • {inspection_summary(ins, include_status=False)}")
    if len(overdue) > 25:
        lines.append(f"  … and {len(overdue) - 25} more")
    text = (
        f"🛑 <b>{len(overdue)} overdue PTI{'s' if len(overdue) != 1 else ''}</b>\n\n"
        + "\n".join(lines)
    )
    return await _broadcast_to_fleet(
        account_id, text,
        keyboard=_dashboard_link_kb("Open Inspections", "/inspections?status=overdue"),
    )


async def send_fleet_digest(account_id: int, counts: dict, line: str) -> int:
    """Daily 09:05 fleet digest — single message summarising the
    review queue + overdue + needs-service buckets."""
    text = (
        "📋 <b>Daily PTI digest</b>\n\n"
        f"{line}"
    )
    return await _broadcast_to_fleet(
        account_id, text,
        keyboard=_dashboard_link_kb("Open Inspections", "/inspections"),
    )


async def notify_submission_to_fleet(account_id: int,
                                      inspection: dict) -> int:
    """Instant ping when a driver submits a PTI.  Surfaces urgent
    defects (OOS / Needs Service) in real time instead of waiting
    for the daily digest.

    Routed through the same fleet broadcast as digests, so per-fleet
    admin teams self-determine who's on review duty by who's
    subscribed.
    """
    vname = inspection.get("vehicle_name", "—")
    defects = int(inspection.get("defects_count") or 0)
    oos = bool(inspection.get("has_oos_defect"))
    severity = "🛑 <b>OOS</b>" if oos else ("⚠️" if defects else "✅")
    detail = (
        f"<b>{defects} defect{'s' if defects != 1 else ''}</b>"
        if defects else "no defects"
    )
    text = (
        f"{severity} <b>PTI submitted · Truck {vname}</b>\n\n"
        f"{detail}{' · Out of service' if oos else ''}\n"
        "Tap to review."
    )
    return await _broadcast_to_fleet(
        account_id, text,
        keyboard=_dashboard_link_kb(
            "Review", f"/inspections?id={inspection.get('id', '')}",
        ),
    )


# ── Fleet broadcast helper ────────────────────────────────────────


async def _broadcast_to_fleet(account_id: int, text: str,
                              keyboard: Optional[InlineKeyboardMarkup] = None) -> int:
    """Send to every fleet / safety / dispatch / owner / admin user in
    the account who has the bot subscribed.  Returns count delivered."""
    platform_db = get_platform_db()
    try:
        admins = await platform_db.get_account_admins(account_id)
    except Exception:
        logger.exception("pti broadcast: list-admins failed acct=%d", account_id)
        return 0
    # Roles eligible for review pings — broader than just "admin" so
    # fleet/safety managers see them too.
    eligible_roles = {Role.OWNER, Role.ADMIN, Role.FLEET, Role.SAFETY, Role.DISPATCH}
    targets: list[int] = []
    for u in admins:
        if u.role in eligible_roles and u.telegram_id:
            targets.append(int(u.telegram_id))
    if not targets:
        # ``get_account_admins`` returns only owner/admin by definition
        # — fall back to a broader users query to reach fleet/safety
        # subscribers too.
        try:
            all_users = await platform_db.list_account_users(account_id)
        except Exception:
            return 0
        for u in all_users:
            if u.role in eligible_roles and u.telegram_id:
                targets.append(int(u.telegram_id))

    sent = 0
    for tid in set(targets):  # dedup
        if await _send_to_telegram(account_id, tid, text, keyboard):
            sent += 1
    logger.info("pti broadcast acct=%d delivered=%d / targets=%d",
                account_id, sent, len(set(targets)))
    return sent
