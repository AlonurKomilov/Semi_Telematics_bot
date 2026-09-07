"""Bot /my_coaching command + ack callback — driver self-service."""

from __future__ import annotations

import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from features.coaching import service as svc
from features.coaching.service import CoachingDisabledError
from capabilities.permissions.roles import can
from capabilities.localization.i18n import t

logger = logging.getLogger(__name__)

ACK_CALLBACK_PREFIX = "coach_ack:"


async def _resolve_driver_id_for_user(account_id: int, user) -> Optional[str]:
    """Return the Samsara driver_id explicitly bound to this user
    (users.samsara_driver_id). Returns None if no admin has linked them.

    The previous most-recent-event-by-truck heuristic leaked other drivers'
    coaching assignments after vehicle reassignment, so this binding is now
    required to come from the admin-set users.samsara_driver_id column.
    """
    did = (getattr(user, "samsara_driver_id", None) or "").strip()
    return did or None


def _severity_emoji(severity: str) -> str:
    return {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(severity, "🟡")


async def cmd_my_coaching(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the caller their pending coaching assignments + ack buttons."""
    user = context.user_data.get("_db_user")
    if user is None:
        await update.message.reply_text(t("access.no_access"))
        return

    if not (can(user.role, "can_manage_coaching")
            or can(user.role, "can_view_coaching")):
        await update.message.reply_text(t("access.no_access"))
        return

    # No account-switch check here: the bot's ``can`` resolves the
    # account's permissions, and the resolver already masked the
    # coaching flags off wherever Coaching is not available.

    driver_id = await _resolve_driver_id_for_user(user.account_id, user)
    if not driver_id:
        await update.message.reply_text(t("coaching.my.no_driver_mapping"))
        return

    try:
        items = await svc.list_assignments(
            user.account_id, driver_id=driver_id, status="pending", limit=20,
        )
    except CoachingDisabledError:
        # The mask normally closes the door above; the service's own
        # answer is the belt for the window where the bot's ``can``
        # fell back to seeds (account priming failed).
        await update.message.reply_text(t("coaching.disabled_for_account"))
        return
    if not items:
        await update.message.reply_text(t("coaching.my.no_pending"))
        return

    lines = ["<b>" + t("coaching.my.title") + "</b>", ""]
    keyboard: list[list[InlineKeyboardButton]] = []
    for a in items:
        topic = a.get("topic_key", "")
        sev = a.get("severity", "medium")
        reason = a.get("reason", "")
        lines.append(
            f"{_severity_emoji(sev)} <b>{topic}</b>"
            + (f" — {reason}" if reason else "")
        )
        keyboard.append([
            InlineKeyboardButton(
                t("coaching.my.ack_button", topic=topic),
                callback_data=f"{ACK_CALLBACK_PREFIX}{a['id']}",
            ),
        ])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cb_coaching_ack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback for the inline 'acknowledge' button."""
    query = update.callback_query
    if query is None or not query.data:
        return
    await query.answer()
    user = context.user_data.get("_db_user")
    if user is None:
        return

    try:
        assignment_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        return

    driver_id = await _resolve_driver_id_for_user(user.account_id, user)
    if not driver_id:
        await query.edit_message_text(t("coaching.my.no_driver_mapping"))
        return

    try:
        ok = await svc.acknowledge(
            user.account_id, assignment_id,
            driver_id=driver_id, user_id=user.telegram_id,
        )
    except CoachingDisabledError:
        await query.edit_message_text(t("coaching.disabled_for_account"))
        return

    if not ok:
        await query.edit_message_text(t("coaching.my.ack_failed"))
        return

    await query.edit_message_text(t("coaching.my.ack_done"))
