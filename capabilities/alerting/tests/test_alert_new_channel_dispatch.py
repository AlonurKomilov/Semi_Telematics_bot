"""N2 — live alerts fan out over the new channels (email + web push).

``send_alert`` delivers Telegram on its own proven path; once an account
switches ``NOTIFICATIONS_LIVE_DISPATCH`` on, the SAME firing alert is also
handed to the source-agnostic Notifications service via
``dispatch_new_channels`` for delivery over the channels a user opted into
in the notification matrix.

Pins the seam contract:
  • flag OFF  → dispatch() is never called (zero behaviour change)
  • flag ON   → dispatch() gets a broadcast NotificationContent whose
    category is the CANONICAL ``alert.<route_key>`` (verbose pipeline
    "fault" → registry "faults"), restricted to the email + web_push
    channels (Telegram is delivered separately), with a plain-text body
    (Telegram HTML tags/entities stripped) and the alert severity
  • an alert type with no registered category is Telegram-only (no
    ungated fan-out)
  • company-scoped accounts deliver, passing dispatch() a per-user
    predicate that drops recipients scoped out of the alert's company
    (parity with Telegram; fail-open on no-company / no-scope / errors)
  • a Notifications failure is swallowed — an alert Telegram is about to
    deliver must never be sunk by an email/push error

The real category registry is used (importing ``capabilities.alerting``
registers ``alert.*``), so a category-naming mismatch would fail here.
"""

from __future__ import annotations

import pytest

import capabilities.alerting            # noqa: F401 — registers alert.* categories
import capabilities.alerting.pipeline as pipeline
from capabilities.alerting.pipeline import (
    AlertSeverity,
    dispatch_new_channels,
    _strip_alert_html,
)
from capabilities.notifications.categories import get_category


class _RecordingDispatch:
    """Stand-in for capabilities.notifications.dispatch — records its args."""

    def __init__(self, raises: Exception | None = None):
        self.calls: list[dict] = []
        self._raises = raises

    async def __call__(self, db, account_id, content, *, channels=None,
                       recipient_filter=None):
        self.calls.append(
            {"db": db, "account_id": account_id, "content": content,
             "channels": tuple(channels) if channels is not None else None,
             "recipient_filter": recipient_filter}
        )
        if self._raises is not None:
            raise self._raises
        return []


def _patch(monkeypatch, *, flag: bool, dispatch: _RecordingDispatch,
           scope: dict | None = None):
    monkeypatch.setattr(pipeline, "_NOTIFICATIONS_LIVE_DISPATCH", flag)
    monkeypatch.setattr(pipeline, "get_platform_db", lambda: "PLATFORM_DB")
    # The helper imports dispatch from capabilities.notifications at call
    # time; patch it at the source so the recording stand-in is used.
    monkeypatch.setattr("capabilities.notifications.dispatch", dispatch)

    async def _fake_scope(account_id):
        return scope or {}
    monkeypatch.setattr(
        "capabilities.alerting.company_scope.load_company_scope", _fake_scope)


@pytest.mark.asyncio
async def test_flag_off_never_dispatches(monkeypatch):
    disp = _RecordingDispatch()
    _patch(monkeypatch, flag=False, dispatch=disp)

    await dispatch_new_channels(
        account_id=7, alert_type="fault", severity=AlertSeverity.CRITICAL,
        vehicle_name="Truck 5", alert_text="<b>engine</b> fault",
    )

    assert disp.calls == []          # inert until an account switches it on


@pytest.mark.asyncio
async def test_flag_on_dispatches_broadcast_content_to_new_channels(monkeypatch):
    disp = _RecordingDispatch()
    _patch(monkeypatch, flag=True, dispatch=disp)

    await dispatch_new_channels(
        account_id=42, alert_type="fault", severity=AlertSeverity.WARNING,
        vehicle_name="Truck 5",
        alert_text="<b>Engine</b> fault &amp; derate <code>P0420</code>",
        maps_url="https://maps.google.com/?q=1,2",
    )

    assert len(disp.calls) == 1
    call = disp.calls[0]
    assert call["db"] == "PLATFORM_DB"
    assert call["account_id"] == 42
    # Telegram is delivered separately — the service only handles the new
    # channels, never re-sends Telegram.
    assert call["channels"] == ("email", "web_push")

    content = call["content"]
    # Canonical category: verbose "fault" → registry "faults".  This is the
    # string dispatch() looks categories up by, so it MUST resolve.
    assert content.category == "alert.faults"
    assert get_category(content.category) is not None
    assert content.severity == "warning"
    assert content.title == "Truck 5 — fault"
    # body is RAW plain text: tags gone, entity decoded (channels escape
    # exactly once at render time).
    assert content.body == "Engine fault & derate P0420"
    assert "<b>" not in content.body and "&amp;" not in content.body
    assert content.url == "https://maps.google.com/?q=1,2"


@pytest.mark.asyncio
async def test_unregistered_alert_type_stays_telegram_only(monkeypatch):
    """An alert type with NO registered category must not fan out
    ungated over the new channels.

    Membership is DERIVED, never listed.  The hardcoded list here read
    "doc_expiry / scorecard / system" and rotted the day those two
    gained real categories (features/drivers/documents/alert.py registers
    alert.documents) — the test then failed for having the wrong
    membership, not for the rule being broken.  Deriving it means the
    rule keeps holding as categories are added."""
    disp = _RecordingDispatch()
    _patch(monkeypatch, flag=True, dispatch=disp)

    candidates = ("doc_expiry", "scorecard", "samsara_sync", "system")
    unregistered = [
        a for a in candidates
        if get_category(
            f"alert.{pipeline._PIPELINE_TO_ROUTE_KEY.get(a, a)}") is None
    ]
    assert unregistered, (
        "expected at least one uncategorised alert type to exercise the "
        "rule — if every type now has a category, delete this test"
    )
    for atype in unregistered:
        await dispatch_new_channels(
            account_id=1, alert_type=atype, severity=AlertSeverity.INFO,
            vehicle_name="Truck 1", alert_text="x",
        )
    assert disp.calls == [], (
        f"uncategorised types fanned out ungated: {unregistered}"
    )


@pytest.mark.asyncio
async def test_company_scoped_account_passes_per_user_predicate(monkeypatch):
    """An account with company-restricted users now DELIVERS (no account-wide
    hold-back), passing dispatch() a per-user predicate that scopes each
    recipient — parity with the Telegram path."""
    disp = _RecordingDispatch()
    _patch(monkeypatch, flag=True, dispatch=disp,
           scope={17: ["ACME"], 18: []})   # user 17 restricted, 18 not

    await dispatch_new_channels(
        account_id=99, alert_type="fault", severity=AlertSeverity.WARNING,
        vehicle_name="Truck 5", alert_text="fault", co="OTHERCO",
    )
    assert len(disp.calls) == 1
    flt = disp.calls[0]["recipient_filter"]
    assert flt is not None
    # The restricted user (ACME only) is dropped for an OTHERCO alert;
    # the unrestricted user and an owner are kept.
    assert flt(17, "fleet") is False
    assert flt(18, "fleet") is True
    assert flt(1, "owner") is True


@pytest.mark.asyncio
async def test_no_company_means_no_filter(monkeypatch):
    """No alert company (co="") → fail-open, no predicate built even when
    users are scoped (matches Telegram: a company-less alert reaches all)."""
    disp = _RecordingDispatch()
    _patch(monkeypatch, flag=True, dispatch=disp, scope={17: ["ACME"]})

    await dispatch_new_channels(
        account_id=99, alert_type="fault", severity=AlertSeverity.WARNING,
        vehicle_name="Truck 5", alert_text="fault", co="",
    )
    assert len(disp.calls) == 1
    assert disp.calls[0]["recipient_filter"] is None


@pytest.mark.asyncio
async def test_unscoped_account_no_filter(monkeypatch):
    """Users present but none restricted → no predicate (deliver to all)."""
    disp = _RecordingDispatch()
    _patch(monkeypatch, flag=True, dispatch=disp,
           scope={17: [], 18: []})         # everyone unrestricted

    await dispatch_new_channels(
        account_id=99, alert_type="health", severity=AlertSeverity.INFO,
        vehicle_name="Truck 5", alert_text="battery low", co="ACME",
    )
    assert len(disp.calls) == 1
    assert disp.calls[0]["recipient_filter"] is None
    assert disp.calls[0]["content"].category == "alert.health"


@pytest.mark.asyncio
async def test_notifications_failure_is_swallowed(monkeypatch):
    disp = _RecordingDispatch(raises=RuntimeError("smtp down"))
    _patch(monkeypatch, flag=True, dispatch=disp)

    # Must NOT raise — an alert Telegram is about to deliver survives an
    # email/push failure.
    await dispatch_new_channels(
        account_id=1, alert_type="health", severity=AlertSeverity.INFO,
        vehicle_name="Reefer 9", alert_text="battery low",
    )
    assert len(disp.calls) == 1       # attempted, then error swallowed


def test_user_sees_company_rules():
    """The pure company gate (fail-open, owner-unrestricted, case-insens)."""
    from capabilities.alerting.company_scope import user_sees_company
    # Owner is always unrestricted, even with a scope row.
    assert user_sees_company(1, "owner", "ACME", {1: ["OTHER"]}) is True
    # No alert company → deliver to all.
    assert user_sees_company(2, "fleet", "", {2: ["OTHER"]}) is True
    # Not in the scope map → unrestricted.
    assert user_sees_company(3, "fleet", "ACME", {}) is True
    # Empty allowed list → unrestricted.
    assert user_sees_company(4, "fleet", "ACME", {4: []}) is True
    # Restricted, case-insensitive match / mismatch.
    assert user_sees_company(5, "fleet", "acme", {5: ["ACME"]}) is True
    assert user_sees_company(6, "fleet", "ACME", {6: ["OTHER"]}) is False


def test_strip_alert_html_removes_tags_and_unescapes_entities():
    assert _strip_alert_html("<b>Truck 5</b>\n&amp; fault <code>P0420</code>") == (
        "Truck 5\n& fault P0420"
    )
    assert _strip_alert_html("") == ""
    assert _strip_alert_html("plain text") == "plain text"
