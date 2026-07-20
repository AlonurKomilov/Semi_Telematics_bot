"""Notification channel contract + the two Telegram transport adapters
(Phase 1a).  Proves the registry, the personal/shared split, and that
each channel sends to the right Telegram primitive with the right args —
without any real network send (the bot is a mock).

This layer is isolated: it imports only infra + telegram, never
capabilities.alerting — so wiring it in later can't create a cycle.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import capabilities.notifications as notif
from capabilities.notifications.channels import Payload, Recipient
from capabilities.notifications.telegram import (
    TelegramDmChannel,
    TelegramTopicChannel,
)


# ── Registry + contract ──────────────────────────────────────────────

def test_registry_registers_both_telegram_channels():
    keys = {c.key for c in notif.list_channels()}
    assert {"telegram_dm", "telegram_topic"} <= keys
    assert notif.get_channel("telegram_dm").personal is True
    assert notif.get_channel("telegram_topic").personal is False
    assert notif.get_channel("nope") is None


def test_personal_vs_shared_split():
    personal = {c.key for c in notif.personal_channels()}
    shared = {c.key for c in notif.shared_channels()}
    assert "telegram_dm" in personal and "telegram_dm" not in shared
    assert "telegram_topic" in shared and "telegram_topic" not in personal
    # A channel is exactly one scope.
    assert personal.isdisjoint(shared)


def test_does_not_import_alerting():
    # Layering guard: the delivery layer must not depend on an event
    # source, or alerting → notifications later would cycle.  Check only
    # actual import LINES (docstrings mention 'alerting' deliberately).
    import inspect
    import importlib
    for m in ("capabilities.notifications",
              "capabilities.notifications.channels",
              "capabilities.notifications.telegram"):
        mod = importlib.import_module(m)
        for line in inspect.getsource(mod).splitlines():
            s = line.strip()
            if s.startswith(("import ", "from ")):
                assert "capabilities.alerting" not in s, (m, line)


# ── Telegram DM (personal) transport ─────────────────────────────────

def _mock_app():
    app = MagicMock()
    app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
    app.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=1000))
    return app


def test_dm_sends_text_to_the_users_chat():
    app = _mock_app()
    ch = TelegramDmChannel()
    rcpt = Recipient(account_id=1, type="user", id="7", address="55501")
    with patch("capabilities.notifications.telegram._bot_for", return_value=app):
        res = asyncio.run(ch.send(rcpt, Payload(text="Fault on Truck 22")))
    assert res.ok and res.provider_ref == "999"
    kw = app.bot.send_message.call_args.kwargs
    assert kw["chat_id"] == 55501            # the user's telegram_id
    assert kw["message_thread_id"] is None   # DM has no thread
    assert kw["text"] == "Fault on Truck 22"


def test_dm_sends_photo_when_present():
    app = _mock_app()
    with patch("capabilities.notifications.telegram._bot_for", return_value=app):
        res = asyncio.run(TelegramDmChannel().send(
            Recipient(account_id=1, type="user", id="7", address="55501"),
            Payload(text="cap", photo_bytes=b"\x89PNG"),
        ))
    assert res.ok and res.provider_ref == "1000"
    app.bot.send_message.assert_not_called()
    assert app.bot.send_photo.call_args.kwargs["caption"] == "cap"


def test_dm_bad_id_fails_closed_no_send():
    app = _mock_app()
    with patch("capabilities.notifications.telegram._bot_for", return_value=app):
        res = asyncio.run(TelegramDmChannel().send(
            Recipient(account_id=1, type="user", id="x", address="not-a-number"),
            Payload(text="hi"),
        ))
    assert not res.ok and res.error == "bad_telegram_id"
    app.bot.send_message.assert_not_called()


def test_dm_no_bot_fails_closed():
    with patch("capabilities.notifications.telegram._bot_for", return_value=None):
        res = asyncio.run(TelegramDmChannel().send(
            Recipient(account_id=1, type="user", id="7", address="55501"),
            Payload(text="hi"),
        ))
    assert not res.ok and res.error == "no_bot"


# ── Telegram topic (shared) transport ────────────────────────────────

def test_topic_sends_to_group_thread():
    app = _mock_app()
    ch = TelegramTopicChannel()
    rcpt = Recipient(account_id=1, type="topic", id="fleet_grp", address="-1002:42")
    with patch("capabilities.notifications.telegram._bot_for", return_value=app):
        res = asyncio.run(ch.send(rcpt, Payload(text="Fleet alert")))
    assert res.ok
    kw = app.bot.send_message.call_args.kwargs
    assert kw["chat_id"] == -1002            # the group chat
    assert kw["message_thread_id"] == 42     # the topic thread


def test_topic_without_thread_is_flat_group():
    app = _mock_app()
    with patch("capabilities.notifications.telegram._bot_for", return_value=app):
        asyncio.run(TelegramTopicChannel().send(
            Recipient(account_id=1, type="topic", id="grp", address="-1005"),
            Payload(text="flat"),
        ))
    kw = app.bot.send_message.call_args.kwargs
    assert kw["chat_id"] == -1005 and kw["message_thread_id"] is None


def test_topic_sender_app_override_uses_that_bot():
    """The owner_admin aggregate cross-post forces the primary bot."""
    account_bot, primary_bot = _mock_app(), _mock_app()
    with patch("capabilities.notifications.telegram._bot_for") as bot_for:
        bot_for.side_effect = lambda acct, override=None: override or account_bot
        asyncio.run(TelegramTopicChannel().send(
            Recipient(account_id=1, type="topic", id="g", address="-100"),
            Payload(text="crit"), sender_app=primary_bot,
        ))
    primary_bot.bot.send_message.assert_awaited_once()
    account_bot.bot.send_message.assert_not_called()


# ── Retry primitive ──────────────────────────────────────────────────

def test_retry_once_on_flood_then_succeeds():
    from capabilities.notifications.telegram import _tg_send_with_retry
    from telegram.error import RetryAfter
    calls = {"n": 0}

    async def _send():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryAfter(0)
        return MagicMock(message_id=7)

    with patch("infra.observability.record_alert_flood"):
        out = asyncio.run(_tg_send_with_retry(_send, what="dm"))
    assert calls["n"] == 2 and out.message_id == 7
