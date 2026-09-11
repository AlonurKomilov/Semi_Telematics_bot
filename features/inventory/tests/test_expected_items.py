"""What a vehicle is SUPPOSED to carry, and what that makes answerable.

Before the catalogue, Inventory could only report what somebody had
recorded.  These prove what declaring the expectation buys:

* a truck that has never had a dashcam typed in is now SHORT one, where
  before it was simply a truck with fewer rows;
* an account that has declared nothing is reported as *not declared*,
  never as complete — the one wrong answer here would be telling somebody
  a truck is fully equipped because nobody said what fully means;
* a damaged item is still ABOARD.  The attention badge already says it is
  damaged, and counting it absent as well would report one fault twice;
* and the Config family's split holds: the COUNT is account-wide truth,
  identical for every role, while the RED is each role's own — safety
  does not go red about dispatch's straps, and neither of them disagrees
  about whether the ELD is aboard.
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


def _headers(db_user, acct, role: str, is_manager: bool = False):
    """`is_manager` because `can_manage_config_role` is seeded at the
    MANAGER tier — a plain employee of a role does not get to re-aim what
    their whole team goes red about."""
    from interfaces.api.auth import create_jwt
    token = create_jwt(
        db_user.telegram_id or 0, acct.id, role,
        user_id=db_user.id, is_manager=is_manager,
    )
    return {"Authorization": f"Bearer {token}"}


TEMPLATE = [
    {"category": "camera", "label": "Dashcam", "quantity": 2, "required": True},
    {"category": "eld", "label": "ELD", "quantity": 1, "required": True},
    {"category": "toll_transponder", "label": "Toll", "quantity": 1, "required": False},
]


class TestTheTemplate:
    async def test_an_account_that_declares_nothing_expects_nothing(self, api):
        app, db = api
        acct = await db.create_account("Expect Co")
        owner = await db.create_user(820001, acct.id, role=Role.OWNER)
        await db.add_vehicle(acct.id, unit_number="301", company_code="", vehicle_type="truck")
        hf = _headers(owner, acct, "owner")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/inventory/expected", headers=hf)
            assert r.status_code == 200, r.text
            assert r.json()["catalogue"]["truck"] == []
            # …and the standard travels with it, so the editor's reset has
            # something to reset TO without a second round trip.
            assert any(row["category"] == "camera" for row in r.json()["standard"]["truck"])

            r = await c.get("/api/inventory/vehicle/301", headers=hf)
            assert r.json()["coverage"] == {"expected": 0, "present": 0, "rows": [], "flagged": []}

    async def test_put_replaces_so_a_row_can_be_deleted(self, api):
        app, db = api
        acct = await db.create_account("Expect Co")
        owner = await db.create_user(820002, acct.id, role=Role.OWNER)
        hf = _headers(owner, acct, "owner")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/inventory/expected", headers=hf,
                            json={"vehicle_type": "truck", "items": TEMPLATE})
            assert r.status_code == 200 and len(r.json()["catalogue"]["truck"]) == 3

            # Drop one and re-send the list the editor is looking at.
            r = await c.put("/api/inventory/expected", headers=hf,
                            json={"vehicle_type": "truck", "items": TEMPLATE[:2]})
            assert r.status_code == 200 and len(r.json()["catalogue"]["truck"]) == 2
            r = await c.get("/api/inventory/expected", headers=hf)
            assert [row["category"] for row in r.json()["catalogue"]["truck"]] == ["camera", "eld"]

    async def test_an_unknown_vehicle_type_is_refused(self, api):
        app, db = api
        acct = await db.create_account("Expect Co")
        owner = await db.create_user(820003, acct.id, role=Role.OWNER)
        hf = _headers(owner, acct, "owner")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/inventory/expected", headers=hf,
                            json={"vehicle_type": "spaceship", "items": []})
            assert r.status_code == 400

    async def test_managing_inventory_does_not_buy_the_right_to_redefine_it(self, api):
        """The mixing the config family exists to remove.

        A fleet manager holds can_manage_inventory — they add and verify
        items all day.  Rewriting what every truck in the account OWES is
        a different act with a different blast radius, and it rides
        can_manage_config_all alone.
        """
        app, db = api
        acct = await db.create_account("Expect Co")
        fleet = await db.create_user(820004, acct.id, role=Role.FLEET)
        hf = _headers(fleet, acct, "fleet")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/inventory/expected", headers=hf,
                            json={"vehicle_type": "truck", "items": TEMPLATE})
            assert r.status_code == 403
            # …but reading what a truck owes is on ordinary view access:
            # everybody who can see inventory needs to know.
            r = await c.get("/api/inventory/expected", headers=hf)
            assert r.status_code == 200


class TestCoverage:
    async def test_a_truck_is_short_what_was_never_recorded(self, api):
        app, db = api
        acct = await db.create_account("Expect Co")
        owner = await db.create_user(820010, acct.id, role=Role.OWNER)
        await db.add_vehicle(acct.id, unit_number="302", company_code="", vehicle_type="truck")
        hf = _headers(owner, acct, "owner")

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
        owner = await db.create_user(820011, acct.id, role=Role.OWNER)
        await db.add_vehicle(acct.id, unit_number="303", company_code="", vehicle_type="truck")
        hf = _headers(owner, acct, "owner")

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
        owner = await db.create_user(820012, acct.id, role=Role.OWNER)
        await db.add_vehicle(acct.id, unit_number="T90", company_code="", vehicle_type="trailer")
        hf = _headers(owner, acct, "owner")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put("/api/inventory/expected", headers=hf,
                        json={"vehicle_type": "truck", "items": TEMPLATE})
            # The truck template must not reach a trailer.
            cov = (await c.get("/api/inventory/vehicle/T90", headers=hf)).json()["coverage"]
            assert cov == {"expected": 0, "present": 0, "rows": [], "flagged": []}


class TestRoleFocus:
    """The red is each role's own; the fact is not.

    The owner's words for why this exists: something showing red for one
    role pulls a second role's focus onto what is not theirs.  Dispatch
    cares that the straps are aboard, safety does not, and safety's
    screen going red about straps costs safety the thing red is for.

    What must NOT vary is the fact.  Both roles read the same
    ``expected``/``present``, because whether the ELD is aboard is a
    property of the truck and not of the reader.
    """

    async def test_the_count_is_one_truth_and_the_red_is_per_role(self, api):
        app, db = api
        acct = await db.create_account("Focus Co")
        owner = await db.create_user(830001, acct.id, role=Role.OWNER)
        safety = await db.create_user(830002, acct.id, role=Role.SAFETY)
        await db.add_vehicle(acct.id, unit_number="401", company_code="", vehicle_type="truck")
        ho = _headers(owner, acct, "owner")
        hs = _headers(safety, acct, "safety", is_manager=True)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Dispatch's straps land in the ONE catalogue, beside the ELD.
            await c.put("/api/inventory/expected", headers=ho, json={
                "vehicle_type": "truck",
                "items": [
                    {"category": "eld", "label": "ELD", "quantity": 1, "required": True},
                    {"category": "straps", "label": "Straps", "quantity": 4, "required": True},
                ],
            })
            # Nothing recorded: the truck is short both.
            before = (await c.get("/api/inventory/vehicle/401", headers=hs)).json()["coverage"]
            assert before["expected"] == 5 and before["present"] == 0
            assert sorted(before["flagged"]) == ["eld", "straps"], \
                "a role that has never narrowed its attention is flagged on everything"

            # Safety narrows to what is safety's.
            r = await c.put("/api/inventory/expected/focus", headers=hs,
                            json={"role": "safety", "categories": ["eld"]})
            assert r.status_code == 200, r.text

            after_safety = (await c.get("/api/inventory/vehicle/401", headers=hs)).json()["coverage"]
            after_owner = (await c.get("/api/inventory/vehicle/401", headers=ho)).json()["coverage"]

            # The RED moved…
            assert after_safety["flagged"] == ["eld"], "safety no longer goes red about straps"
            assert sorted(after_owner["flagged"]) == ["eld", "straps"]
            # …and the FACT did not.  This is the blast-radius rule: two
            # roles must never disagree about what the truck owes.
            for cov in (after_safety, after_owner):
                assert cov["expected"] == 5 and cov["present"] == 0
            assert [r["category"] for r in after_safety["rows"]] == \
                   [r["category"] for r in after_owner["rows"]]

    async def test_narrowed_to_nothing_is_not_the_same_as_never_narrowed(self, api):
        app, db = api
        acct = await db.create_account("Focus Co")
        owner = await db.create_user(830010, acct.id, role=Role.OWNER)
        safety = await db.create_user(830011, acct.id, role=Role.SAFETY)
        await db.add_vehicle(acct.id, unit_number="402", company_code="", vehicle_type="truck")
        ho = _headers(owner, acct, "owner")
        hs = _headers(safety, acct, "safety", is_manager=True)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put("/api/inventory/expected", headers=ho, json={
                "vehicle_type": "truck",
                "items": [{"category": "eld", "label": "ELD", "quantity": 1, "required": True}],
            })
            await c.put("/api/inventory/expected/focus", headers=hs,
                        json={"role": "safety", "categories": []})
            cov = (await c.get("/api/inventory/vehicle/402", headers=hs)).json()["coverage"]
            # "Stop flagging me entirely" is a thing a role can say — and
            # it still owes the item, which the rows go on reporting.
            assert cov["flagged"] == []
            assert cov["rows"][0]["short"] == 1

    async def test_one_role_cannot_silence_another(self, api):
        """The own-role wall.

        Without it a fleet manager could quietly stop the safety team
        being told about a missing dashcam — which is worse than either
        role's noise, because nobody would know it had happened.
        """
        app, db = api
        acct = await db.create_account("Focus Co")
        fleet = await db.create_user(830020, acct.id, role=Role.FLEET)
        hf = _headers(fleet, acct, "fleet", is_manager=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/inventory/expected/focus", headers=hf,
                            json={"role": "safety", "categories": []})
            assert r.status_code == 403
            # …their own is theirs.
            r = await c.put("/api/inventory/expected/focus", headers=hf,
                            json={"role": "fleet", "categories": ["camera"]})
            assert r.status_code == 200, r.text
