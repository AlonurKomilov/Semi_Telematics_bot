"""Guard: fuel money belongs to ONE truck.

Two defects, one cause — unit numbers are reused across companies.

The SQL grouped by vehicle_name alone, so two different trucks became
one row: their gallons, their cost and their odometer range added
together. Wrong money on its face, and merged BEFORE any caller could
separate them, so a company-scoped reader saw the other company's spend
inside their own row with nothing left to filter on. It groups by
(vehicle_name, company_code) now and carries the provider id onto the
row so the identity ladder can decide it.

get_vehicle_fuel_costs then looked its truck up by name and took the
first match — a coin toss between the two rows. It resolves first.
"""

import pytest

from features.vehicles.fuel.ai_tool import get_vehicle_fuel_costs


class _V:
    def __init__(self, vid, ref, unit, company):
        self.id, self.telematics_ref = vid, ref
        self.unit_number, self.company_code = unit, company
        self.is_active = True


OSY = _V(42, "sam_42", "103", "OSY")
G1 = _V(99, "sam_99", "103", "G1")


def _agg(company, vehicle_id, cost, gallons=100.0, entries=5):
    return {"vehicle_name": "103", "company_code": company,
            "vehicle_id": vehicle_id, "entries": entries,
            "total_gallons": gallons, "total_cost": cost, "avg_price": 4.0,
            "first_odo": 0, "last_odo": 1000}


def _entry(company, vehicle_id, cost=40.0):
    return {"date": "2026-09-01", "gallons": 10.0, "total_cost": cost,
            "price_per_gallon": 4.0, "odometer_miles": 500,
            "company_code": company, "vehicle_id": vehicle_id,
            "vehicle_name": "103"}


class _DB:
    def __init__(self, registry, aggs, entries):
        self._registry, self._aggs, self._entries = registry, aggs, entries

    async def list_vehicles(self, account_id, **kw):
        return list(self._registry)

    async def get_fuel_summary(self, account_id, start_date=None, end_date=None):
        return list(self._aggs)

    async def get_fuel_entries(self, account_id, vehicle_name=None, limit=None):
        return list(self._entries)

    async def get_fuel_entry_stats(self, account_id):
        return {"count": 10, "first_date": "2026-01-01", "last_date": "2026-09-01"}


@pytest.mark.asyncio
async def test_the_totals_are_one_companys_not_both():
    db = _DB([OSY, G1],
             [_agg("OSY", "sam_42", 400.0), _agg("G1", "sam_99", 9000.0)],
             [_entry("OSY", "sam_42")])
    res = await get_vehicle_fuel_costs(
        {"vehicle_name": "103", "company": "OSY"}, None, account_id=1, db=db)

    assert res["total_cost"] == 400.0, res


@pytest.mark.asyncio
async def test_the_sample_rows_belong_to_the_same_truck():
    db = _DB([OSY, G1],
             [_agg("OSY", "sam_42", 400.0), _agg("G1", "sam_99", 9000.0)],
             [_entry("OSY", "sam_42", 40.0), _entry("G1", "sam_99", 900.0)])
    res = await get_vehicle_fuel_costs(
        {"vehicle_name": "103", "company": "OSY"}, None, account_id=1, db=db)

    costs = [f["total_cost"] for f in res["recent_fills"]]
    assert costs == [40.0], costs


@pytest.mark.asyncio
async def test_a_hand_entered_fill_up_is_not_dropped():
    """A typed entry has no provider id — it must still count as the
    truck's own, judged by company."""
    db = _DB([OSY, G1],
             [_agg("OSY", "sam_42", 400.0)],
             [_entry("OSY", "", 40.0), _entry("G1", "sam_99", 900.0)])
    res = await get_vehicle_fuel_costs(
        {"vehicle_name": "103", "company": "OSY"}, None, account_id=1, db=db)
    assert [f["total_cost"] for f in res["recent_fills"]] == [40.0]


@pytest.mark.asyncio
async def test_an_ambiguous_number_asks_which_company():
    db = _DB([OSY, G1], [_agg("OSY", "sam_42", 400.0)], [_entry("OSY", "sam_42")])
    res = await get_vehicle_fuel_costs(
        {"vehicle_name": "103"}, None, account_id=1, db=db)
    assert res.get("error"), res
