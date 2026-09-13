"""A task completed from the BOT is closed on every surface.

The stored status has a historical schism: the Telegram bot's "✓ Done"
button writes ``"done"`` (interfaces/bot/maintenance.py), while the API
and the dashboard write ``"completed"``.  The adapter, the recurring
spawn and the dashboard's own badge helper all know both spellings.

The Python urgency classifier did not.  So a task a driver marked done
in Telegram stayed OPEN for everything that derives urgency — the AI
maintenance tools, the AI context snapshot and the Overview page — and
because the alert that carried the button only fires once a task is
past due, it came back as OVERDUE.  Forever: nothing ever re-closes it.

These pin the vocabulary to ONE object so the spellings cannot drift
apart again.
"""

from __future__ import annotations

import pytest

from adapters.storage.maintenance import CLOSED_TASK_STATUSES
from features.maintenance.ai_tool import _CLOSED, _bucket_open_tasks
from features.maintenance.service import classify_task_urgency
from features.work_orders.task_links import CLOSED_STATUSES, is_open_task


def _overdue_task(status: str) -> dict:
    """A task whose due date is long past — urgent unless it is closed."""
    return {
        "id": 1,
        "vehicle_name": "231",
        "task_type": "oil_change",
        "description": "Oil change",
        "status": status,
        "due_date": "2020-01-01",
    }


@pytest.mark.parametrize("status", ["completed", "done", "cancelled"])
def test_closed_statuses_never_classify_as_urgent(status):
    assert classify_task_urgency(_overdue_task(status)) is None


@pytest.mark.parametrize("status", ["completed", "done", "cancelled"])
def test_closed_statuses_never_reach_an_open_bucket(status):
    overdue, due_soon, pending = _bucket_open_tasks([_overdue_task(status)])
    assert (overdue, due_soon, pending) == ([], [], [])


def test_the_bot_spelling_is_the_one_that_regressed():
    """Belt and braces: 'done' specifically, not just the family."""
    assert "done" in CLOSED_TASK_STATUSES
    assert classify_task_urgency(_overdue_task("done")) is None
    assert not is_open_task(_overdue_task("done"))


def test_an_open_task_past_due_is_still_overdue():
    """The guard above must not have been bought by closing everything."""
    assert classify_task_urgency(_overdue_task("pending")) == "overdue"
    overdue, _, _ = _bucket_open_tasks([_overdue_task("pending")])
    assert len(overdue) == 1


def test_every_surface_shares_one_vocabulary_object():
    """Same object, not merely equal — equality can drift on one edit."""
    assert _CLOSED is CLOSED_TASK_STATUSES
    assert CLOSED_STATUSES is CLOSED_TASK_STATUSES
