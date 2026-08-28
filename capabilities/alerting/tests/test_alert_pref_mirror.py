"""Legacy alert-pref writes mirror into the notification matrix.

After the DM fanout moved to the spine, the matrix is the DELIVERY SSOT
— a toggle that only wrote ``users.alert_*`` would stop affecting what
arrives.  ``mirror_alert_prefs_to_matrix`` is the single helper all
three writers (dashboard PUT /user/me/alerts, bot per-type toggle, bot
master on/off) share:

  • bare-type toggles → notification_pref rows (alert.{type}, immediate)
  • alerts_on=True  → channel upsert (address=telegram_id, verified,
    master on — a linked Telegram id is implicitly verified)
  • alerts_on=False → master switch off (prefs kept)
  • mirror errors are swallowed (legacy write already landed; the next
    successful mirror converges the stores)

Also pins the update_delivery edit contract the reminder path relies
on: the edited message re-renders its action buttons (meta gets the
correlation key stamped).
"""

from __future__ import annotations

import pytest

from capabilities.alerting.prefs_mirror import mirror_alert_prefs_to_matrix
from capabilities.notifications.channels import (
    DeliveryResult,
    NotificationContent,
    register_channel,
)
from capabilities.notifications.service import update_delivery
from capabilities.notifications.telegram import render_telegram


@pytest.fixture(autouse=True)
def _registry_isolation():
    from capabilities.notifications import channels as _ch
    snap = dict(_ch._CHANNELS)
    yield
    _ch._CHANNELS.clear(); _ch._CHANNELS.update(snap)


class _MatrixDb:
    def __init__(self, *, raises: bool = False):
        self.prefs: list[tuple] = []
        self.upserts: list[tuple] = []
        self.disables: list[tuple] = []
        self._raises = raises

    async def set_notification_pref(self, account_id, rtype, rid, channel,
                                    category, *, enabled, cadence):
        if self._raises:
            raise RuntimeError("db down")
        self.prefs.append((account_id, rid, channel, category, enabled, cadence))

    async def upsert_notification_channel(self, account_id, rtype, rid, channel,
                                          *, address, verified, enabled_master):
        if self._raises:
            raise RuntimeError("db down")
        self.upserts.append((account_id, rid, channel, address, verified,
                             enabled_master))

    async def disable_notification_channel(self, account_id, rtype, rid, channel):
        if self._raises:
            raise RuntimeError("db down")
        self.disables.append((account_id, rid, channel))


class _User:
    id = 42
    account_id = 7
    telegram_id = 1527638770


@pytest.mark.asyncio
async def test_toggles_mirror_to_pref_rows():
    db = _MatrixDb()
    await mirror_alert_prefs_to_matrix(
        db, _User(), toggles={"faults": False, "events": True})
    assert (7, "42", "telegram_dm", "alert.faults", False, "immediate") in db.prefs
    assert (7, "42", "telegram_dm", "alert.events", True, "immediate") in db.prefs
    assert db.upserts == [] and db.disables == []


@pytest.mark.asyncio
async def test_master_on_upserts_verified_channel():
    db = _MatrixDb()
    await mirror_alert_prefs_to_matrix(db, _User(), alerts_on=True)
    assert db.upserts == [(7, "42", "telegram_dm", "1527638770", True, True)]


@pytest.mark.asyncio
async def test_master_off_disables_channel_keeps_prefs():
    db = _MatrixDb()
    await mirror_alert_prefs_to_matrix(db, _User(), alerts_on=False)
    assert db.disables == [(7, "42", "telegram_dm")]
    assert db.prefs == []


@pytest.mark.asyncio
async def test_mirror_errors_are_swallowed():
    db = _MatrixDb(raises=True)
    await mirror_alert_prefs_to_matrix(
        db, _User(), toggles={"faults": True}, alerts_on=False)   # no raise


# ── update_delivery re-renders action buttons on edits ──────────────

class _TgEditChannel:
    """Edit-capable channel whose render is the REAL Telegram renderer,
    so the button-rebuild path is exercised end to end."""
    key = "fake_tg_edit"
    personal = True
    supports_edit = True

    def __init__(self):
        self.edited_payloads: list = []

    def render(self, recipient, content):
        return render_telegram(content)

    async def send(self, recipient, payload):
        return DeliveryResult(ok=True)

    async def edit(self, recipient, handle, payload):
        self.edited_payloads.append(payload)
        return DeliveryResult(ok=True)


class _LedgerDb:
    async def get_notification_deliveries(self, account_id, key, *, channel=""):
        return [{"channel": "fake_tg_edit", "recipient_type": "user",
                 "recipient_id": "42",
                 "handle": {"chat_id": 1, "message_id": 2, "kind": "text"}}]


@pytest.mark.asyncio
async def test_update_delivery_rebuilds_action_buttons():
    ch = _TgEditChannel()
    register_channel(ch)
    await update_delivery(
        _LedgerDb(), 7, "alert:11",
        NotificationContent(title="", body="reminder 2/4",
                            severity="warning",
                            actions=[{"id": "ack", "label": "✅ Acknowledge"}]))
    payload = ch.edited_payloads[0]
    assert payload.markup is not None
    assert payload.markup.inline_keyboard[0][0].callback_data \
        == "notif_act:alert:11:ack"
