"""Quiet hours on the notifications spine.

Pins the contract (docs/architecture/alert-dm-migration.md):

  • dispatch() defers an IMMEDIATE send into the 'quiet' cadence ONLY
    when: the channel disturbs (respects_quiet_hours), the recipient's
    registered rule says quiet, and severity is below critical —
    critical always cuts through; non-disturbing channels never defer
  • the provider is FAIL-OPEN: none registered / provider error → deliver
  • flush_quiet_deferrals: holds a recipient's batch while still quiet,
    delivers ONE summary when the window ends, re-checks consent at send
    time, clears only on success
  • source digest renderers: a single-source batch renders in the
    source's voice; renderer errors and mixed-source batches fall back
    to the generic digest
  • the alerting-side registrations: the quiet rule delegates to the DND
    SSOT (unknown user → deliver), and the 'alert' renderer produces the
    off-shift summary with critical lines spelled out
  • Telegram document rail: a content document becomes send_document with
    kind="document" in the handle; document edits go via caption

All fakes — no Telegram, no Postgres, no scheduler.
"""

from __future__ import annotations

import pytest

import capabilities.alerting.spine_quiet as spine_quiet
import capabilities.notifications.quiet_hours as qh
import capabilities.notifications.service as service
from capabilities.notifications.channels import (
    DeliveryResult,
    NotificationContent,
    Payload,
    register_channel,
)
from capabilities.notifications.quiet_hours import (
    QUIET_DEFER_CADENCE,
    is_recipient_quiet,
    register_quiet_hours_provider,
    severity_bypasses_quiet,
)
from capabilities.notifications.service import (
    dispatch,
    flush_quiet_deferrals,
    register_digest_renderer,
)
from capabilities.notifications.telegram import _send


@pytest.fixture(autouse=True)
def _restore_provider():
    """Each test picks its own provider; restore the module default so
    test order can't leak a stale rule (spine_quiet registers one at
    import)."""
    prev = qh._provider
    yield
    qh._provider = prev


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

class _QuietChannel:
    """Personal channel that disturbs (defers under quiet hours)."""
    key = "fake_quiet"
    personal = True
    respects_quiet_hours = True

    def __init__(self):
        self.sent: list = []

    def render(self, recipient, content):
        return Payload(text=f"{content.title}|{content.body}")

    async def send(self, recipient, payload):
        self.sent.append((recipient, payload))
        return DeliveryResult(ok=True)


class _CalmChannel(_QuietChannel):
    """Personal channel that does NOT disturb (no quiet deferral)."""
    key = "fake_calm"
    respects_quiet_hours = False


class _FakeDb:
    def __init__(self, subs=None):
        self._subs = subs or []
        self.enqueued: list[dict] = []
        self.cleared: list[list] = []
        self.queue: list[dict] = []
        self.conn: dict | None = {"address": "77", "verified": True,
                                  "enabled_master": True}

    async def get_notification_subscribers(self, account_id, category, channel):
        return list(self._subs)

    async def get_roles_for_users(self, ids):
        return {i: "fleet" for i in ids}

    async def enqueue_digest_item(self, account_id, rtype, rid, channel,
                                  cadence, category, line, address, *,
                                  severity="info"):
        self.enqueued.append({
            "account_id": account_id, "recipient_type": rtype,
            "recipient_id": rid, "channel": channel, "cadence": cadence,
            "category": category, "summary": line, "severity": severity,
        })

    async def fetch_due_digest_items(self, cadence, limit):
        return [i for i in self.queue if i["cadence"] == cadence]

    async def clear_digest_items(self, ids, *, account_id):
        self.cleared.append(list(ids))

    async def get_notification_channel(self, account_id, rtype, rid, key):
        return self.conn


def _sub(uid: int) -> dict:
    return {"recipient_type": "user", "recipient_id": uid,
            "address": str(uid), "cadence": "immediate"}


def _quiet(value: bool):
    async def rule(account_id, user_id):
        return value
    register_quiet_hours_provider(rule)


def _item(i: int, *, channel="fake_quiet", category="alert.faults",
          severity="info") -> dict:
    return {"id": i, "account_id": 1, "recipient_type": "user",
            "recipient_id": "77", "channel": channel,
            "cadence": QUIET_DEFER_CADENCE, "category": category,
            "summary": f"line {i}", "severity": severity, "address": "77"}


# ── policy primitives ───────────────────────────────────────────────

def test_severity_bypass_table():
    assert severity_bypasses_quiet("critical")
    assert not severity_bypasses_quiet("warning")
    assert not severity_bypasses_quiet("info")
    assert not severity_bypasses_quiet("unknown")


@pytest.mark.asyncio
async def test_no_provider_means_not_quiet():
    qh._provider = None
    assert await is_recipient_quiet(1, 77) is False


@pytest.mark.asyncio
async def test_provider_error_fails_open():
    async def broken(a, u):
        raise RuntimeError("scheduler down")
    register_quiet_hours_provider(broken)
    assert await is_recipient_quiet(1, 77) is False


# ── dispatch deferral ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_quiet_recipient_defers_on_disturbing_channel():
    ch = _QuietChannel()
    register_channel(ch)
    _quiet(True)
    db = _FakeDb(subs=[_sub(77)])
    await dispatch(db, 1, NotificationContent(title="t", category="alert.faults"),
                   channels=["fake_quiet"])
    assert ch.sent == []
    assert len(db.enqueued) == 1
    assert db.enqueued[0]["cadence"] == QUIET_DEFER_CADENCE


@pytest.mark.asyncio
async def test_critical_cuts_through_quiet():
    ch = _QuietChannel()
    register_channel(ch)
    _quiet(True)
    db = _FakeDb(subs=[_sub(77)])
    await dispatch(db, 1, NotificationContent(
        title="t", category="alert.faults", severity="critical"),
        channels=["fake_quiet"])
    assert len(ch.sent) == 1 and db.enqueued == []


@pytest.mark.asyncio
async def test_calm_channel_never_defers():
    ch = _CalmChannel()
    register_channel(ch)
    _quiet(True)
    db = _FakeDb(subs=[_sub(77)])
    await dispatch(db, 1, NotificationContent(title="t", category="alert.faults"),
                   channels=["fake_calm"])
    assert len(ch.sent) == 1 and db.enqueued == []


@pytest.mark.asyncio
async def test_not_quiet_delivers_immediately():
    ch = _QuietChannel()
    register_channel(ch)
    _quiet(False)
    db = _FakeDb(subs=[_sub(77)])
    await dispatch(db, 1, NotificationContent(title="t", category="alert.faults"),
                   channels=["fake_quiet"])
    assert len(ch.sent) == 1 and db.enqueued == []


# ── quiet flush ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_flush_holds_while_still_quiet():
    ch = _QuietChannel()
    register_channel(ch)
    _quiet(True)
    db = _FakeDb()
    db.queue = [_item(1), _item(2)]
    sent = await flush_quiet_deferrals(db)
    assert sent == 0 and ch.sent == [] and db.cleared == []


@pytest.mark.asyncio
async def test_flush_delivers_one_summary_when_window_ends():
    ch = _QuietChannel()
    register_channel(ch)
    _quiet(False)
    db = _FakeDb()
    db.queue = [_item(1), _item(2), _item(3)]
    sent = await flush_quiet_deferrals(db)
    assert sent == 1
    assert len(ch.sent) == 1                      # ONE summary, not three
    assert db.cleared == [[1, 2, 3]]


@pytest.mark.asyncio
async def test_flush_drops_batch_when_consent_revoked():
    ch = _QuietChannel()
    register_channel(ch)
    _quiet(False)
    db = _FakeDb()
    db.conn = {"address": "77", "verified": True, "enabled_master": False}
    db.queue = [_item(1)]
    sent = await flush_quiet_deferrals(db)
    assert sent == 0 and ch.sent == []
    assert db.cleared == [[1]]                    # dropped, not kept


# ── digest renderers ────────────────────────────────────────────────

def test_source_renderer_used_for_single_source_batch():
    def my_renderer(items, cadence):
        return NotificationContent(title="custom!", body=str(len(items)))
    register_digest_renderer("testsrc2", my_renderer)
    content = service._render_digest(
        [{"category": "testsrc2.a", "summary": "x", "severity": "info"},
         {"category": "testsrc2.b", "summary": "y", "severity": "info"}],
        QUIET_DEFER_CADENCE)
    assert content.title == "custom!" and content.body == "2"


def test_renderer_error_falls_back_to_generic():
    def broken(items, cadence):
        raise RuntimeError("boom")
    register_digest_renderer("brokensrc", broken)
    content = service._render_digest(
        [{"category": "brokensrc.a", "summary": "x", "severity": "info"}],
        "hourly")
    assert "summary" in content.title             # generic digest title


def test_mixed_source_batch_uses_generic():
    def never(items, cadence):
        raise AssertionError("must not be called")
    register_digest_renderer("mixsrc", never)
    content = service._render_digest(
        [{"category": "mixsrc.a", "summary": "x", "severity": "info"},
         {"category": "othersrc.b", "summary": "y", "severity": "info"}],
        "hourly")
    assert "summary" in content.title


# ── alerting-side registrations ─────────────────────────────────────

def test_alert_renderer_off_shift_summary():
    items = [_item(1, category="alert.faults"),
             _item(2, category="alert.faults"),
             _item(3, category="alert.parking", severity="critical")]
    content = spine_quiet._render_alert_digest(items, QUIET_DEFER_CADENCE)
    assert "off shift" in content.title and "3 alerts" in content.title
    assert "faults: 2" in content.body
    assert "🔴 line 3" in content.body            # critical spelled out
    assert content.severity == "critical"


@pytest.mark.asyncio
async def test_alert_quiet_rule_unknown_user_delivers(monkeypatch):
    class _Db:
        async def get_user(self, uid):
            return None

    def _pdb():
        return _Db()
    monkeypatch.setattr("infra.platform.get_platform_db", _pdb)
    assert await spine_quiet._alert_quiet_rule(1, 999) is False


@pytest.mark.asyncio
async def test_alert_quiet_rule_delegates_to_dnd(monkeypatch):
    user = type("U", (), {"account_id": 4})()

    class _Db:
        async def get_user(self, uid):
            return user

    def _pdb():
        return _Db()

    async def _tdb(aid):
        return "TENANT"
    monkeypatch.setattr("infra.platform.get_platform_db", _pdb)
    monkeypatch.setattr("infra.platform.get_tenant_db", _tdb)

    async def fake_dnd(u, tenant):
        assert u is user and tenant == "TENANT"
        return True
    monkeypatch.setattr("capabilities.alerting.dnd.is_user_dnd_active", fake_dnd)
    assert await spine_quiet._alert_quiet_rule(4, 77) is True


# ── Telegram document rail ──────────────────────────────────────────

class _DocBot:
    def __init__(self):
        self.docs: list[dict] = []

    async def send_document(self, **kw):
        self.docs.append(kw)

        class _R:
            message_id = 55
        return _R()


@pytest.mark.asyncio
async def test_send_document_branch_and_handle_kind():
    bot = _DocBot()
    app = type("A", (), {"bot": bot})()
    res = await _send(app, chat_id=9, thread_id=None,
                      payload=Payload(text="report", document_bytes=b"%PDF",
                                      document_name="shift.pdf"),
                      what="dm")
    assert res.ok
    assert bot.docs[0]["filename"] == "shift.pdf"
    assert res.handle["kind"] == "document"


# ── source-side deferral (the retired private queue's replacement) ──

@pytest.mark.asyncio
async def test_defer_notification_enqueues_quiet_item():
    db = _FakeDb()
    ok = await qh.defer_notification(
        db, 7, user_id=42, address="1527638770",
        category="alert.camera", line="📷 Camera alerts (2 warnings)",
        severity="warning")
    assert ok is True
    item = db.enqueued[0]
    assert item["cadence"] == QUIET_DEFER_CADENCE
    assert item["recipient_id"] == "42" and item["channel"] == "telegram_dm"
    assert item["category"] == "alert.camera"
    assert item["severity"] == "warning"


@pytest.mark.asyncio
async def test_defer_notification_failure_returns_false():
    class _Broken(_FakeDb):
        async def enqueue_digest_item(self, *a, **kw):
            raise RuntimeError("db down")
    ok = await qh.defer_notification(
        _Broken(), 7, user_id=42, address="x",
        category="alert.documents", line="l")
    assert ok is False                            # caller may send live
