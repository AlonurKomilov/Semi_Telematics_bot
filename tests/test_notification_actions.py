"""Action buttons + callback routing on the notifications spine.

Pins the contract (docs/architecture/alert-dm-migration.md):

  • callback data ``notif_act:{correlation_key}:{action_id}`` builds and
    parses round-trip — including correlation keys that contain colons —
    and refuses to build past Telegram's 64-byte cap
  • ``render_telegram`` turns ``content.actions`` into an inline keyboard
    ONLY when the routing address (meta correlation_key) exists; a
    missing key or an over-long button drops the buttons, never ships
    dead ones
  • ``handle_action_callback`` routes a press to the registered
    ``{source}.{action_id}`` handler with a faithful ActionContext, and
    answers politely on unknown actions / missing handlers / handler
    crashes
  • the alerting-side ``alert.ack`` handler acks the HISTORY row (same
    cascade as the dashboard), edits every recorded delivery via
    ``update_delivery(clear=True)``, and never lets a cosmetic edit
    failure undo the ack

All fakes — no Telegram, no Postgres.
"""

from __future__ import annotations

import pytest

import capabilities.alerting.spine_actions as spine_actions
from capabilities.notifications.actions import (
    ActionContext,
    build_action_callback_data,
    get_action_handler,
    handle_action_callback,
    parse_action_callback_data,
    register_action_handler,
)
from capabilities.notifications.channels import NotificationContent
from capabilities.notifications.telegram import render_telegram


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


# ── callback data build / parse ─────────────────────────────────────

def test_roundtrip_with_colons_in_correlation_key():
    data = build_action_callback_data("alert:12345", "ack")
    assert data == "notif_act:alert:12345:ack"
    assert parse_action_callback_data(data) == ("alert:12345", "ack")


def test_build_refuses_over_64_bytes():
    assert build_action_callback_data("alert:" + "9" * 80, "ack") is None


def test_parse_rejects_foreign_and_malformed():
    assert parse_action_callback_data("ack_alert_5") is None
    assert parse_action_callback_data("notif_act:") is None
    assert parse_action_callback_data("notif_act:no-action-part") is None


# ── rendering ───────────────────────────────────────────────────────

def _content(**kw) -> NotificationContent:
    base = dict(title="Fault", body="ECU 128", category="alert.faults",
                actions=[{"id": "ack", "label": "✅ Acknowledge"}])
    base.update(kw)
    return NotificationContent(**base)


def test_render_builds_keyboard_when_correlation_key_present():
    c = _content(meta={"correlation_key": "alert:7"})
    payload = render_telegram(c)
    assert payload.markup is not None
    btn = payload.markup.inline_keyboard[0][0]
    assert btn.text == "✅ Acknowledge"
    assert btn.callback_data == "notif_act:alert:7:ack"


def test_render_drops_buttons_without_correlation_key():
    assert render_telegram(_content()).markup is None


def test_render_drops_buttons_when_key_too_long():
    c = _content(meta={"correlation_key": "alert:" + "9" * 80})
    assert render_telegram(c).markup is None


def test_render_without_actions_has_no_markup():
    assert render_telegram(_content(actions=[])).markup is None


def test_render_empty_title_body_only():
    p = render_telegram(NotificationContent(title="", body="✅ Acked by AK"))
    assert "<b></b>" not in p.text
    assert p.text == "✅ Acked by AK"


# ── callback dispatch ───────────────────────────────────────────────

class _FakeQuery:
    def __init__(self, data: str, *, with_message: bool = True):
        self.data = data
        self.answers: list[tuple] = []
        self.from_user = type("U", (), {"id": 555, "full_name": "AK",
                                        "first_name": "AK"})()
        if with_message:
            self.message = type("M", (), {
                "chat_id": 900, "message_id": 33, "photo": None,
                "message_thread_id": None, "text": "🔴 Fault ECU 128",
                "caption": None,
            })()
        else:
            self.message = None

    async def answer(self, text="", show_alert=False):
        self.answers.append((text, show_alert))


class _FakeUpdate:
    def __init__(self, query):
        self.callback_query = query


class _FakeContext:
    def __init__(self, account_id=1):
        self.bot_data = {"account_id": account_id}


@pytest.mark.asyncio
async def test_dispatch_routes_to_registered_handler():
    seen: list[ActionContext] = []

    async def handler(ctx: ActionContext):
        seen.append(ctx)
        return "Handled!"

    register_action_handler("testsrc.poke", handler)
    q = _FakeQuery("notif_act:testsrc:42:poke")
    await handle_action_callback(_FakeUpdate(q), _FakeContext(account_id=9))
    assert q.answers == [("Handled!", False)]
    ctx = seen[0]
    assert ctx.account_id == 9
    assert ctx.correlation_key == "testsrc:42"
    assert ctx.action_id == "poke" and ctx.source == "testsrc"
    assert ctx.presser_telegram_id == 555 and ctx.presser_name == "AK"
    assert ctx.message_handle == {"chat_id": 900, "message_id": 33,
                                  "kind": "text"}
    assert ctx.message_text == "🔴 Fault ECU 128"


@pytest.mark.asyncio
async def test_dispatch_unknown_handler_answers_politely():
    q = _FakeQuery("notif_act:ghost:1:vanish")
    await handle_action_callback(_FakeUpdate(q), _FakeContext())
    assert "no longer available" in q.answers[0][0]


@pytest.mark.asyncio
async def test_dispatch_handler_crash_answers_politely():
    async def bad(ctx):
        raise RuntimeError("boom")

    register_action_handler("crashsrc.go", bad)
    q = _FakeQuery("notif_act:crashsrc:1:go")
    await handle_action_callback(_FakeUpdate(q), _FakeContext())
    assert "went wrong" in q.answers[0][0]


# ── alert.ack (the alerting-side handler) ───────────────────────────

class _FakeTenant:
    def __init__(self, *, history_ack=True, delivery_ack=False):
        self._history_ack = history_ack
        self._delivery_ack = delivery_ack
        self.history_calls: list = []
        self.delivery_calls: list = []

    async def acknowledge_alert_history(self, hid, tid, *, account_id):
        self.history_calls.append((hid, tid, account_id))
        return self._history_ack

    async def acknowledge_alert(self, aid, tid, *, account_id):
        self.delivery_calls.append((aid, tid, account_id))
        return self._delivery_ack


def _ctx(key="alert:77", text="🔴 Fault") -> ActionContext:
    return ActionContext(
        account_id=4, correlation_key=key, action_id="ack", source="alert",
        presser_telegram_id=555, presser_name="AK", message_text=text)


@pytest.mark.asyncio
async def test_alert_ack_registered_at_boot():
    assert get_action_handler("alert.ack") is not None


@pytest.mark.asyncio
async def test_alert_ack_acks_history_and_updates_deliveries(monkeypatch):
    tenant = _FakeTenant(history_ack=True)
    async def _fake_tenant_db(aid):
        return tenant
    monkeypatch.setattr("infra.platform.get_tenant_db", _fake_tenant_db)
    updates: list = []

    async def fake_update(db, account_id, key, content, **kw):
        updates.append((account_id, key, content, kw))
        return []

    monkeypatch.setattr(spine_actions, "update_delivery", fake_update)
    toast = await spine_actions._handle_ack(_ctx())
    assert toast == "Acknowledged ✅"
    assert tenant.history_calls == [(77, 555, 4)]
    assert tenant.delivery_calls == []            # history ack sufficed
    account_id, key, content, kw = updates[0]
    assert (account_id, key) == (4, "alert:77")
    assert "Acknowledged by AK" in content.body
    assert "🔴 Fault" in content.body             # original text preserved
    assert kw.get("clear") is True                # final edit → ledger clear


@pytest.mark.asyncio
async def test_alert_ack_falls_back_to_delivery_ack(monkeypatch):
    tenant = _FakeTenant(history_ack=False, delivery_ack=True)
    async def _fake_tenant_db(aid):
        return tenant
    monkeypatch.setattr("infra.platform.get_tenant_db", _fake_tenant_db)

    async def fake_update(*a, **kw):
        return []

    monkeypatch.setattr(spine_actions, "update_delivery", fake_update)
    toast = await spine_actions._handle_ack(_ctx())
    assert toast == "Acknowledged ✅"
    assert tenant.delivery_calls == [(77, 555, 4)]


@pytest.mark.asyncio
async def test_alert_ack_already_acked(monkeypatch):
    tenant = _FakeTenant(history_ack=False, delivery_ack=False)
    async def _fake_tenant_db(aid):
        return tenant
    monkeypatch.setattr("infra.platform.get_tenant_db", _fake_tenant_db)
    toast = await spine_actions._handle_ack(_ctx())
    assert toast == "Already acknowledged"


@pytest.mark.asyncio
async def test_alert_ack_edit_failure_does_not_undo_ack(monkeypatch):
    tenant = _FakeTenant(history_ack=True)
    async def _fake_tenant_db(aid):
        return tenant
    monkeypatch.setattr("infra.platform.get_tenant_db", _fake_tenant_db)

    async def broken_update(*a, **kw):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(spine_actions, "update_delivery", broken_update)
    toast = await spine_actions._handle_ack(_ctx())
    assert toast == "Acknowledged ✅"             # ack stands


@pytest.mark.asyncio
async def test_alert_ack_invalid_key():
    assert await spine_actions._handle_ack(_ctx(key="alert:not-a-number")) \
        == "Invalid alert"
