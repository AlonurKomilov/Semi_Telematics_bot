"""What a vehicle is SUPPOSED to carry, and what that makes answerable.

Before the template, Inventory could only report what somebody had
recorded.  These prove the three things declaring the expectation buys:

* a truck that has never had a dashcam typed in is now SHORT one, where
  before it was simply a truck with fewer rows;
* an account that has declared nothing is reported as *not declared*,
  never as complete — the one wrong answer here would be telling somebody
  a truck is fully equipped because nobody said what fully means;
* a damaged item is still ABOARD.  The attention badge already says it is
  damaged, and counting it absent as well would report one fault twice.
"""
from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("OBJECT_STORE_BACKEND", "disk")
os.environ.setdefault("OBJECT_STORE_ROOT", "/tmp/inventory_expected_test_store")

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


TEMPLATE = [
    {"category": "camera", "label": "Dashcam", "quantity": 2, "required": True, "sort_order": 1},
    {"category": "eld", "label": "ELD", "quantity": 1, "required": True, "sort_order": 2},
    {"category": "toll_transponder", "label": "Toll", "quantity": 1, "required": False, "sort_order": 3},
]


class TestTheTemplate:
    async def test_an_account_that_declares_nothing_expects_nothing(self, api):
        app, db = api
        acct = await db.create_account("Expect Co")
        fleet = await db.create_user(820001, acct.id, role=Role.FLEET)
        await db.add_vehicle(acct.id, unit_number="301", company_code="", vehicle_type="truck")
        hf = _headers(fleet, acct, "fleet")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/inventory/expected", headers=hf)
            assert r.status_code == 200, r.text
            assert r.json()["items"] == []
            # …and the standard travels with it, so the editor's reset has
            # something to reset TO without a second round trip.
            assert any(row["category"] == "camera" for row in r.json()["standard"])

            r = await c.get("/api/inventory/vehicle/301", headers=hf)
            assert r.json()["coverage"] == {"expected": 0, "present": 0, "rows": []}

    async def test_put_replaces_so_a_row_can_be_deleted(self, api):
        app, db = api
        acct = await db.create_account("Expect Co")
        fleet = await db.create_user(820002, acct.id, role=Role.FLEET)
        hf = _headers(fleet, acct, "fleet")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/inventory/expected", headers=hf,
                            json={"vehicle_type": "truck", "items": TEMPLATE})
            assert r.status_code == 200 and r.json()["count"] == 3

            # Drop one and re-send the list the editor is looking at.
            r = await c.put("/api/inventory/expected", headers=hf,
                            json={"vehicle_type": "truck", "items": TEMPLATE[:2]})
            assert r.status_code == 200 and r.json()["count"] == 2
            r = await c.get("/api/inventory/expected", headers=hf)
            assert [row["category"] for row in r.json()["items"]] == ["camera", "eld"]

    async def test_an_unknown_vehicle_type_is_refused(self, api):
        app, db = api
        acct = await db.create_account("Expect Co")
        fleet = await db.create_user(820003, acct.id, role=Role.FLEET)
        hf = _headers(fleet, acct, "fleet")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/inventory/expected?vehicle_type=spaceship", headers=hf)
            assert r.status_code == 400

    async def test_a_viewer_reads_the_template_and_cannot_rewrite_it(self, api):
        app, db = api
        acct = await db.create_account("Expect Co")
        driver = await db.create_user(820004, acct.id, role=Role.DRIVER)
        hd = _headers(driver, acct, "driver")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/inventory/expected", headers=hd,
                            json={"vehicle_type": "truck", "items": TEMPLATE})
            assert r.status_code == 403


class TestCoverage:
    async def test_a_truck_is_short_what_was_never_recorded(self, api):
        app, db = api
        acct = await db.create_account("Expect Co")
        fleet = await db.create_user(820010, acct.id, role=Role.FLEET)
        await db.add_vehicle(acct.id, unit_number="302", company_code="", vehicle_type="truck")
        hf = _headers(fleet, acct, "fleet")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put("/api/inventory/expected", headers=hf,
                        json={"vehicle_type": "truck", "items": TEMPLATE})

            # One camera of the two owed, and no ELD at all.
            await c.post("/api/inventory/vehicle/302", headers=hf,
                         json={"category": "camera", "label": "Front dashcam"})

            cov = (await c.get("/api/inventory/vehicle/302", headers=hf)).json()["coverage"]
            # 2 cameras + 1 ELD are REQUIRED; the optional transponder is
            # declared and counted on its row but never held against the
            # truck, or a complete truck reads as incomplete forever.
            assert cov["expected"] == 3
            assert cov["present"] == 1
            by_cat = {row["category"]: row for row in cov["rows"]}
            assert by_cat["camera"]["short"] == 1
            assert by_cat["eld"]["short"] == 1
            assert by_cat["toll_transponder"]["required"] is False
            assert by_cat["toll_transponder"]["short"] == 1

    async def test_damaged_is_still_aboard_but_missing_is_not(self, api):
        app, db = api
        acct = await db.create_account("Expect Co")
        fleet = await db.create_user(820011, acct.id, role=Role.FLEET)
        await db.add_vehicle(acct.id, unit_number="303", company_code="", vehicle_type="truck")
        hf = _headers(fleet, acct, "fleet")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put("/api/inventory/expected", headers=hf, json={
                "vehicle_type": "truck",
                "items": [{"category": "eld", "label": "ELD", "quantity": 1, "required": True}],
            })
            r = await c.post("/api/inventory/vehicle/303", headers=hf,
                             json={"category": "eld", "label": "ELD unit"})
            item_id = r.json()["item_id"]

            await c.patch(f"/api/inventory/items/{item_id}", headers=hf,
                          json={"status": "damaged", "note": "cracked screen"})
            cov = (await c.get("/api/inventory/vehicle/303", headers=hf)).json()["coverage"]
            assert cov["present"] == 1, "a damaged ELD is still in the truck"

            await c.patch(f"/api/inventory/items/{item_id}", headers=hf,
                          json={"status": "missing", "note": "gone"})
            cov = (await c.get("/api/inventory/vehicle/303", headers=hf)).json()["coverage"]
            assert cov["present"] == 0, "a missing ELD is not"

    async def test_a_trailer_expects_nothing_by_default(self, api):
        app, db = api
        acct = await db.create_account("Expect Co")
        fleet = await db.create_user(820012, acct.id, role=Role.FLEET)
        await db.add_vehicle(acct.id, unit_number="T90", company_code="", vehicle_type="trailer")
        hf = _headers(fleet, acct, "fleet")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put("/api/inventory/expected", headers=hf,
                        json={"vehicle_type": "truck", "items": TEMPLATE})
            # The truck template must not reach a trailer.
            cov = (await c.get("/api/inventory/vehicle/T90", headers=hf)).json()["coverage"]
            assert cov == {"expected": 0, "present": 0, "rows": []}
