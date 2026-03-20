"""Fleet alert commands — alert settings, toggle, disable, history, pending."""

from datetime import datetime, timezone

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
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    company_codes = await get_user_company_codes(user.account_id)

    if not user.alerts_on:
        # Enable alerts and show settings
        await db.update_user(user.id, alerts_on=True)
        user = await db.get_user_by_telegram_id(user.telegram_id)
        context.user_data["_db_user"] = user

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  🔔  <b>ALERT SETTINGS</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  Checking every {ALERT_INTERVAL} min\n"
        f"  across {len(company_codes)} {'companies' if len(company_codes) != 1 else 'company'}.\n"
        "\n"
        "  Tap a category to toggle it:"
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
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    col = f"alert_{alert_type}"
    current = getattr(user, col, True)
    await db.update_user(user.id, **{col: not current})
    user = await db.get_user_by_telegram_id(user.telegram_id)
    context.user_data["_db_user"] = user

    # Re-show the settings menu
    await cmd_alerts(update, context)


@_require_registered
async def cmd_alert_disable_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Turn off all alerts (sets alerts_on = 0)."""
    user = context.user_data["_db_user"]
    await db.update_user(user.id, alerts_on=False)
    user = await db.get_user_by_telegram_id(user.telegram_id)
    context.user_data["_db_user"] = user

    company_codes = await get_user_company_codes(user.account_id)
    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  🔕  <b>ALERTS OFF</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "  All auto-notifications disabled.\n"
        "  Tap 🔔 Alerts to re-enable."
    )
    kb = main_menu_kb(user.role, company_codes)
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_alert_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show alert acknowledgment history for the account."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_alerts_all") and not can(user.role, "can_alerts_own"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    history = await db.get_alert_history(user.account_id, limit=20)
    if not history:
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📜  <b>ALERT HISTORY</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n  No alert history yet."
        )
    else:
        lines = [
            "━━━━━━━━━━━━━━━━━━━",
            "  📜  <b>ALERT HISTORY</b>",
            "━━━━━━━━━━━━━━━━━━━",
            f"\n  Last {len(history)} alerts:\n",
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
                line += f" (esc: {esc_level})"
            line += f"\n     <code>{ts}</code>"

            if status == "acknowledged" and a.get("acknowledged_at"):
                ack_ts = a["acknowledged_at"][:16].replace("T", " ")
                line += f" → acked {ack_ts}"
            elif status == "expired":
                line += " → expired"

            lines.append(line)

        text = "\n".join(lines)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 Alert Settings", callback_data="cmd_alerts")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_pending_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show currently active (unacknowledged) alerts for the account."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_alerts_all") and not can(user.role, "can_alerts_own"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    pending = await db.get_pending_alerts(user.account_id)
    if not pending:
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ✅  <b>NO PENDING ALERTS</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n  All alerts have been acknowledged\n"
            "  or expired. No action needed."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📜 Alert History", callback_data="cmd_alert_history")],
            [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
        ])
    else:
        now = datetime.now(timezone.utc)
        lines = [
            "━━━━━━━━━━━━━━━━━━━",
            "  🔴  <b>PENDING ALERTS</b>",
            "━━━━━━━━━━━━━━━━━━━",
            f"\n  {len(pending)} unacknowledged alert{'s' if len(pending) != 1 else ''}:\n",
        ]
        ack_buttons = []
        for a in pending[:10]:
            mins_ago = int((now - datetime.fromisoformat(a["created_at"])).total_seconds() / 60)
            esc = a.get("escalation_level", 0)
            truck = a.get("vehicle_name", "?")
            atype = a.get("alert_type", "fault")
            lines.append(
                f"  🚛 <b>{truck}</b> — {atype}\n"
                f"     {mins_ago} min ago • escalation: {esc}"
            )
            ack_buttons.append([InlineKeyboardButton(
                f"✅ Ack {truck}",
                callback_data=f"ack_alert_{a['id']}"
            )])

        if len(pending) > 10:
            lines.append(f"\n  <i>… and {len(pending) - 10} more</i>")

        text = "\n".join(lines)
        ack_buttons.append([InlineKeyboardButton("📜 Alert History", callback_data="cmd_alert_history")])
        ack_buttons.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
        kb = InlineKeyboardMarkup(ack_buttons)

    await _show(update, context, [text], keyboard=kb)
