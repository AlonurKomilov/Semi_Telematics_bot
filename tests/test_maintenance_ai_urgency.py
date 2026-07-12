"""The maintenance AI tools classify urgency by DERIVED state (date / mileage /
engine-hours via ``classify_task_urgency``), not the stored ``status`` column —
so a stored-``pending`` task whose truck rolled past its due odometer counts as
overdue, exactly like the Maintenance page's chips.  This pins the bug where
the dashboard showed "Overdue 1" while the AI answered "no overdue tasks".
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from features.maintenance.ai_tool import (
    get_maintenance_summary,
    get_vehicle_maintenance,
)


class _FakeDB:
    def __init__(self, tasks, state_rows=None):
        self._tasks = tasks
        self._state_rows = state_rows

    async def get_maintenance_tasks(self, account_id, vehicle_name=None):
        if vehicle_name:
            return [t for t in self._tasks if t.get("vehicle_name") == vehicle_name]
        return list(self._tasks)

    async def get_vehicle_state(self, account_id, **kwargs):
        if self._state_rows is None:
            raise RuntimeError("warehouse unavailable")  # exercised: merge degrades
        return list(self._state_rows)


def _tasks():
    return [
        # Stored 'pending' but 25 mi PAST due odometer → derived OVERDUE.
        {"vehicle_name": "228", "task_type": "oil", "status": "pending",
         "due_miles": 218_331, "last_odometer": 218_356.5},
        # Stored 'pending', 4,297 mi to go (< 5,000 threshold) → DUE SOON.
        {"vehicle_name": "233", "task_type": "oil", "status": "pending",
         "due_miles": 286_347, "last_odometer": 282_050},
        # Stored 'pending', 40k mi to go → stays PENDING.
        {"vehicle_name": "107", "task_type": "oil", "status": "pending",
         "due_miles": 542_069, "last_odometer": 502_000},
        # Closed → excluded from every bucket.
        {"vehicle_name": "228", "task_type": "tires", "status": "completed",
         "due_miles": 100_000, "last_odometer": 218_356.5},
    ]


@pytest.mark.asyncio
async def test_summary_counts_mileage_overdue_despite_stored_pending():
    res = await get_maintenance_summary({}, None, account_id=1, db=_FakeDB(_tasks()))
    assert res["total_overdue"] == 1        # truck 228: 25 mi past due
    assert res["total_due_soon"] == 1       # truck 233: 4,297 mi to go
    assert res["total_pending"] == 1        # truck 107 only
    assert res["overdue_tasks"][0]["vehicle"] == "228"
    assert res["overdue_tasks"][0]["status"] == "overdue"       # derived label
    # Priority mirrors the DB: overdue auto-promotes to critical (the
    # scheduled overdue-marker jobs persist the same value), regardless
    # of the priority the task was created with.
    assert res["overdue_tasks"][0]["priority"] == "critical"
    assert res["due_soon_tasks"][0]["priority"] == "medium"     # unchanged when not overdue
    assert res["overdue_tasks"][0]["miles_remaining"] == -26    # round(218331-218356.5)
    assert res["due_soon_tasks"][0]["vehicle"] == "233"
    # Completed task excluded from the by-type breakdown too.
    assert res["tasks_by_type"] == {"oil": 3}


@pytest.mark.asyncio
async def test_vehicle_view_derives_urgency_and_orders_most_urgent_first():
    res = await get_vehicle_maintenance(
        {"vehicle_name": "228"}, None, account_id=1, db=_FakeDB(_tasks()),
    )
    assert res["total_tasks"] == 1          # the completed one is excluded
    assert res["total_overdue"] == 1
    assert res["tasks"][0]["status"] == "overdue"


@pytest.mark.asyncio
async def test_stored_overdue_still_counts():
    db = _FakeDB([{"vehicle_name": "9", "task_type": "brakes",
                   "status": "overdue"}])
    res = await get_maintenance_summary({}, None, account_id=1, db=db)
    assert res["total_overdue"] == 1


@pytest.mark.asyncio
async def test_live_odometer_merge_flips_stale_stored_reading_to_overdue():
    """The truck-228 production bug: the task's stored ``last_odometer`` was
    stranded 49 mi short of due (the alerted_at filter stops the 6-h job from
    refreshing it), so classifying against it said "due soon" while the truck
    had actually rolled past due.  The AI tool must merge LIVE vehicle_state
    readings (same as the dashboard endpoint) before classifying.
    """
    db = _FakeDB(
        [{"vehicle_name": "228", "vehicle_id": "v-228", "task_type": "oil",
          "status": "pending", "due_miles": 218_331,
          "last_odometer": 218_282}],          # stale: 49 mi to go
        state_rows=[{"vehicle_id": "v-228", "vehicle_name": "228",
                     "company_code": "A", "odometer_mi": 218_356.5}],  # live: past due
    )
    res = await get_maintenance_summary({}, None, account_id=1, db=db)
    assert res["total_overdue"] == 1
    assert res["total_due_soon"] == 0
    assert res["overdue_tasks"][0]["current_odometer"] == 218_356.5
    assert res["overdue_tasks"][0]["miles_remaining"] == -26


@pytest.mark.asyncio
async def test_live_merge_failure_degrades_to_stored_readings():
    """Warehouse outage (get_vehicle_state raises) must not break the tool —
    it classifies against the stored readings, exactly like the list view.
    """
    db = _FakeDB(
        [{"vehicle_name": "228", "task_type": "oil", "status": "pending",
          "due_miles": 218_331, "last_odometer": 218_282}],
        state_rows=None,                        # get_vehicle_state raises
    )
    res = await get_maintenance_summary({}, None, account_id=1, db=db)
    assert res["total_due_soon"] == 1           # stored reading: 49 mi to go
    assert res["total_overdue"] == 0
