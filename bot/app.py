"""Application builder, post_init, and main entry point."""

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from permissions import SYSTEM_OWNER_IDS, role_display
from samsara_client import populate_company_display
from bot.i18n import t

import bot.config as _cfg
from bot.config import (
    TELEGRAM_TOKEN,
    WEBHOOK_URL, WEBHOOK_PORT, WEBHOOK_SECRET, USE_WEBHOOK,
    db, logger, _active_messages,
)
from bot.keyboards import main_menu_kb, system_owner_kb
from bot.registration import cmd_start, cmd_register, cmd_join, cmd_help
from bot.fleet import cmd_faults, cmd_truck, cmd_fuel, cmd_alerts, cmd_health, cmd_efficiency
from bot.management import (
    cmd_account, cmd_invite, cmd_users, cmd_setrole,
    cmd_remove, cmd_addcompany, cmd_removecompany,
    cmd_addgroup, cmd_removegroup, cmd_groups,
    handle_chat_shared,
)
from bot.admin import (
    cmd_admin, cmd_accounts, cmd_sysaccount,
    cmd_broadcast, cmd_sys_disable_account,
)
from bot.callbacks import handle_callback, handle_text
from bot.alerts import initialize_known_faults, check_unsafe_parking
from bot.scheduler import register_all as _register_jobs
from bot.events import cmd_events
import bot.redis_client as rcache
import encryption
from bot.auth import _get_user
import ai
from bot.helpers import _show, _render_audit_log
from bot.keyboards import user_settings_kb, back_kb


async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the chat ID — works in any chat (no auth check).
    This lets users discover a group's ID so they can authorize it."""
    chat = update.effective_chat
    chat_type = chat.type if chat else "unknown"
    chat_id = chat.id if chat else 0
    title = chat.title if chat and chat.title else "Private Chat"
    await update.message.reply_text(
        f"💬 <b>Chat Info</b>\n\n"
        f"  🆔 ID: <code>{chat_id}</code>\n"
        f"  📝 Title: {title}\n"
        f"  📋 Type: {chat_type}\n\n"
        f"  Use this ID with /addgroup in\n"
        f"  your private chat with the bot.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user notification settings menu."""
    user, _, _ = await _get_user(update)
    if not user:
        await update.message.reply_text("Please /start first.")
        return
    context.user_data["_db_user"] = user
    await _show(update, context, [
        "━━━━━━━━━━━━━━━━━━━\n"
        "  ⚙️  <b>SETTINGS</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\nConfigure your notification preferences:"
    ], keyboard=user_settings_kb(user))


async def cmd_audit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent audit log entries."""
    from permissions import can
    user, _, _ = await _get_user(update)
    if not user:
        await update.message.reply_text("Please /start first.")
        return
    if not can(user.role, "can_manage_users"):
        await update.message.reply_text(t("access.no_access"))
        return
    text = await _render_audit_log(user.account_id, db)
    await _show(update, context, [text], keyboard=back_kb())


async def post_init(app: Application):
    # Initialize encryption (must happen before DB reads)
    encryption.init_encryption()

    # Initialize database
    await db.initialize()

    # Give AI client access to DB for per-account model persistence
    ai.set_db(db)

    # Migrate plaintext API keys → encrypted (one-time, idempotent)
    await db.migrate_encrypt_api_keys()

    # Initialize Redis (optional — graceful fallback if unavailable)
    await rcache.init_redis()

    # Capture bot username for deep-link generation
    me = await app.bot.get_me()
    _cfg.bot_username = me.username or ""
    logger.info(f"Bot username: @{_cfg.bot_username}")

    # Set bot commands
    await app.bot.set_my_commands([
        BotCommand("start", "🏠 Main menu"),
        BotCommand("register", "📝 Register company"),
        BotCommand("join", "🔑 Join with invite code"),
        BotCommand("faults", "🔧 Fault report (PDF)"),
        BotCommand("truck", "🚛 Truck detail"),
        BotCommand("fuel", "⛽ Low fuel"),
        BotCommand("alerts", "🔔 Auto-alerts"),
        BotCommand("invite", "✉️ Invite team member"),
        BotCommand("account", "🏢 Account info"),
        BotCommand("users", "👥 Manage users"),
        BotCommand("addorg", "📡 Connect company"),
        BotCommand("groups", "💬 Manage group access"),
        BotCommand("chatid", "🆔 Show chat ID"),
        BotCommand("health", "🏥 Vehicle health"),
        BotCommand("efficiency", "📊 Efficiency report"),
        BotCommand("admin", "⚙️ System admin panel"),
        BotCommand("settings", "🔧 Notification settings"),
        BotCommand("audit", "📋 View audit log"),
        BotCommand("help", "ℹ️ Help"),
    ])
    logger.info("Commands set")

    # Initialize known faults for alerts
    await initialize_known_faults()

    # Run initial parking check now that DB is ready
    try:
        await check_unsafe_parking(app)
    except Exception as e:
        logger.warning("Initial parking check failed: %s", e)

    # Notify system owner(s) that bot is online
    sys_accounts = await db.list_accounts()
    sys_total_users = await db.count_all_users()
    for soid in SYSTEM_OWNER_IDS:
        try:
            sys_msg = (
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "  ⚙️  <b>Bot is Online</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "\n"
                f"  System Owner Dashboard\n"
                f"  🏢 {len(sys_accounts)} accounts\n"
                f"  👥 {sys_total_users} users\n"
                "  Use /admin for full stats"
            )
            msg = await app.bot.send_message(
                chat_id=soid,
                text=sys_msg,
                parse_mode=ParseMode.HTML,
                reply_markup=system_owner_kb(),
            )
            _active_messages[(soid, soid)] = [msg.message_id]
        except Exception as e:
            logger.warning(f"Startup msg to system owner {soid}: {e}")

    # Send startup message to all registered customer users
    accounts = await db.list_accounts()
    for account in accounts:
        acct_companies = await db.get_account_companies(account.id)
        populate_company_display(acct_companies)
        company_codes = [o.code for o in acct_companies]
        company_text = ", ".join(company_codes) if company_codes else "No companies yet"

        users = await db.list_account_users(account.id)
        for user in users:
            try:
                kb = main_menu_kb(user.role, company_codes)
                startup = (
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    "     🟢  <b>Bot is Online</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    "\n"
                    f"  {role_display(user.role)}\n"
                    f"  🏢 {account.name}\n"
                    f"  Monitoring: {company_text}\n"
                    "  Tap a button to begin ▾"
                )
                msg = await app.bot.send_message(
                    chat_id=user.telegram_id,
                    text=startup,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
                _active_messages[(user.telegram_id, user.telegram_id)] = [msg.message_id]
            except Exception as e:
                logger.warning(f"Startup msg to {user.telegram_id}: {e}")


async def post_shutdown(app: Application):
    """Clean up resources on bot shutdown."""
    await rcache.close_redis()
    logger.info("Redis connection closed")


def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    logger.info("Starting Semi Telematics Bot — multi-tenant mode")

    app = Application.builder().token(TELEGRAM_TOKEN).concurrent_updates(True).post_init(post_init).post_shutdown(post_shutdown).build()

    # Registration
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("join", cmd_join))

    # Fleet commands
    app.add_handler(CommandHandler("faults", cmd_faults))
    app.add_handler(CommandHandler("truck", cmd_truck))
    app.add_handler(CommandHandler("fuel", cmd_fuel))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("efficiency", cmd_efficiency))

    # Events
    app.add_handler(CommandHandler("events", cmd_events))

    # Management
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CommandHandler("account", cmd_account))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("setrole", cmd_setrole))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("addorg", cmd_addcompany))
    app.add_handler(CommandHandler("removeorg", cmd_removecompany))

    # Group / channel authorization
    app.add_handler(CommandHandler("addgroup", cmd_addgroup))
    app.add_handler(CommandHandler("removegroup", cmd_removegroup))
    app.add_handler(CommandHandler("groups", cmd_groups))
    app.add_handler(CommandHandler("chatid", cmd_chatid))

    # User settings & audit
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("audit", cmd_audit))

    # System owner admin
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("accounts", cmd_accounts))
    app.add_handler(CommandHandler("sysaccount", cmd_sysaccount))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("sysdisable", cmd_sys_disable_account))

    # Callback router
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Native chat picker (ChatShared)
    app.add_handler(MessageHandler(filters.StatusUpdate.CHAT_SHARED, handle_chat_shared))

    # Text input handler (for interactive prompts: register, join, truck, etc.)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Scheduled alerts
    scheduler = AsyncIOScheduler()
    _register_jobs(scheduler, app)
    scheduler.start()

    if USE_WEBHOOK:
        if not WEBHOOK_SECRET:
            logger.warning(
                "WEBHOOK_SECRET is not set! Webhook requests will not be validated. "
                "Set WEBHOOK_SECRET env var for production security."
            )
        logger.info(f"Starting webhook mode on port {WEBHOOK_PORT}")
        app.run_webhook(
            listen="127.0.0.1",
            port=WEBHOOK_PORT,
            url_path="/webhook",
            webhook_url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET or None,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
    else:
        logger.info("Starting polling mode")
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
