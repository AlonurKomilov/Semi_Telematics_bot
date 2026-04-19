"""BotRegistry — manages per-account Telegram Application instances.

Each account gets its own ``telegram.ext.Application`` running with its
own bot token.  All instances share the same handler functions — handlers
are stateless and use ``context.bot_data["account_id"]`` for scoping.

Usage::

    registry = BotRegistry()
    await registry.start_bot(account_id=1, token="123:ABC", webhook_base=None)
    bot = registry.get(1)                # → Application | None
    await registry.stop_bot(1)
    await registry.stop_all()
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from encryption import decrypt

logger = logging.getLogger(__name__)


def _register_handlers(app: Application) -> None:
    """Attach all command/callback/text handlers to a bot Application.

    Imports are done inside the function to keep this module lightweight
    and avoid circular imports at import time.
    """
    from bot.app import cmd_chatid, cmd_settings, cmd_audit
    from bot.registration import cmd_start, cmd_register, cmd_join, cmd_help
    from bot.fleet import (
        cmd_faults, cmd_truck, cmd_fuel, cmd_alerts,
        cmd_health, cmd_efficiency,
    )
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
    from bot.events import cmd_events
    from bot.knowledge import cmd_tips

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
    app.add_handler(CommandHandler("tips", cmd_tips))

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
    app.add_handler(MessageHandler(
        filters.StatusUpdate.CHAT_SHARED, handle_chat_shared,
    ))

    # Text input handler (for interactive prompts)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_text,
    ))


async def _build_bot_app(
    token: str,
    account_id: int,
    webhook_url: Optional[str] = None,
    webhook_secret: str = "",
) -> Application:
    """Build, initialize and start a Telegram Application for one account.

    The application is ready to receive updates after this returns.
    ``bot_data["account_id"]`` is set so handlers can scope their work.
    """
    app = (
        Application.builder()
        .token(token)
        .concurrent_updates(True)
        .build()
    )

    # Tag the application so handlers know which account this is
    app.bot_data["account_id"] = account_id

    _register_handlers(app)

    # Initialize (connects to Telegram, resolves bot info)
    await app.initialize()

    # Capture bot username
    me = await app.bot.get_me()
    app.bot_data["bot_username"] = me.username or ""

    # Set bot commands
    await app.bot.set_my_commands([
        BotCommand("start", "🏠 Main menu"),
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
        BotCommand("settings", "🔧 Notification settings"),
        BotCommand("audit", "📋 View audit log"),
        BotCommand("help", "ℹ️ Help"),
    ])

    # Start updater
    assert app.updater is not None, "Application built without updater"
    if webhook_url:
        per_bot_path = f"/webhook/{account_id}"
        full_url = f"{webhook_url.rstrip('/')}{per_bot_path}"
        await app.updater.start_webhook(
            listen="127.0.0.1",
            port=0,  # OS picks a free port — traffic routed via reverse proxy
            url_path=per_bot_path,
            webhook_url=full_url,
            secret_token=webhook_secret or None,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
    else:
        await app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )

    await app.start()
    return app


class BotRegistry:
    """Manages per-account Telegram bot Application instances."""

    def __init__(self):
        self._bots: dict[int, Application] = {}

    async def start_bot(
        self,
        account_id: int,
        encrypted_token: str,
        webhook_base: Optional[str] = None,
        webhook_secret: str = "",
    ) -> Application:
        """Start a Telegram bot for one account.

        Args:
            account_id: The account this bot belongs to.
            encrypted_token: Fernet-encrypted (or plaintext) bot token.
            webhook_base: If set, use webhook mode with this base URL.
            webhook_secret: Per-bot webhook secret for validation.

        Returns:
            The running Application instance.
        """
        if account_id in self._bots:
            logger.warning("Bot for account %d already running — stopping first", account_id)
            await self.stop_bot(account_id)

        token = decrypt(encrypted_token)
        app = await _build_bot_app(
            token=token,
            account_id=account_id,
            webhook_url=webhook_base,
            webhook_secret=webhook_secret,
        )
        self._bots[account_id] = app
        logger.info(
            "Bot started for account %d (@%s)",
            account_id,
            app.bot_data.get("bot_username", "?"),
        )
        return app

    async def stop_bot(self, account_id: int) -> None:
        """Stop and remove a bot for one account."""
        app = self._bots.pop(account_id, None)
        if not app:
            return
        try:
            if app.updater and app.updater.running:
                await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception:
            logger.exception("Error stopping bot for account %d", account_id)

    async def restart_bot(
        self,
        account_id: int,
        encrypted_token: str,
        webhook_base: Optional[str] = None,
        webhook_secret: str = "",
    ) -> Application:
        """Stop and restart a bot (e.g., after token change)."""
        await self.stop_bot(account_id)
        return await self.start_bot(
            account_id, encrypted_token, webhook_base, webhook_secret,
        )

    def get(self, account_id: int) -> Optional[Application]:
        """Get the running Application for an account, or None."""
        return self._bots.get(account_id)

    async def start_all(self, platform_db, webhook_base: Optional[str] = None) -> int:
        """Start bots for all accounts that have a configured token.

        Returns the number of bots successfully started.
        """
        accounts = await platform_db.get_accounts_with_bot_tokens()
        started = 0
        for acct in accounts:
            try:
                await self.start_bot(
                    account_id=acct.id,
                    encrypted_token=acct.bot_token_encrypted,
                    webhook_base=webhook_base,
                    webhook_secret=acct.webhook_secret,
                )
                started += 1
            except Exception:
                logger.exception("Failed to start bot for account %d", acct.id)
        logger.info("Started %d / %d bots", started, len(accounts))
        return started

    async def stop_all(self) -> None:
        """Stop all running bots — call during shutdown."""
        account_ids = list(self._bots.keys())
        for aid in account_ids:
            await self.stop_bot(aid)
        logger.info("All bots stopped")

    @property
    def active_accounts(self) -> list[int]:
        """List account IDs with running bots."""
        return list(self._bots.keys())

    def __contains__(self, account_id: int) -> bool:
        return account_id in self._bots

    def __len__(self) -> int:
        return len(self._bots)


# ── Module-level singleton ──────────────────────────────────────

_registry: Optional[BotRegistry] = None
_system_app: Optional[Application] = None


def init_registry(system_app: Optional[Application] = None) -> BotRegistry:
    """Create (or return existing) singleton registry.

    *system_app* is the fallback Application for accounts without
    their own bot token.
    """
    global _registry, _system_app
    if _registry is None:
        _registry = BotRegistry()
    if system_app is not None:
        _system_app = system_app
    return _registry


def get_registry() -> Optional[BotRegistry]:
    """Return the singleton BotRegistry, or None if not yet initialized."""
    return _registry


def get_app_for_account(account_id: int) -> Optional[Application]:
    """Resolve the Application for *account_id*.

    Returns the per-account bot if running, otherwise None.
    NEVER falls back to the system bot — account-specific messages
    must only be sent via the account's own bot.
    """
    if _registry:
        return _registry.get(account_id)
    return None
