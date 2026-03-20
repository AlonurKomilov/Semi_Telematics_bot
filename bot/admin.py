"""System owner commands — platform-wide administration."""

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from formatters import (
    format_admin_dashboard,
    format_admin_accounts_list,
    format_admin_account_detail,
)

from bot.config import db, logger, invalidate_client
from bot.keyboards import system_owner_kb
from bot.helpers import _show, _show_loading, _safe_error
from bot.auth import _require_system_owner


@_require_system_owner
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """System owner dashboard — bot-wide analytics."""
    await _show_loading(update, context, "⏳ Loading admin dashboard…")
    try:
        stats = await db.get_system_stats()
        text = format_admin_dashboard(stats)
        await _show(update, context, [text], keyboard=system_owner_kb())
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)])


@_require_system_owner
async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all accounts (system owner)."""
    try:
        accounts = await db.list_accounts(active_only=False)
        text = format_admin_accounts_list(accounts)
        await _show(update, context, [text], keyboard=system_owner_kb())
    except Exception as e:
        logger.error(f"Accounts list error: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)])


@_require_system_owner
async def cmd_sysaccount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View detailed info about a specific account: /sysaccount <id>"""
    if not context.args:
        await _show(update, context, [
            "ℹ️  Usage:  /sysaccount <b>ID</b>\n\n"
            "  Example:  /sysaccount 1"
        ], keyboard=system_owner_kb())
        return

    try:
        acct_id = int(context.args[0])
    except ValueError:
        await _show(update, context,
                    ["❌ Invalid account ID."],
                    keyboard=system_owner_kb())
        return

    account = await db.get_account(acct_id)
    if not account:
        await _show(update, context,
                    [f"❌ Account #{acct_id} not found."],
                    keyboard=system_owner_kb())
        return

    companies = await db.get_account_companies(acct_id, active_only=False)
    users = await db.list_account_users(acct_id)
    text = format_admin_account_detail(account, companies, users)
    await _show(update, context, [text], keyboard=system_owner_kb())


@_require_system_owner
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast a message to all registered users: /broadcast <message>"""
    if not context.args:
        await _show(update, context, [
            "ℹ️  Usage:\n\n"
            "  /broadcast <b>Your message here</b>\n\n"
            "  This sends to ALL registered users\n"
            "  across ALL accounts."
        ], keyboard=system_owner_kb())
        return

    message_text = " ".join(context.args)
    broadcast = (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "  📢  <b>SYSTEM NOTICE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  {message_text}\n"
    )

    accounts = await db.list_accounts()
    sent, failed = 0, 0
    for account in accounts:
        users = await db.list_account_users(account.id)
        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user.telegram_id,
                    text=broadcast,
                    parse_mode=ParseMode.HTML,
                )
                sent += 1
            except Exception as e:
                logger.warning(f"Broadcast to {user.telegram_id}: {e}")
                failed += 1

    await _show(update, context, [
        f"✅ Broadcast sent.\n\n"
        f"  ✉️  Delivered: {sent}\n"
        f"  ❌  Failed: {failed}"
    ], keyboard=system_owner_kb())


@_require_system_owner
async def cmd_sys_disable_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable/enable an account: /sysdisable <account_id>"""
    if not context.args:
        await _show(update, context, [
            "ℹ️  Usage:  /sysdisable <b>account_id</b>"
        ], keyboard=system_owner_kb())
        return

    try:
        acct_id = int(context.args[0])
    except ValueError:
        await _show(update, context, ["❌ Invalid account ID."],
                    keyboard=system_owner_kb())
        return

    account = await db.get_account(acct_id)
    if not account:
        await _show(update, context, [f"❌ Account #{acct_id} not found."],
                    keyboard=system_owner_kb())
        return

    new_state = not account.is_active
    await db.update_account(acct_id, is_active=new_state)

    # Invalidate client cache if disabling
    if not new_state:
        await invalidate_client(acct_id)

    status = "✅ ENABLED" if new_state else "🔴 DISABLED"
    await _show(update, context, [
        f"{status} account <b>{account.name}</b> (#{acct_id})"
    ], keyboard=system_owner_kb())
