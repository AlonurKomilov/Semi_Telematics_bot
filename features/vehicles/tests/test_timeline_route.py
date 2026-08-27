"""GET /api/vehicles/{name}/timeline — the VehicleDetail chart feed.

Reads the warehouse hourly grain and reverses it to oldest-first, 404s
in the BODY (not the status) when the vehicle is unknown, and returns an
empty series when the warehouse flag is off.

Split from tests/test_phase_e_routes.py, which held this and the safety
heatmap under one "phase E" banner. Two features, two route modules,
neither subordinate to the other — so it was a split, not a move. The
shared fixture is duplicated deliberately: each half now stubs only what
it actually needs, which is shorter than the combined one it replaced.
(The old name also carried a phase label, which CLAUDE.md forbids.)
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def timeline_app(pg_db, monkeypatch):
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

    async def _fake_vehicle_detail(account_id, vehicle_name, *, company=None):
        if vehicle_name.lower() == "t-9":
            return [{"id": "veh-9", "name": "T-9", "_org": "PE"}]
        return []

    monkeypatch.setattr(
        "features.vehicles.router._svc_vehicle_detail", _fake_vehicle_detail,
    )

    # Warehouse reader returns DESC; the route reverses to oldest-first.
    async def _fake_timeline(account_id, *, vehicle_id=None, hours=168):
        assert vehicle_id == "veh-9"
        return [
            {"hour_utc": "2026-01-02T03:00:00", "miles": 30, "max_speed_mph": 65, "harsh_event_count": 0},
            {"hour_utc": "2026-01-02T02:00:00", "miles": 28, "max_speed_mph": 62, "harsh_event_count": 1},
            {"hour_utc": "2026-01-02T01:00:00", "miles": 10, "max_speed_mph": 55, "harsh_event_count": 0},
        ]

    monkeypatch.setattr(
        "features.vehicles.warehouse.readers.get_vehicle_state_hour", _fake_timeline,
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


class TestVehicleTimelineRoute:
    async def test_returns_points_oldest_first(self, timeline_app):
        async with AsyncClient(
            transport=ASGITransport(app=timeline_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/vehicles/T-9/timeline?days=7",
                headers=_h(timeline_app["token_owner"]),
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

    async def test_404_when_vehicle_unknown(self, timeline_app):
        async with AsyncClient(
            transport=ASGITransport(app=timeline_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/vehicles/T-NOPE/timeline",
                headers=_h(timeline_app["token_owner"]),
            )
            assert r.status_code == 200  # body-level 404 contract
            body = r.json()
            assert body["points"] == []
            assert body.get("error")

    async def test_empty_points_when_flag_off(self, timeline_app, monkeypatch):
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
            transport=ASGITransport(app=timeline_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/vehicles/T-9/timeline",
                headers=_h(timeline_app["token_owner"]),
            )
            assert r.status_code == 200
            assert r.json()["points"] == []


# ── /safety/events/heatmap (E26) ────────────────────────────────
