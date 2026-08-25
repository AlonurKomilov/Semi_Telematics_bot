"""Dismissing a callout — the record is the point.

The owner's reason for wanting this: if an employee later acts on
wrong information (a truck reporting 0 miles because its device cannot
read the engine), they need to tell "the system never told them" from
"the system told them and they closed it".

That makes ONE ordering non-negotiable — the trail entry is written
before anything is hidden, and a failure there aborts the whole call.
A dismissal nobody can reconstruct is worse for the owner than a
callout that would not close.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from capabilities.callouts.router import DISMISSED_KEY, DismissBody, dismiss_callout


class _PlatformDB:
    def __init__(self, stored: str = ""):
        self.stored = stored
        self.writes: list[tuple[int, str, str]] = []
        self.fail_write = False

    async def get_user_preference(self, user_id, key, default=""):
        return self.stored or default

    async def set_user_preference(self, user_id, key, value):
        if self.fail_write:
            raise RuntimeError("preference store unavailable")
        self.writes.append((user_id, key, value))
        self.stored = value


@pytest.fixture
def recorded(monkeypatch):
    """Capture trail writes; flip ``fail`` to simulate an outage."""
    calls: list[dict] = []
    state = {"fail": False}

    async def _record(db, account_id, actor_user_id, action, entity_type,
                      entity_id, **kw):
        if state["fail"]:
            raise RuntimeError("tenant db unavailable")
        calls.append({
            "account_id": account_id, "actor": actor_user_id,
            "action": action, "entity_type": entity_type,
            "entity_id": entity_id, **kw,
        })

    monkeypatch.setattr(
        "capabilities.callouts.router.record_simple", _record,
    )
    return SimpleNamespace(calls=calls, state=state)


@pytest.fixture
def as_user(monkeypatch):
    async def _get_db_user(user, platform_db=None):
        return SimpleNamespace(id=7)
    monkeypatch.setattr(
        "capabilities.callouts.router.get_current_db_user", _get_db_user,
    )
    return {"account_id": 42, "id": 7}


_ID = "vehicle.no_engine_data@vehicle:281#2026-05-12T09:14:00"


@pytest.mark.asyncio
async def test_dismissal_is_recorded_and_stored(recorded, as_user):
    db = _PlatformDB()
    body = DismissBody(callout_id=_ID, entity_type="vehicle",
                       entity_id="548640", rendered="No data: Engine")
    out = await dismiss_callout(body, user=as_user, platform_db=db, tenant_db=None)

    assert out == {"ok": True, "callout_id": _ID, "dismissed": True}
    assert json.loads(db.stored)["entries"][_ID] > 0
    (call,) = recorded.calls
    assert call["actor"] == 7 and call["account_id"] == 42
    assert call["entity_type"] == "vehicle" and call["entity_id"] == "548640"


@pytest.mark.asyncio
async def test_trail_failure_refuses_the_dismissal(recorded, as_user):
    """Nothing is hidden that could not be recorded.

    The callout stays on screen and the preference is untouched, so the
    reader is mildly annoyed instead of the owner losing the evidence.
    """
    from fastapi import HTTPException

    recorded.state["fail"] = True
    db = _PlatformDB()
    with pytest.raises(HTTPException) as err:
        await dismiss_callout(
            DismissBody(callout_id=_ID), user=as_user,
            platform_db=db, tenant_db=None,
        )
    assert err.value.status_code == 503
    assert db.writes == [], "nothing may be hidden without a record"


@pytest.mark.asyncio
async def test_preference_failure_keeps_the_record(recorded, as_user):
    """The harmless direction of the same asymmetry: the record stands
    and the callout simply comes back."""
    db = _PlatformDB()
    db.fail_write = True
    out = await dismiss_callout(
        DismissBody(callout_id=_ID), user=as_user,
        platform_db=db, tenant_db=None,
    )
    assert out["ok"] is True
    assert len(recorded.calls) == 1, "the audit entry survives"


@pytest.mark.asyncio
async def test_the_note_never_claims_the_reader_understood(recorded, as_user):
    """It proves the callout was shown and closed — not read, not
    understood.  Verbs like "acknowledged" invite exactly that
    over-reading in a dispute, so they must not appear."""
    db = _PlatformDB()
    await dismiss_callout(
        DismissBody(callout_id=_ID, rendered="No data: Engine"),
        user=as_user, platform_db=db, tenant_db=None,
    )
    note = recorded.calls[0]["note"].lower()
    assert "dismissed" in note
    for overclaim in ("acknowledg", "confirm", "accept", "understood", "read"):
        assert overclaim not in note


@pytest.mark.asyncio
async def test_rendered_text_is_kept_for_the_dispute(recorded, as_user):
    """An argument about WHAT it said is settled by the record, not by
    whatever the copy happens to say months later."""
    db = _PlatformDB()
    await dismiss_callout(
        DismissBody(callout_id=_ID, rendered="No data: Engine"),
        user=as_user, platform_db=db, tenant_db=None,
    )
    assert recorded.calls[0]["context"]["rendered"] == "No data: Engine"


@pytest.mark.asyncio
async def test_undo_removes_the_entry_and_is_also_recorded(recorded, as_user):
    """A dismissal that could be silently walked back is a record you
    cannot trust — so the reverse is an event too."""
    db = _PlatformDB(json.dumps({"v": 1, "entries": {_ID: 123}}))
    out = await dismiss_callout(
        DismissBody(callout_id=_ID, undo=True), user=as_user,
        platform_db=db, tenant_db=None,
    )
    assert out["dismissed"] is False
    assert _ID not in json.loads(db.stored)["entries"]
    assert recorded.calls[0]["action"] == "callout.undismiss"


@pytest.mark.asyncio
async def test_corrupt_stored_value_does_not_break_dismissal(recorded, as_user):
    """Hand-edited or half-written JSON must degrade to an empty map,
    never to a 500 that leaves the reader unable to close anything."""
    db = _PlatformDB("{not json")
    await dismiss_callout(
        DismissBody(callout_id=_ID), user=as_user,
        platform_db=db, tenant_db=None,
    )
    assert _ID in json.loads(db.stored)["entries"]


@pytest.mark.asyncio
async def test_entries_are_capped(recorded, as_user):
    from capabilities.callouts.router import MAX_ENTRIES
    old = {f"k{i}": i + 1 for i in range(MAX_ENTRIES + 20)}
    db = _PlatformDB(json.dumps({"v": 1, "entries": old}))
    await dismiss_callout(
        DismissBody(callout_id=_ID), user=as_user,
        platform_db=db, tenant_db=None,
    )
    entries = json.loads(db.stored)["entries"]
    assert len(entries) <= MAX_ENTRIES
    assert _ID in entries, "the newest dismissal is never the one evicted"
