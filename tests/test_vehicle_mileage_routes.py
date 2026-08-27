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
            "INSERT INTO vehicle_state_day "
            "(account_id, vehicle_id, vehicle_name, "
            " bucket_start, miles, odometer_eod) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (acct.id, vid, name, day_s, miles, odo),
        )
    # 107: 10,000 → 10,600 over the range; 213: 8,000 → 9,000.
    await day("v107", "107", "2026-07-01", 10_000)
    await day("v107", "107", "2026-07-02", 10_250, 250)
    await day("v107", "107", "2026-07-03", 10_600, 350)
    await day("v213", "213", "2026-07-01", 8_000)
    await day("v213", "213", "2026-07-03", 9_000, 1000)
    # 999 has vehicle_state_live but no telemetry rows → the no_data list.
    await db._db.commit()

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = db
    from interfaces.api.app import create_api
    app = create_api()
    yield {
        "app": app, "acct": acct, "db": db,
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


class TestUnitMerge:
    """One row per unit — but ONLY within a company.

    Same unit number in two companies = two different trucks (production:
    "103" in G1 and OSY).  Same number twice in ONE company = a gateway
    swap (production: PTG's "6729").  vehicle_state_live's unique constraint
    blocks creating the second case through the API, so the grouping is
    exercised directly on the pure helper.
    """

    @staticmethod
    def _row(name, company, miles, **kw):
        return {"vehicle_id": kw.get("vid", f"{name}-{company}-{miles}"),
                "vehicle_name": name, "company": company, "miles": miles,
                "start_odo": kw.get("start_odo", 0),
                "end_odo": kw.get("end_odo", miles),
                "start_read_on": "2026-07-01", "end_read_on": "2026-07-03",
                "days_covered": kw.get("days", 2), "flag": kw.get("flag", "")}

    def test_same_name_different_companies_stay_separate(self):
        from features.vehicles.router import _merge_unit_rows
        out = _merge_unit_rows([
            self._row("103", "G1", 500.0),
            self._row("103", "OSY", 300.0),
        ])
        assert len(out) == 2
        assert {r["company"] for r in out} == {"G1", "OSY"}
        assert all(r["flag"] == "" for r in out)

    def test_gateway_swap_in_one_company_merges(self):
        from features.vehicles.router import _merge_unit_rows
        out = _merge_unit_rows([
            self._row("6729", "PTG", 600.0, vid="old", days=2),
            self._row("6729", "PTG", 400.0, vid="new", days=3),
        ])
        assert len(out) == 1
        assert out[0]["miles"] == 1000.0        # the unit's real driving
        assert out[0]["days_covered"] == 3
        assert out[0]["flag"] == "device_change"
        assert out[0]["vehicle_id"] == "old"    # the device that drove most

    def test_retired_device_with_no_miles_is_absorbed_silently(self):
        from features.vehicles.router import _merge_unit_rows
        out = _merge_unit_rows([
            self._row("6729", "PTG", 900.0, vid="live"),
            self._row("6729", "PTG", 0.0, vid="dead"),
        ])
        assert len(out) == 1 and out[0]["miles"] == 900.0
        # only ONE device drove — no need to warn about the odometer span
        assert out[0]["flag"] == ""

    def test_sorted_by_miles_desc(self):
        from features.vehicles.router import _merge_unit_rows
        out = _merge_unit_rows([
            self._row("a", "X", 10.0), self._row("b", "X", 90.0),
        ])
        assert [r["vehicle_name"] for r in out] == ["b", "a"]


class TestDataFreshness:
    @pytest.mark.asyncio
    async def test_data_through_reports_newest_stored_day(self, mileage_app):
        r = await _get(mileage_app["app"], f"/api/vehicles/mileage?{RANGE}",
                       mileage_app["token_owner"])
        assert r.json()["data_through"] == "2026-07-03"


class TestTimeOfDayParams:
    """start/end accept datetimes; times are account-tz; honesty fields
    say when the tiers couldn't answer at that precision."""

    @pytest.mark.asyncio
    async def test_datetime_params_accepted_with_honesty_fields(self, mileage_app):
        r = await _get(mileage_app["app"],
                       "/api/vehicles/mileage?start=2026-07-02T08:00&end=2026-07-03T20:00",
                       mileage_app["token_owner"])
        assert r.status_code == 200
        body = r.json()
        assert body["time_requested"] is True
        # Fixture has only daily rows — no snapshot/hourly tier can
        # answer 08:00, so every returned vehicle is named imprecise.
        names = {v["vehicle_name"] for v in body["vehicles"]}
        assert set(body["imprecise_time_for"]) == names

    @pytest.mark.asyncio
    async def test_date_only_reports_no_time_request(self, mileage_app):
        r = await _get(mileage_app["app"], f"/api/vehicles/mileage?{RANGE}",
                       mileage_app["token_owner"])
        assert r.status_code == 200
        body = r.json()
        assert body["time_requested"] is False
        assert body["imprecise_time_for"] == []

    @pytest.mark.asyncio
    async def test_bad_time_is_422(self, mileage_app):
        r = await _get(mileage_app["app"],
                       "/api/vehicles/mileage?start=2026-07-02T99:00&end=2026-07-03",
                       mileage_app["token_owner"])
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_trips_window_honors_exact_times(self, mileage_app, monkeypatch):
        import infra.services as _svc
        captured = {}

        class _SC:
            async def get_vehicle_trips(self, vehicle_id, start_ms, end_ms):
                captured["start_ms"], captured["end_ms"] = start_ms, end_ms
                return []

        class _MC:
            clients = {"MF": _SC()}

        async def fake_get_client(account_id, **kw):
            return _MC()
        monkeypatch.setattr(_svc, "get_client", fake_get_client)
        r = await _get(
            mileage_app["app"],
            "/api/vehicles/107/trips?start=2026-07-02T08:00&end=2026-07-02T20:00",
            mileage_app["token_owner"])
        assert r.status_code == 200
        from datetime import datetime, timezone as _tz
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")   # account default tz
        want_start = int(datetime(2026, 7, 2, 8, 0, tzinfo=et).timestamp() * 1000)
        want_end = int(datetime(2026, 7, 2, 20, 0, tzinfo=et).timestamp() * 1000)
        assert captured["start_ms"] == want_start
        assert captured["end_ms"] == want_end
