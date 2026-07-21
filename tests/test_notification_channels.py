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
from capabilities.notifications.channels import (
    NotificationContent,
    Payload,
    Recipient,
)
from capabilities.notifications.email import EmailChannel
from capabilities.notifications.telegram import (
    TelegramDmChannel,
    TelegramTopicChannel,
    render_telegram,
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


_NOTIF_MODULES = (
    "capabilities.notifications",
    "capabilities.notifications.channels",
    "capabilities.notifications.telegram",
    "capabilities.notifications.email",
    "capabilities.notifications.service",
    "capabilities.notifications.lifecycle",
    "capabilities.notifications.tokens",
    "capabilities.notifications.retention",
    "capabilities.notifications.webpush",
    "capabilities.notifications.vapid",
    "capabilities.notifications.router",
)


def test_does_not_import_alerting():
    # Layering guard: the delivery CORE must not depend on an event source,
    # or alerting → notifications later would cycle.  router.py is exempt —
    # it's the HTTP composition skin (already exempt from the interfaces
    # guard below), a leaf only app.py imports, so it may reference alerting
    # relevance for role-gating without forming the delivery-core cycle.
    # Check only actual import LINES (docstrings mention 'alerting'
    # deliberately).
    import inspect
    import importlib
    for m in _NOTIF_MODULES:
        if m.endswith(".router"):
            continue
        mod = importlib.import_module(m)
        for line in inspect.getsource(mod).splitlines():
            s = line.strip()
            if s.startswith(("import ", "from ")):
                assert "capabilities.alerting" not in s, (m, line)


def test_only_router_imports_interfaces():
    """The delivery core stays framework-free: ONLY router.py may reach
    into interfaces.api (deps/rate_limit).  Everything else would couple
    the capability to the web layer."""
    import inspect
    import importlib
    for m in _NOTIF_MODULES:
        if m.endswith(".router"):
            continue
        mod = importlib.import_module(m)
        for line in inspect.getsource(mod).splitlines():
            s = line.strip()
            if s.startswith(("import ", "from ")):
                assert "interfaces.api" not in s, (m, line)


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


# ── Semantic render: content → per-channel Payload (Phase 4) ─────────

def _rcpt(**kw):
    kw.setdefault("account_id", 1)
    kw.setdefault("type", "user")
    kw.setdefault("id", "7")
    return Recipient(**kw)


def test_telegram_render_escapes_exactly_once():
    """The regression that bit before: content is RAW; render escapes it
    once.  ``escape_html`` is NOT idempotent for '&', so a double render
    would produce '&amp;amp;' — assert we get single escaping."""
    content = NotificationContent(
        title="Fault: Truck A & B", body="value < 5 > 1", severity="critical")
    p = render_telegram(content)
    assert "Truck A &amp; B" in p.text and "&amp;amp;" not in p.text
    assert "&lt; 5 &gt; 1" in p.text
    assert "<b>" in p.text and "🔴" in p.text          # bold title + severity icon
    assert p.parse_mode == "HTML"


def test_telegram_render_carries_photo():
    content = NotificationContent(title="Cam", photo_bytes=b"\x89PNG")
    assert render_telegram(content).photo_bytes == b"\x89PNG"


def test_telegram_render_does_not_make_raw_markup_live():
    """Content is raw plain text — a literal <a href> must render as inert
    text, not a clickable link (plain html.escape, not the allow-list)."""
    content = NotificationContent(
        title='Note', body='<a href="https://evil.example">click</a>')
    text = render_telegram(content).text
    assert "&lt;a href=" in text                 # escaped, inert
    assert "<a href=" not in text                 # NOT a live link


def test_telegram_render_clamps_to_transport_limit_without_breaking_entities():
    """A digest full of ampersands escapes to > the raw cap; render must
    clamp under Telegram's 4096 limit and never leave a dangling entity
    (which would make Telegram reject the whole message)."""
    from capabilities.notifications.telegram import _TELEGRAM_TEXT_MAX
    content = NotificationContent(title="D", body="A & B " * 2000)  # ~12k chars raw
    text = render_telegram(content).text
    assert len(text) <= _TELEGRAM_TEXT_MAX
    assert text.endswith("…")
    # No truncated '&amp;' — every '&' must be a complete entity.
    import re
    for m in re.finditer(r"&[^;]*", text):
        frag = text[m.start():m.start() + 5]
        assert ";" in frag, f"dangling entity at {m.start()}: {frag!r}"


def test_telegram_photo_caption_uses_stricter_limit():
    from capabilities.notifications.telegram import _TELEGRAM_CAPTION_MAX
    content = NotificationContent(title="Cam", body="x " * 2000,
                                  photo_bytes=b"\x89PNG")
    assert len(render_telegram(content).text) <= _TELEGRAM_CAPTION_MAX


def test_severity_maps_do_not_drift():
    """All three per-transport severity dicts key only the canonical
    vocabulary — a new severity can't be half-wired."""
    from capabilities.notifications.channels import SEVERITIES
    from capabilities.notifications.email import _SEV_SUBJECT_PREFIX
    from capabilities.notifications.service import _SEV_RANK
    from capabilities.notifications.telegram import _SEV_ICON
    for d in (_SEV_ICON, _SEV_SUBJECT_PREFIX, _SEV_RANK):
        assert set(d).issubset(set(SEVERITIES)), d


def test_email_render_builds_subject_html_and_unsubscribe(monkeypatch):
    monkeypatch.setenv("AUTH_BASE_URL", "https://x.test")
    content = NotificationContent(
        title="Low fuel · Truck 22", body="Tank at 8% & dropping",
        severity="warning", alert_type="fuel")
    p = EmailChannel().render(_rcpt(address="me@x.com"), content)

    assert p.subject == "[Warning] Low fuel · Truck 22"     # severity prefix
    assert "Tank at 8% & dropping" in p.text                # text/plain raw
    html = p.extra["html"]
    assert "Tank at 8% &amp; dropping" in html              # html escaped once
    assert "&amp;amp;" not in html
    assert "unsubscribe" in html.lower() or "turn these off" in html.lower()
    assert p.extra["list_unsubscribe"].startswith("<https://x.test")


def test_email_render_critical_prefix():
    p = EmailChannel().render(
        _rcpt(address="a@b.com"),
        NotificationContent(title="Engine fault", severity="critical"))
    assert p.subject.startswith("[Critical]")


# ── Email send guards ────────────────────────────────────────────────

def test_email_send_bad_address_fails_closed():
    res = asyncio.run(EmailChannel().send(
        _rcpt(address="not-an-email"), Payload(text="x", subject="s")))
    assert not res.ok and res.error == "bad_email"


def test_email_send_unconfigured_fails_closed(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    res = asyncio.run(EmailChannel().send(
        _rcpt(address="me@x.com"), Payload(text="x", subject="s")))
    assert not res.ok and res.error == "email_not_configured"


def test_email_send_calls_transport_with_unsubscribe_header(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_FROM", "noreply@x.test")
    captured = {}

    def _fake_send_email(**kw):
        captured.update(kw)
        return True

    with patch("capabilities.email.send_email", _fake_send_email), \
         patch("capabilities.email.is_email_configured", return_value=True):
        payload = Payload(text="body", subject="Subj",
                          extra={"html": "<p>body</p>",
                                 "list_unsubscribe": "<https://x.test/u>"})
        res = asyncio.run(EmailChannel().send(_rcpt(address="me@x.com"), payload))

    assert res.ok
    assert captured["to"] == "me@x.com" and captured["subject"] == "Subj"
    assert captured["html_body"] == "<p>body</p>"
    hdrs = captured["extra_headers"]
    assert hdrs["List-Unsubscribe"] == "<https://x.test/u>"
    assert hdrs["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_email_channel_is_registered_and_personal():
    assert notif.get_channel("email").personal is True
    assert "email" in {c.key for c in notif.personal_channels()}


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
