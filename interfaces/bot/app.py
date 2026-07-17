"""Application builder, post_init, and main entry point.

Handler registration lives in ``interfaces/bot/handler_setup.py`` — the
single place that wires commands/callbacks/text handlers to a bot
Application.  Both ``build_app()`` here (system bot) and the per-account
``bot_registry`` go through it.

Three small handlers (``cmd_chatid``, ``cmd_settings``, ``cmd_audit``)
are defined in this module because ``handler_setup`` imports them from
here — they live alongside ``build_app`` so they share the system-bot
keyboards/audit utilities without pulling more cross-module imports.
"""

import asyncio

from telegram import Update, BotCommand
from telegram.ext import AIORateLimiter, Application, ContextTypes
from telegram.constants import ParseMode

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from capabilities.permissions.roles import SYSTEM_OWNER_IDS
from capabilities.localization.i18n import t

import interfaces.bot.config as _cfg
from interfaces.bot.config import (
    TELEGRAM_TOKEN,
    WEBHOOK_URL, WEBHOOK_PORT, WEBHOOK_SECRET, USE_WEBHOOK,
    logger,
)
from interfaces.bot.state import db
from interfaces.bot.keyboards import user_settings_kb, back_kb
from interfaces.bot.auth import _get_user
from interfaces.bot.helpers import _show, _render_audit_log
from interfaces.bot.scheduler import register_all as _register_jobs
from capabilities.alerting import initialize_known_faults, check_unsafe_parking


async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the chat ID — works in any chat (no auth check).
    This lets users discover a group's ID so they can authorize it."""
    msg = update.message
    if msg is None:
        return
    chat = update.effective_chat
    chat_type = chat.type if chat else "unknown"
    chat_id = chat.id if chat else 0
    title = chat.title if chat and chat.title else "Private Chat"
    await msg.reply_text(
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
    msg = update.message
    if msg is None or context.user_data is None:
        return
    user, _, _ = await _get_user(update)
    if not user:
        await msg.reply_text("Please /start first.")
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
    from capabilities.permissions.roles import can
    msg = update.message
    if msg is None:
        return
    user, _, _ = await _get_user(update)
    if not user:
        await msg.reply_text("Please /start first.")
        return
    if not can(user.role, "can_manage_users"):
        await msg.reply_text(t("access.no_access"))
        return
    text = await _render_audit_log(user.account_id)
    await _show(update, context, [text], keyboard=back_kb())


async def post_init(app: Application):
    # Platform-global init (encryption, DB, router, AI, Redis, key migration)
    # is handled by infra.startup.initialize() — called from run.py BEFORE
    # the bot Application is created.
    #
    # If core.platform is not yet initialized (e.g. standalone build_app()
    # call from legacy code paths), fall back to doing it here.
    from infra import platform as _platform
    if _platform._db is None:
        import infra.startup
        await infra.startup.initialize()

    # Capture bot username for deep-link generation
    me = await app.bot.get_me()
    _cfg.bot_username = me.username or ""
    logger.info(f"Bot username: @{_cfg.bot_username}")

    # Set the LOGIN-bot command menu — minimal signup/registration set
    # only.  Per-account bots advertise the full tenant command list via
    # ``bot_registry._build_bot_app`` (the same set we used to expose
    # here).  Listing tenant commands on the login bot would just be
    # misleading — a fresh visitor who picks /faults from the menu
    # would get "command not found".
    await app.bot.set_my_commands([
        BotCommand("start",    "🏠 Welcome / sign in"),
        BotCommand("register", "📝 Register a new account"),
        BotCommand("join",     "🔑 Join with invite code"),
        BotCommand("help",     "ℹ️ How 4truck works"),
        BotCommand("chatid",   "🆔 Show my chat id"),
    ])
    logger.info("Login bot command menu set (minimal)")

    # Initialize known faults and run initial parking check in background
    # (these make Samsara API calls that can take minutes — don't block startup)
    async def _deferred_init():
        try:
            await initialize_known_faults()
            logger.info("Known faults initialized")
        except Exception as e:
            logger.warning("initialize_known_faults failed: %s", e)
            try:
                from infra.error_reporter import report_error
                await report_error(e, source="startup", job_name="initialize_known_faults")
            except Exception:
                pass
        try:
            await check_unsafe_parking(app)
            logger.info("Initial parking check done")
        except Exception as e:
            logger.warning("Initial parking check failed: %s", e)
            try:
                from infra.error_reporter import report_error
                await report_error(e, source="startup", job_name="check_unsafe_parking")
            except Exception:
                pass

    _bg_task = asyncio.create_task(_deferred_init(), name="deferred-init")
    # Store reference on the app to prevent GC of the background task
    app.bot_data["_deferred_init_task"] = _bg_task

    # Notify system owner(s) that the bot is online.  We route this
    # through the SYSTEM bot client, not the customer daemon, so the
    # operator chat history with the customer-facing bot stays free
    # of operator-only content.  See infra/system_bot.py for the
    # send-only client; the buttons are URL buttons that open the
    # system.4truck.us console (no callback handler required).
    try:
        from infra import system_bot
        sys_accounts = await db.list_accounts()
        sys_total_users = await db.count_all_users()
        sys_msg = (
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "  ⚙️  <b>Bot is Online</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  🏢 {len(sys_accounts)} accounts\n"
            f"  👥 {sys_total_users} users\n"
            "\n"
            "  Operator console: <code>system.4truck.us</code>"
        )
        sent = await system_bot.send_to_owners(
            SYSTEM_OWNER_IDS,
            sys_msg,
            reply_markup=system_bot.system_console_kb(),
        )
        logger.info(
            "Startup ping sent via system bot to %d/%d owners",
            sent, len(list(SYSTEM_OWNER_IDS)),
        )
    except Exception as e:
        logger.warning("Startup system-owner ping failed: %s", e)


async def post_shutdown(app: Application):
    """Clean up resources on bot shutdown.

    Redis and DB are now closed by infra.startup.shutdown() in run.py.
    This hook remains for any per-bot cleanup.
    """
    logger.info("Bot application shut down")


def build_app() -> Application:
    """Build and return the Telegram Application with all handlers.

    Does NOT start the bot — caller is responsible for lifecycle.
    """
    if not TELEGRAM_TOKEN:
        raise RuntimeError(
            "Customer-facing bot token not set — define TELEGRAM_LOGIN_BOT_TOKEN "
            "(preferred) or TELEGRAM_BOT_TOKEN (legacy fallback) in .env."
        )

    app = (Application.builder()
           .token(TELEGRAM_TOKEN)
           .concurrent_updates(True)
           # Queue sends under Telegram flood limits instead of failing
           # (see infra/bot_registry._build_bot_app for the numbers).
           .rate_limiter(AIORateLimiter())
           .post_init(post_init)
           .post_shutdown(post_shutdown)
           .build())

    async def _ptb_error_handler(update: object, context: object) -> None:
        exc = getattr(context, "error", None)
        try:
            from infra.error_reporter import report_error
            await report_error(exc, source="bot")
        except Exception:
            pass

    app.add_error_handler(_ptb_error_handler)

    # Login bot gets the MINIMAL handler set — /start /register /join
    # /help /chatid only.  Tenant commands (/faults /vehicle etc.)
    # belong on per-account bots in bot_registry, not on the global
    # login bot; routing 1000 customers' tenant traffic through one
    # PTB Application is the bottleneck we explicitly want to avoid.
    from interfaces.bot.handler_setup import register_login_handlers
    register_login_handlers(app)

    return app


def main():
    """Standalone bot entry point (blocking — runs polling or webhook)."""
    logger.info("Starting 4truck Bot — multi-tenant mode")

    app = build_app()

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
