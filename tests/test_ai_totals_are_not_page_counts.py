"""Guard: a money total or a pipeline count is never measured from a page.

Three results derived an account-level number from whatever rows a
capped query happened to return, and presented it under a key that
claims to be the whole:

* get_vehicle_fuel_costs summed `total_cost` over a LIMIT 200 fetch,
  under a schema that says "omit days for all-time". A truck past 200
  fill-ups had its fuel spend understated by an unknown amount and
  stated as fact.
* get_fuel_cost_summary returned twenty per-vehicle rows and nothing
  else, so "what did we spend on fuel" was answered by adding up twenty
  of eighty trucks.
* get_driver_applications built `total` and the per-stage histogram from
  a 500-row page, while its own schema told the model the counts "always
  cover every application".
"""

import pytest


@pytest.mark.asyncio
async def test_fuel_costs_take_the_total_from_the_uncapped_aggregate():
    from features.vehicles.fuel.ai_tool import get_vehicle_fuel_costs

    page = [{"date": "2026-09-01", "gallons": 100.0, "total_cost": 400.0,
             "price_per_gallon": 4.0, "odometer_miles": 1000}] * 200

    class _DB:
        async def list_vehicles(self, account_id, **kw):
            """The registry, which the tool now consults to pick WHICH
            truck. Empty here on purpose: these tests are about the
            money coming from the uncapped aggregate rather than the
            page, and an empty registry is the "cannot say" path that
            keeps the name query. Twins have their own file."""
            return []

        async def get_fuel_entries(self, account_id, vehicle_name=None, limit=None):
            return list(page)

        async def get_fuel_summary(self, account_id, start_date=None, end_date=None):
            # The truth: 900 fill-ups, not the 200 the page carries.
            return [{"vehicle_name": "103", "entries": 900,
                     "total_gallons": 45000.0, "total_cost": 180000.0,
                     "avg_price": 4.0}]

        async def get_fuel_entry_stats(self, account_id):
            return {"count": 900, "first_date": "2024-01-01", "last_date": "2026-09-01"}

    res = await get_vehicle_fuel_costs(
        {"vehicle_name": "103"}, None, account_id=1, db=_DB())

    assert res["total_cost"] == 180000.0, (
        "the money must come from the aggregate, not from the capped page"
    )
    assert res["entry_count"] == 900
    assert res["totals_cover"] == "all fill-ups"
    # The page is still what the sample rows come from.
    assert len(res["recent_fills"]) == 10


@pytest.mark.asyncio
async def test_fuel_costs_say_so_when_the_aggregate_is_unavailable():
    from features.vehicles.fuel.ai_tool import get_vehicle_fuel_costs

    class _DB:
        async def list_vehicles(self, account_id, **kw):
            return []

        async def get_fuel_entries(self, account_id, vehicle_name=None, limit=None):
            return [{"date": "2026-09-01", "gallons": 10.0, "total_cost": 40.0,
                     "price_per_gallon": 4.0, "odometer_miles": 1}]

        async def get_fuel_summary(self, *a, **k):
            raise RuntimeError("aggregate unavailable")

        async def get_fuel_entry_stats(self, account_id):
            return {"count": 1, "first_date": "x", "last_date": "y"}

    res = await get_vehicle_fuel_costs(
        {"vehicle_name": "103"}, None, account_id=1, db=_DB())
    assert res["total_cost"] == 40.0
    assert "could not be read" in res["totals_cover"], (
        "a total that might be partial must never look exact"
    )


@pytest.mark.asyncio
async def test_the_fuel_summary_total_covers_every_vehicle():
    from features.vehicles.fuel.ai_tool import get_fuel_cost_summary

    fleet = [
        {"vehicle_name": f"T-{i:03d}", "entries": 5, "total_gallons": 100.0,
         "total_cost": 400.0, "avg_price": 4.0, "first_odo": 0, "last_odo": 1000}
        for i in range(80)
    ]

    class _DB:
        async def get_fuel_summary(self, account_id, start_date=None, end_date=None):
            return list(fleet)

        async def get_fuel_entry_stats(self, account_id):
            return {"count": 400, "first_date": "x", "last_date": "y"}

    res = await get_fuel_cost_summary({}, None, account_id=1, db=_DB())

    assert res["vehicle_count"] == 80
    assert res["vehicles_shown"] == 20
    assert res["truncated"] is True
    assert res["account_total_cost"] == 32000.0, (
        "80 trucks x $400 — adding up the twenty rows would give 8000"
    )
    assert res["account_entry_count"] == 400


@pytest.mark.asyncio
async def test_applications_total_comes_from_the_database():
    from features.applications.ai_tool import get_driver_applications

    page = [{"status": "submitted", "reference": f"R{i}", "first_name": "A",
             "last_name": "B", "submitted_at": "2026-09-01"} for i in range(500)]

    class _DB:
        async def list_driver_applications(self, account_id, limit=None):
            return list(page)

        async def count_driver_applications(self, account_id, status=""):
            return 1200

    res = await get_driver_applications({}, None, account_id=1, db=_DB())

    assert res["total"] == 1200, "the total is the database's, not the page's"
    assert res["counted_from"] == 500
    assert "newest 500 of 1200" in res["by_stage_covers"]


@pytest.mark.asyncio
async def test_applications_say_the_counts_are_complete_when_they_are():
    from features.applications.ai_tool import get_driver_applications

    page = [{"status": "submitted", "reference": "R1", "first_name": "A",
             "last_name": "B", "submitted_at": "2026-09-01"}]

    class _DB:
        async def list_driver_applications(self, account_id, limit=None):
            return list(page)

        async def count_driver_applications(self, account_id, status=""):
            return 1

    res = await get_driver_applications({}, None, account_id=1, db=_DB())
    assert res["total"] == 1
    assert res["by_stage_covers"] == "every application"
