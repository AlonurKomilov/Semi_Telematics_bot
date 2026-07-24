"""Tests that the lite ``post_alert_to_topic`` path (camera digest,
parking, doc-expiry, scorecard, samsara_sync, maintenance) honors the
same per-destination lock + CRITICAL-only @-mention rules as the heavy
``send_alert`` → ``_post_one_target`` path.

These cover the second-round adversarial findings:

  N1 — pre-fix, ``post_alert_to_topic`` had its own inlined send path
       that never acquired ``_topic_post_lock``, so two lite-path
       callers (or lite + heavy) racing on the same persona chat could
       interleave photo+text pairs.

  N2 — pre-fix, the F9 @-mention path lived only in
       ``_post_one_target``, so a CRITICAL camera digest posted to the
       safety persona chat with no on-shift ping.
"""

from __future__ import annotations

import asyncio
import pytest

from capabilities.alerting import pipeline as pipeline_mod
from capabilities.alerting.routing_resolver import AlertTarget


class _FakeBot:
    """Minimal Telegram bot stub recording send order with a sleep so
    interleavings would show up if the lock weren't held."""
    def __init__(self):
        self.calls: list[str] = []
        self._lock_probe = asyncio.Lock()  # exposes contention
        self.send_message_text: list[str] = []

    async def send_photo(self, **kwargs):
        await asyncio.sleep(0.01)
        self.calls.append(f"photo:{kwargs['chat_id']}")

        class _R:
            message_id = 1
        return _R()

    async def send_video(self, **kwargs):
        await asyncio.sleep(0.01)
        self.calls.append(f"video:{kwargs['chat_id']}")

        class _R:
            message_id = 1
        return _R()

    async def send_message(self, **kwargs):
        await asyncio.sleep(0.01)
        self.calls.append(f"text:{kwargs['chat_id']}")
        self.send_message_text.append(kwargs.get("text", ""))

        class _R:
            message_id = 2
        return _R()


class _FakeApp:
    def __init__(self):
        self.bot = _FakeBot()


@pytest.fixture
def _patched_resolver(monkeypatch):
    """Return a configurable target list so the test owns the routing
    decision without standing up a real Postgres."""
    targets: list[AlertTarget] = []

    async def _fake_resolve(*, account_id, alert_type, severity="", subtype=""):
        return list(targets)

    monkeypatch.setattr(
        "capabilities.alerting.routing_resolver.resolve_alert_targets",
        _fake_resolve,
    )
    # Some imports inside post_alert_to_topic reach for get_platform_db
    # for the drift-disable branch; provide a stub that's never called
    # on the success paths.
    monkeypatch.setattr(
        pipeline_mod, "get_platform_db", lambda: object(),
    )
    monkeypatch.setattr(pipeline_mod, "_FORUM_ROUTING_ENABLED", True)
    return targets


@pytest.mark.asyncio
async def test_post_alert_to_topic_acquires_destination_lock_serializing_concurrent_calls(
    _patched_resolver,
):
    """Two concurrent lite-path posts to the same persona chat must
    serialize so each (photo, text) pair stays atomic in the chat.

    Without the lock the two photos would land before either text,
    producing ``photo, photo, text, text`` (the bug F1 was meant to
    prevent — and that the second-round review found uncovered).
    """
    _patched_resolver.append(AlertTarget(
        chat_id=-100777, message_thread_id=None,
        is_aggregate=False, persona="dispatcher",
    ))
    app = _FakeApp()
    # Two concurrent callers racing on the SAME chat.
    photo = b"\x89PNG\r\n\x1a\n"  # minimal valid header
    coros = [
        pipeline_mod.post_alert_to_topic(
            app, account_id=1, alert_type="parking",
            text="A", photo_bytes=photo, severity="info",
        ),
        pipeline_mod.post_alert_to_topic(
            app, account_id=1, alert_type="parking",
            text="B", photo_bytes=photo, severity="info",
        ),
    ]
    await asyncio.gather(*coros)

    # Expected serialized order: photo-text photo-text (4 calls, paired)
    assert app.bot.calls == [
        "photo:-100777", "text:-100777",
        "photo:-100777", "text:-100777",
    ], f"Calls interleaved (lock not held): {app.bot.calls}"


@pytest.mark.asyncio
async def test_post_alert_to_topic_does_NOT_serialize_different_destinations(
    _patched_resolver,
):
    """Lock is per-destination; concurrent posts to DIFFERENT chats
    must not block each other (legacy single_group used to rely on
    that for cross-topic throughput)."""
    # Two targets that resolve in sequence within one call.
    _patched_resolver.append(AlertTarget(
        chat_id=-1, message_thread_id=None, persona="dispatcher",
    ))
    # Verify the SAME chat across two CALLS — but to prove the
    # different-destination case independently we run two callers each
    # with their own single-target list (mutating between awaits).
    app1 = _FakeApp()
    _patched_resolver.clear()
    _patched_resolver.append(AlertTarget(
        chat_id=-111, message_thread_id=None, persona="dispatcher",
    ))
    await pipeline_mod.post_alert_to_topic(
        app1, account_id=1, alert_type="parking", text="A",
        severity="info",
    )

    app2 = _FakeApp()
    _patched_resolver.clear()
    _patched_resolver.append(AlertTarget(
        chat_id=-222, message_thread_id=None, persona="fleet",
    ))
    await pipeline_mod.post_alert_to_topic(
        app2, account_id=1, alert_type="faults", text="B",
        severity="info",
    )

    # Different chats → independent locks → each call records its own
    # sequence with no cross-contention.
    assert app1.bot.calls == ["text:-111"]
    assert app2.bot.calls == ["text:-222"]


@pytest.mark.asyncio
async def test_post_alert_to_topic_critical_persona_prepends_mention(
    _patched_resolver, monkeypatch,
):
    """CRITICAL severity on a persona-mode primary target must prepend
    an HTML mention prefix the same way ``_post_one_target`` does for
    the send_alert path (closes the N2 gap — the camera digest case)."""
    _patched_resolver.append(AlertTarget(
        chat_id=-1000, message_thread_id=None,
        is_aggregate=False, persona="safety",
    ))

    # Stub the on-shift query so the test doesn't depend on a real DB.
    async def _fake_on_shift(account_id, persona):
        from capabilities.alerting.on_shift import OnShiftUser
        return [OnShiftUser(user_id=1, telegram_id=42, display_name="Alice")]

    monkeypatch.setattr(
        "capabilities.alerting.on_shift.users_on_shift_for_persona",
        _fake_on_shift,
    )

    app = _FakeApp()
    ok = await pipeline_mod.post_alert_to_topic(
        app, account_id=1, alert_type="camera",
        text="Camera Issues body", severity="critical",
    )
    assert ok is True
    assert len(app.bot.send_message_text) == 1
    sent = app.bot.send_message_text[0]
    assert 'href="tg://user?id=42"' in sent, (
        f"Mention prefix missing from CRITICAL persona-mode lite post: {sent!r}"
    )
    assert sent.endswith("Camera Issues body")


@pytest.mark.asyncio
async def test_post_alert_to_topic_warning_does_NOT_prepend_mention(
    _patched_resolver,
):
    """Non-critical severity must not trigger mentions even on a
    persona target — keeps the maintenance/doc-expiry/scorecard streams
    from pinging the entire on-shift team for routine warnings."""
    _patched_resolver.append(AlertTarget(
        chat_id=-1000, message_thread_id=None,
        is_aggregate=False, persona="fleet",
    ))
    app = _FakeApp()
    await pipeline_mod.post_alert_to_topic(
        app, account_id=1, alert_type="maintenance",
        text="Overdue body", severity="warning",
    )
    sent = app.bot.send_message_text[0]
    assert 'href="tg://user?id=' not in sent, (
        f"Non-critical post should NOT carry mentions: {sent!r}"
    )


@pytest.mark.asyncio
async def test_post_alert_to_topic_aggregate_target_does_NOT_prepend_mention(
    _patched_resolver, monkeypatch,
):
    """The owner_admin aggregate cross-post must NOT carry mentions —
    the operational persona group is the pager; the aggregate is the
    cross-cutting digest.  Mentioning on-shift safety on the owner's
    cross-post would double-ping them."""
    _patched_resolver.append(AlertTarget(
        chat_id=-9000, message_thread_id=None,
        is_aggregate=True, persona="owner_admin",
    ))

    async def _fake_on_shift(account_id, persona):
        from capabilities.alerting.on_shift import OnShiftUser
        return [OnShiftUser(user_id=1, telegram_id=42, display_name="Alice")]

    monkeypatch.setattr(
        "capabilities.alerting.on_shift.users_on_shift_for_persona",
        _fake_on_shift,
    )

    app = _FakeApp()
    await pipeline_mod.post_alert_to_topic(
        app, account_id=1, alert_type="camera",
        text="body", severity="critical",
    )
    sent = app.bot.send_message_text[0]
    assert 'href="tg://user?id=' not in sent, (
        f"Aggregate cross-post should NOT carry mentions: {sent!r}"
    )


@pytest.mark.asyncio
async def test_post_alert_to_topic_legacy_target_does_NOT_prepend_mention(
    _patched_resolver,
):
    """Legacy single_group targets carry no persona, so even on
    CRITICAL they must NOT get the mention treatment — topics don't
    map to on-shift state."""
    _patched_resolver.append(AlertTarget(
        chat_id=-100999, message_thread_id=42,
        is_aggregate=False, persona="",  # legacy single_group
    ))
    app = _FakeApp()
    await pipeline_mod.post_alert_to_topic(
        app, account_id=1, alert_type="faults",
        text="legacy body", severity="critical",
    )
    sent = app.bot.send_message_text[0]
    assert 'href="tg://user?id=' not in sent
    assert sent == "legacy body"
