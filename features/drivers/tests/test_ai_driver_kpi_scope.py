"""Guard: the driver KPI tools narrow to the caller's own trucks.

The service filters by NAME (`vehicle_nums`), which cannot tell two
companies' same-numbered trucks apart — so a driver who only ever drove
the OTHER company's "103" came back inside a scope that names "103", and
their MPG, idle percentage and eco score were narrated as the caller's
own fleet's.

A driver row is not a vehicle row: it carries a LIST of trucks, and a
driver belongs to the caller when ANY of those trucks does. This is a
NARROWING pass over the service's own result — it can only remove rows,
never add them. Pushing identity down into the KPI service is the other
half, and waits for the scorecard work that will reshape these rows.
"""

import pytest

from features.drivers.ai_tool import get_driver_efficiency, get_driver_scorecard


def _driver(name, *vehicles):
    """vehicles: (provider_id, display_name) pairs."""
    return {
        "driver_id": name, "driver_name": name,
        "_drive_pct": 80.0, "_idle_pct": 10.0, "_mpg": 7.0,
        "_miles": 900, "_green_pct": 85.0, "_drive_h": 40,
        "_idle_h": 4, "_overspeed_min": 2, "_anticipation_pct": 70.0,
        "_vehicle_summaries": [
            {"vehicle": {"id": vid, "name": vname}} for vid, vname in vehicles
        ],
    }


PINNED = {"days": 7, "_scope_vehicles": ["103"],
          "_scope_identities": [[42, "sam_42", "103"]]}


@pytest.fixture
def _svc(monkeypatch):
    import features.drivers.ai_tool as mod
    rows = [
        _driver("Ours", ("sam_42", "103")),
        _driver("Theirs", ("sam_99", "103")),      # the twin's driver
    ]

    async def _eff(account_id, days=7, vehicle_nums=None):
        # The service filters by NAME, so both come back.
        if vehicle_nums is None:
            return list(rows)
        want = {v.strip().lower() for v in vehicle_nums}
        return [
            d for d in rows
            if any((vs["vehicle"]["name"] or "").lower() in want
                   for vs in d["_vehicle_summaries"])
        ]

    monkeypatch.setattr(mod, "_svc_drv_eff", _eff, raising=False)
    return rows


@pytest.mark.asyncio
async def test_efficiency_drops_the_twins_driver(_svc):
    res = await get_driver_efficiency(dict(PINNED), None, account_id=1)
    names = [d["name"] for d in res["drivers"]]
    assert names == ["Ours"], names


@pytest.mark.asyncio
async def test_the_scorecard_drops_them_too(_svc):
    res = await get_driver_scorecard(dict(PINNED), None, account_id=1)
    names = [d["name"] for d in res["drivers"]]
    assert names == ["Ours"], names


@pytest.mark.asyncio
async def test_an_unrestricted_caller_sees_everyone(_svc):
    res = await get_driver_efficiency({"days": 7}, None, account_id=1)
    assert len(res["drivers"]) == 2


@pytest.mark.asyncio
async def test_an_empty_scope_fails_closed(_svc):
    res = await get_driver_efficiency(
        {"days": 7, "_scope_vehicles": []}, None, account_id=1)
    assert res["drivers"] == []


@pytest.mark.asyncio
async def test_a_driver_of_several_trucks_is_kept_for_any_one_of_them(_svc):
    """Any truck in scope keeps the driver — their totals span the
    fleet they drove, and dropping them would hide a driver the caller
    genuinely manages."""
    import features.drivers.ai_tool as mod

    async def _eff(account_id, days=7, vehicle_nums=None):
        return [_driver("Mixed", ("sam_99", "999"), ("sam_42", "103"))]

    mod._svc_drv_eff = _eff
    res = await get_driver_efficiency(dict(PINNED), None, account_id=1)
    assert [d["name"] for d in res["drivers"]] == ["Mixed"]
