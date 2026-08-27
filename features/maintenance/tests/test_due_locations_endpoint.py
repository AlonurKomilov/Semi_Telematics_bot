"""GET /api/maintenance/due-locations — the MaintenanceMarkersLayer feed.

Server-aggregated per-vehicle counts for a map overlay: the frontend
never fetches the raw rows, so the shape contract is what matters and is
asserted here.

Split from tests/test_live_map_overlay_endpoints.py. That file paired
this with the parking utilisation heatmap because both draw on the Live
Map — but "Live Map" is a dashboard SURFACE, not a backend package, and
the two endpoints live in different features with neither serving as the
other's setup. The shared fixture is duplicated; it is 25 lines, and the
alternative was filing three maintenance tests under parking or vice
versa.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def due_locations_app(pg_db):
    db = pg_db
    acct = await db.create_account("Due Locations Co")
    await db.add_company(acct.id, "LMO", "samsara_test", "LMO")
    owner = await db.create_user(960001, acct.id, role=Role.OWNER)
    driver = await db.create_user(960002, acct.id, role=Role.DRIVER)
    fleet = await db.create_user(960003, acct.id, role=Role.FLEET)

    tokens = {
        "owner": create_jwt(owner.telegram_id, acct.id, "owner"),
        "driver": create_jwt(driver.telegram_id, acct.id, "driver"),
        "fleet": create_jwt(fleet.telegram_id, acct.id, "fleet"),
    }

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = db

    from interfaces.api.app import create_api
    app = create_api()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {"client": client, "db": db, "acct": acct, "tokens": tokens}
    _cp._db = _old


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_maintenance_due_locations_empty_account(due_locations_app):
    s = due_locations_app
    r = await s["client"].get(
        "/api/maintenance/due-locations", headers=_hdr(s["tokens"]["fleet"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"items": [], "count": 0}


@pytest.mark.asyncio
async def test_maintenance_due_locations_aggregates_per_vehicle(due_locations_app):
    """Three tasks across two vehicles → two aggregated rows.  Counts
    distinguish pending vs. overdue per the schema's status column."""
    s = due_locations_app
    db = s["db"]
    acct = s["acct"]
    # Two pending tasks on Truck-1, one overdue on Truck-2.
    await db.add_maintenance_task(
        acct.id, "LMO", "Truck-1", "oil_change", "Oil change",
        vehicle_id="v-001",
    )
    await db.add_maintenance_task(
        acct.id, "LMO", "Truck-1", "tire_rotation", "Tires",
        vehicle_id="v-001",
    )
    tid = await db.add_maintenance_task(
        acct.id, "LMO", "Truck-2", "brake", "Brakes",
        vehicle_id="v-002",
    )
    await db.update_maintenance_status(tid, "overdue", acct.id)

    r = await s["client"].get(
        "/api/maintenance/due-locations", headers=_hdr(s["tokens"]["fleet"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2

    by_name = {row["vehicle_name"]: row for row in body["items"]}
    assert by_name["Truck-1"]["pending_count"] == 2
    assert by_name["Truck-1"]["overdue_count"] == 0
    assert by_name["Truck-2"]["pending_count"] == 0
    assert by_name["Truck-2"]["overdue_count"] == 1


@pytest.mark.asyncio
async def test_maintenance_due_locations_driver_scope_returns_empty_when_no_trucks(due_locations_app):
    """Driver has ``can_maintenance_vehicle=True`` (they can see their own
    truck's tasks) but no truck assignments in this fixture, so the
    response is an empty list — not a 403.  This matches the
    storage-mixin contract: _own scope with no trucks safe-denies to
    an empty result so we never leak the unfiltered account list."""
    s = due_locations_app
    db = s["db"]
    acct = s["acct"]
    # Seed a task that exists on the account but isn't on a driver's truck.
    await db.add_maintenance_task(
        acct.id, "LMO", "OtherTruck", "oil_change", "Oil",
        vehicle_id="v-x",
    )

    r = await s["client"].get(
        "/api/maintenance/due-locations", headers=_hdr(s["tokens"]["driver"]),
    )
    assert r.status_code == 200
    assert r.json() == {"items": [], "count": 0}
