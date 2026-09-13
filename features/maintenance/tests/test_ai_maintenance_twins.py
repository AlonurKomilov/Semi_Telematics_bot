"""Guard: maintenance answers about ONE truck.

get_vehicle_maintenance asked the store for every task whose
vehicle_name matched, so two same-numbered trucks in different
companies were merged into one answer: the other company's services,
due dates and odometer readings narrated as this truck's, and the
overdue count a sum of two trucks.

Task rows carry vehicle_id (the provider id) and company_code. A task
created on the dashboard has an EMPTY vehicle_id — that is the case the
scope ladder used to deny outright — so it falls back to the company
rather than being dropped.
"""

import pytest

from features.maintenance.ai_tool import get_vehicle_maintenance


class _V:
    def __init__(self, vid, ref, unit, company):
        self.id, self.telematics_ref = vid, ref
        self.unit_number, self.company_code = unit, company
        self.is_active = True


OSY = _V(42, "sam_42", "103", "OSY")
G1 = _V(99, "sam_99", "103", "G1")


def _task(tid, vehicle_id, company, name="103"):
    """`description` is what the projection emits, so the fixtures are
    told apart by it rather than by a column the tool does not return."""
    return {"id": tid, "vehicle_id": vehicle_id, "company_code": company,
            "vehicle_name": name, "task_type": "oil_change",
            "description": f"task-{tid}",
            "status": "pending", "due_date": "2026-01-01",
            "due_odometer": None, "notes": ""}


class _DB:
    def __init__(self, registry, tasks):
        self._registry, self._tasks = registry, tasks

    async def list_vehicles(self, account_id, **kw):
        return list(self._registry)

    async def get_maintenance_tasks(self, account_id, vehicle_name=None, **kw):
        rows = self._tasks
        if vehicle_name:
            rows = [t for t in rows if t["vehicle_name"] == vehicle_name]
        return list(rows)


@pytest.fixture(autouse=True)
def _no_live_readings(monkeypatch):
    import features.maintenance.service as svc

    async def _apply(db, account_id, tasks):
        return None

    async def _today(account_id):
        import datetime
        return datetime.date(2026, 9, 1)

    monkeypatch.setattr(svc, "apply_live_readings", _apply, raising=False)
    monkeypatch.setattr(svc, "account_today", _today, raising=False)


@pytest.mark.asyncio
async def test_the_twins_tasks_are_not_merged_in():
    db = _DB([OSY, G1],
             [_task(1, "sam_42", "OSY"), _task(2, "sam_99", "G1")])
    res = await get_vehicle_maintenance(
        {"vehicle_name": "103", "company": "OSY"}, None, account_id=1, db=db)
    assert res["total_tasks"] == 1, res
    assert res["tasks"][0]["description"] == "task-1"


@pytest.mark.asyncio
async def test_a_dashboard_task_with_no_provider_id_is_kept():
    """Empty vehicle_id is the common case for a hand-entered task — and
    the case the ladder used to deny outright."""
    db = _DB([OSY, G1],
             [_task(1, "", "OSY"), _task(2, "sam_99", "G1")])
    res = await get_vehicle_maintenance(
        {"vehicle_name": "103", "company": "OSY"}, None, account_id=1, db=db)
    assert res["total_tasks"] == 1, res
    assert res["tasks"][0]["description"] == "task-1"


@pytest.mark.asyncio
async def test_an_ambiguous_number_asks_which_company():
    db = _DB([OSY, G1], [_task(1, "sam_42", "OSY")])
    res = await get_vehicle_maintenance(
        {"vehicle_name": "103"}, None, account_id=1, db=db)
    assert res.get("error"), res


@pytest.mark.asyncio
async def test_a_denied_twin_is_refused():
    db = _DB([OSY, G1], [_task(2, "sam_99", "G1")])
    res = await get_vehicle_maintenance(
        {"vehicle_name": "103", "company": "G1",
         "_scope_vehicles": ["103"], "_scope_identities": [[42, "sam_42", "103"]]},
        None, account_id=1, db=db)
    assert res.get("error"), res
    assert "vehicle access" in res["error"].lower()
