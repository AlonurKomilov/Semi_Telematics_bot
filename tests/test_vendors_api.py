"""Vendors API permission gates (Phase A).

Pins the manager-only access model: ``can_work_orders_all`` for every
endpoint, reads included — a vendor profile aggregates ALL trucks'
work orders + account-wide spend, so vehicle-scoped drivers (who see
only their own truck's WOs on the WO endpoints) must get 403 here,
never a filtered view that could silently widen later.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def seeded(pg_db):
    database = pg_db
    acct = await database.create_account("Vendor Co")
    await database.add_company(acct.id, "VC", "key_vc", "Vendor Co")
    owner = await database.create_user(8101, acct.id, role=Role.OWNER)
    fleet = await database.create_user(8102, acct.id, role=Role.FLEET)
    driver = await database.create_user(8103, acct.id, role=Role.DRIVER)

    v = await database.resolve_or_create_vendor(acct.id, "Gate Test Repair", phone="555-9")
    await database.add_work_order(
        acct.id, "VC", "T-1", "Gate Test Repair",
        vendor_id=v["id"], service_date="2026-07-01", total_cost=100,
    )

    import infra.platform as _cp
    _old_db = _cp._db
    _cp._db = database
    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": database, "acct": acct, "vendor": v,
        "token_owner": create_jwt(owner.telegram_id, acct.id, "owner"),
        "token_fleet": create_jwt(fleet.telegram_id, acct.id, "fleet"),
        "token_driver": create_jwt(driver.telegram_id, acct.id, "driver"),
    }
    _cp._db = _old_db


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_fleet_manager_full_access(seeded):
    """Fleet role (the default owner of this feature) can list, read
    profiles, create, and merge."""
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/vendors", headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200
        assert any(v["name"] == "Gate Test Repair" for v in r.json()["vendors"])

        vid = seeded["vendor"]["id"]
        r = await c.get(f"/api/vendors/{vid}", headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200
        body = r.json()
        assert body["vendor"]["id"] == vid
        assert len(body["work_orders"]) == 1

        r = await c.post("/api/vendors", headers=_h(seeded["token_fleet"]),
                         json={"name": "Second Shop"})
        assert r.status_code == 200
        second = r.json()

        r = await c.post(
            f"/api/vendors/{second['id']}/merge-into/{vid}",
            headers=_h(seeded["token_fleet"]),
        )
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_driver_gets_403_everywhere(seeded):
    """Vehicle-scope WO access must NOT open the vendors surface —
    the profile aggregates other trucks' records."""
    transport = ASGITransport(app=seeded["app"])
    vid = seeded["vendor"]["id"]
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        for method, url, kw in (
            ("GET", "/api/vendors", {}),
            ("GET", f"/api/vendors/{vid}", {}),
            ("POST", "/api/vendors", {"json": {"name": "Nope"}}),
            ("PUT", f"/api/vendors/{vid}", {"json": {"phone": "1"}}),
            ("POST", f"/api/vendors/{vid}/merge-into/999", {}),
        ):
            r = await c.request(method, url, headers=_h(seeded["token_driver"]), **kw)
            assert r.status_code == 403, f"{method} {url} -> {r.status_code}"


@pytest.mark.asyncio
async def test_owner_can_read(seeded):
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/vendors", headers=_h(seeded["token_owner"]))
        assert r.status_code == 200
