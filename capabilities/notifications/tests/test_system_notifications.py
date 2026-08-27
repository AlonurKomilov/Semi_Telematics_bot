"""N7 — the system.* source: billing + sign-in notices.

Pins the System-tab producers:

  • system.billing — BROADCAST + MANDATORY, audience = can_manage_billing
    (owner/admin hear it; fleet/driver never do); the billing Telegram
    funnel mirrors each event into the in-app inbox with a clean title
    map and HTML-stripped body; a notification failure never breaks the
    Telegram path.
  • system.security — TARGETED + MANDATORY; new-device detection is
    label-based and fail-closed; the announce carries the Review-sessions
    inline action and never raises out of a login.
"""

from __future__ import annotations

import pytest

import capabilities.platform.billing.notifications as bn
import interfaces.api.security_notifications as sec
from capabilities.notifications.categories import BROADCAST, TARGETED, get_category


class _RecordingDispatch:
    def __init__(self, raises=None):
        self.calls = []
        self._raises = raises

    async def __call__(self, db, account_id, content, *, channels=None,
                       recipient_filter=None):
        self.calls.append({"account_id": account_id, "content": content,
                           "channels": channels})
        if self._raises:
            raise self._raises
        return []


class _RecordingNotify:
    def __init__(self, raises=None):
        self.calls = []
        self._raises = raises

    async def __call__(self, db, account_id, user_id, content, *, channels=None):
        self.calls.append({"account_id": account_id, "user_id": user_id,
                           "content": content, "channels": channels})
        if self._raises:
            raise self._raises
        return []


# ── system.billing ───────────────────────────────────────────────────

def test_billing_category_broadcast_mandatory_audience():
    cat = get_category("system.billing")
    assert cat is not None and cat.kind == BROADCAST and cat.mandatory
    assert cat.source == "system"
    # Audience = permission SSOT: billing-capable roles only.
    assert cat.audience("owner") is True
    assert cat.audience("admin") is True
    assert cat.audience("fleet") is False
    assert cat.audience("driver") is False
    assert cat.audience("not_a_role") is False      # fail-closed


@pytest.mark.asyncio
async def test_billing_event_mirrors_to_inbox(monkeypatch):
    disp = _RecordingDispatch()
    monkeypatch.setattr("capabilities.notifications.dispatch", disp)
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: "DB",
                        raising=False)

    await bn._record_in_inbox(
        "payment_failed", 42,
        "<b>Payment failed</b> — update your card by <b>Aug 1</b>.")

    assert len(disp.calls) == 1
    call = disp.calls[0]
    assert call["account_id"] == 42
    assert call["channels"] == ["in_app"]            # Telegram/Stripe own the rest
    c = call["content"]
    assert c.category == "system.billing"
    assert c.title == "Payment failed"
    assert c.severity == "warning"
    assert "<b>" not in c.body                       # HTML stripped
    assert c.body == "Payment failed — update your card by Aug 1."
    assert c.url == "/billing"
    assert c.meta["context"] == "Billing"
    assert c.meta["action"] == {"label": "Open billing", "url": "/billing"}


@pytest.mark.asyncio
async def test_billing_inbox_failure_never_raises(monkeypatch):
    disp = _RecordingDispatch(raises=RuntimeError("inbox down"))
    monkeypatch.setattr("capabilities.notifications.dispatch", disp)
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: "DB",
                        raising=False)
    # Must NOT raise — the Telegram send already happened.
    await bn._record_in_inbox("payment_recovered", 1, "ok")
    assert len(disp.calls) == 1


# ── system.security ──────────────────────────────────────────────────

def test_security_category_targeted_mandatory():
    cat = get_category("system.security")
    assert cat is not None and cat.kind == TARGETED and cat.mandatory
    assert cat.source == "system"


class _SessionsDB:
    def __init__(self, labels, raises=False):
        self._labels = labels
        self._raises = raises

    async def list_user_sessions(self, user_id):
        if self._raises:
            raise RuntimeError("db down")
        return [{"device_label": l} for l in self._labels]


@pytest.mark.asyncio
async def test_is_new_device_label_matching():
    db = _SessionsDB(["Chrome · Windows", "Safari · iPhone"])
    assert await sec.is_new_device(db, 1, "Firefox · Linux") is True
    assert await sec.is_new_device(db, 1, "Chrome · Windows") is False


@pytest.mark.asyncio
async def test_is_new_device_fails_closed_on_error():
    """A storage blip must not spam sign-in notices — treat as NOT new."""
    assert await sec.is_new_device(_SessionsDB([], raises=True), 1, "X") is False


@pytest.mark.asyncio
async def test_announce_signin_content_and_action(monkeypatch):
    notify = _RecordingNotify()
    monkeypatch.setattr(sec, "notify_user", notify)
    monkeypatch.setattr(sec, "_SECURITY_EVENTS_ENABLED", True)

    await sec.announce_new_device_signin(
        "DB", 42, 7, device_label="Chrome · Windows", ip="1.2.3.4")

    assert len(notify.calls) == 1
    call = notify.calls[0]
    assert call["account_id"] == 42 and call["user_id"] == 7
    assert call["channels"] is None                  # every personal channel
    c = call["content"]
    assert c.category == "system.security"
    assert c.title == "New sign-in — Chrome · Windows"
    assert "1.2.3.4" in c.body and "review" in c.body.lower()
    assert c.url == "/profile"
    assert c.meta["context"] == "Security"
    assert c.meta["action"] == {"label": "Review sessions", "url": "/profile"}


@pytest.mark.asyncio
async def test_announce_signin_kill_switch_and_non_fatal(monkeypatch):
    notify = _RecordingNotify()
    monkeypatch.setattr(sec, "notify_user", notify)
    monkeypatch.setattr(sec, "_SECURITY_EVENTS_ENABLED", False)
    await sec.announce_new_device_signin("DB", 1, 2, device_label="X")
    assert notify.calls == []

    # Failure never raises out of a login.
    notify2 = _RecordingNotify(raises=RuntimeError("smtp down"))
    monkeypatch.setattr(sec, "notify_user", notify2)
    monkeypatch.setattr(sec, "_SECURITY_EVENTS_ENABLED", True)
    await sec.announce_new_device_signin("DB", 1, 2, device_label="X")
    assert len(notify2.calls) == 1
