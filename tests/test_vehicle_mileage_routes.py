"""Period-mileage routes (/vehicles/mileage + /vehicles/{name}/mileage).

Through the ASGI harness — pins the visibility contract (owner sees
every vehicle + the honest ``no_data`` list; an own-vehicle caller sees
only their assigned truck and can't detail-fetch someone else's) and
the honest-range rule (bad or out-of-retention ranges are 422, never
silent zeros).  The delta engine itself is covered by
tests/test_period_mileage.py.
"""

from __future__ import annotations

import os
import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def mileage_app(pg_db):
    db = pg_db
    acct = await db.create_account("Mileage Fleet Co")
    await db.add_company(acct.id, "MF", "key_mf", "Mileage Fleet Co")
    owner = await db.create_user(12001, acct.id, role=Role.OWNER)
    driver = await db.create_user(12002, acct.id, role=Role.DRIVER)
    await db.assign_vehicle(
        user_id=driver.id, account_id=acct.id, truck_num="107",
        assigned_by=0, is_primary=True,
    )

    await db.upsert_vehicle_state(acct.id, [
        {"vehicle_id": "v107", "vehicle_name": "107", "company_code": "MF"},
        {"vehicle_id": "v213", "vehicle_name": "213", "company_code": "MF"},
        {"vehicle_id": "v999", "vehicle_name": "999", "company_code": "MF"},
    ])

    async def day(vid, name, day_s, odo, miles=0.0):
        await db._db.execute(
            "INSERT INTO vehicle_telemetry "
            "(account_id, vehicle_id, vehicle_name, granularity, "
            " bucket_start, miles, odometer_eod) "
            "VALUES (?, ?, ?, 'daily', ?, ?, ?)",
            (acct.id, vid, name, day_s, miles, odo),
        )
    # 107: 10,000 → 10,600 over the range; 213: 8,000 → 9,000.
    await day("v107", "107", "2026-07-01", 10_000)
    await day("v107", "107", "2026-07-02", 10_250, 250)
    await day("v107", "107", "2026-07-03", 10_600, 350)
    await day("v213", "213", "2026-07-01", 8_000)
    await day("v213", "213", "2026-07-03", 9_000, 1000)
    # 999 has vehicle_state but no telemetry rows → the no_data list.
    await db._db.commit()

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = db
    from interfaces.api.app import create_api
    app = create_api()
    yield {
        "app": app, "acct": acct,
        "token_owner": create_jwt(owner.telegram_id, acct.id, "owner"),
        "token_driver": create_jwt(driver.telegram_id, acct.id, "driver"),
    }
    _cp._db = _old


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _get(app, path, tok):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t",
    ) as c:
        return await c.get(path, headers=_h(tok))


RANGE = "start=2026-07-02&end=2026-07-03"


class TestAccountMileage:
    @pytest.mark.asyncio
    async def test_owner_sees_all_plus_no_data(self, mileage_app):
        r = await _get(mileage_app["app"],
                       f"/api/vehicles/mileage?{RANGE}",
                       mileage_app["token_owner"])
        assert r.status_code == 200
        body = r.json()
        by_name = {v["vehicle_name"]: v for v in body["vehicles"]}
        assert by_name["107"]["miles"] == 600.0
        assert by_name["213"]["miles"] == 1000.0
        assert body["total_miles"] == 1600.0
        assert body["no_data"] == ["999"]

    @pytest.mark.asyncio
    async def test_driver_sees_only_assigned_truck(self, mileage_app):
        r = await _get(mileage_app["app"],
                       f"/api/vehicles/mileage?{RANGE}",
                       mileage_app["token_driver"])
        assert r.status_code == 200
        body = r.json()
        assert [v["vehicle_name"] for v in body["vehicles"]] == ["107"]
        assert "213" not in body["no_data"]

    @pytest.mark.asyncio
    async def test_bad_ranges_are_422(self, mileage_app):
        app, tok = mileage_app["app"], mileage_app["token_owner"]
        assert (await _get(app, "/api/vehicles/mileage?start=2026-07-05&end=2026-07-01", tok)).status_code == 422
        assert (await _get(app, "/api/vehicles/mileage?start=bad&end=2026-07-01", tok)).status_code == 422
        assert (await _get(app, "/api/vehicles/mileage?start=2020-01-01&end=2020-02-01", tok)).status_code == 422


class TestVehicleMileage:
    @pytest.mark.asyncio
    async def test_detail_with_daily_bars(self, mileage_app):
        r = await _get(mileage_app["app"],
                       f"/api/vehicles/107/mileage?{RANGE}",
                       mileage_app["token_owner"])
        assert r.status_code == 200
        body = r.json()
        assert body["miles"] == 600.0 and body["no_data"] is False
        assert [d["miles"] for d in body["days"]] == [250.0, 350.0]

    @pytest.mark.asyncio
    async def test_driver_cannot_detail_other_truck(self, mileage_app):
        r = await _get(mileage_app["app"],
                       f"/api/vehicles/213/mileage?{RANGE}",
                       mileage_app["token_driver"])
        assert r.status_code == 404
