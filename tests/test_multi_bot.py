"""Tests for per-account bot lifecycle — BotRegistry, handler scoping, isolation.

BotRegistry manages per-account Telegram Application instances.
These tests mock the Telegram API to avoid real network calls while
verifying the registry's lifecycle, handler registration, and scoping.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ENCRYPTION_KEY", "")


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def _make_fake_bot_info(username: str = "test_bot"):
    """Create a fake User object returned by bot.get_me()."""
    user = MagicMock()
    user.username = username
    return user


# ═══════════════════════════════════════════════════════════════════
#  BotRegistry lifecycle tests
# ═══════════════════════════════════════════════════════════════════

class TestBotRegistry:
    """BotRegistry — start, stop, restart, get, start_all, stop_all."""

    async def test_start_bot_creates_app(self):
        """start_bot() creates and caches an Application."""
        from infra.bot_registry import BotRegistry

        registry = BotRegistry()

        with patch("infra.bot_registry._build_bot_app", new_callable=AsyncMock) as mock_build:
            fake_app = MagicMock()
            mock_build.return_value = fake_app

            app = await registry.start_bot(
                account_id=1,
                encrypted_token="test_token_123",
            )

            assert app is fake_app
            assert 1 in registry
            assert registry.get(1) is fake_app
            assert len(registry) == 1

    async def test_start_bot_duplicate_stops_first(self):
        """start_bot() with existing bot stops the old one first."""
        from infra.bot_registry import BotRegistry

        registry = BotRegistry()

        with patch("infra.bot_registry._build_bot_app", new_callable=AsyncMock) as mock_build:
            app1 = MagicMock()
            app1.updater = MagicMock()
            app1.updater.running = True
            app1.updater.stop = AsyncMock()
            app1.stop = AsyncMock()
            app1.shutdown = AsyncMock()

            app2 = MagicMock()

            mock_build.side_effect = [app1, app2]

            await registry.start_bot(1, "token1")
            assert registry.get(1) is app1

            await registry.start_bot(1, "token2")
            assert registry.get(1) is app2

            # First app should have been stopped
            app1.updater.stop.assert_awaited_once()
            app1.stop.assert_awaited_once()
            app1.shutdown.assert_awaited_once()

    async def test_stop_bot_removes(self):
        """stop_bot() stops and removes the application."""
        from infra.bot_registry import BotRegistry

        registry = BotRegistry()

        fake_app = MagicMock()
        fake_app.updater = MagicMock()
        fake_app.updater.running = True
        fake_app.updater.stop = AsyncMock()
        fake_app.stop = AsyncMock()
        fake_app.shutdown = AsyncMock()

        with patch("infra.bot_registry._build_bot_app", new_callable=AsyncMock, return_value=fake_app):
            await registry.start_bot(1, "token")

        await registry.stop_bot(1)

        assert 1 not in registry
        assert registry.get(1) is None
        fake_app.stop.assert_awaited_once()
        fake_app.shutdown.assert_awaited_once()

    async def test_stop_bot_nonexistent_noop(self):
        """stop_bot() on unknown account is a safe no-op."""
        from infra.bot_registry import BotRegistry

        registry = BotRegistry()
        await registry.stop_bot(999)  # should not raise

    async def test_restart_bot(self):
        """restart_bot() stops old and starts new."""
        from infra.bot_registry import BotRegistry

        registry = BotRegistry()

        app1 = MagicMock()
        app1.updater = MagicMock()
        app1.updater.running = True
        app1.updater.stop = AsyncMock()
        app1.stop = AsyncMock()
        app1.shutdown = AsyncMock()

        app2 = MagicMock()

        with patch("infra.bot_registry._build_bot_app", new_callable=AsyncMock) as mock_build:
            mock_build.side_effect = [app1, app2]

            await registry.start_bot(1, "token_old")
            restarted = await registry.restart_bot(1, "token_new")

            assert restarted is app2
            assert registry.get(1) is app2
            app1.stop.assert_awaited_once()

    async def test_get_nonexistent(self):
        """get() returns None for unknown account."""
        from infra.bot_registry import BotRegistry

        registry = BotRegistry()
        assert registry.get(999) is None

    async def test_stop_all(self):
        """stop_all() stops all running bots."""
        from infra.bot_registry import BotRegistry

        registry = BotRegistry()

        apps = {}
        for aid in [1, 2, 3]:
            app = MagicMock()
            app.updater = MagicMock()
            app.updater.running = True
            app.updater.stop = AsyncMock()
            app.stop = AsyncMock()
            app.shutdown = AsyncMock()
            apps[aid] = app

        with patch("infra.bot_registry._build_bot_app", new_callable=AsyncMock) as mock_build:
            mock_build.side_effect = [apps[1], apps[2], apps[3]]
            await registry.start_bot(1, "t1")
            await registry.start_bot(2, "t2")
            await registry.start_bot(3, "t3")

        assert len(registry) == 3

        await registry.stop_all()

        assert len(registry) == 0
        for app in apps.values():
            app.stop.assert_awaited_once()

    async def test_active_accounts(self):
        """active_accounts lists account IDs of running bots."""
        from infra.bot_registry import BotRegistry

        registry = BotRegistry()

        with patch("infra.bot_registry._build_bot_app", new_callable=AsyncMock) as mock_build:
            mock_build.side_effect = [MagicMock(), MagicMock()]
            await registry.start_bot(10, "t10")
            await registry.start_bot(20, "t20")

        assert sorted(registry.active_accounts) == [10, 20]


# ═══════════════════════════════════════════════════════════════════
#  Handler registration tests
# ═══════════════════════════════════════════════════════════════════

class TestHandlerRegistration:
    """Verify register_handlers attaches all expected handlers."""

    async def test_handlers_attached(self):
        """register_handlers adds command, callback, and text handlers."""
        from interfaces.bot.handler_setup import register_handlers
        from telegram.ext import Application

        # Build a real Application (but never start it)
        app = Application.builder().token("0:FAKE").build()
        register_handlers(app)

        # Collect all handler types
        all_handlers = []
        for group_handlers in app.handlers.values():
            all_handlers.extend(group_handlers)

        # Should have command handlers for at least these commands
        from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler
        cmd_names = set()
        has_callback = False
        has_text = False

        for h in all_handlers:
            if isinstance(h, CommandHandler):
                cmd_names.update(h.commands)
            elif isinstance(h, CallbackQueryHandler):
                has_callback = True
            elif isinstance(h, MessageHandler):
                has_text = True

        # System-owner commands ({admin, accounts, sysaccount, broadcast,
        # sysdisable}) live on the SYSTEM bot only — registered by
        # ``register_system_handlers``, not the per-tenant
        # ``register_handlers`` we're exercising here.  See
        # interfaces/bot/handler_setup.py:269 for the split.  This test
        # validates the tenant-bot handler surface only.
        expected_commands = {
            "start", "help", "register", "join", "faults", "vehicle",
            "fuel", "alerts", "health", "efficiency", "events",
            "invite", "account", "users", "setrole", "remove",
            "addorg", "removeorg", "addgroup", "removegroup", "groups",
            "chatid", "settings", "audit",
        }

        missing = expected_commands - cmd_names
        assert not missing, f"Missing command handlers: {missing}"
        assert has_callback, "No CallbackQueryHandler registered"
        assert has_text, "No text MessageHandler registered"


# ═══════════════════════════════════════════════════════════════════
#  Bot scoping tests
# ═══════════════════════════════════════════════════════════════════

class _ChainingBuilder:
    """Stands in for telegram.ext.ApplicationBuilder.

    Every step returns the builder and only ``build()`` returns the app —
    mirrored GENERICALLY on purpose.  The previous mock stubbed the
    chain step by step, so when the real chain gained
    ``.rate_limiter(AIORateLimiter())`` the unstubbed step handed
    ``.build()`` a different MagicMock, and the test died on "MagicMock
    can't be used in 'await'" instead of on anything it was written to
    check.  A generic stand-in cannot rot that way.
    """

    def __init__(self, app):
        self._app = app

    def build(self):
        return self._app

    def __getattr__(self, _name):
        return lambda *a, **kw: self


class TestBotScoping:
    """Handlers can resolve account_id via bot_data."""

    async def test_bot_data_account_id(self):
        """_build_bot_app sets bot_data['account_id']."""
        import infra.bot_registry as _reg_mod
        from infra.bot_registry import _build_bot_app

        # Ensure handler setup is injected (normally done by run.py)
        _reg_mod.set_handler_setup(lambda app: None)

        # Mock the Telegram API calls
        with patch("infra.bot_registry.Application") as MockAppClass:
            mock_app = MagicMock()
            mock_app.bot_data = {}
            mock_app.bot = MagicMock()
            mock_app.bot.get_me = AsyncMock(return_value=_make_fake_bot_info("mybot"))
            mock_app.bot.set_my_commands = AsyncMock()
            mock_app.initialize = AsyncMock()
            mock_app.start = AsyncMock()
            mock_app.updater = MagicMock()
            mock_app.updater.start_polling = AsyncMock()
            mock_app.handlers = {}
            mock_app.add_handler = MagicMock()

            MockAppClass.builder.return_value = _ChainingBuilder(mock_app)

            app = await _build_bot_app(token="123:FAKE", account_id=42)

            assert app.bot_data["account_id"] == 42
            assert app.bot_data["bot_username"] == "mybot"

    async def test_multiple_bots_independent_state(self):
        """Two bots have independent bot_data dictionaries."""
        from infra.bot_registry import BotRegistry

        registry = BotRegistry()

        app1 = MagicMock()
        app1.bot_data = {"account_id": 1}

        app2 = MagicMock()
        app2.bot_data = {"account_id": 2}

        with patch("infra.bot_registry._build_bot_app", new_callable=AsyncMock) as mock_build:
            mock_build.side_effect = [app1, app2]
            await registry.start_bot(1, "t1")
            await registry.start_bot(2, "t2")

        bot1 = registry.get(1)
        bot2 = registry.get(2)

        assert bot1 is not bot2
        assert bot1.bot_data["account_id"] == 1
        assert bot2.bot_data["account_id"] == 2

        # Cleanup
        for aid in [1, 2]:
            app = registry.get(aid)
            app.updater = MagicMock()
            app.updater.running = False
            app.stop = AsyncMock()
            app.shutdown = AsyncMock()
        await registry.stop_all()


# ═══════════════════════════════════════════════════════════════════
#  start_all integration tests
# ═══════════════════════════════════════════════════════════════════

class TestStartAll:
    """BotRegistry.start_all() with mocked platform_db."""

    async def test_start_all_from_db(self):
        """start_all() reads accounts from DB and starts bots."""
        from infra.bot_registry import BotRegistry

        registry = BotRegistry()

        # Mock platform_db.get_accounts_with_bot_tokens()
        acct1 = MagicMock()
        acct1.id = 1
        acct1.bot_token_encrypted = "token_1"
        acct1.webhook_secret = ""

        acct2 = MagicMock()
        acct2.id = 2
        acct2.bot_token_encrypted = "token_2"
        acct2.webhook_secret = "secret_2"

        mock_db = AsyncMock()
        mock_db.get_accounts_with_bot_tokens.return_value = [acct1, acct2]

        with patch("infra.bot_registry._build_bot_app", new_callable=AsyncMock) as mock_build:
            mock_build.side_effect = [MagicMock(), MagicMock()]

            started = await registry.start_all(mock_db)

            assert started == 2
            assert len(registry) == 2
            assert sorted(registry.active_accounts) == [1, 2]

    async def test_start_all_partial_failure(self):
        """start_all() continues if one bot fails to start."""
        from infra.bot_registry import BotRegistry

        registry = BotRegistry()

        acct1 = MagicMock()
        acct1.id = 1
        acct1.bot_token_encrypted = "bad_token"
        acct1.webhook_secret = ""

        acct2 = MagicMock()
        acct2.id = 2
        acct2.bot_token_encrypted = "good_token"
        acct2.webhook_secret = ""

        mock_db = AsyncMock()
        mock_db.get_accounts_with_bot_tokens.return_value = [acct1, acct2]

        with patch("infra.bot_registry._build_bot_app", new_callable=AsyncMock) as mock_build:
            mock_build.side_effect = [RuntimeError("Invalid token"), MagicMock()]

            started = await registry.start_all(mock_db)

            assert started == 1
            assert 1 not in registry
            assert 2 in registry

    async def test_start_all_no_accounts(self):
        """start_all() with no bot tokens returns 0."""
        from infra.bot_registry import BotRegistry

        registry = BotRegistry()

        mock_db = AsyncMock()
        mock_db.get_accounts_with_bot_tokens.return_value = []

        started = await registry.start_all(mock_db)
        assert started == 0
        assert len(registry) == 0
