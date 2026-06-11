"""Cross-company vehicle-name collision safety tests.

Two trucks named "103" can legitimately exist under a single
account when the account has multiple companies (G1 + OSY).  The
scheduler previously keyed its odometer lookup by vehicle name
alone, which silently merged the two trucks' state rows in a
last-write-wins dict and corrupted ``last_odometer`` on every
matching task every 6 hours.

These tests guarantee:

  * ``_route_task_to_state_row`` picks the right vehicle_state row
    when the task carries a vehicle_id.
  * It picks the right row when the task carries (company, name).
  * It returns None when the task carries only a name and that
    name resolves to multiple companies — better empty than wrong.
  * ``mark_overdue_tasks_by_mileage`` updates ``last_odometer``
    using the right vehicle's value, not whichever happened to win
    a dict-iteration race.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from features.maintenance.service import (
    _index_state_rows_for_routing,
    _route_task_to_state_row,
    mark_overdue_tasks_by_mileage,
)


# ── Routing helpers ───────────────────────────────────────────────


def _state_row(vid: str, cc: str, name: str, odo: float = 0, hrs: float = 0):
    return {
        "vehicle_id":  vid,
        "company_code": cc,
        "vehicle_name": name,
        "odometer_mi": odo,
        "engine_hours": hrs,
    }


def test_indexing_dedupes_ambiguous_names():
    """When two companies have the same vehicle name, the unique-name
    index removes the entry so the name-only fallback can't match."""
    rows = [
        _state_row("v-G1", "G1", "103", odo=267_136),
        _state_row("v-OSY", "OSY", "103", odo=928_011),
        _state_row("v-OSY-238", "OSY", "238", odo=300_230),
    ]
    by_id, by_cc_name, by_unique = _index_state_rows_for_routing(rows)
    assert by_id["v-G1"]["odometer_mi"] == 267_136
    assert by_id["v-OSY"]["odometer_mi"] == 928_011
    assert by_cc_name[("G1", "103")]["odometer_mi"] == 267_136
    assert by_cc_name[("OSY", "103")]["odometer_mi"] == 928_011
    # 238 appears once → in the unique index; 103 appears twice → NOT.
    assert "103" not in by_unique
    assert by_unique["238"]["odometer_mi"] == 300_230


def test_route_by_vehicle_id_wins_first():
    rows = [
        _state_row("v-G1", "G1", "103", odo=267_136),
        _state_row("v-OSY", "OSY", "103", odo=928_011),
    ]
    by_id, by_cc_name, by_unique = _index_state_rows_for_routing(rows)
    task = {"vehicle_id": "v-G1", "vehicle_name": "103", "company_code": ""}
    live = _route_task_to_state_row(task, by_id, by_cc_name, by_unique)
    assert live is not None
    assert live["odometer_mi"] == 267_136


def test_route_by_company_name_when_vehicle_id_missing():
    rows = [
        _state_row("v-G1", "G1", "103", odo=267_136),
        _state_row("v-OSY", "OSY", "103", odo=928_011),
    ]
    by_id, by_cc_name, by_unique = _index_state_rows_for_routing(rows)
    task = {"vehicle_id": "", "vehicle_name": "103", "company_code": "OSY"}
    live = _route_task_to_state_row(task, by_id, by_cc_name, by_unique)
    assert live is not None
    assert live["odometer_mi"] == 928_011


def test_route_returns_none_on_ambiguous_name_only_lookup():
    """No vehicle_id, no company_code, ambiguous name → no match
    rather than guessing."""
    rows = [
        _state_row("v-G1", "G1", "103", odo=267_136),
        _state_row("v-OSY", "OSY", "103", odo=928_011),
    ]
    by_id, by_cc_name, by_unique = _index_state_rows_for_routing(rows)
    task = {"vehicle_id": "", "vehicle_name": "103", "company_code": ""}
    live = _route_task_to_state_row(task, by_id, by_cc_name, by_unique)
    assert live is None


def test_route_falls_back_to_name_when_unambiguous():
    rows = [
        _state_row("v-G1", "G1", "103", odo=267_136),
        _state_row("v-OSY", "OSY", "238", odo=300_230),
    ]
    by_id, by_cc_name, by_unique = _index_state_rows_for_routing(rows)
    task = {"vehicle_id": "", "vehicle_name": "238", "company_code": ""}
    live = _route_task_to_state_row(task, by_id, by_cc_name, by_unique)
    assert live is not None
    assert live["odometer_mi"] == 300_230


# ── End-to-end scheduler ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_mileage_scheduler_uses_correct_vehicle_for_each_task():
    """A task for the G1 truck gets G1's odometer; a task for the OSY
    truck gets OSY's — never crossed."""
    tasks = [
        {
            "id": 1,
            "vehicle_id": "v-G1",
            "vehicle_name": "103",
            "company_code": "G1",
            "due_miles": 284_507,
        },
        {
            "id": 2,
            "vehicle_id": "v-OSY",
            "vehicle_name": "103",
            "company_code": "OSY",
            "due_miles": 1_000_000,  # OSY's 928_011 < this → still pending
        },
    ]
    state_rows = [
        _state_row("v-G1", "G1", "103", odo=267_136),
        _state_row("v-OSY", "OSY", "103", odo=928_011),
    ]
    tenant = MagicMock()
    tenant.get_pending_tasks_by_miles = AsyncMock(return_value=tasks)
    tenant.get_vehicle_state = AsyncMock(return_value=state_rows)
    tenant.update_maintenance_last_odometer_bulk = AsyncMock()
    tenant.update_maintenance_status_bulk = AsyncMock()
    tenant.mark_tasks_alerted_bulk = AsyncMock()

    await mark_overdue_tasks_by_mileage(42, tenant)

    # Both odometer updates should fire, each with its OWN truck's value.
    bulk_args = tenant.update_maintenance_last_odometer_bulk.call_args
    pairs = dict(bulk_args.args[1])  # [(task_id, odometer), ...]
    assert pairs[1] == 267_136.0  # G1's task
    assert pairs[2] == 928_011.0  # OSY's task


@pytest.mark.asyncio
async def test_mileage_scheduler_skips_ambiguous_name_only_tasks():
    """A legacy task with no vehicle_id and no company_code, whose
    name collides across companies, must NOT be cross-merged.  Better
    to skip the update than to write the wrong vehicle's odometer."""
    tasks = [
        {
            "id": 1,
            "vehicle_id": "",
            "vehicle_name": "103",
            "company_code": "",
            "due_miles": 300_000,
        },
    ]
    state_rows = [
        _state_row("v-G1", "G1", "103", odo=267_136),
        _state_row("v-OSY", "OSY", "103", odo=928_011),
    ]
    tenant = MagicMock()
    tenant.get_pending_tasks_by_miles = AsyncMock(return_value=tasks)
    tenant.get_vehicle_state = AsyncMock(return_value=state_rows)
    tenant.update_maintenance_last_odometer_bulk = AsyncMock()
    tenant.update_maintenance_status_bulk = AsyncMock()
    tenant.mark_tasks_alerted_bulk = AsyncMock()

    await mark_overdue_tasks_by_mileage(42, tenant)

    # No odometer update fires for the ambiguous task — the pre-fix
    # behaviour would have written OSY's 928_011 here, corrupting the
    # task and flipping its status to overdue.
    tenant.update_maintenance_last_odometer_bulk.assert_not_called()
    tenant.update_maintenance_status_bulk.assert_not_called()
