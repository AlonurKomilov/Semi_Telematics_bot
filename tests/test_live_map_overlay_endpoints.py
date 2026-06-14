"""API tests for the Phase B Live Map overlay endpoints.

Covers:
  GET /api/maintenance/due-locations  — Fleet's MaintenanceMarkersLayer
  GET /api/parking/utilisation/heatmap  — Owner/Admin's UtilisationHeatmap

Both endpoints are server-aggregated counts/points for map overlays —
the frontend never fetches the raw rows for performance reasons, so
the shape contract matters and is asserted here.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def app_client(pg_db):
    db = pg_db
    acct = await db.create_account("Live Map Overlay Co")
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


# ── /maintenance/due-locations ───────────────────────────────────


@pytest.mark.asyncio
async def test_maintenance_due_locations_empty_account(app_client):
    s = app_client
    r = await s["client"].get(
        "/api/maintenance/due-locations", headers=_hdr(s["tokens"]["fleet"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"items": [], "count": 0}


@pytest.mark.asyncio
async def test_maintenance_due_locations_aggregates_per_vehicle(app_client):
    """Three tasks across two vehicles → two aggregated rows.  Counts
    distinguish pending vs. overdue per the schema's status column."""
    s = app_client
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
async def test_maintenance_due_locations_driver_scope_returns_empty_when_no_trucks(app_client):
    """Driver has ``can_maintenance_vehicle=True`` (they can see their own
    truck's tasks) but no truck assignments in this fixture, so the
    response is an empty list — not a 403.  This matches the
    storage-mixin contract: _own scope with no trucks safe-denies to
    an empty result so we never leak the unfiltered account list."""
    s = app_client
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


# ── /parking/utilisation/heatmap ───────────────────────────────────


@pytest.mark.asyncio
async def test_utilisation_heatmap_empty_account(app_client):
    s = app_client
    r = await s["client"].get(
        "/api/parking/utilisation/heatmap", headers=_hdr(s["tokens"]["owner"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"points": [], "count": 0, "days": 30}


@pytest.mark.asyncio
async def test_utilisation_heatmap_skips_zero_coord_and_zero_duration(app_client):
    """Server filters: points at (0, 0) and zero-duration rows are
    excluded so they don't pollute the heatmap weight calculation."""
    s = app_client
    db = s["db"]
    acct = s["acct"]
    # Three events: one valid, one at origin, one with zero duration.
    await db.upsert_parking_event(
        account_id=acct.id, vehicle_id="v-1", vehicle_name="T-1",
        company_code="LMO",
        latitude=37.7749, longitude=-122.4194,
        address="SF",
        first_stopped=db._now(),
        duration_hours=8.0,
        location_class="unsafe",
    )
    await db.upsert_parking_event(
        account_id=acct.id, vehicle_id="v-2", vehicle_name="T-2",
        company_code="LMO",
        latitude=0, longitude=0,  # geocoder failed
        address="",
        first_stopped=db._now(),
        duration_hours=4.0,
        location_class="unknown",
    )
    await db.upsert_parking_event(
        account_id=acct.id, vehicle_id="v-3", vehicle_name="T-3",
        company_code="LMO",
        latitude=34.0, longitude=-118.0,
        address="LA",
        first_stopped=db._now(),
        duration_hours=0,  # zero — would weight as 0 anyway
        location_class="safe",
    )

    r = await s["client"].get(
        "/api/parking/utilisation/heatmap?days=30",
        headers=_hdr(s["tokens"]["owner"]),
    )
    assert r.status_code == 200
    body = r.json()
    # Only the one valid event should land in points.
    assert body["count"] == 1
    [pt] = body["points"]
    assert pt[0] == pytest.approx(37.7749)
    assert pt[1] == pytest.approx(-122.4194)
    assert pt[2] == pytest.approx(8.0)


@pytest.mark.asyncio
async def test_utilisation_heatmap_caps_weight_at_24h(app_client):
    """A 72-hour weekend stop is capped at 24 so it doesn't drown out
    busy-weekday stops on the heatmap."""
    s = app_client
    db = s["db"]
    acct = s["acct"]
    await db.upsert_parking_event(
        account_id=acct.id, vehicle_id="v-1", vehicle_name="T-1",
        company_code="LMO",
        latitude=37.7749, longitude=-122.4194,
        address="SF yard",
        first_stopped=db._now(),
        duration_hours=72.0,
        location_class="safe",
    )
    r = await s["client"].get(
        "/api/parking/utilisation/heatmap", headers=_hdr(s["tokens"]["owner"]),
    )
    body = r.json()
    assert body["count"] == 1
    assert body["points"][0][2] == pytest.approx(24.0)


@pytest.mark.asyncio
async def test_utilisation_heatmap_driver_blocked(app_client):
    """Driver doesn't have ``can_vehicle_all`` → 403.  Overlay hides
    on this status (UtilisationHeatmap is Owner/Admin only)."""
    s = app_client
    r = await s["client"].get(
        "/api/parking/utilisation/heatmap", headers=_hdr(s["tokens"]["driver"]),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_utilisation_heatmap_days_param_roundtrips(app_client):
    s = app_client
    r = await s["client"].get(
        "/api/parking/utilisation/heatmap?days=7",
        headers=_hdr(s["tokens"]["owner"]),
    )
    assert r.json()["days"] == 7
