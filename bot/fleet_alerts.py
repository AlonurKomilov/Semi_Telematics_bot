"""Fleet alert commands — alert settings, toggle, disable, history, pending."""

from datetime import datetime, timezone
from bot.i18n import t

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from permissions import can
from bot.config import db, ALERT_INTERVAL, get_user_company_codes
from bot.keyboards import main_menu_kb, alert_settings_kb
from bot.helpers import _show
from bot.auth import _require_registered


@_require_registered
async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show alert settings menu.

    First tap enables alerts_on (if off). Subsequent taps show the
    per-type settings keyboard so users can fine-tune categories.
    """
    user = context.user_data["_db_user"]
    if not can(user.role, "can_alerts_all") and not can(user.role, "can_alerts_own"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    company_codes = await get_user_company_codes(user.account_id)

    if not user.alerts_on:
        # Enable alerts and show settings
        await db.update_user(user.id, alerts_on=True)
        user = await db.get_user_by_telegram_id(user.telegram_id)
        context.user_data["_db_user"] = user

    sep = t("alert_format.separator")
    text = (
        f"{sep}\n"
        f"  {t('alert_settings.header')}\n"
        f"{sep}\n"
        "\n"
        f"  {t('alert_settings.check_interval').format(minutes=ALERT_INTERVAL)}\n"
        f"  {t('alert_settings.company_count').format(count=len(company_codes))}\n"
        "\n"
        f"  {t('alert_settings.tap_toggle')}"
    )
    kb = alert_settings_kb(user)
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_alert_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           alert_type: str):
    """Toggle a specific alert type on/off and refresh the settings menu."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_alerts_all") and not can(user.role, "can_alerts_own"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    col = f"alert_{alert_type}"
    current = getattr(user, col, True)
    await db.update_user(user.id, **{col: not current})
    user = await db.get_user_by_telegram_id(user.telegram_id)
    context.user_data["_db_user"] = user

    # Re-show the settings menu
    await cmd_alerts(update, context)


@_require_registered
async def cmd_ai_alert_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              ai_type: str):
    """Toggle proactive AI for a specific alert type and refresh settings."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_alerts_all") and not can(user.role, "can_alerts_own"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    col = f"ai_{ai_type}"
    current = getattr(user, col, False)
    await db.update_user(user.id, **{col: not current})
    user = await db.get_user_by_telegram_id(user.telegram_id)
    context.user_data["_db_user"] = user

    # Re-show the AI onalerts screen
    from bot.ai import cmd_ai_alerts
    await cmd_ai_alerts(update, context)


@_require_registered
async def cmd_alert_disable_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Turn off all alerts (sets alerts_on = 0)."""
    user = context.user_data["_db_user"]
    await db.update_user(user.id, alerts_on=False)
    user = await db.get_user_by_telegram_id(user.telegram_id)
    context.user_data["_db_user"] = user

    company_codes = await get_user_company_codes(user.account_id)
    sep = t("alert_format.separator")
    text = (
        f"{sep}\n"
        f"  {t('alerts.disabled_title')}\n"
        f"{sep}\n"
        "\n"
        f"  {t('alerts.disabled_msg')}\n"
        f"  {t('alerts.disabled_hint')}"
    )
    kb = main_menu_kb(user.role, company_codes)
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_alert_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show alert acknowledgment history for the account."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_alerts_all") and not can(user.role, "can_alerts_own"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    history = await db.get_alert_history(user.account_id, limit=20)
    if not history:
        sep = t("alert_format.separator")
        text = (
            f"{sep}\n"
            f"  {t('alerts.history_title')}\n"
            f"{sep}\n"
            f"\n  {t('alerts.history_empty')}"
        )
    else:
        sep = t("alert_format.separator")
        lines = [
            sep,
            f"  {t('alerts.history_title')}",
            sep,
            f"\n  {t('alerts.history_last').format(count=len(history))}\n",
        ]
        for a in history:
            ts = a["created_at"][:16].replace("T", " ")
            status = a.get("status", "active")
            status_icon = {"active": "🔴", "acknowledged": "✅", "expired": "⏰"}.get(status, "❓")
            truck = a.get("vehicle_name", "?")
            atype = a.get("alert_type", "fault")
            esc_level = a.get("escalation_level", 0)

            line = f"  {status_icon} <b>{truck}</b> — {atype}"
            if esc_level > 0:
                line += f" {t('alerts.history_escalation').format(level=esc_level)}"
            line += f"\n     <code>{ts}</code>"

            if status == "acknowledged" and a.get("acknowledged_at"):
                ack_ts = a["acknowledged_at"][:16].replace("T", " ")
                line += f" {t('alerts.history_acked').format(time=ack_ts)}"
            elif status == "expired":
                line += f" {t('alerts.history_expired_label')}"

            lines.append(line)

        text = "\n".join(lines)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t("alerts.history_btn_settings"), callback_data="cmd_alerts")],
        [InlineKeyboardButton(t("menu.back_main"), callback_data="cmd_menu")],
    ])
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_pending_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show currently active (unacknowledged) alerts for the account."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_alerts_all") and not can(user.role, "can_alerts_own"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    pending = await db.get_pending_alerts(user.account_id)
    if not pending:
        sep = t("alert_format.separator")
        text = (
            f"{sep}\n"
            f"  {t('alerts.no_pending_title')}\n"
            f"{sep}\n"
            f"\n  {t('alerts.no_pending_msg')}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t("alerts.history_btn"), callback_data="cmd_alert_history")],
            [InlineKeyboardButton(t("menu.back_main"), callback_data="cmd_menu")],
        ])
    else:
        now = datetime.now(timezone.utc)
        sep = t("alert_format.separator")
        lines = [
            sep,
            f"  {t('alerts.pending_title')}",
            sep,
            f"\n  {t('alerts.pending_count').format(count=len(pending))}\n",
        ]
        ack_buttons = []
        for a in pending[:10]:
            mins_ago = int((now - datetime.fromisoformat(a["created_at"])).total_seconds() / 60)
            esc = a.get("escalation_level", 0)
            truck = a.get("vehicle_name", "?")
            atype = a.get("alert_type", "fault")
            lines.append(
                f"  🚛 <b>{truck}</b> — {atype}\n"
                f"     {t('alerts.pending_detail').format(mins=mins_ago, level=esc)}"
            )
            ack_buttons.append([InlineKeyboardButton(
                t("alerts.ack_btn").format(label=truck),
                callback_data=f"ack_alert_{a['id']}"
            )])

        if len(pending) > 10:
            lines.append(f"\n  <i>{t('alerts.pending_more').format(count=len(pending) - 10)}</i>")

        text = "\n".join(lines)
        ack_buttons.append([InlineKeyboardButton(t("alerts.history_btn"), callback_data="cmd_alert_history")])
        ack_buttons.append([InlineKeyboardButton(t("menu.back_main"), callback_data="cmd_menu")])
        kb = InlineKeyboardMarkup(ack_buttons)

    await _show(update, context, [text], keyboard=kb)
