"""N3 — team.invite_accepted, the first NON-alert notification source.

The worked example of the TARGETED recipient model (``notify_user``):
accepting a team invite tells the person who sent it, on their connected
personal channels, opt-out.  Pins:

  • the category is registered as TARGETED (so the prefs UI can list it and
    ``notify_user`` never mistakes it for a broadcast),
  • flag off → nothing delivers (controlled rollout),
  • flag on + a human inviter → notify_user gets the right targeted content
    (category, one recipient = the inviter, account scoped),
  • system/operator invites (no human inviter) and self-notifies are
    skipped,
  • a delivery failure is swallowed — it must never undo a completed
    redemption.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import features.settings.invites.notifications as n3
from features.settings.invites.notifications import (
    INVITE_ACCEPTED,
    announce_invite_accepted,
)
from capabilities.notifications.categories import TARGETED, get_category


class _FakeDB:
    def __init__(self, created_by):
        self._created_by = created_by

    async def get_invite(self, code):
        if self._created_by is None:
            return None
        return SimpleNamespace(code=code, created_by=self._created_by)


class _RecordingNotify:
    def __init__(self, raises=None):
        self.calls = []
        self._raises = raises

    async def __call__(self, db, account_id, user_id, content, *, channels=None):
        self.calls.append(
            {"account_id": account_id, "user_id": user_id, "content": content})
        if self._raises:
            raise self._raises
        return []


def _user(uid=200, account_id=42, name="Dana Driver"):
    return SimpleNamespace(id=uid, account_id=account_id, display_name=name)


def _patch(monkeypatch, *, flag, notify, created_by):
    monkeypatch.setattr(n3, "_TEAM_EVENTS_ENABLED", flag)
    monkeypatch.setattr(n3, "notify_user", notify)
    return _FakeDB(created_by)


def test_category_registered_as_targeted():
    cat = get_category(INVITE_ACCEPTED)
    assert cat is not None
    assert cat.kind == TARGETED
    assert cat.mandatory is False
    assert cat.source == "team"          # namespaced by source for N4 grouping


@pytest.mark.asyncio
async def test_flag_off_never_notifies(monkeypatch):
    notify = _RecordingNotify()
    db = _patch(monkeypatch, flag=False, notify=notify, created_by=7)
    await announce_invite_accepted(db, "ABC-123", _user(), role_display="Driver")
    assert notify.calls == []


@pytest.mark.asyncio
async def test_flag_on_notifies_inviter_with_targeted_content(monkeypatch):
    notify = _RecordingNotify()
    db = _patch(monkeypatch, flag=True, notify=notify, created_by=7)

    await announce_invite_accepted(
        db, "ABC-123", _user(uid=200, account_id=42, name="Dana Driver"),
        role_display="Driver")

    assert len(notify.calls) == 1
    call = notify.calls[0]
    assert call["account_id"] == 42
    assert call["user_id"] == 7                 # the INVITER, not the new user
    content = call["content"]
    assert content.category == INVITE_ACCEPTED
    assert content.title == "Invite accepted"
    assert content.body == "Dana Driver joined as Driver."
    assert content.severity == "info"


@pytest.mark.asyncio
async def test_body_without_role(monkeypatch):
    notify = _RecordingNotify()
    db = _patch(monkeypatch, flag=True, notify=notify, created_by=7)
    await announce_invite_accepted(db, "ABC-123", _user(name="Sam"), role_display="")
    assert notify.calls[0]["content"].body == "Sam joined your team."


@pytest.mark.asyncio
async def test_system_invite_no_inviter_skipped(monkeypatch):
    notify = _RecordingNotify()
    db = _patch(monkeypatch, flag=True, notify=notify, created_by=-1)  # sentinel
    await announce_invite_accepted(db, "ABC-123", _user(), role_display="Driver")
    assert notify.calls == []


@pytest.mark.asyncio
async def test_missing_invite_skipped(monkeypatch):
    notify = _RecordingNotify()
    db = _patch(monkeypatch, flag=True, notify=notify, created_by=None)  # get_invite→None
    await announce_invite_accepted(db, "GONE", _user(), role_display="Driver")
    assert notify.calls == []


@pytest.mark.asyncio
async def test_self_notify_skipped(monkeypatch):
    notify = _RecordingNotify()
    # inviter id == the new user's id — don't ping yourself.
    db = _patch(monkeypatch, flag=True, notify=notify, created_by=200)
    await announce_invite_accepted(db, "ABC-123", _user(uid=200), role_display="Driver")
    assert notify.calls == []


@pytest.mark.asyncio
async def test_delivery_failure_is_swallowed(monkeypatch):
    notify = _RecordingNotify(raises=RuntimeError("telegram down"))
    db = _patch(monkeypatch, flag=True, notify=notify, created_by=7)
    # Must NOT raise — the redemption is already committed.
    await announce_invite_accepted(db, "ABC-123", _user(), role_display="Driver")
    assert len(notify.calls) == 1        # attempted, error swallowed
