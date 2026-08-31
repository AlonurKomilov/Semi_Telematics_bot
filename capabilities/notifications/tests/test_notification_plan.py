"""Delivery plans — the one hand-off from a source to every destination.

Pins the plan contract (capabilities/alerting/docs/alert-dm-migration.md):

  • shared targets render + send through their channel with the
    sender hint (declared via accepts_sender_hint), the prefix applied
    AFTER the escaping render, and a ledger row per successful send
    (recipient_type='topic', keyed by the correlation key)
  • per-target failure isolation: a dead group never blocks the next
    target or the personal fanout; failed sends record nothing
  • personal fanouts delegate to dispatch() with the plan's
    correlation key and the source's recipient predicate
  • the real TelegramTopicChannel resolves the hinted persona's Sub
    bot (fail-open to primary), serializes per destination, stamps the
    sender into the handle, and edits re-resolve the SAME sender
    (Telegram only lets the author bot edit)

All fakes — no Telegram, no Postgres.
"""

from __future__ import annotations

import pytest

import capabilities.notifications.plan as plan_mod
import capabilities.notifications.telegram as tg_mod
from capabilities.notifications.channels import (
    DeliveryResult,
    NotificationContent,
    Payload,
    Recipient,
    register_channel,
)
from capabilities.notifications.plan import (
    DeliveryPlan,
    PersonalSend,
    Target,
    deliver,
)
from capabilities.notifications.telegram import TelegramTopicChannel


@pytest.fixture(autouse=True)
def _registry_isolation():
    from capabilities.notifications import channels as _ch
    snap = dict(_ch._CHANNELS)
    yield
    _ch._CHANNELS.clear(); _ch._CHANNELS.update(snap)


class _SharedChannel:
    key = "fake_shared"
    personal = False
    accepts_sender_hint = True

    def __init__(self, *, fail_addresses: set | None = None):
        self.sent: list[dict] = []
        self._fail = fail_addresses or set()

    def render(self, recipient, content):
        return Payload(text=f"<{content.title}>")   # marker for prefix order

    async def send(self, recipient, payload, *, sender_hint=""):
        if recipient.address in self._fail:
            return DeliveryResult(ok=False, error="kicked")
        self.sent.append({"address": recipient.address, "text": payload.text,
                          "sender_hint": sender_hint})
        return DeliveryResult(ok=True, provider_ref="9",
                              handle={"chat_id": 1, "message_id": 9,
                                      "kind": "text"})


class _PlainChannel(_SharedChannel):
    """No accepts_sender_hint — plan must call send without the kwarg."""
    key = "fake_plain"
    accepts_sender_hint = False

    async def send(self, recipient, payload):      # no hint kwarg at all
        self.sent.append({"address": recipient.address, "text": payload.text})
        return DeliveryResult(ok=True, handle={"m": 1})


class _LedgerDb:
    def __init__(self):
        self.recorded: list[dict] = []

    async def record_notification_delivery(self, account_id, *, channel,
                                           recipient_type, recipient_id,
                                           category, correlation_key, handle):
        self.recorded.append({
            "channel": channel, "recipient_type": recipient_type,
            "recipient_id": recipient_id,
            "correlation_key": correlation_key, "handle": handle,
        })


def _plan(**kw) -> DeliveryPlan:
    base = dict(
        correlation_key="alert:5",
        contents=[NotificationContent(title="grp", category="alert.faults"),
                  NotificationContent(title="dm", category="alert.faults")],
        shared=[Target(channel="fake_shared", address="-100:7", id="safety",
                       sender_hint="safety", prefix_html="<a>@ping</a>")],
        personal=[PersonalSend(channels=("telegram_dm",), content_index=1)],
    )
    base.update(kw)
    return DeliveryPlan(**base)


# ── deliver(): shared targets ───────────────────────────────────────

@pytest.mark.asyncio
async def test_shared_target_send_prefix_ledger(monkeypatch):
    ch = _SharedChannel()
    register_channel(ch)
    recorded_dispatch = []

    async def _fake_dispatch(db, account_id, content, **kw):
        recorded_dispatch.append(kw)
        return [DeliveryResult(ok=True)]
    monkeypatch.setattr(plan_mod, "dispatch", _fake_dispatch)

    db = _LedgerDb()
    result = await deliver(db, 1, _plan())
    # prefix applied AFTER render (sits before the rendered marker)
    assert ch.sent[0]["text"] == "<a>@ping</a>\n<grp>"
    assert ch.sent[0]["sender_hint"] == "safety"
    # ledger row for the topic copy
    assert db.recorded[0]["recipient_type"] == "topic"
    assert db.recorded[0]["recipient_id"] == "safety"
    assert db.recorded[0]["correlation_key"] == "alert:5"
    # personal fanout delegated with the same correlation key
    assert recorded_dispatch[0]["correlation_key"] == "alert:5"
    assert recorded_dispatch[0]["channels"] == ("telegram_dm",)
    assert result.any_shared_ok is True


@pytest.mark.asyncio
async def test_channel_without_hint_support_gets_plain_send(monkeypatch):
    ch = _PlainChannel()
    register_channel(ch)

    async def _no_dispatch(*a, **kw):
        return []
    monkeypatch.setattr(plan_mod, "dispatch", _no_dispatch)
    res = await deliver(_LedgerDb(), 1, _plan(
        shared=[Target(channel="fake_plain", address="-5")], personal=[]))
    assert res.shared[0][1].ok is True     # no TypeError from the kwarg


@pytest.mark.asyncio
async def test_failed_target_isolated_and_unrecorded(monkeypatch):
    ch = _SharedChannel(fail_addresses={"-100:BAD"})
    register_channel(ch)

    async def _no_dispatch(*a, **kw):
        return [DeliveryResult(ok=True)]
    monkeypatch.setattr(plan_mod, "dispatch", _no_dispatch)

    db = _LedgerDb()
    result = await deliver(db, 1, _plan(shared=[
        Target(channel="fake_shared", address="-100:BAD", id="hr"),
        Target(channel="fake_shared", address="-100:7", id="safety"),
    ]))
    (t1, r1), (t2, r2) = result.shared
    assert r1.ok is False and r2.ok is True          # isolation
    assert [r["recipient_id"] for r in db.recorded] == ["safety"]
    assert len(result.personal) == 1                 # fanout still ran


@pytest.mark.asyncio
async def test_no_correlation_key_no_ledger(monkeypatch):
    ch = _SharedChannel()
    register_channel(ch)

    async def _no_dispatch(*a, **kw):
        return []
    monkeypatch.setattr(plan_mod, "dispatch", _no_dispatch)
    db = _LedgerDb()
    await deliver(db, 1, _plan(correlation_key="", personal=[]))
    assert db.recorded == []


@pytest.mark.asyncio
async def test_unknown_channel_reported_not_raised(monkeypatch):
    async def _no_dispatch(*a, **kw):
        return []
    monkeypatch.setattr(plan_mod, "dispatch", _no_dispatch)
    res = await deliver(_LedgerDb(), 1, _plan(
        shared=[Target(channel="ghost", address="-1")], personal=[]))
    assert res.shared[0][1].error == "unknown_channel"


# ── the real topic channel: sender resolution + handle + edit ───────

class _Bot:
    def __init__(self, name):
        self.name = name
        self.sent: list[dict] = []
        self.edits: list[dict] = []

    async def send_message(self, **kw):
        self.sent.append(kw)

        class _R:
            message_id = 44
        return _R()

    async def edit_message_text(self, **kw):
        self.edits.append(kw)

        class _R:
            message_id = kw["message_id"]
        return _R()


class _App:
    def __init__(self, bot):
        self.bot = bot


@pytest.mark.asyncio
async def test_topic_channel_uses_hinted_sub_bot_and_stamps_handle(monkeypatch):
    primary, sub = _Bot("primary"), _Bot("sub")
    monkeypatch.setattr(tg_mod, "_bot_for",
                        lambda account_id, override=None: override or _App(primary))
    monkeypatch.setattr("infra.bot_registry.get_sender_for_persona",
                        lambda aid, persona: _App(sub) if persona == "safety" else None)
    ch = TelegramTopicChannel()
    res = await ch.send(
        Recipient(account_id=1, type="topic", id="safety", address="-100:7"),
        Payload(text="x"), sender_hint="safety")
    assert sub.sent and not primary.sent            # Sub bot posted
    assert res.handle["sender"] == "safety"

    # Edit re-resolves the SAME sender from the handle.
    await ch.edit(
        Recipient(account_id=1, type="topic", id="safety", address="-100:7"),
        res.handle, Payload(text="edited"))
    assert sub.edits and not primary.edits


@pytest.mark.asyncio
async def test_topic_channel_falls_open_to_primary(monkeypatch):
    primary = _Bot("primary")
    monkeypatch.setattr(tg_mod, "_bot_for",
                        lambda account_id, override=None: override or _App(primary))
    monkeypatch.setattr("infra.bot_registry.get_sender_for_persona",
                        lambda aid, persona: None)   # sub bot down/missing
    ch = TelegramTopicChannel()
    res = await ch.send(
        Recipient(account_id=1, type="topic", id="hr", address="-9"),
        Payload(text="x"), sender_hint="hr")
    assert res.ok and primary.sent                   # never eats the post
