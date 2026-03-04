"""Application builder, post_init, and main entry point."""

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from permissions import SYSTEM_OWNER_IDS, role_display
from samsara_client import populate_org_display

import bot.config as _cfg
from bot.config import (
    TELEGRAM_TOKEN, ALERT_INTERVAL,
    db, logger, _active_messages,
)
from bot.keyboards import main_menu_kb, system_owner_kb
from bot.registration import cmd_start, cmd_register, cmd_join
from bot.fleet import cmd_faults, cmd_truck, cmd_critical, cmd_fuel, cmd_alerts
from bot.management import (
    cmd_account, cmd_invite, cmd_users, cmd_setrole,
    cmd_remove, cmd_addorg, cmd_removeorg,
)
from bot.admin import (
    cmd_admin, cmd_accounts, cmd_sysaccount,
    cmd_broadcast, cmd_sys_disable_account,
)
from bot.callbacks import handle_callback, handle_text
from bot.alerts import check_new_faults, initialize_known_faults


async def post_init(app: Application):
    # Initialize database
    await db.initialize()

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
        BotCommand("critical", "🚨 Critical faults (PDF)"),
        BotCommand("fuel", "⛽ Low fuel"),
        BotCommand("alerts", "🔔 Auto-alerts"),
        BotCommand("invite", "✉️ Invite team member"),
        BotCommand("account", "🏢 Account info"),
        BotCommand("users", "👥 Manage users"),
        BotCommand("addorg", "📡 Connect company"),
        BotCommand("admin", "⚙️ System admin panel"),
        BotCommand("help", "ℹ️ Help"),
    ])
    logger.info("Commands set")

    # Initialize known faults for alerts
    await initialize_known_faults()

    # Notify system owner(s) that bot is online
    sys_accounts = await db.list_accounts()
    sys_total_users = await db.count_all_users()
    for soid in SYSTEM_OWNER_IDS:
        try:
            sys_msg = (
                "╔══════════════════════════╗\n"
                "     ⚙️  <b>Bot is Online</b>\n"
                "╚══════════════════════════╝\n"
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
            _active_messages[soid] = [msg.message_id]
        except Exception as e:
            logger.warning(f"Startup msg to system owner {soid}: {e}")

    # Send startup message to all registered customer users
    accounts = await db.list_accounts()
    for account in accounts:
        acct_orgs = await db.get_account_orgs(account.id)
        populate_org_display(acct_orgs)
        org_codes = [o.code for o in acct_orgs]
        org_text = ", ".join(org_codes) if org_codes else "No orgs yet"

        users = await db.list_account_users(account.id)
        for user in users:
            try:
                kb = main_menu_kb(user.role, org_codes)
                startup = (
                    "╔══════════════════════════╗\n"
                    "     🟢  <b>Bot is Online</b>\n"
                    "╚══════════════════════════╝\n"
                    "\n"
                    f"  {role_display(user.role)}\n"
                    f"  🏢 {account.name}\n"
                    f"  Monitoring: {org_text}\n"
                    "  Tap a button to begin ▾"
                )
                msg = await app.bot.send_message(
                    chat_id=user.telegram_id,
                    text=startup,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
                _active_messages[user.telegram_id] = [msg.message_id]
            except Exception as e:
                logger.warning(f"Startup msg to {user.telegram_id}: {e}")


def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    logger.info("Starting Semi Telematics Bot — multi-tenant mode")

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    # Registration
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("join", cmd_join))

    # Fleet commands
    app.add_handler(CommandHandler("faults", cmd_faults))
    app.add_handler(CommandHandler("truck", cmd_truck))
    app.add_handler(CommandHandler("critical", cmd_critical))
    app.add_handler(CommandHandler("fuel", cmd_fuel))
    app.add_handler(CommandHandler("alerts", cmd_alerts))

    # Management
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CommandHandler("account", cmd_account))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("setrole", cmd_setrole))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("addorg", cmd_addorg))
    app.add_handler(CommandHandler("removeorg", cmd_removeorg))

    # System owner admin
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("accounts", cmd_accounts))
    app.add_handler(CommandHandler("sysaccount", cmd_sysaccount))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("sysdisable", cmd_sys_disable_account))

    # Callback router
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Text input handler (for interactive prompts: register, join, truck, etc.)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Scheduled alerts
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_new_faults, "interval",
        minutes=ALERT_INTERVAL, args=[app], id="fault_check",
    )
    scheduler.start()

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
