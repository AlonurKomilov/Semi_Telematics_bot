"""Guard: safety events follow the truck, not the label on it.

get_vehicle_events compared the model's raw vehicle string to the
provider's `vehicle_name` by exact equality. The registry keeps a
truck's unit number across a provider rename — the upsert matches on the
telematics ref — so a truck the provider now calls "229 Idris Ahmed"
answered ZERO safety events to somebody asking about "229". On a safety
question, zero reads as a clean week.

The rows carry `vehicle_id`, so once the resolver has said WHICH truck
this is, rung 2 claims its rows whatever they are called.
"""

import pytest

from features.events.ai_tool import get_vehicle_events


class _V:
    def __init__(self, vid, ref, unit, company=""):
        self.id, self.telematics_ref = vid, ref
        self.unit_number, self.company_code = unit, company
        self.is_active = True


class _DB:
    def __init__(self, rows):
        self._rows = rows

    async def list_vehicles(self, account_id, **kw):
        return list(self._rows)


def _ev(eid, vehicle_id, name):
    return {"event_id": eid, "vehicle_id": vehicle_id, "vehicle_name": name,
            "event_name": "Harsh Brake", "driver_name": "D", "time": "2026-09-01",
            "g_force": 1.2}


@pytest.mark.asyncio
async def test_a_renamed_truck_keeps_its_events(monkeypatch):
    import features.events.ai_tool as mod

    async def _svc(account_id, days=7):
        return [_ev(1, "sam_42", "229 Idris Ahmed")]

    monkeypatch.setattr(mod, "_svc_events", _svc, raising=False)
    res = await get_vehicle_events(
        {"vehicle_name": "229", "days": 7}, None,
        account_id=1, db=_DB([_V(42, "sam_42", "229")]))

    assert res["total_events"] == 1, (
        "exact name equality reported a clean week for a renamed truck"
    )


@pytest.mark.asyncio
async def test_the_twins_events_are_not_claimed(monkeypatch):
    import features.events.ai_tool as mod

    async def _svc(account_id, days=7):
        return [_ev(1, "sam_42", "229"), _ev(2, "sam_99", "229")]

    monkeypatch.setattr(mod, "_svc_events", _svc, raising=False)
    db = _DB([_V(42, "sam_42", "229", "OSY")])
    res = await get_vehicle_events(
        {"vehicle_name": "229", "days": 7}, None, account_id=1, db=db)

    assert res["total_events"] == 1
    assert res["events"][0]["type"] == "Harsh Brake"


@pytest.mark.asyncio
async def test_an_unregistered_truck_still_matches_by_name(monkeypatch):
    """The registry cannot always say — a retired or mistyped unit. That
    path keeps the name filter the archived-vehicle contract needs."""
    import features.events.ai_tool as mod

    async def _svc(account_id, days=7):
        return [_ev(1, "sam_7", "888")]

    monkeypatch.setattr(mod, "_svc_events", _svc, raising=False)
    res = await get_vehicle_events(
        {"vehicle_name": "888", "days": 7}, None, account_id=1, db=_DB([]))
    assert res["total_events"] == 1
