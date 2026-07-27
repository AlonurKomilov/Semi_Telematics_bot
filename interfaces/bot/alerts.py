"""Fleet alert commands — alert settings, toggle, disable, history, pending."""

from datetime import datetime, timezone
from capabilities.localization.i18n import t

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from capabilities.permissions.roles import can
from capabilities.alerting.service import filter_alerts_by_access
from interfaces.bot.config import ALERT_INTERVAL
from interfaces.bot.state import get_user_company_codes, get_platform_db, get_tenant_db
from interfaces.bot.keyboards import main_menu_kb, alert_settings_kb
from interfaces.bot.helpers import _show
from interfaces.bot.auth import _require_registered


@_require_registered
async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show alert settings menu.

    First tap enables alerts_on (if off). Subsequent taps show the
    per-type settings keyboard so users can fine-tune categories.
    """
    user = context.user_data["_db_user"]
    if not can(user.role, "can_alerts_all") and not can(user.role, "can_alerts_vehicle"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    company_codes = await get_user_company_codes(user.account_id)

    if not user.alerts_on:
        # Enable alerts and show settings
        platform = get_platform_db()
        await platform.update_user(user.id, alerts_on=True)
        from capabilities.alerting.prefs_mirror import (
            mirror_alert_prefs_to_matrix)
        await mirror_alert_prefs_to_matrix(platform, user, alerts_on=True)
        user = await platform.get_user_by_telegram_id(user.telegram_id)
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
    from capabilities.alerting.relevance import alert_types_for_user
    _relevant = set(await alert_types_for_user(
        user.role, user.account_id,
        is_manager=bool(getattr(user, "is_manager", False)),
        is_primary_owner=bool(getattr(user, "is_primary_owner", False)),
    ))
    kb = alert_settings_kb(user, relevant=_relevant)
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_alert_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           alert_type: str):
    """Toggle a specific alert type on/off and refresh the settings menu."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_alerts_all") and not can(user.role, "can_alerts_vehicle"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    col = f"alert_{alert_type}"
    current = getattr(user, col, True)
    platform = get_platform_db()
    await platform.update_user(user.id, **{col: not current})
    # Delivery reads the notification matrix — mirror the toggle there
    # (legacy column stays as the keyboard's checkmark cache).
    from capabilities.alerting.prefs_mirror import mirror_alert_prefs_to_matrix
    await mirror_alert_prefs_to_matrix(
        platform, user, toggles={alert_type: not current})
    user = await platform.get_user_by_telegram_id(user.telegram_id)
    context.user_data["_db_user"] = user

    # Re-show the settings menu
    await cmd_alerts(update, context)


@_require_registered
async def cmd_ai_alert_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              ai_type: str):
    """Toggle proactive AI for a specific alert type and refresh settings."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_alerts_all") and not can(user.role, "can_alerts_vehicle"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    col = f"ai_{ai_type}"
    current = getattr(user, col, False)
    platform = get_platform_db()
    await platform.update_user(user.id, **{col: not current})
    user = await platform.get_user_by_telegram_id(user.telegram_id)
    context.user_data["_db_user"] = user

    # Re-show the AI onalerts screen
    from interfaces.bot.ai import cmd_ai_alerts
    await cmd_ai_alerts(update, context)


@_require_registered
async def cmd_alert_disable_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Turn off all alerts (sets alerts_on = 0)."""
    user = context.user_data["_db_user"]
    platform = get_platform_db()
    await platform.update_user(user.id, alerts_on=False)
    from capabilities.alerting.prefs_mirror import mirror_alert_prefs_to_matrix
    await mirror_alert_prefs_to_matrix(platform, user, alerts_on=False)
    user = await platform.get_user_by_telegram_id(user.telegram_id)
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
    if not can(user.role, "can_alerts_all") and not can(user.role, "can_alerts_vehicle"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    history = await tenant.get_alert_history(user.account_id, limit=20)
    # Filter for _own-permission users to their assigned truck only
    if can(user.role, "can_alerts_vehicle") and not can(user.role, "can_alerts_all"):
        trucks = [user.truck_num] if user.truck_num else []
        history = filter_alerts_by_access(history, trucks)
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

            line = f"  {status_icon} <b>{truck}</b> — {atype}"
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
    if not can(user.role, "can_alerts_all") and not can(user.role, "can_alerts_vehicle"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    pending = await tenant.get_pending_alerts(user.account_id)
    # Filter for _own-permission users to their assigned truck only
    if can(user.role, "can_alerts_vehicle") and not can(user.role, "can_alerts_all"):
        trucks = [user.truck_num] if user.truck_num else []
        pending = filter_alerts_by_access(pending, trucks)
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
            truck = a.get("vehicle_name", "?")
            atype = a.get("alert_type", "fault")
            lines.append(
                f"  🚛 <b>{truck}</b> — {atype}\n"
                f"     {mins_ago} min ago"
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
