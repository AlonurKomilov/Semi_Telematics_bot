"""Driver-role isolation for the parking-safety feature.

Background
----------
A Telegram screenshot showed a driver receiving a "Parking Safety —
28 vehicles needing attention" message listing trucks unrelated to the
driver's assigned vehicle.  Root cause: the bot's parking callback
handlers and one API helper accepted the ``can_*_own`` permission
without then filtering events to the driver's truck — every active /
historical parking event in the account leaked across to drivers.

These tests pin the fixed behaviour so the leak cannot regress:

* ``/api/parking/active`` — own-only callers see only their truck row.
* ``/api/parking/history`` — own-only callers see only their truck row.
* ``/api/parking/{id}`` — own-only callers cannot fetch detail for
  another truck's event (404).
* ``/api/parking/{id}/map-image`` — same 404 guard.
* ``interfaces.bot.callbacks.parking._filter_to_own_vehicles`` —
  pure-function filter used by the bot, mirrors the API's substring
  match.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Database, Role
from interfaces.api.auth import create_jwt


# ── Fixture ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def parking_app(tmp_path):
    db_path = str(tmp_path / "parking.db")
    database = Database(db_path)
    await database.initialize()

    acct = await database.create_account("Parking Co")
    await database.add_company(acct.id, "PC", "key_pc", "Parking Co")
    driver = await database.create_user(11001, acct.id, role=Role.DRIVER)
    owner = await database.create_user(11002, acct.id, role=Role.OWNER)
    await database.assign_vehicle(
        user_id=driver.id, account_id=acct.id, truck_num="107",
        assigned_by=0, is_primary=True,
    )

    # Three active parking events in this account.  Only "107" belongs
    # to the driver above — the other two simulate other-fleet trucks
    # whose data must NOT leak into a driver's parking view.
    own_event = await database.upsert_parking_event(
        account_id=acct.id, vehicle_id="v107",
        vehicle_name="107", company_code="PC",
        latitude=40.0, longitude=-74.0,
        address="I-95 Shoulder",
        first_stopped="2026-04-01T10:00:00",
        duration_hours=2.5, location_class="unsafe",
    )
    other_event = await database.upsert_parking_event(
        account_id=acct.id, vehicle_id="v213",
        vehicle_name="213", company_code="PC",
        latitude=41.0, longitude=-75.0,
        address="Garden State Pkwy",
        first_stopped="2026-04-01T11:00:00",
        duration_hours=18.7, location_class="unsafe",
    )
    await database.upsert_parking_event(
        account_id=acct.id, vehicle_id="v571701",
        vehicle_name="571701", company_code="PC",
        latitude=42.0, longitude=-76.0,
        address="Truck Stop Plaza",
        first_stopped="2026-04-01T12:00:00",
        duration_hours=1.4, location_class="unknown",
    )

    token_driver = create_jwt(driver.telegram_id, acct.id, "driver")
    token_owner = create_jwt(owner.telegram_id, acct.id, "owner")

    import infra.platform as _cp
    from adapters.storage.tenant_router import LegacyRouter
    _old_router, _old_db = _cp._router, _cp._db
    _cp._router = LegacyRouter(database)
    _cp._db = database

    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": database, "acct": acct, "driver": driver,
        "token_driver": token_driver, "token_owner": token_owner,
        "own_event_id": own_event["id"], "other_event_id": other_event["id"],
    }

    _cp._router, _cp._db = _old_router, _old_db
    await database.close()


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── API: list endpoints ────────────────────────────────────────────


class TestParkingApiDriverIsolation:
    async def test_active_driver_sees_only_own_truck(self, parking_app):
        async with AsyncClient(
            transport=ASGITransport(app=parking_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/parking/active",
                headers=_h(parking_app["token_driver"]),
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["count"] == 1
            assert data["events"][0]["vehicle_name"] == "107"

    async def test_active_owner_sees_full_fleet(self, parking_app):
        async with AsyncClient(
            transport=ASGITransport(app=parking_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/parking/active",
                headers=_h(parking_app["token_owner"]),
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["count"] == 3
            names = {e["vehicle_name"] for e in data["events"]}
            assert names == {"107", "213", "571701"}

    async def test_history_driver_sees_only_own_truck(self, parking_app, tmp_path):
        # Resolve all events so they appear in the history endpoint.
        db = parking_app["db"]
        acct = parking_app["acct"]
        for vid in ("v107", "v213", "v571701"):
            await db.resolve_parking_event(acct.id, vid)
        async with AsyncClient(
            transport=ASGITransport(app=parking_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/parking/history?days=30",
                headers=_h(parking_app["token_driver"]),
            )
            assert r.status_code == 200, r.text
            data = r.json()
            for e in data["events"]:
                assert e["vehicle_name"] == "107"

    async def test_stats_summary_driver_counts_only_own(self, parking_app):
        async with AsyncClient(
            transport=ASGITransport(app=parking_app["app"]), base_url="http://t"
        ) as c:
            r_d = await c.get(
                "/api/parking/stats/summary",
                headers=_h(parking_app["token_driver"]),
            )
            r_o = await c.get(
                "/api/parking/stats/summary",
                headers=_h(parking_app["token_owner"]),
            )
            assert r_d.status_code == 200 and r_o.status_code == 200
            assert r_d.json()["total_parked"] == 1
            assert r_o.json()["total_parked"] == 3


# ── API: detail / map-image / resolve endpoints ───────────────────


class TestParkingApiDetailGuards:
    async def test_detail_driver_404s_other_truck(self, parking_app):
        async with AsyncClient(
            transport=ASGITransport(app=parking_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                f"/api/parking/{parking_app['other_event_id']}",
                headers=_h(parking_app["token_driver"]),
            )
            assert r.status_code == 404

    async def test_detail_driver_200_own_truck(self, parking_app):
        async with AsyncClient(
            transport=ASGITransport(app=parking_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                f"/api/parking/{parking_app['own_event_id']}",
                headers=_h(parking_app["token_driver"]),
            )
            assert r.status_code == 200
            assert r.json()["vehicle_name"] == "107"

    async def test_resolve_driver_blocked_for_other_truck(self, parking_app):
        async with AsyncClient(
            transport=ASGITransport(app=parking_app["app"]), base_url="http://t"
        ) as c:
            r = await c.post(
                f"/api/parking/{parking_app['other_event_id']}/resolve",
                headers=_h(parking_app["token_driver"]),
            )
            assert r.status_code == 404

    async def test_map_image_driver_blocked_for_other_truck(self, parking_app):
        async with AsyncClient(
            transport=ASGITransport(app=parking_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                f"/api/parking/{parking_app['other_event_id']}/map-image",
                headers=_h(parking_app["token_driver"]),
            )
            assert r.status_code == 404


# ── Bot helper: pure-function filter ───────────────────────────────


class TestBotParkingFilter:
    def test_filter_keeps_own_truck_substring_match(self):
        from interfaces.bot.callbacks.parking import _filter_to_own_vehicles

        class _U:
            truck_num = "107"

        events = [
            {"vehicle_name": "107"},
            {"vehicle_name": "Truck-107A"},
            {"vehicle_name": "213"},
            {"vehicle_name": "571701"},
        ]
        kept = _filter_to_own_vehicles(events, _U())
        names = {e["vehicle_name"] for e in kept}
        assert names == {"107", "Truck-107A"}

    def test_filter_empty_when_no_truck_assigned(self):
        from interfaces.bot.callbacks.parking import _filter_to_own_vehicles

        class _U:
            truck_num = None

        assert _filter_to_own_vehicles([{"vehicle_name": "107"}], _U()) == []
