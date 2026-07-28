"""Role-default page layouts (tier two of the page-config model).

What must hold:
  * WHO — owner/admin set any role's default; a MANAGER only their own
    role's; a plain employee nobody's.  The grant is UI affordance; the
    API's is_manager+role re-check is the enforcement, so a mis-seeded
    flag can never let a fleet manager rearrange the safety team's page.
  * WHAT — shape-only validation here (the backend has no section
    registry); garbage shapes are refused so the table can't become a
    scratchpad.
  * READ — any authed user, because everyone needs their own role's
    default to render their page and the rows hold nothing but ids.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def seeded(pg_db):
    db = pg_db
    acct = await db.create_account("Layouts Co")
    owner = await db.create_user(9401, acct.id, role=Role.OWNER)
    fleet_mgr = await db.create_user(9402, acct.id, role=Role.FLEET)
    await db.update_user(fleet_mgr.id, is_manager=True)
    fleet_emp = await db.create_user(9403, acct.id, role=Role.FLEET)

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = db
    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": db, "acct": acct,
        "owner": create_jwt(owner.telegram_id, acct.id, "owner"),
        "mgr": create_jwt(fleet_mgr.telegram_id, acct.id, "fleet",
                          is_manager=True),
        "emp": create_jwt(fleet_emp.telegram_id, acct.id, "fleet"),
    }
    _cp._db = _old


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


SECTIONS = ["header", "live_ack_panel", "control_bar", "bulk_error",
            "results", "incident_drillin_drawer"]


class TestWhoMayWrite:
    async def test_owner_sets_any_role(self, seeded):
        async with await _client(seeded["app"]) as c:
            r = await c.put("/api/page-layouts/dispatcher/alerts",
                            json={"sections": SECTIONS},
                            headers=_h(seeded["owner"]))
            assert r.status_code == 200
            assert r.json()["sections"] == SECTIONS

    async def test_manager_sets_their_own_role(self, seeded):
        async with await _client(seeded["app"]) as c:
            r = await c.put("/api/page-layouts/fleet/alerts",
                            json={"sections": SECTIONS},
                            headers=_h(seeded["mgr"]))
            assert r.status_code == 200

    async def test_manager_cannot_touch_another_role(self, seeded):
        """The line that matters: a fleet manager rearranging the SAFETY
        team's page would be cross-team authority nobody granted."""
        async with await _client(seeded["app"]) as c:
            r = await c.put("/api/page-layouts/safety/alerts",
                            json={"sections": SECTIONS},
                            headers=_h(seeded["mgr"]))
            assert r.status_code == 403

    async def test_plain_employee_cannot_write_even_their_own_role(self, seeded):
        async with await _client(seeded["app"]) as c:
            r = await c.put("/api/page-layouts/fleet/alerts",
                            json={"sections": SECTIONS},
                            headers=_h(seeded["emp"]))
            assert r.status_code == 403

    async def test_delete_gated_the_same_way(self, seeded):
        async with await _client(seeded["app"]) as c:
            await c.put("/api/page-layouts/safety/alerts",
                        json={"sections": SECTIONS}, headers=_h(seeded["owner"]))
            r = await c.delete("/api/page-layouts/safety/alerts",
                               headers=_h(seeded["mgr"]))
            assert r.status_code == 403
            r = await c.delete("/api/page-layouts/safety/alerts",
                               headers=_h(seeded["owner"]))
            assert r.status_code == 200


class TestShapeValidation:
    async def test_unknown_role_and_feature_refused(self, seeded):
        async with await _client(seeded["app"]) as c:
            r = await c.put("/api/page-layouts/pirate/alerts",
                            json={"sections": SECTIONS}, headers=_h(seeded["owner"]))
            assert r.status_code == 422
            r = await c.put("/api/page-layouts/fleet/not-a-page",
                            json={"sections": SECTIONS}, headers=_h(seeded["owner"]))
            assert r.status_code == 422

    async def test_duplicates_and_empties_refused(self, seeded):
        async with await _client(seeded["app"]) as c:
            r = await c.put("/api/page-layouts/fleet/alerts",
                            json={"sections": ["a", "a"]}, headers=_h(seeded["owner"]))
            assert r.status_code == 422
            r = await c.put("/api/page-layouts/fleet/alerts",
                            json={"sections": ["  ", ""]}, headers=_h(seeded["owner"]))
            assert r.status_code == 422

    async def test_delete_of_nothing_is_404_not_silent_ok(self, seeded):
        async with await _client(seeded["app"]) as c:
            r = await c.delete("/api/page-layouts/hr/alerts",
                               headers=_h(seeded["owner"]))
            assert r.status_code == 404


class TestReadAndRoundTrip:
    async def test_any_user_reads_and_the_shape_round_trips(self, seeded):
        async with await _client(seeded["app"]) as c:
            await c.put("/api/page-layouts/fleet/alerts",
                        json={"sections": SECTIONS}, headers=_h(seeded["owner"]))
            r = await c.get("/api/page-layouts", headers=_h(seeded["emp"]))
            assert r.status_code == 200
            assert r.json()["layouts"]["fleet"]["alerts"] == SECTIONS

    async def test_upsert_replaces_rather_than_duplicating(self, seeded):
        db, acct = seeded["db"], seeded["acct"]
        await db.upsert_page_layout(acct.id, "fleet", "alerts", ["a", "b"], 9401)
        await db.upsert_page_layout(acct.id, "fleet", "alerts", ["b", "a"], 9401)
        rows = await db.get_page_layouts(acct.id)
        assert len(rows) == 1
        assert rows[0]["sections"] == ["b", "a"]

    async def test_rows_are_tenant_scoped(self, seeded):
        """Account B never sees — and can never clear — account A's rows.

        account_id comes only from the JWT, so the worst a hostile
        tenant can do is address their OWN (empty) namespace.
        """
        db = seeded["db"]
        other = await db.create_account("Other Co")
        other_owner = await db.create_user(9409, other.id, role=Role.OWNER)
        tok = create_jwt(other_owner.telegram_id, other.id, "owner")
        async with await _client(seeded["app"]) as c:
            await c.put("/api/page-layouts/fleet/alerts",
                        json={"sections": SECTIONS}, headers=_h(seeded["owner"]))
            r = await c.get("/api/page-layouts", headers=_h(tok))
            assert r.json()["layouts"] == {}
            r = await c.delete("/api/page-layouts/fleet/alerts", headers=_h(tok))
            assert r.status_code == 404
            r = await c.get("/api/page-layouts", headers=_h(seeded["owner"]))
            assert r.json()["layouts"]["fleet"]["alerts"] == SECTIONS

    async def test_requires_auth(self, seeded):
        async with await _client(seeded["app"]) as c:
            r = await c.get("/api/page-layouts")
            assert r.status_code in (401, 403, 422)
