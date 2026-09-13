"""Guard: parking rows are decided by identity, not by a name string.

The tool compared lowercased vehicle NAMES against the caller's allowed
set and dropped the injected `_scope_identities` on the floor. Two
consequences, in opposite directions:

* a same-numbered truck in another company walked straight in, because
  a name cannot tell twins apart;
* the caller's OWN truck dropped out the moment the provider renamed it
  ("229" becoming "229 Idris Ahmed"), because the name no longer
  matched — and an empty list reads as "nothing is parked".

parking_events rows carry `vehicle_id`, the provider id, so rung 2
settles both.
"""

import pytest

from features.parking.ai_tool import get_parked_vehicles


def _ev(eid, vehicle_id, name, hours=60.0):
    return {"id": eid, "vehicle_id": vehicle_id, "vehicle_name": name,
            "duration_hours": hours, "location_class": "unsafe",
            "company_code": "OSY", "address": "somewhere"}


class _DB:
    def __init__(self, active, history=None):
        self._a, self._h = active, history or []

    async def get_active_parking_events(self, account_id, attention_only=True):
        return list(self._a)

    async def get_parking_history(self, account_id, days=0, limit=50):
        return list(self._h)


PINNED = {"min_days": 1, "_scope_vehicles": ["229"],
          "_scope_identities": [[42, "sam_42", "229"]]}


@pytest.mark.asyncio
async def test_the_twin_does_not_walk_in_on_a_shared_number():
    db = _DB([_ev(1, "sam_42", "229"), _ev(2, "sam_99", "229")])
    res = await get_parked_vehicles(dict(PINNED), None, account_id=1, db=db)
    assert res["count"] == 1, res
    assert res["vehicles"][0]["vehicle"] == "229"


@pytest.mark.asyncio
async def test_a_renamed_truck_is_still_the_callers_own():
    """The registry keeps the unit number across a provider rename; the
    provider id is what survives it."""
    db = _DB([_ev(1, "sam_42", "229 Idris Ahmed")])
    res = await get_parked_vehicles(dict(PINNED), None, account_id=1, db=db)
    assert res["count"] == 1, (
        "name equality dropped the caller's own truck the day it was renamed"
    )


@pytest.mark.asyncio
async def test_an_unrestricted_caller_sees_both():
    db = _DB([_ev(1, "sam_42", "229"), _ev(2, "sam_99", "229")])
    res = await get_parked_vehicles({"min_days": 1}, None, account_id=1, db=db)
    assert res["count"] == 2


@pytest.mark.asyncio
async def test_an_empty_scope_fails_closed():
    db = _DB([_ev(1, "sam_42", "229")])
    res = await get_parked_vehicles(
        {"min_days": 1, "_scope_vehicles": []}, None, account_id=1, db=db)
    assert res["count"] == 0
