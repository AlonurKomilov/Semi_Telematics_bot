"""What the customer's surfaces learn when the plan leaves a feature out.

``/me`` carries the plan (label, the ids left out, the exact flags the
mask forced off), and a door the plan closed says so in its 403 —
``plan_excluded`` naming the feature — while a door the role simply
lacks keeps the plain denial.
"""

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def app(pg_db, monkeypatch):
    db = pg_db
    acct = await db.create_account("Plan Excluded Co")          # tier free
    owner = await db.create_user(920001, acct.id, role=Role.OWNER)
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "_db", db)
    from interfaces.api.app import create_api
    transport = ASGITransport(app=create_api())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {"client": client, "db": db, "acct": acct,
               "hdr": {"Authorization": f"Bearer {create_jwt(owner.telegram_id, acct.id, 'owner')}"}}


async def _narrow_free(db, without: str):
    from capabilities.permissions.plans import EXCLUDABLE, invalidate_plans
    await db.upsert_plan("free", label="Free", included=[i for i in EXCLUDABLE if i != without])
    invalidate_plans()


@pytest.mark.asyncio
async def test_me_carries_the_plan_and_it_is_empty_while_everything_is_included(app):
    r = await app["client"].get("/api/user/me", headers=app["hdr"])
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert plan == {"tier": "free", "label": "Free", "excluded": [], "excluded_flags": []}


@pytest.mark.asyncio
async def test_me_names_what_the_plan_left_out_and_the_flags_the_mask_forced_off(app):
    from capabilities.permissions.plans import _FLAGS_OF
    await _narrow_free(app["db"], "maintenance")
    r = await app["client"].get("/api/user/me", headers=app["hdr"])
    plan = r.json()["plan"]
    assert plan["excluded"] == ["maintenance"]
    assert plan["excluded_flags"] == sorted(_FLAGS_OF["maintenance"])
    # and the permissions block agrees — the same resolver, the same answer
    perms = r.json()["permissions"]
    assert perms.get("can_view_maintenance") is False
    assert perms.get("can_view_vehicles") is True


@pytest.mark.asyncio
async def test_a_plain_member_is_not_handed_the_list_of_what_the_company_did_not_buy(app):
    """The plan's name is everyone's; what it leaves out is told only to
    whoever can change it — on /me and in a door's 403 alike."""
    await _narrow_free(app["db"], "maintenance")
    driver = await app["db"].create_user(920003, app["acct"].id, role=Role.DRIVER)
    dh = {"Authorization": f"Bearer {create_jwt(driver.telegram_id, app['acct'].id, 'driver')}"}
    plan = (await app["client"].get("/api/user/me", headers=dh)).json()["plan"]
    assert plan == {"tier": "free", "label": "Free", "excluded": [], "excluded_flags": []}
    # a driver holds maintenance at vehicle width; the plan closed it — the driver hears the plain denial
    r = await app["client"].get("/api/maintenance/tasks", headers=dh)
    assert r.status_code == 403 and r.json()["detail"] == "Insufficient permissions", r.text


@pytest.mark.asyncio
async def test_a_door_the_plan_closed_says_so_and_a_door_the_role_lacks_does_not(app):
    c, hdr = app["client"], app["hdr"]
    # before: the owner opens maintenance
    r = await c.get("/api/maintenance/tasks", headers=hdr)
    assert r.status_code != 403, r.text
    await _narrow_free(app["db"], "maintenance")
    r = await c.get("/api/maintenance/tasks", headers=hdr)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "plan_excluded", "feature": "maintenance", "message": "Not in your plan"}
    # a door that is not for sale is untouched
    r = await c.get("/api/billing/summary", headers=hdr)
    assert r.status_code != 403, r.text
    # a door the role lacks (a driver never manages work orders, so the
    # vendor directory is closed to them) keeps the plain denial — the
    # plan includes everything again, so it is not the plan's doing
    driver = await app["db"].create_user(920002, app["acct"].id, role=Role.DRIVER)
    dh = {"Authorization": f"Bearer {create_jwt(driver.telegram_id, app['acct'].id, 'driver')}"}
    await app["db"].upsert_plan("free", label="Free", included=["*"])
    from capabilities.permissions.plans import invalidate_plans
    invalidate_plans()
    r = await c.get("/api/vendors", headers=dh)
    assert r.status_code == 403 and r.json()["detail"] == "Insufficient permissions", r.text
