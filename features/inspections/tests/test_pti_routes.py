"""API-route tests for PTI inspections + templates.

End-to-end through the ASGI app harness — same pattern other route
tests use (parking, maintenance, driver_routes).  Covers:

  * Driver isolation: drivers can only see/touch own inspections
  * Fleet review flow: list + detail + review with status guards
  * Template editor: gated on can_manage_account
  * Permission denials: drivers can't list account-wide, can't review,
    can't edit template
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Role
from features.inspections.templates import (
    STANDARD_DOT_TRAILER_ITEMS,
    STANDARD_DOT_TRUCK_ITEMS,
)
from interfaces.api.auth import create_jwt


# ── Fixture ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def pti_app(pg_db):
    database = pg_db

    acct = await database.create_account("PTI Fleet")
    await database.add_company(acct.id, "PF", "key_pf", "PTI Fleet")
    driver = await database.create_user(11001, acct.id, role=Role.DRIVER)
    owner = await database.create_user(11002, acct.id, role=Role.OWNER)
    other_driver = await database.create_user(
        11003, acct.id, role=Role.DRIVER,
    )
    await database.assign_vehicle(
        user_id=driver.id, account_id=acct.id, truck_num="107",
        assigned_by=0, is_primary=True,
    )
    await database.assign_vehicle(
        user_id=other_driver.id, account_id=acct.id, truck_num="213",
        assigned_by=0, is_primary=True,
    )

    token_driver = create_jwt(driver.telegram_id, acct.id, "driver")
    token_owner  = create_jwt(owner.telegram_id, acct.id, "owner")
    token_other  = create_jwt(other_driver.telegram_id, acct.id, "driver")

    import infra.platform as _cp
    _old_db = _cp._db
    _cp._db = database

    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app":           app,
        "db":            database,
        "acct":          acct,
        "driver":        driver,
        "owner":         owner,
        "other_driver":  other_driver,
        "token_driver":  token_driver,
        "token_owner":   token_owner,
        "token_other":   token_other,
    }

    _cp._db = _old_db


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _spawn(db, acct, driver):
    """Helper: call the storage layer directly to spawn an inspection +
    attach template items.  Routes will then operate on the result."""
    tpl = await db.get_active_template_with_items(acct.id, "truck")
    ins_id = await db.create_inspection(
        account_id=acct.id, user_id=driver.id, vehicle_name="107",
        inspection_type="weekly",
        template_id=int(tpl["id"]),
        template_version=int(tpl["version"]),
        scheduled_for="2026-06-01T06:00:00",
        due_by="2026-06-08T06:00:00",
    )
    await db.add_inspection_items(ins_id, tpl["items"])
    return ins_id


# ── Driver-facing routes ───────────────────────────────────────────


class TestDriverRoutes:
    async def test_get_current_returns_open_inspection(self, pti_app):
        ins_id = await _spawn(
            pti_app["db"], pti_app["acct"], pti_app["driver"],
        )
        async with AsyncClient(
            transport=ASGITransport(app=pti_app["app"]), base_url="http://t",
        ) as c:
            r = await c.get(
                "/api/inspections/me/current",
                headers=_h(pti_app["token_driver"]),
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["inspection"] is not None
            assert int(body["inspection"]["id"]) == ins_id
            assert len(body["inspection"]["items"]) == len(STANDARD_DOT_TRUCK_ITEMS)

    async def test_get_current_returns_null_when_none(self, pti_app):
        async with AsyncClient(
            transport=ASGITransport(app=pti_app["app"]), base_url="http://t",
        ) as c:
            r = await c.get(
                "/api/inspections/me/current",
                headers=_h(pti_app["token_driver"]),
            )
            assert r.status_code == 200
            assert r.json()["inspection"] is None

    async def test_patch_item_updates_status(self, pti_app):
        ins_id = await _spawn(
            pti_app["db"], pti_app["acct"], pti_app["driver"],
        )
        async with AsyncClient(
            transport=ASGITransport(app=pti_app["app"]), base_url="http://t",
        ) as c:
            r = await c.patch(
                f"/api/inspections/{ins_id}/items/brakes_service",
                headers=_h(pti_app["token_driver"]),
                json={"status": "ok", "notes": "all good"},
            )
            assert r.status_code == 200, r.text
            item = r.json()["item"]
            assert item["status"] == "ok"
            assert item["notes"] == "all good"

    async def test_driver_cant_touch_other_drivers_inspection(self, pti_app):
        ins_id = await _spawn(
            pti_app["db"], pti_app["acct"], pti_app["driver"],
        )
        async with AsyncClient(
            transport=ASGITransport(app=pti_app["app"]), base_url="http://t",
        ) as c:
            r = await c.patch(
                f"/api/inspections/{ins_id}/items/brakes_service",
                headers=_h(pti_app["token_other"]),
                json={"status": "ok"},
            )
            # 404 (not 403) so existence isn't leaked.
            assert r.status_code == 404

    async def test_submit_rejects_incomplete(self, pti_app):
        ins_id = await _spawn(
            pti_app["db"], pti_app["acct"], pti_app["driver"],
        )
        async with AsyncClient(
            transport=ASGITransport(app=pti_app["app"]), base_url="http://t",
        ) as c:
            # Mark one item only — submit should 409 with incomplete_inspection.
            await c.patch(
                f"/api/inspections/{ins_id}/items/brakes_service",
                headers=_h(pti_app["token_driver"]),
                json={"status": "ok"},
            )
            r = await c.post(
                f"/api/inspections/{ins_id}/submit",
                headers=_h(pti_app["token_driver"]),
            )
            assert r.status_code == 409
            body = r.json()
            assert body["detail"]["error_code"] == "incomplete_inspection"


# ── Fleet-facing routes ────────────────────────────────────────────


class TestFleetRoutes:
    async def test_list_visible_to_owner_only(self, pti_app):
        await _spawn(pti_app["db"], pti_app["acct"], pti_app["driver"])
        async with AsyncClient(
            transport=ASGITransport(app=pti_app["app"]), base_url="http://t",
        ) as c:
            r_owner = await c.get(
                "/api/inspections",
                headers=_h(pti_app["token_owner"]),
            )
            r_driver = await c.get(
                "/api/inspections",
                headers=_h(pti_app["token_driver"]),
            )
            assert r_owner.status_code == 200
            assert r_owner.json()["total"] == 1
            # Driver lacks can_inspections_all → 403.
            assert r_driver.status_code == 403

    async def test_review_requires_submitted_status(self, pti_app):
        ins_id = await _spawn(
            pti_app["db"], pti_app["acct"], pti_app["driver"],
        )
        async with AsyncClient(
            transport=ASGITransport(app=pti_app["app"]), base_url="http://t",
        ) as c:
            # Inspection still in 'scheduled' — review must 409.
            r = await c.post(
                f"/api/inspections/{ins_id}/review",
                headers=_h(pti_app["token_owner"]),
                json={"review_status": "approved", "review_notes": "x"},
            )
            assert r.status_code == 409

    async def test_review_approves_submitted_inspection(self, pti_app):
        ins_id = await _spawn(
            pti_app["db"], pti_app["acct"], pti_app["driver"],
        )
        # Walk through + submit via storage (faster than calling 18
        # PATCH routes; the patch-route test is above).
        items = await pti_app["db"].get_inspection_items(ins_id)
        for it in items:
            await pti_app["db"].update_inspection_item(
                int(it["id"]),
                status="na" if it["requires_media"] else "ok",
            )
        await pti_app["db"].mark_inspection_in_progress(ins_id)
        await pti_app["db"].submit_inspection(
            ins_id, defects_count=0, has_oos_defect=False,
        )

        async with AsyncClient(
            transport=ASGITransport(app=pti_app["app"]), base_url="http://t",
        ) as c:
            r = await c.post(
                f"/api/inspections/{ins_id}/review",
                headers=_h(pti_app["token_owner"]),
                json={"review_status": "approved", "review_notes": "LGTM"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["inspection"]["review_status"] == "approved"
            assert body["inspection"]["status"] == "reviewed"

    async def test_driver_cannot_review(self, pti_app):
        ins_id = await _spawn(
            pti_app["db"], pti_app["acct"], pti_app["driver"],
        )
        async with AsyncClient(
            transport=ASGITransport(app=pti_app["app"]), base_url="http://t",
        ) as c:
            r = await c.post(
                f"/api/inspections/{ins_id}/review",
                headers=_h(pti_app["token_driver"]),
                json={"review_status": "approved", "review_notes": ""},
            )
            assert r.status_code == 403


# ── Template editor routes ────────────────────────────────────────


class TestTemplateRoutes:
    async def test_get_template_returns_truck_and_trailer(self, pti_app):
        async with AsyncClient(
            transport=ASGITransport(app=pti_app["app"]), base_url="http://t",
        ) as c:
            r = await c.get(
                "/api/inspections/template",
                headers=_h(pti_app["token_owner"]),
            )
            assert r.status_code == 200
            body = r.json()
            assert body["truck"] is not None
            assert body["trailer"] is not None
            assert len(body["truck"]["items"]) == len(STANDARD_DOT_TRUCK_ITEMS)
            assert len(body["trailer"]["items"]) == len(STANDARD_DOT_TRAILER_ITEMS)

    async def test_upsert_template_item(self, pti_app):
        async with AsyncClient(
            transport=ASGITransport(app=pti_app["app"]), base_url="http://t",
        ) as c:
            r = await c.put(
                "/api/inspections/template/truck/items/custom_belt",
                headers=_h(pti_app["token_owner"]),
                json={
                    "label": "Belt pulley inspection",
                    "category": "engine",
                    "requires_media": True,
                    "required": True,
                    "sort_order": 99,
                },
            )
            assert r.status_code == 200, r.text

    async def test_driver_cannot_edit_template(self, pti_app):
        async with AsyncClient(
            transport=ASGITransport(app=pti_app["app"]), base_url="http://t",
        ) as c:
            r = await c.put(
                "/api/inspections/template/truck/items/custom_belt",
                headers=_h(pti_app["token_driver"]),
                json={
                    "label": "X", "category": "cab",
                    "requires_media": False, "required": True, "sort_order": 1,
                },
            )
            assert r.status_code == 403

    async def test_reset_template(self, pti_app):
        async with AsyncClient(
            transport=ASGITransport(app=pti_app["app"]), base_url="http://t",
        ) as c:
            # Add a custom item first.
            await c.put(
                "/api/inspections/template/truck/items/custom_x",
                headers=_h(pti_app["token_owner"]),
                json={
                    "label": "X", "category": "cab",
                    "requires_media": False, "required": True, "sort_order": 1,
                },
            )
            r = await c.post(
                "/api/inspections/template/reset",
                headers=_h(pti_app["token_owner"]),
                json={"vehicle_type": "truck"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["items_count"] == len(STANDARD_DOT_TRUCK_ITEMS)
            # Confirm the custom item is gone.
            r = await c.get(
                "/api/inspections/template",
                headers=_h(pti_app["token_owner"]),
            )
            keys = {i["item_key"] for i in r.json()["truck"]["items"]}
            assert "custom_x" not in keys
