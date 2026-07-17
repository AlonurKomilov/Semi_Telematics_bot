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

import asyncio
import logging
from typing import Callable, Optional

from telegram import Update, BotCommand
from telegram.ext import AIORateLimiter, Application

from infra.crypto import decrypt

logger = logging.getLogger(__name__)

# ── Cross-process liveness heartbeat ──────────────────────────────
# The bot service writes ``bot:alive:<account_id>`` to Redis with a
# short TTL while ``start_bot`` keeps a refresher task alive.  The
# API process (which has its own empty in-memory registry because
# ENABLE_BOT=0 on that systemd unit) checks Redis instead of its own
# dict to answer "is this account's bot actually polling Telegram?".
#
# Refresh every HEARTBEAT_REFRESH_SECS, TTL HEARTBEAT_TTL_SECS.  TTL
# is ~3× the refresh interval so a single missed tick doesn't flap
# the dashboard badge — only a sustained outage (~90 s) shows as
# "not running."
HEARTBEAT_KEY_PREFIX = "bot:alive:"
HEARTBEAT_TTL_SECS = 90
HEARTBEAT_REFRESH_SECS = 30


def heartbeat_key(account_id: int) -> str:
    """Redis key that signals 'bot for account N is currently polling'."""
    return f"{HEARTBEAT_KEY_PREFIX}{account_id}"


async def is_bot_alive(account_id: int) -> bool:
    """Cross-process liveness probe — used by the API to answer
    /admin/bot-config without needing the bot process's in-memory
    registry.  Returns False when Redis is unavailable so a Redis
    outage degrades the badge to 'Configured' rather than incorrectly
    claiming 'Running'."""
    from adapters.cache.redis import exists
    return await exists(heartbeat_key(account_id))

# ── Handler setup injection ───────────────────────────────────────
# Set via set_handler_setup() at startup (called from interfaces/bot/).
# Keeps this core module free of interface-layer imports.
_handler_setup_fn: Optional[Callable[[Application], None]] = None


def set_handler_setup(fn: Callable[[Application], None]) -> None:
    """Register the function that attaches handlers to each bot Application.

    Call this once at startup, before any bots are started::

        import infra.bot_registry as registry
        from interfaces.bot.handler_setup import register_handlers
        registry.set_handler_setup(register_handlers)
    """
    global _handler_setup_fn
    _handler_setup_fn = fn


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
        # Telegram flood control: ~30 msg/s per bot, ~20 msg/min per
        # group.  Without a limiter a 100-vehicle alert burst exceeds
        # the per-group window and sends start failing with RetryAfter.
        # The limiter queues instead of failing; the alert pipeline adds
        # a retry on top for anything that still slips through.
        .rate_limiter(AIORateLimiter())
        .build()
    )

    # Tag the application so handlers know which account this is
    app.bot_data["account_id"] = account_id

    if _handler_setup_fn is None:
        raise RuntimeError(
            "Bot handler setup function not registered. "
            "Call infra.bot_registry.set_handler_setup() before starting bots."
        )
    _handler_setup_fn(app)

    # Initialize (connects to Telegram, resolves bot info)
    await app.initialize()

    # Capture bot username
    me = await app.bot.get_me()
    app.bot_data["bot_username"] = me.username or ""

    # Set the Telegram slash-popup command list — only commands the
    # bot actually executes locally appear here.  CRUD/management/
    # PDF surfaces moved to the dashboard; typing those commands
    # still works (you get a "moved → open on dashboard" reply) but
    # listing them in the popup would imply they still do their old
    # job, which is misleading.
    await app.bot.set_my_commands([
        BotCommand("start",       "🏠 Main menu"),
        BotCommand("join",        "🔑 Join with invite code"),
        # ── Driver-on-truck workflows ──────────────────────────
        BotCommand("pti",         "📋 Pre-trip inspection"),
        BotCommand("invoice",     "🧾 Submit a shop invoice"),
        BotCommand("fuelcost",    "⛽ Log a fill-up"),
        BotCommand("my_pay",      "💵 My paystub"),
        BotCommand("my_coaching", "🎓 My coaching tasks"),
        # ── Quick lookups + diagnostics ────────────────────────
        BotCommand("vehicle",     "🚛 Vehicle quick lookup"),
        BotCommand("cam",         "📷 Camera check (one truck)"),
        BotCommand("tips",        "📚 Knowledge base"),
        BotCommand("ai",          "🤖 AI assistant"),
        BotCommand("events",      "🚦 Safety events"),
        BotCommand("alerts",      "🔔 Alert settings + pending"),
        # ── Settings / housekeeping ────────────────────────────
        BotCommand("settings",    "⚙️ Notification settings"),
        BotCommand("invite",      "✉️ Invite team member"),
        BotCommand("audit",       "📋 Audit log"),
        BotCommand("chatid",      "🆔 Show chat ID"),
        BotCommand("help",        "ℹ️ Help"),
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
        # Per-account heartbeat refresher tasks.  One asyncio.Task per
        # running bot; cancelled in stop_bot so we don't keep refreshing
        # a key after the underlying Application has been torn down.
        self._heartbeat_tasks: dict[int, asyncio.Task] = {}

    async def _heartbeat_loop(self, account_id: int) -> None:
        """Refresh the Redis liveness key on a fixed interval.

        Runs inside the bot process for as long as the corresponding
        bot Application is up.  Cancelled by ``stop_bot``.  A Redis
        outage is logged at debug level only — the next successful
        refresh re-establishes the key.
        """
        from adapters.cache.redis import setex_flag
        key = heartbeat_key(account_id)
        try:
            while True:
                try:
                    await setex_flag(key, HEARTBEAT_TTL_SECS)
                except Exception:
                    logger.debug("Heartbeat write failed for account %d", account_id)
                await asyncio.sleep(HEARTBEAT_REFRESH_SECS)
        except asyncio.CancelledError:
            raise

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

        # Write the heartbeat key immediately so a dashboard reader
        # in the next ~30 s sees "Running" without waiting for the
        # refresher's first tick.  Then spawn the refresher.
        from adapters.cache.redis import setex_flag
        try:
            await setex_flag(heartbeat_key(account_id), HEARTBEAT_TTL_SECS)
        except Exception:
            logger.debug("Initial heartbeat write failed for account %d", account_id)
        self._heartbeat_tasks[account_id] = asyncio.create_task(
            self._heartbeat_loop(account_id),
            name=f"bot-heartbeat-{account_id}",
        )

        logger.info(
            "Bot started for account %d (@%s)",
            account_id,
            app.bot_data.get("bot_username", "?"),
        )
        return app

    async def stop_bot(self, account_id: int) -> None:
        """Stop and remove a bot for one account."""
        # Cancel the heartbeat refresher first so it can't race with
        # the key deletion below (would otherwise be possible for the
        # loop to write the key back after we DELETE it).
        task = self._heartbeat_tasks.pop(account_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            from adapters.cache.redis import delete
            await delete(heartbeat_key(account_id))
        except Exception:
            logger.debug("Heartbeat delete failed for account %d", account_id)

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
