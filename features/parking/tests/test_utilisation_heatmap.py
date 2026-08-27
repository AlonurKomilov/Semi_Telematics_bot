"""GET /api/parking/utilisation/heatmap — the UtilisationHeatmap feed.

Server-aggregated points for a map overlay: zero coordinates and
zero-duration stays are dropped, weight is capped at 24h, and a driver
is refused outright.

Split from tests/test_live_map_overlay_endpoints.py — see the sibling
features/maintenance/tests/test_due_locations_endpoint.py for why.
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
async def heatmap_app(pg_db):
    db = pg_db
    acct = await db.create_account("Utilisation Heatmap Co")
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
async def test_utilisation_heatmap_empty_account(heatmap_app):
    s = heatmap_app
    r = await s["client"].get(
        "/api/parking/utilisation/heatmap", headers=_hdr(s["tokens"]["owner"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"points": [], "count": 0, "days": 30}


@pytest.mark.asyncio
async def test_utilisation_heatmap_skips_zero_coord_and_zero_duration(heatmap_app):
    """Server filters: points at (0, 0) and zero-duration rows are
    excluded so they don't pollute the heatmap weight calculation."""
    s = heatmap_app
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
async def test_utilisation_heatmap_caps_weight_at_24h(heatmap_app):
    """A 72-hour weekend stop is capped at 24 so it doesn't drown out
    busy-weekday stops on the heatmap."""
    s = heatmap_app
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
async def test_utilisation_heatmap_driver_blocked(heatmap_app):
    """Driver doesn't have ``can_vehicle_all`` → 403.  Overlay hides
    on this status (UtilisationHeatmap is Owner/Admin only)."""
    s = heatmap_app
    r = await s["client"].get(
        "/api/parking/utilisation/heatmap", headers=_hdr(s["tokens"]["driver"]),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_utilisation_heatmap_days_param_roundtrips(heatmap_app):
    s = heatmap_app
    r = await s["client"].get(
        "/api/parking/utilisation/heatmap?days=7",
        headers=_hdr(s["tokens"]["owner"]),
    )
    assert r.json()["days"] == 7
