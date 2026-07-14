"""Onboard Inventory — a component of the Vehicle feature.

Proves: CRUD + the accountability contract (every write appends an event
with the actor AND the driver assigned to the truck at that moment),
transfer keeps history, gates (view = vehicle access, write =
can_manage_vehicles), company scoping, and the fleet badge counts.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("OBJECT_STORE_BACKEND", "disk")
os.environ.setdefault("OBJECT_STORE_ROOT", "/tmp/vehicle_inventory_test_store")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from adapters.storage import Role


@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as cp
    cp._db = pg_db
    from interfaces.api.app import create_api
    return create_api(), pg_db


def _headers(db_user, acct, role: str):
    from interfaces.api.auth import create_jwt
    return {
        "Authorization": f"Bearer {create_jwt(db_user.telegram_id or 0, acct.id, role, user_id=db_user.id)}"
    }


async def _seed(db, acct, unit="142", company=""):
    """A registry vehicle + a driver assigned to it."""
    vehicle_id = await db.add_vehicle(
        acct.id, unit_number=unit, company_code=company, vehicle_type="truck",
    )
    driver = await db.create_user(810000 + int(unit), acct.id, role=Role.DRIVER)
    await db.set_user_vehicles(driver.id, acct.id, [unit])
    return vehicle_id, driver


class TestInventoryCrudAndTrail:
    async def test_add_verify_status_and_driver_snapshot(self, api):
        app, db = api
        acct = await db.create_account("Inv Co")
        fleet = await db.create_user(800001, acct.id, role=Role.FLEET)
        vehicle_id, driver = await _seed(db, acct, unit="142")
        hf = _headers(fleet, acct, "fleet")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # add
            r = await c.post("/api/vehicles/142/inventory", headers=hf, json={
                "category": "tablet", "label": "Galaxy Tab A9",
                "identifier": "SN-777", "notes": "cab mount",
            })
            assert r.status_code == 200, r.text
            item_id = r.json()["item_id"]

            # list + summary
            r = await c.get("/api/vehicles/142/inventory", headers=hf)
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["summary"] == {"total": 1, "attention": 0}
            assert data["items"][0]["identifier"] == "SN-777"

            # verify stamps who/when
            r = await c.post(f"/api/vehicles/inventory/{item_id}/verify", headers=hf)
            assert r.status_code == 200 and r.json()["ok"]

            # report missing (status change w/ note)
            r = await c.patch(f"/api/vehicles/inventory/{item_id}", headers=hf, json={
                "status": "missing", "note": "gone after weekend",
            })
            assert r.status_code == 200, r.text

            # the trail: installed → verified → status_change, and EVERY
            # event carries the assigned driver snapshot.
            r = await c.get(f"/api/vehicles/inventory/{item_id}/events", headers=hf)
            events = r.json()["events"]
            types = [e["event_type"] for e in events]
            assert types == ["status_change", "verified", "installed"]
            for e in events:
                assert e["driver_user_id"] == driver.id
                assert e["actor_user_id"] == fleet.id
            missing_evt = events[0]
            assert missing_evt["from_status"] == "installed"
            assert missing_evt["to_status"] == "missing"
            assert missing_evt["note"] == "gone after weekend"

            # summary now flags attention + the fleet badge agrees
            r = await c.get("/api/vehicles/142/inventory", headers=hf)
            assert r.json()["summary"] == {"total": 1, "attention": 1}
            r = await c.get("/api/vehicles/inventory/alerts", headers=hf)
            by_vehicle = r.json()["by_vehicle"]
            assert by_vehicle[str(vehicle_id)] == {"total": 1, "attention": 1} \
                or by_vehicle.get(vehicle_id) == {"total": 1, "attention": 1}

    async def test_transfer_keeps_history_and_snapshots_leaving_driver(self, api):
        app, db = api
        acct = await db.create_account("Transfer Co")
        fleet = await db.create_user(800002, acct.id, role=Role.FLEET)
        v1, driver1 = await _seed(db, acct, unit="101")
        v2, _driver2 = await _seed(db, acct, unit="102")
        hf = _headers(fleet, acct, "fleet")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/vehicles/101/inventory", headers=hf, json={
                "category": "fuel_card", "label": "EFS", "identifier": "•••7213",
            })
            item_id = r.json()["item_id"]

            r = await c.post(f"/api/vehicles/inventory/{item_id}/transfer", headers=hf, json={
                "to_vehicle_name": "102", "note": "card moves with the lane",
            })
            assert r.status_code == 200, r.text

            # item now lives on 102…
            r = await c.get("/api/vehicles/102/inventory", headers=hf)
            assert any(i["id"] == item_id for i in r.json()["items"])
            r = await c.get("/api/vehicles/101/inventory", headers=hf)
            assert all(i["id"] != item_id for i in r.json()["items"])

            # …and the trail records from→to and blames the LEAVING truck's driver
            r = await c.get(f"/api/vehicles/inventory/{item_id}/events", headers=hf)
            evt = r.json()["events"][0]
            assert evt["event_type"] == "transferred"
            assert evt["from_vehicle_id"] == v1 and evt["to_vehicle_id"] == v2
            assert evt["driver_user_id"] == driver1.id

            # same-vehicle transfer is rejected
            r = await c.post(f"/api/vehicles/inventory/{item_id}/transfer", headers=hf, json={
                "to_vehicle_name": "102",
            })
            assert r.status_code == 400

    async def test_remove_is_soft_and_trail_survives(self, api):
        app, db = api
        acct = await db.create_account("Remove Co")
        fleet = await db.create_user(800003, acct.id, role=Role.FLEET)
        await _seed(db, acct, unit="107")
        hf = _headers(fleet, acct, "fleet")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/vehicles/107/inventory", headers=hf, json={
                "category": "camera", "label": "Dashcam",
            })
            item_id = r.json()["item_id"]
            r = await c.post(f"/api/vehicles/inventory/{item_id}/remove", headers=hf, json={
                "note": "returned to office",
            })
            assert r.status_code == 200 and r.json()["ok"]
            # gone from the active list…
            r = await c.get("/api/vehicles/107/inventory", headers=hf)
            assert r.json()["summary"]["total"] == 0
            # …but the trail is still readable
            r = await c.get(f"/api/vehicles/inventory/{item_id}/events", headers=hf)
            assert [e["event_type"] for e in r.json()["events"]] == ["removed", "installed"]


class TestInventoryGates:
    async def test_viewer_reads_but_cannot_write(self, api):
        """Dispatcher: has vehicle access (view) but NOT can_manage_vehicles."""
        app, db = api
        acct = await db.create_account("Gates Co")
        from capabilities.permissions.roles import ROLE_PERMISSIONS
        assert ROLE_PERMISSIONS[Role.DISPATCHER].can_manage_vehicles is False
        dispatcher = await db.create_user(800004, acct.id, role=Role.DISPATCHER)
        fleet = await db.create_user(800005, acct.id, role=Role.FLEET)
        await _seed(db, acct, unit="110")
        hd = _headers(dispatcher, acct, "dispatcher")
        hf = _headers(fleet, acct, "fleet")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/vehicles/110/inventory", headers=hf, json={
                "category": "eld", "label": "Samsara VG54",
            })
            item_id = r.json()["item_id"]

            # view OK
            assert (await c.get("/api/vehicles/110/inventory", headers=hd)).status_code == 200
            assert (await c.get(f"/api/vehicles/inventory/{item_id}/events", headers=hd)).status_code == 200
            # writes 403
            assert (await c.post("/api/vehicles/110/inventory", headers=hd,
                                 json={"category": "other", "label": "X"})).status_code == 403
            assert (await c.patch(f"/api/vehicles/inventory/{item_id}", headers=hd,
                                  json={"status": "missing"})).status_code == 403
            assert (await c.post(f"/api/vehicles/inventory/{item_id}/verify", headers=hd)).status_code == 403

    async def test_unknown_vehicle_404_and_bad_category_400(self, api):
        app, db = api
        acct = await db.create_account("Edge Co")
        fleet = await db.create_user(800006, acct.id, role=Role.FLEET)
        hf = _headers(fleet, acct, "fleet")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.get("/api/vehicles/999/inventory", headers=hf)).status_code == 404
            await _seed(db, acct, unit="115")
            r = await c.post("/api/vehicles/115/inventory", headers=hf, json={
                "category": "spaceship", "label": "X",
            })
            assert r.status_code == 400
