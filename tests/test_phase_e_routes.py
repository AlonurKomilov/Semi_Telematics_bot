"""Tests for new endpoints (user-facing).

Covers:
- ``GET /api/vehicles/{name}/timeline`` (E25 — VehicleDetail timeline).
  - 404 (in-body) when vehicle is not found.
  - Returns oldest-first ``points`` from the warehouse reader.
  - Returns empty ``points`` when the warehouse flag is off.
- ``GET /api/safety/events/heatmap`` (E26 — LiveMap heat layer).
  - Returns lat/lon/weight triples from ``safety_event_log``.
  - ``can_events_vehicle`` callers without a truck assignment get an empty list.
  - Empty list when the warehouse flag is off.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest_asyncio

from httpx import ASGITransport, AsyncClient

from adapters.storage import Database, Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def phase_e_app(pg_db, monkeypatch):
    database = pg_db

    acct = await database.create_account("PhaseE Co")
    await database.add_company(acct.id, "PE", "key_pe", "PhaseE Co")
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

    # ── Stubs for /timeline ───────────────────────────────────────
    # Vehicle catalog: one vehicle named "T-9" with id "veh-9".
    async def _fake_vehicle_detail(account_id, vehicle_name, *, company=None):
        if vehicle_name.lower() == "t-9":
            return [{"id": "veh-9", "name": "T-9", "_org": "PE"}]
        return []

    monkeypatch.setattr(
        "features.vehicles.router._svc_vehicle_detail",
        _fake_vehicle_detail,
    )

    # Warehouse reader returns DESC; route reverses to oldest-first.
    async def _fake_timeline(account_id, *, vehicle_id=None, hours=168):
        assert vehicle_id == "veh-9"
        # Newest-first as the real reader does.
        return [
            {"hour_utc": "2026-01-02T03:00:00", "miles": 30, "max_speed_mph": 65, "harsh_event_count": 0},
            {"hour_utc": "2026-01-02T02:00:00", "miles": 28, "max_speed_mph": 62, "harsh_event_count": 1},
            {"hour_utc": "2026-01-02T01:00:00", "miles": 10, "max_speed_mph": 55, "harsh_event_count": 0},
        ]

    monkeypatch.setattr(
        "features.vehicles.warehouse.readers.get_vehicle_state_hour",
        _fake_timeline,
    )

    # Default: warehouse flag ON for the timeline + heatmap reads.  Tests that
    # need it OFF flip it back via monkeypatch.
    monkeypatch.setattr(
        "features.vehicles.warehouse.readers._enabled", lambda: True,
    )

    # ── Stubs for /heatmap ────────────────────────────────────────
    # Patch the Database get_safety_events_warehouse method directly.
    async def _fake_events_warehouse(account_id, *, days=30, limit=10000, **kw):
        return [
            {"lat": 40.0, "lon": -74.0, "vehicle_name": "T-9"},
            {"lat": 40.1, "lon": -74.1, "vehicle_name": "T-9"},
            {"lat": 40.2, "lon": -74.2, "vehicle_name": "T-OTHER"},
            # Row missing lat/lon should be skipped.
            {"lat": None, "lon": None, "vehicle_name": "T-9"},
        ]

    # Bind onto the actual tenant DB instance the router serves.
    from infra.services import get_tenant_db
    tenant = await get_tenant_db(acct.id)
    assert tenant is not None
    monkeypatch.setattr(
        tenant, "get_safety_events_warehouse", _fake_events_warehouse,
        raising=False,
    )

    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": database, "acct": acct,
        "token_owner": token_owner, "token_driver": token_driver,
    }

    _cp._db = _old_db
    # database.close() handled by the pg_db fixture's own teardown


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── /fleet/vehicle/{name}/timeline (E25) ────────────────────────


class TestVehicleTimelineRoute:
    async def test_returns_points_oldest_first(self, phase_e_app):
        async with AsyncClient(
            transport=ASGITransport(app=phase_e_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/vehicles/T-9/timeline?days=7",
                headers=_h(phase_e_app["token_owner"]),
            )
            assert r.status_code == 200
            body = r.json()
            assert body["name"] == "T-9"
            assert body["vehicle_id"] == "veh-9"
            assert body["days"] == 7
            hours = [p["hour_utc"] for p in body["points"]]
            # Reader returned DESC; the route reverses to ASC for charts.
            assert hours == [
                "2026-01-02T01:00:00",
                "2026-01-02T02:00:00",
                "2026-01-02T03:00:00",
            ]

    async def test_404_when_vehicle_unknown(self, phase_e_app):
        async with AsyncClient(
            transport=ASGITransport(app=phase_e_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/vehicles/T-NOPE/timeline",
                headers=_h(phase_e_app["token_owner"]),
            )
            assert r.status_code == 200  # body-level 404 contract
            body = r.json()
            assert body["points"] == []
            assert body.get("error")

    async def test_empty_points_when_flag_off(self, phase_e_app, monkeypatch):
        monkeypatch.setattr(
            "features.vehicles.warehouse.readers._enabled", lambda: False,
        )
        # The reader short-circuits on flag off.
        async def _empty(account_id, *, vehicle_id=None, hours=168):
            return []
        monkeypatch.setattr(
            "features.vehicles.warehouse.readers.get_vehicle_state_hour",
            _empty,
        )
        async with AsyncClient(
            transport=ASGITransport(app=phase_e_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/vehicles/T-9/timeline",
                headers=_h(phase_e_app["token_owner"]),
            )
            assert r.status_code == 200
            assert r.json()["points"] == []


# ── /safety/events/heatmap (E26) ────────────────────────────────


class TestHeatmapRoute:
    async def test_returns_lat_lon_weight_triples(self, phase_e_app):
        async with AsyncClient(
            transport=ASGITransport(app=phase_e_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/events/heatmap?days=30",
                headers=_h(phase_e_app["token_owner"]),
            )
            assert r.status_code == 200
            body = r.json()
            assert body["days"] == 30
            # 3 rows have lat/lon (the 4th is dropped).
            assert body["count"] == 3
            assert all(len(p) == 3 for p in body["points"])
            assert [p[0] for p in body["points"]] == [40.0, 40.1, 40.2]

    async def test_driver_with_truck_filtered_to_own(self, phase_e_app):
        # Driver role hits can_events_vehicle → only rows for their truck name.
        async with AsyncClient(
            transport=ASGITransport(app=phase_e_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/events/heatmap",
                headers=_h(phase_e_app["token_driver"]),
            )
            assert r.status_code == 200
            body = r.json()
            # Only the two T-9 rows with coords.
            assert body["count"] == 2

    async def test_empty_when_flag_off(self, phase_e_app, monkeypatch):
        monkeypatch.setattr(
            "features.vehicles.warehouse.readers._enabled", lambda: False,
        )
        async with AsyncClient(
            transport=ASGITransport(app=phase_e_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/events/heatmap",
                headers=_h(phase_e_app["token_owner"]),
            )
            assert r.status_code == 200
            assert r.json()["points"] == []
