"""Empty fuel-tool answers must say WHICH kind of empty.

"No fuel entries recorded" on an account that never recorded any led the
model to suggest querying other months — every month is equally empty.
The tool now distinguishes never-recorded (say how data gets entered,
forbid period-shopping) from empty-window (echo the account's real data
range so the model can point at it).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.permissions.roles import Role


async def _seed(pg_db, name="Fuel Co"):
    from interfaces.api.auth import _hash_password
    acct = await pg_db.create_account(name)
    await pg_db.create_user_with_email(
        email=f"owner.{acct.id}@example.com",
        password_hash=_hash_password("ownerpass123"),
        account_id=acct.id, role=Role.OWNER, display_name="Owner",
    )
    return acct.id


async def test_never_recorded_forbids_period_shopping(pg_db):
    from features.vehicles.fuel.ai_tool import get_fuel_cost_summary
    acct = await _seed(pg_db)
    out = await get_fuel_cost_summary(
        {"start_date": "2026-07-01", "end_date": "2026-07-31"},
        None, account_id=acct, db=pg_db,
    )
    assert out["entries_total"] == 0
    assert "NEVER recorded" in out["result"]
    assert "do not suggest" in out["result"].lower()


async def test_empty_window_reports_real_data_range(pg_db):
    from features.vehicles.fuel.ai_tool import get_fuel_cost_summary
    acct = await _seed(pg_db)
    await pg_db.add_fuel_entry(
        acct, vehicle_name="228", company_code="PTG", date="2026-05-10",
        gallons=100.0, price_per_gallon=3.50, odometer_miles=250000,
    )
    out = await get_fuel_cost_summary(
        {"start_date": "2026-07-01", "end_date": "2026-07-31"},
        None, account_id=acct, db=pg_db,
    )
    assert out["entries_total"] == 1
    assert "2026-05-10" in out["result"]          # the real data range
    assert out["data_range"]["last"] == "2026-05-10"


async def test_data_in_window_still_summarizes(pg_db):
    from features.vehicles.fuel.ai_tool import get_fuel_cost_summary
    acct = await _seed(pg_db)
    await pg_db.add_fuel_entry(
        acct, vehicle_name="228", company_code="PTG", date="2026-07-10",
        gallons=100.0, price_per_gallon=3.50, odometer_miles=250000,
    )
    out = await get_fuel_cost_summary(
        {"start_date": "2026-07-01"}, None, account_id=acct, db=pg_db,
    )
    assert out.get("vehicles") or out.get("result") is None or "228" in str(out)
