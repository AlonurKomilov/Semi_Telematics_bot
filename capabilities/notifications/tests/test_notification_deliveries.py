"""Delivery handles + the edit verb on the notifications spine.

Pins the contract docs/architecture/alert-dm-migration.md rests on:

  • a successful Telegram send returns an edit ``handle``
    (chat_id / message_id / kind, + thread_id for topics)
  • ``_edit`` picks the right Telegram verb from ``kind`` (text vs photo
    caption) and treats "message is not modified" as success
  • ``dispatch(correlation_key=...)`` records each successful send in the
    ``notification_deliveries`` ledger; no correlation_key → no ledger
    writes; a failed send is never recorded; a ledger failure never sinks
    the delivery
  • ``update_delivery`` renders NEW content once per recorded row and
    edits in place via the channel's stored handle; channels without
    ``supports_edit`` are skipped; ``clear=True`` drops the rows only
    when every edit succeeded

Everything runs on fakes — no Postgres, no Telegram.
"""

from __future__ import annotations

import pytest

import capabilities.notifications.telegram as tg_mod
from capabilities.notifications.channels import (
    DeliveryResult,
    NotificationContent,
    Payload,
    Recipient,
    register_channel,
)
from capabilities.notifications.service import dispatch, update_delivery
from capabilities.notifications.telegram import (
    TelegramDmChannel,
    _edit,
    _send,
)


@pytest.fixture(autouse=True)
def _registry_isolation():
    """Snapshot/restore the spine's global registries — fake channels /
    renderers / handlers registered here must never leak into other test
    files sharing this worker (e.g. the routes tests iterate
    personal_channels() and would see our fakes)."""
    from capabilities.notifications import actions as _act
    from capabilities.notifications import channels as _ch
    from capabilities.notifications import service as _svc
    ch_snap = dict(_ch._CHANNELS)
    h_snap = dict(_act._HANDLERS)
    r_snap = dict(_svc._DIGEST_RENDERERS)
    yield
    _ch._CHANNELS.clear(); _ch._CHANNELS.update(ch_snap)
    _act._HANDLERS.clear(); _act._HANDLERS.update(h_snap)
    _svc._DIGEST_RENDERERS.clear(); _svc._DIGEST_RENDERERS.update(r_snap)


# ── fakes ────────────────────────────────────────────────────────────

class _Msg:
    def __init__(self, message_id: int = 42):
        self.message_id = message_id


class _FakeBot:
    def __init__(self, *, edit_raises: Exception | None = None):
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self._edit_raises = edit_raises

    async def send_message(self, **kw):
        self.sent.append({"kind": "text", **kw})
        return _Msg(101)

    async def send_photo(self, **kw):
        self.sent.append({"kind": "photo", **kw})
        return _Msg(102)

    async def edit_message_text(self, **kw):
        if self._edit_raises:
            raise self._edit_raises
        self.edits.append({"kind": "text", **kw})
        return _Msg(kw["message_id"])

    async def edit_message_caption(self, **kw):
        if self._edit_raises:
            raise self._edit_raises
        self.edits.append({"kind": "photo", **kw})
        return _Msg(kw["message_id"])


class _FakeApp:
    def __init__(self, bot: _FakeBot):
        self.bot = bot


class _FakeDb:
    """Ledger + subscriber surface the service layer touches."""

    def __init__(self, subs: list[dict] | None = None):
        self._subs = subs or []
        self.recorded: list[dict] = []
        self.cleared: list[str] = []
        self.record_raises: Exception | None = None
        self.delivery_rows: list[dict] = []

    async def get_notification_subscribers(self, account_id, category, channel):
        return list(self._subs)

    async def get_roles_for_users(self, ids):
        return {i: "fleet" for i in ids}

    async def record_notification_delivery(self, account_id, *, channel,
                                           recipient_type, recipient_id,
                                           category, correlation_key, handle):
        if self.record_raises:
            raise self.record_raises
        self.recorded.append({
            "account_id": account_id, "channel": channel,
            "recipient_type": recipient_type, "recipient_id": recipient_id,
            "category": category, "correlation_key": correlation_key,
            "handle": handle,
        })

    async def get_notification_deliveries(self, account_id, correlation_key):
        return [r for r in self.delivery_rows
                if r.get("correlation_key", correlation_key) == correlation_key]

    async def clear_notification_deliveries(self, account_id, correlation_key):
        self.cleared.append(correlation_key)
        return len(self.delivery_rows)


class _EditableChannel:
    """Registry test double with full edit support."""
    key = "fake_editable"
    personal = True
    supports_edit = True

    def __init__(self):
        self.sends: list = []
        self.edits: list = []
        self.edit_ok = True

    def render(self, recipient, content):
        return Payload(text=f"{content.title}|{content.body}")

    async def send(self, recipient, payload):
        self.sends.append((recipient, payload))
        return DeliveryResult(ok=True, provider_ref="1",
                              handle={"chat_id": 7, "message_id": 1, "kind": "text"})

    async def edit(self, recipient, handle, payload):
        self.edits.append((recipient, handle, payload))
        return DeliveryResult(ok=self.edit_ok,
                              error="" if self.edit_ok else "boom")


class _NoEditChannel:
    """Registered channel WITHOUT supports_edit (email-shaped)."""
    key = "fake_noedit"
    personal = True

    def __init__(self):
        self.edits: list = []

    def render(self, recipient, content):
        return Payload(text=content.title)

    async def send(self, recipient, payload):
        return DeliveryResult(ok=True)


# ── Telegram leaf: handle population ────────────────────────────────

@pytest.mark.asyncio
async def test_send_returns_text_handle():
    bot = _FakeBot()
    res = await _send(_FakeApp(bot), chat_id=555, thread_id=None,
                      payload=Payload(text="hi"), what="dm")
    assert res.ok
    assert res.handle == {"chat_id": 555, "message_id": 101, "kind": "text"}


@pytest.mark.asyncio
async def test_send_returns_photo_handle_with_thread():
    bot = _FakeBot()
    res = await _send(_FakeApp(bot), chat_id=555, thread_id=99,
                      payload=Payload(text="hi", photo_bytes=b"img"), what="topic")
    assert res.ok
    assert res.handle == {"chat_id": 555, "message_id": 102,
                          "kind": "photo", "thread_id": 99}


@pytest.mark.asyncio
async def test_failed_send_has_empty_handle():
    res = await _send(None, chat_id=1, thread_id=None,
                      payload=Payload(text="x"), what="dm")
    assert not res.ok
    assert res.handle == {}


# ── Telegram leaf: edit verb selection ──────────────────────────────

@pytest.mark.asyncio
async def test_edit_text_uses_edit_message_text():
    bot = _FakeBot()
    res = await _edit(_FakeApp(bot),
                      handle={"chat_id": 5, "message_id": 101, "kind": "text"},
                      payload=Payload(text="new"), what="dm-edit")
    assert res.ok
    assert bot.edits[0]["kind"] == "text"
    assert bot.edits[0]["text"] == "new"


@pytest.mark.asyncio
async def test_edit_photo_uses_edit_message_caption():
    bot = _FakeBot()
    res = await _edit(_FakeApp(bot),
                      handle={"chat_id": 5, "message_id": 102, "kind": "photo"},
                      payload=Payload(text="new cap"), what="dm-edit")
    assert res.ok
    assert bot.edits[0]["kind"] == "photo"
    assert bot.edits[0]["caption"] == "new cap"


@pytest.mark.asyncio
async def test_edit_not_modified_is_success():
    bot = _FakeBot(edit_raises=RuntimeError(
        "Bad Request: message is not modified"))
    res = await _edit(_FakeApp(bot),
                      handle={"chat_id": 5, "message_id": 101, "kind": "text"},
                      payload=Payload(text="same"), what="dm-edit")
    assert res.ok


@pytest.mark.asyncio
async def test_edit_bad_handle_fails_closed():
    res = await _edit(_FakeApp(_FakeBot()), handle={},
                      payload=Payload(text="x"), what="dm-edit")
    assert not res.ok and res.error == "bad_handle"


@pytest.mark.asyncio
async def test_dm_channel_declares_and_routes_edit(monkeypatch):
    bot = _FakeBot()
    monkeypatch.setattr(tg_mod, "_bot_for",
                        lambda account_id, override=None: _FakeApp(bot))
    ch = TelegramDmChannel()
    assert getattr(ch, "supports_edit", False)
    res = await ch.edit(Recipient(account_id=1, type="user", id="9"),
                        {"chat_id": 9, "message_id": 101, "kind": "text"},
                        Payload(text="edited"))
    assert res.ok and bot.edits


# ── service: ledger recording on dispatch ───────────────────────────

def _sub(uid: int) -> dict:
    return {"recipient_type": "user", "recipient_id": uid,
            "address": str(uid), "cadence": "immediate"}


@pytest.mark.asyncio
async def test_dispatch_records_ledger_with_correlation_key():
    ch = _EditableChannel()
    register_channel(ch)
    db = _FakeDb(subs=[_sub(11), _sub(12)])
    results = await dispatch(
        db, 1, NotificationContent(title="t", category="x.y"),
        channels=["fake_editable"], correlation_key="alert:77")
    assert all(r.ok for r in results) and len(results) == 2
    assert len(db.recorded) == 2
    assert db.recorded[0]["correlation_key"] == "alert:77"
    assert db.recorded[0]["handle"]["message_id"] == 1


@pytest.mark.asyncio
async def test_dispatch_without_correlation_key_writes_nothing():
    ch = _EditableChannel()
    register_channel(ch)
    db = _FakeDb(subs=[_sub(11)])
    await dispatch(db, 1, NotificationContent(title="t", category="x.y"),
                   channels=["fake_editable"])
    assert db.recorded == []


@pytest.mark.asyncio
async def test_ledger_failure_never_sinks_the_delivery():
    ch = _EditableChannel()
    register_channel(ch)
    db = _FakeDb(subs=[_sub(11)])
    db.record_raises = RuntimeError("ledger down")
    results = await dispatch(
        db, 1, NotificationContent(title="t", category="x.y"),
        channels=["fake_editable"], correlation_key="alert:1")
    assert len(results) == 1 and results[0].ok   # delivery still reported ok


# ── service: update_delivery ────────────────────────────────────────

def _row(channel: str, uid: str = "11") -> dict:
    return {"channel": channel, "recipient_type": "user", "recipient_id": uid,
            "handle": {"chat_id": int(uid), "message_id": 1, "kind": "text"},
            "correlation_key": "alert:77"}


@pytest.mark.asyncio
async def test_update_delivery_edits_each_recorded_row():
    ch = _EditableChannel()
    register_channel(ch)
    db = _FakeDb()
    db.delivery_rows = [_row("fake_editable", "11"), _row("fake_editable", "12")]
    results = await update_delivery(
        db, 1, "alert:77", NotificationContent(title="acked", body="by AK"))
    assert len(results) == 2 and all(r.ok for r in results)
    # rendered fresh content reached the edit
    assert ch.edits[0][2].text == "acked|by AK"
    assert ch.edits[0][1]["message_id"] == 1
    assert db.cleared == []                       # no clear unless asked


@pytest.mark.asyncio
async def test_update_delivery_skips_channels_without_edit():
    noedit = _NoEditChannel()
    register_channel(noedit)
    db = _FakeDb()
    db.delivery_rows = [_row("fake_noedit")]
    results = await update_delivery(
        db, 1, "alert:77", NotificationContent(title="new"))
    assert results == [] and noedit.edits == []


@pytest.mark.asyncio
async def test_update_delivery_clear_only_when_all_ok():
    ch = _EditableChannel()
    register_channel(ch)
    db = _FakeDb()
    db.delivery_rows = [_row("fake_editable")]
    await update_delivery(db, 1, "alert:77",
                          NotificationContent(title="final"), clear=True)
    assert db.cleared == ["alert:77"]

    ch.edit_ok = False
    db2 = _FakeDb()
    db2.delivery_rows = [_row("fake_editable")]
    await update_delivery(db2, 1, "alert:77",
                          NotificationContent(title="final"), clear=True)
    assert db2.cleared == []                      # kept for a later retry
