"""Alert DMs through the notifications spine — the reader/writer switch.

Pins the fanout contract (docs/architecture/alert-dm-migration.md):

  • ``_dm_via_spine``: per-account ``alert_dm_spine`` setting, ships OFF,
    fail-closed to the proven legacy path on any settings error
  • ``_spine_dm_fanout``: dispatches on the ``telegram_dm`` channel only,
    correlation key ``alert:{history_id}``, ✅ Acknowledge action only for
    ackable severities WITH a history id; unregistered category or an
    outer dispatch error returns False so the caller falls back to the
    legacy loop (delivery guaranteed over dedup in the parity window)
  • the recipient predicate mirrors the legacy loop exactly: a driver
    WITH a truck only hears that truck (substring on vehicle name), a
    driver without one is not narrowed, company scope composes on top

All fakes — no Telegram, no Postgres.
"""

from __future__ import annotations

import pytest

import capabilities.alerting  # noqa: F401 — registers alert.* categories
import capabilities.alerting.pipeline as pipeline
from adapters.storage import Role
from capabilities.alerting.pipeline import (
    AlertSeverity,
    _dm_via_spine,
    _spine_dm_fanout,
)


class _RecordingDispatch:
    def __init__(self, raises: Exception | None = None):
        self.calls: list[dict] = []
        self._raises = raises

    async def __call__(self, db, account_id, content, *, channels=None,
                       recipient_filter=None, correlation_key=""):
        self.calls.append({
            "account_id": account_id, "content": content,
            "channels": tuple(channels) if channels is not None else None,
            "recipient_filter": recipient_filter,
            "correlation_key": correlation_key,
        })
        if self._raises:
            raise self._raises
        return []


class _Sub:
    def __init__(self, uid, role=Role.FLEET, truck_num=""):
        self.id = uid
        self.role = role
        self.truck_num = truck_num


class _LedgerDb:
    async def get_notification_deliveries(self, account_id, key, *, channel=""):
        return []


def _patch(monkeypatch, dispatch, *, scope=None):
    monkeypatch.setattr("capabilities.notifications.dispatch", dispatch)
    monkeypatch.setattr(pipeline, "get_platform_db", lambda: _LedgerDb())

    async def _fake_scope(account_id):
        return scope or {}
    monkeypatch.setattr(
        "capabilities.alerting.company_scope.load_company_scope", _fake_scope)


async def _run(monkeypatch, dispatch, *, alert_type="fault",
               severity=AlertSeverity.CRITICAL, history_id=77,
               needs_ack=True, subscribers=None, co="G1", vname="Truck 105"):
    _patch(monkeypatch, dispatch)
    return await _spine_dm_fanout(
        account_id=1, alert_type=alert_type, severity=severity,
        alert_text="<b>fault</b> on Truck 105", vname=vname,
        photo_bytes=None, video_url="", history_id=history_id,
        needs_ack=needs_ack, subscribers=subscribers or [], co=co)


# ── the account switch ──────────────────────────────────────────────

class _SettingsDb:
    def __init__(self, value=None, raises=False):
        self._v, self._raises = value, raises

    async def get_account_setting(self, account_id, key):
        if self._raises:
            raise RuntimeError("db down")
        return self._v


@pytest.mark.asyncio
async def test_switch_reads_setting():
    assert await _dm_via_spine(_SettingsDb("1"), 1) is True
    assert await _dm_via_spine(_SettingsDb("0"), 1) is False
    assert await _dm_via_spine(_SettingsDb(None), 1) is False


@pytest.mark.asyncio
async def test_switch_fails_closed_to_legacy():
    assert await _dm_via_spine(_SettingsDb(raises=True), 1) is False


# ── the fanout ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatches_telegram_dm_with_correlation_and_ack(monkeypatch):
    disp = _RecordingDispatch()
    handled = await _run(monkeypatch, disp)
    assert handled is True
    call = disp.calls[0]
    assert call["channels"] == ("telegram_dm",)
    assert call["correlation_key"] == "alert:77"
    assert call["content"].category == "alert.faults"
    assert call["content"].actions == [{"id": "ack", "label": "✅ Acknowledge"}]
    assert "<b>" not in call["content"].body          # HTML stripped


@pytest.mark.asyncio
async def test_info_has_no_ack_action(monkeypatch):
    disp = _RecordingDispatch()
    await _run(monkeypatch, disp, severity=AlertSeverity.INFO, needs_ack=False)
    assert disp.calls[0]["content"].actions == []


@pytest.mark.asyncio
async def test_no_history_id_means_no_key_and_no_buttons(monkeypatch):
    disp = _RecordingDispatch()
    await _run(monkeypatch, disp, history_id=None)
    call = disp.calls[0]
    assert call["correlation_key"] == "" and call["content"].actions == []


@pytest.mark.asyncio
async def test_unregistered_category_falls_back_to_legacy(monkeypatch):
    disp = _RecordingDispatch()
    handled = await _run(monkeypatch, disp, alert_type="mystery_type")
    assert handled is False and disp.calls == []


@pytest.mark.asyncio
async def test_dispatch_error_falls_back_to_legacy(monkeypatch):
    disp = _RecordingDispatch(raises=RuntimeError("spine down"))
    handled = await _run(monkeypatch, disp)
    assert handled is False


# ── the recipient predicate (legacy-parity scoping) ─────────────────

@pytest.mark.asyncio
async def test_driver_truck_rule_mirrors_legacy(monkeypatch):
    disp = _RecordingDispatch()
    subs = [
        _Sub(1, Role.DRIVER, truck_num="105"),   # matches "Truck 105"
        _Sub(2, Role.DRIVER, truck_num="207"),   # other truck → dropped
        _Sub(3, Role.DRIVER, truck_num=""),      # no truck → not narrowed
        _Sub(4, Role.FLEET),                     # staff → never narrowed
    ]
    await _run(monkeypatch, disp, subscribers=subs, vname="Truck 105")
    pred = disp.calls[0]["recipient_filter"]
    assert pred(1, "driver") is True
    assert pred(2, "driver") is False
    assert pred(3, "driver") is True
    assert pred(4, "fleet") is True
    # A driver the legacy list never knew (matrix-only row): not narrowed —
    # the parity log surfaces the diff instead of silently dropping.
    assert pred(99, "driver") is True


@pytest.mark.asyncio
async def test_company_scope_composes_with_truck_rule(monkeypatch):
    disp = _RecordingDispatch()
    _patch(monkeypatch, disp, scope={7: ["G1"]})

    def _sees(uid, role, co, scope):
        return not (uid == 7 and co != "G1")
    monkeypatch.setattr(
        "capabilities.alerting.company_scope.user_sees_company", _sees)
    await _spine_dm_fanout(
        account_id=1, alert_type="fault", severity=AlertSeverity.CRITICAL,
        alert_text="x", vname="Truck 105", photo_bytes=None, video_url="",
        history_id=5, needs_ack=True,
        subscribers=[_Sub(7, Role.FLEET)], co="OSY")
    pred = disp.calls[0]["recipient_filter"]
    assert pred(7, "fleet") is False              # scoped out of OSY
    assert pred(8, "fleet") is True
