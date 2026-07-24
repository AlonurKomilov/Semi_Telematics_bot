"""N6 — ai.action_executed: the third notification source (AI).

An executed AI proposal leaves a persistent record in the APPROVER's
in-app inbox — inbox-only by design (they were on-screen; the value is
the durable trail, not an interruption).  Pins:

  • the category registers as TARGETED with source "ai",
  • the announce helper targets the approver, in_app channel ONLY,
  • title is actor-led ("AI completed: <action>"), body prefers the
    executor's summary/message, context chip = "AI",
  • kill switch NOTIFICATIONS_AI_EVENTS=0 silences it,
  • a notification failure never raises out (the action already ran).
"""

from __future__ import annotations

import pytest

import capabilities.ai.notifications as ai_n
from capabilities.ai.notifications import (
    AI_ACTION_EXECUTED,
    announce_ai_action_executed,
)
from capabilities.notifications.categories import TARGETED, get_category


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


def test_category_registered_targeted_ai_source():
    cat = get_category(AI_ACTION_EXECUTED)
    assert cat is not None
    assert cat.kind == TARGETED
    assert cat.mandatory is False
    assert cat.source == "ai"


@pytest.mark.asyncio
async def test_announce_targets_approver_in_app_only(monkeypatch):
    notify = _RecordingNotify()
    monkeypatch.setattr(ai_n, "notify_user", notify)
    monkeypatch.setattr(ai_n, "_AI_EVENTS_ENABLED", True)

    await announce_ai_action_executed(
        "DB", 42, 7, tool="add_inventory_item",
        result={"summary": "Added dashcam to Truck 245."})

    assert len(notify.calls) == 1
    call = notify.calls[0]
    assert call["account_id"] == 42 and call["user_id"] == 7
    assert call["channels"] == ["in_app"]          # never email/push noise
    c = call["content"]
    assert c.category == AI_ACTION_EXECUTED
    assert c.title == "AI completed: Add inventory item"
    assert c.body == "Added dashcam to Truck 245."
    assert c.meta == {"context": "AI"}
    assert c.url == "/ai/chat"


@pytest.mark.asyncio
async def test_announce_fallback_body_without_summary(monkeypatch):
    notify = _RecordingNotify()
    monkeypatch.setattr(ai_n, "notify_user", notify)
    monkeypatch.setattr(ai_n, "_AI_EVENTS_ENABLED", True)
    await announce_ai_action_executed("DB", 1, 2, tool="update_vehicle", result={})
    assert notify.calls[0]["content"].body == "Executed after your approval."


@pytest.mark.asyncio
async def test_kill_switch_silences(monkeypatch):
    notify = _RecordingNotify()
    monkeypatch.setattr(ai_n, "notify_user", notify)
    monkeypatch.setattr(ai_n, "_AI_EVENTS_ENABLED", False)
    await announce_ai_action_executed("DB", 1, 2, tool="t", result=None)
    assert notify.calls == []


@pytest.mark.asyncio
async def test_notify_failure_is_swallowed(monkeypatch):
    notify = _RecordingNotify(raises=RuntimeError("inbox down"))
    monkeypatch.setattr(ai_n, "notify_user", notify)
    monkeypatch.setattr(ai_n, "_AI_EVENTS_ENABLED", True)
    # Must NOT raise — the AI action already executed and finalized.
    await announce_ai_action_executed("DB", 1, 2, tool="t", result=None)
    assert len(notify.calls) == 1
