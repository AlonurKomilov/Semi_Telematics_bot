"""Guard: the movement history belongs to the truck that was asked for.

The adapter resolves a name with
`SELECT vehicle_id FROM vehicle_state_live WHERE vehicle_name = ? LIMIT 1`,
so a shared unit number returned whichever row the database happened to
list first — and then that truck's ENTIRE movement history: every
position, every stop, every mile, narrated as the caller's own truck's
week.

The registry knows which truck it is and the adapter already accepts a
provider id, so the tool passes that instead of the number.
"""

import pytest

from features.vehicles.ai_tool import get_vehicle_history


class _V:
    def __init__(self, vid, ref, unit, company):
        self.id, self.telematics_ref = vid, ref
        self.unit_number, self.company_code = unit, company
        self.is_active = True


OSY = _V(42, "sam_42", "103", "OSY")
G1 = _V(99, "sam_99", "103", "G1")


class _DB:
    def __init__(self, registry):
        self._registry = registry
        self.asked = {}

    async def list_vehicles(self, account_id, **kw):
        return list(self._registry)

    async def query_vehicle_state_history(self, account_id, *, vehicle_id=None,
                                          vehicle_name=None, days=7, max_rows=200):
        self.asked = {"vehicle_id": vehicle_id, "vehicle_name": vehicle_name}
        return [{"captured_at": "2026-09-01T00:00:00Z", "lat": 1.0, "lon": 2.0,
                 "speed_mph": 0, "engine_state": "Off", "odometer_mi": 100}]


@pytest.mark.asyncio
async def test_the_history_is_asked_for_by_provider_id():
    db = _DB([OSY, G1])
    res = await get_vehicle_history(
        {"vehicle_name": "103", "company": "OSY", "days": 7},
        None, account_id=1, db=db)

    assert db.asked["vehicle_id"] == "sam_42", db.asked
    assert db.asked["vehicle_name"] is None, (
        "passing the number lets the adapter's LIMIT 1 pick the twin"
    )
    assert res["count"] == 1


@pytest.mark.asyncio
async def test_an_ambiguous_number_asks_which_company():
    db = _DB([OSY, G1])
    res = await get_vehicle_history(
        {"vehicle_name": "103", "days": 7}, None, account_id=1, db=db)
    assert res.get("error"), res
    assert db.asked == {}, "nothing should have been queried"


@pytest.mark.asyncio
async def test_a_denied_twin_is_refused():
    db = _DB([OSY, G1])
    res = await get_vehicle_history(
        {"vehicle_name": "103", "company": "G1", "days": 7,
         "_scope_vehicles": ["103"], "_scope_identities": [[42, "sam_42", "103"]]},
        None, account_id=1, db=db)
    assert res.get("error"), res
    assert "vehicle access" in res["error"].lower()


@pytest.mark.asyncio
async def test_a_truck_with_no_provider_link_still_answers_by_name():
    """A manual or TMS-only truck has no telematics_ref — the name is
    all there is, and the tool must still answer."""
    manual = _V(7, "", "888", "OSY")
    db = _DB([manual])
    res = await get_vehicle_history(
        {"vehicle_name": "888", "days": 7}, None, account_id=1, db=db)
    assert db.asked["vehicle_name"] == "888"
    assert res["count"] == 1
