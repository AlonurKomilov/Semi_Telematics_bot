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


def _store(rows):
    """A ``_svc_events`` stand-in that narrows the way the store does.

    ``vehicle_id`` is a SUPERSET filter, not a membership test: one
    truck's rows plus every row that carries no provider id (the ladder
    decides those by unit name).  Mirroring it here keeps these guards
    honest — they judge the ladder against exactly the rows production
    now hands it.
    """
    async def _svc(account_id, days=7, company=None, vehicle_id=None):
        if not vehicle_id:
            return list(rows)
        return [r for r in rows
                if not r.get("vehicle_id") or r["vehicle_id"] == vehicle_id]
    return _svc



@pytest.mark.asyncio
async def test_a_renamed_truck_keeps_its_events(monkeypatch):
    import features.events.ai_tool as mod

    monkeypatch.setattr(
        mod, "_svc_events", _store([_ev(1, "sam_42", "229 Idris Ahmed")]), raising=False)
    res = await get_vehicle_events(
        {"vehicle_name": "229", "days": 7}, None,
        account_id=1, db=_DB([_V(42, "sam_42", "229")]))

    assert res["total_events"] == 1, (
        "exact name equality reported a clean week for a renamed truck"
    )


@pytest.mark.asyncio
async def test_the_twins_events_are_not_claimed(monkeypatch):
    """The LADDER denies the twin, not the query.

    The store now narrows by provider id before these rows arrive, so
    a plain store double would split the twins for free and this guard
    would quietly stop testing anything.  The double here hands BOTH
    rows over — which is exactly what the live-Samsara fallback does,
    since that path has no per-vehicle endpoint — so the denial has to
    come from the ladder, where it belongs.
    """
    import features.events.ai_tool as mod

    async def _account_wide(account_id, days=7, company=None, vehicle_id=None):
        return [_ev(1, "sam_42", "229"), _ev(2, "sam_99", "229")]

    monkeypatch.setattr(mod, "_svc_events", _account_wide, raising=False)
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

    monkeypatch.setattr(
        mod, "_svc_events", _store([_ev(1, "sam_7", "888")]), raising=False)
    res = await get_vehicle_events(
        {"vehicle_name": "888", "days": 7}, None, account_id=1, db=_DB([]))
    assert res["total_events"] == 1
