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


class TestVehicleTrips:
    """Trips drill-in — live-Samsara route with a stubbed client."""

    @staticmethod
    def _stub_client(trips=None, raise_unavailable=False):
        from adapters.telematics.samsara.circuit_breaker import (
            SamsaraUnavailable,
        )

        class _SC:
            async def get_vehicle_trips(self, vehicle_id, start_ms, end_ms):
                if raise_unavailable:
                    raise SamsaraUnavailable("breaker open")
                return trips or []

        class _MC:
            clients = {"MF": _SC()}
        return _MC()

    @pytest.mark.asyncio
    async def test_trips_summary_and_rows(self, mileage_app, monkeypatch):
        import infra.services as _svc
        stub = self._stub_client(trips=[
            {"startMs": 1_753_500_000_000, "endMs": 1_753_503_600_000,
             "startLocation": "Yard, Columbus OH",
             "endLocation": "Pilot #221, Dayton OH",
             "distanceMeters": 80_467, "driverId": 42},
            {"startMs": 1_753_510_000_000, "endMs": 1_753_512_000_000,
             "startLocation": "Pilot #221, Dayton OH",
             "endLocation": "Receiver, Cincinnati OH",
             "distanceMeters": 40_233},
        ])

        async def fake_get_client(account_id, **kw):
            return stub
        monkeypatch.setattr(_svc, "get_client", fake_get_client)

        r = await _get(mileage_app["app"],
                       f"/api/vehicles/107/trips?{RANGE}",
                       mileage_app["token_owner"])
        assert r.status_code == 200
        body = r.json()
        assert body["trip_count"] == 2
        assert body["total_trip_miles"] == 75.0     # 50 + 25 miles
        # newest first
        assert body["trips"][0]["start_location"] == "Pilot #221, Dayton OH"
        assert body["trips"][1]["miles"] == 50.0
        assert body["trips"][1]["duration_min"] == 60.0

    @pytest.mark.asyncio
    async def test_driver_cannot_fetch_other_trucks_trips(
        self, mileage_app, monkeypatch,
    ):
        import infra.services as _svc

        async def fake_get_client(account_id, **kw):  # pragma: no cover
            raise AssertionError("visibility wall must reject first")
        monkeypatch.setattr(_svc, "get_client", fake_get_client)
        r = await _get(mileage_app["app"],
                       f"/api/vehicles/213/trips?{RANGE}",
                       mileage_app["token_driver"])
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_samsara_down_is_honest_503(self, mileage_app, monkeypatch):
        import infra.services as _svc
        stub = self._stub_client(raise_unavailable=True)

        async def fake_get_client(account_id, **kw):
            return stub
        monkeypatch.setattr(_svc, "get_client", fake_get_client)
        r = await _get(mileage_app["app"],
                       f"/api/vehicles/107/trips?{RANGE}",
                       mileage_app["token_owner"])
        assert r.status_code == 503
        assert "mileage totals still work" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_in_progress_trip_duration_is_sane(
        self, mileage_app, monkeypatch,
    ):
        # Samsara marks an unfinished trip with endMs = int64 max;
        # trusting it printed a 2.5-trillion-hour duration.
        import infra.services as _svc
        stub = self._stub_client(trips=[
            {"startMs": 1_753_500_000_000, "endMs": 9_223_372_036_854_775_807,
             "startLocation": "I-84, Ontario OR",
             "distanceMeters": 16_093},
        ])

        async def fake_get_client(account_id, **kw):
            return stub
        monkeypatch.setattr(_svc, "get_client", fake_get_client)
        r = await _get(mileage_app["app"],
                       f"/api/vehicles/107/trips?{RANGE}",
                       mileage_app["token_owner"])
        assert r.status_code == 200
        body = r.json()
        t = body["trips"][0]
        assert t["in_progress"] is True
        assert t["end_ms"] == 0
        # start → now, not start → int64 max; and never negative.
        assert 0 <= t["duration_min"] < 60 * 24 * 400
        assert body["driving_min"] < 60 * 24 * 400
