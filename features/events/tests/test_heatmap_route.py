"""GET /api/safety/events/heatmap — the LiveMap heat layer.

Returns lat/lon/weight triples from safety_event_log, drops rows with no
coordinates, narrows a can_events_vehicle caller to their own truck, and
returns nothing when the warehouse flag is off.

Split from tests/test_phase_e_routes.py — see the sibling
features/vehicles/tests/test_timeline_route.py for why.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def heatmap_app(pg_db, monkeypatch):
    database = pg_db

    acct = await database.create_account("Timeline Co")
    await database.add_company(acct.id, "PE", "key_pe", "Timeline Co")
    owner = await database.create_user(9101, acct.id, role=Role.OWNER)
    driver = await database.create_user(9102, acct.id, role=Role.DRIVER)
    await database.assign_vehicle(
        user_id=driver.id, account_id=acct.id, truck_num="T-9",
        assigned_by=0, is_primary=True,
    )

    token_owner = create_jwt(owner.telegram_id, acct.id, "owner")
    token_driver = create_jwt(driver.telegram_id, acct.id, "driver")

    import infra.platform as _cp
    _old_db = _cp._db
    _cp._db = database

    async def _fake_events_warehouse(account_id, *, days=30, limit=10000, **kw):
        return [
            {"lat": 40.0, "lon": -74.0, "vehicle_name": "T-9"},
            {"lat": 40.1, "lon": -74.1, "vehicle_name": "T-9"},
            {"lat": 40.2, "lon": -74.2, "vehicle_name": "T-OTHER"},
            # Row missing lat/lon should be skipped.
            {"lat": None, "lon": None, "vehicle_name": "T-9"},
        ]

    from infra.services import get_tenant_db
    tenant = await get_tenant_db(acct.id)
    assert tenant is not None
    monkeypatch.setattr(
        tenant, "get_safety_events_warehouse", _fake_events_warehouse,
        raising=False,
    )
    monkeypatch.setattr(
        "features.vehicles.warehouse.readers._enabled", lambda: True,
    )

    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": database, "acct": acct,
        "token_owner": token_owner, "token_driver": token_driver,
    }

    _cp._db = _old_db


class TestHeatmapRoute:
    async def test_returns_lat_lon_weight_triples(self, heatmap_app):
        async with AsyncClient(
            transport=ASGITransport(app=heatmap_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/events/heatmap?days=30",
                headers=_h(heatmap_app["token_owner"]),
            )
            assert r.status_code == 200
            body = r.json()
            assert body["days"] == 30
            # 3 rows have lat/lon (the 4th is dropped).
            assert body["count"] == 3
            assert all(len(p) == 3 for p in body["points"])
            assert [p[0] for p in body["points"]] == [40.0, 40.1, 40.2]

    async def test_driver_with_truck_filtered_to_own(self, heatmap_app):
        # Driver role hits can_events_vehicle → only rows for their truck name.
        async with AsyncClient(
            transport=ASGITransport(app=heatmap_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/events/heatmap",
                headers=_h(heatmap_app["token_driver"]),
            )
            assert r.status_code == 200
            body = r.json()
            # Only the two T-9 rows with coords.
            assert body["count"] == 2

    async def test_empty_when_flag_off(self, heatmap_app, monkeypatch):
        monkeypatch.setattr(
            "features.vehicles.warehouse.readers._enabled", lambda: False,
        )
        async with AsyncClient(
            transport=ASGITransport(app=heatmap_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/events/heatmap",
                headers=_h(heatmap_app["token_owner"]),
            )
            assert r.status_code == 200
            assert r.json()["points"] == []
