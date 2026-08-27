"""Driver management is the Drivers feature's own domain, gated on
``can_manage_drivers`` (NOT ``can_manage_users``).

Proves the separation: a Fleet lead (can_manage_drivers, no staff-admin) runs
the roster; a Dispatcher (neither) is blocked; the driver invite can only ever
create a driver.  Endpoint URLs are unchanged — only the gate + ownership moved.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("OBJECT_STORE_BACKEND", "disk")
os.environ.setdefault("OBJECT_STORE_ROOT", "/tmp/driver_mgmt_test_store")

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
    return {"Authorization": f"Bearer {create_jwt(db_user.telegram_id or 0, acct.id, role, user_id=db_user.id)}"}


class TestDriverManagement:
    async def test_fleet_runs_roster_without_staff_admin(self, api):
        app, db = api
        acct = await db.create_account("Roster Co")
        # Fleet has can_manage_drivers=True but can_manage_users=False.
        from capabilities.permissions.roles import ROLE_PERMISSIONS
        assert ROLE_PERMISSIONS[Role.FLEET].can_manage_drivers is True
        assert ROLE_PERMISSIONS[Role.FLEET].can_manage_users is False
        fleet = await db.create_user(700001, acct.id, role=Role.FLEET)
        drv = await db.create_user(700002, acct.id, role=Role.DRIVER)
        hf = _headers(fleet, acct, "fleet")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Invite a driver (link-channel, truck bound at claim)
            r = await c.post("/api/admin/drivers/invite", headers=hf, json={"truck_num": "107"})
            assert r.status_code == 200, r.text
            assert r.json()["role"] == "driver" and r.json()["truck_num"] == "107"
            # Assign trucks to a driver
            r = await c.put(f"/api/admin/users/{drv.id}/trucks", headers=hf, json={"trucks": ["107", "108"]})
            assert r.status_code == 200, r.text
            r = await c.get(f"/api/admin/users/{drv.id}/trucks", headers=hf)
            assert {t["vehicle_num"] for t in r.json()["trucks"]} == {"107", "108"}

    async def test_dispatcher_is_blocked(self, api):
        app, db = api
        acct = await db.create_account("Roster Co2")
        from capabilities.permissions.roles import ROLE_PERMISSIONS
        assert ROLE_PERMISSIONS[Role.DISPATCHER].can_manage_drivers is False
        disp = await db.create_user(700010, acct.id, role=Role.DISPATCHER)
        drv = await db.create_user(700011, acct.id, role=Role.DRIVER)
        hd = _headers(disp, acct, "dispatcher")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.post("/api/admin/drivers/invite", headers=hd, json={})).status_code == 403
            assert (await c.put(f"/api/admin/users/{drv.id}/trucks", headers=hd, json={"trucks": []})).status_code == 403
            assert (await c.get("/api/admin/users/integration-links", headers=hd)).status_code == 403
            assert (await c.get(f"/api/admin/users/{drv.id}/trucks", headers=hd)).status_code == 403

    async def test_invite_is_hard_locked_to_driver(self, api):
        app, db = api
        acct = await db.create_account("Roster Co3")
        owner = await db.create_user(700020, acct.id, role=Role.OWNER)
        ho = _headers(owner, acct, "owner")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Even smuggling a role field can't make it staff — the endpoint
            # ignores extras and hardcodes Role.DRIVER.
            r = await c.post("/api/admin/drivers/invite", headers=ho, json={"role": "admin"})
            assert r.status_code == 200, r.text
            assert r.json()["role"] == "driver"
