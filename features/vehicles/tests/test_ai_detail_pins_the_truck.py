"""Guard: vehicle detail answers about the truck the resolver named.

get_vehicle_detail took matches[0] — the coin toss the resolver exists
to end. It is reachable whenever the resolved registry row carries no
company_code: company_for then passes None, the provider returns every
org's record answering to that unit number, and the caller was handed
whichever one the provider happened to list first. VIN, fuel, DEF,
position — all of somebody else's truck.

Detail rows key the provider vehicle on `id`, so the registry row's
telematics_ref names exactly one of them.
"""

import pytest

from features.vehicles.ai_tool import get_vehicle_detail


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


def _rec(pid, name="103", vin="VIN"):
    return {"id": pid, "name": name, "vin": vin, "make": "Freightliner",
            "model": "Cascadia", "year": 2021, "location": {},
            "fuel": {}, "def_level": {}, "_org": ""}


@pytest.mark.asyncio
async def test_the_resolved_truck_wins_over_the_providers_order(monkeypatch):
    """The registry row has NO company code — the path that made
    company_for pass None and left matches[0] to decide."""
    import features.vehicles.ai_tool as mod

    async def _detail(account_id, vehicle, company=None):
        # The provider lists the OTHER truck first.
        return [_rec("sam_99", vin="THEIRS"), _rec("sam_42", vin="OURS")]

    monkeypatch.setattr(mod, "_svc_detail", _detail, raising=False)
    db = _DB([_V(42, "sam_42", "103")])          # one registry row, no company
    res = await get_vehicle_detail({"vehicle_name": "103"}, None, account_id=1, db=db)

    assert res.get("vin") == "OURS", res


@pytest.mark.asyncio
async def test_an_unresolvable_truck_keeps_the_old_path(monkeypatch):
    """Retired, unregistered or mistyped — the registry cannot say, and
    the tool must still answer rather than refuse."""
    import features.vehicles.ai_tool as mod

    async def _detail(account_id, vehicle, company=None):
        return [_rec("sam_7", name="888", vin="ONLY")]

    monkeypatch.setattr(mod, "_svc_detail", _detail, raising=False)
    res = await get_vehicle_detail(
        {"vehicle_name": "888"}, None, account_id=1, db=_DB([]))
    assert res.get("vin") == "ONLY", res
