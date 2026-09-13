"""Guard: the odometer answers about the truck that was asked for.

get_vehicle_odometer was the only vehicle-keyed tool in its file that
neither went through the twin-safe resolver nor filtered through the
identity ladder. It picked the first row whose NAME matched, so:

* a scoped caller asking about their own unit number could be answered
  with the other company's twin's mileage, and
* an unscoped owner asking about a shared number got a silent coin toss
  between two trucks, where every sibling tool asks which company.
"""

import pytest

from features.vehicles.ai_tool import get_vehicle_odometer


class _V:
    def __init__(self, vid, ref, unit, company):
        self.id, self.telematics_ref = vid, ref
        self.unit_number, self.company_code = unit, company
        self.is_active = True


class _DB:
    def __init__(self, registry, states):
        self._registry, self._states = registry, states

    async def list_vehicles(self, account_id, **kw):
        return list(self._registry)

    async def get_vehicle_state(self, account_id, vehicle_nums=None):
        return list(self._states)


OSY = _V(42, "sam_42", "103", "OSY")
G1 = _V(99, "sam_99", "103", "G1")


def _state(registry_id, name, miles):
    return {"registry_id": registry_id, "vehicle_name": name,
            "vehicle_id": f"sam_{registry_id}", "odometer_mi": miles,
            "odometer_time": "2026-09-01T00:00:00Z"}


@pytest.mark.asyncio
async def test_a_scoped_caller_gets_their_own_twin(monkeypatch):
    import infra.platform as ip
    db = _DB([OSY, G1], [_state(42, "103", 100000), _state(99, "103", 555555)])

    async def _tenant(account_id):
        return db

    monkeypatch.setattr(ip, "get_tenant_db", _tenant)
    res = await get_vehicle_odometer(
        {"vehicle_name": "103", "company": "OSY",
         "_scope_vehicles": ["103"], "_scope_identities": [[42, "sam_42", "103"]]},
        None, account_id=1, db=db)

    assert res.get("odometer_miles") == 100000, res


@pytest.mark.asyncio
async def test_a_denied_twin_is_refused_not_answered(monkeypatch):
    import infra.platform as ip
    db = _DB([OSY, G1], [_state(99, "103", 555555)])

    async def _tenant(account_id):
        return db

    monkeypatch.setattr(ip, "get_tenant_db", _tenant)
    res = await get_vehicle_odometer(
        {"vehicle_name": "103", "company": "G1",
         "_scope_vehicles": ["103"], "_scope_identities": [[42, "sam_42", "103"]]},
        None, account_id=1, db=db)

    assert res.get("error"), res
    assert "vehicle access" in res["error"].lower()
    assert "odometer_miles" not in res


@pytest.mark.asyncio
async def test_an_unscoped_owner_is_asked_which_company(monkeypatch):
    """Every sibling tool asks; this one used to pick one silently."""
    import infra.platform as ip
    db = _DB([OSY, G1], [_state(42, "103", 100000), _state(99, "103", 555555)])

    async def _tenant(account_id):
        return db

    monkeypatch.setattr(ip, "get_tenant_db", _tenant)
    res = await get_vehicle_odometer(
        {"vehicle_name": "103"}, None, account_id=1, db=db)
    assert res.get("error"), res
    assert "odometer_miles" not in res
