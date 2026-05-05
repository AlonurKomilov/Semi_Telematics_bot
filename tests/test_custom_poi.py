"""Tests for the per-tenant custom POI layer feature.

Covers:
  * CRUD over /api/map/custom-layers (overpass + csv source types)
  * Permission gates: only roles with `can_manage_poi_layers` can write
  * Tenant isolation: account A's layer is invisible to account B
  * Overpass validator rejects unsafe tokens (`;`, `out`, recursion, …)
  * CSV upload happy path + invalid-row skipping + size guard
  * Pin-drop discovery (network is mocked)

The fixture mirrors `tests/test_api_routes.py` so tests run against a real
ASGI app with a temp SQLite database.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Database, Role
from interfaces.api.auth import create_jwt


# ---------------------------------------------------------------------------
# Fixture — same shape as test_api_routes.db_and_app, scoped to this module.
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def app_ctx(tmp_path):
    db_path = str(tmp_path / "custom_poi.db")
    database = Database(db_path)
    await database.initialize()

    acct_a = await database.create_account("Tenant A")
    acct_b = await database.create_account("Tenant B")
    await database.add_company(acct_a.id, "COMPA", "key_a", "Company A")
    await database.add_company(acct_b.id, "COMPB", "key_b", "Company B")
    owner_a  = await database.create_user(1001, acct_a.id, role=Role.OWNER)
    owner_b  = await database.create_user(2001, acct_b.id, role=Role.OWNER)
    driver_a = await database.create_user(1002, acct_a.id, role=Role.DRIVER)

    token_owner_a  = create_jwt(owner_a.telegram_id,  acct_a.id, "owner")
    token_owner_b  = create_jwt(owner_b.telegram_id,  acct_b.id, "owner")
    token_driver_a = create_jwt(driver_a.telegram_id, acct_a.id, "driver")

    import infra.platform as _cp
    from adapters.storage.tenant_router import LegacyRouter
    _old_router, _old_db = _cp._router, _cp._db
    _cp._router = LegacyRouter(database)
    _cp._db = database

    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "db": database,
        "app": app,
        "acct_a": acct_a, "acct_b": acct_b,
        "owner_a_token":  token_owner_a,
        "owner_b_token":  token_owner_b,
        "driver_a_token": token_driver_a,
    }

    _cp._router, _cp._db = _old_router, _old_db
    await database.close()


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class TestCrud:
    async def test_create_overpass_layer_and_list(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            r = await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["owner_a_token"]),
                json={
                    "label":          "Truck Washes",
                    "color":          "#7c3aed",
                    "icon":           "🚿",
                    "source_type":    "overpass",
                    "overpass_query": 'node["amenity"="car_wash"]["hgv"="yes"]',
                    "default_on":     False,
                },
            )
            assert r.status_code == 201, r.text
            created = r.json()
            assert created["label"] == "Truck Washes"
            assert created["source_type"] == "overpass"
            assert created["id"] > 0

            r2 = await c.get("/api/map/custom-layers", headers=_h(app_ctx["owner_a_token"]))
            assert r2.status_code == 200
            layers = r2.json()["layers"]
            assert any(lyr["id"] == created["id"] for lyr in layers)

    async def test_patch_layer_metadata(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            r = await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["owner_a_token"]),
                json={
                    "label": "Initial", "color": "#0ea5e9", "icon": "📍",
                    "source_type": "csv", "default_on": False,
                },
            )
            lid = r.json()["id"]

            r2 = await c.patch(
                f"/api/map/custom-layers/{lid}",
                headers=_h(app_ctx["owner_a_token"]),
                json={"label": "Renamed", "default_on": True},
            )
            assert r2.status_code == 200, r2.text
            patched = r2.json()
            assert patched["label"] == "Renamed"
            assert patched["default_on"] is True

    async def test_patch_overpass_on_csv_layer_rejected(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            r = await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["owner_a_token"]),
                json={
                    "label": "CSV Layer", "color": "#0ea5e9", "icon": "📍",
                    "source_type": "csv", "default_on": False,
                },
            )
            lid = r.json()["id"]

            r2 = await c.patch(
                f"/api/map/custom-layers/{lid}",
                headers=_h(app_ctx["owner_a_token"]),
                json={"overpass_query": 'node["amenity"="fuel"]'},
            )
            assert r2.status_code == 422

    async def test_delete_layer(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            r = await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["owner_a_token"]),
                json={
                    "label": "Doomed", "color": "#ef4444", "icon": "📍",
                    "source_type": "csv", "default_on": False,
                },
            )
            lid = r.json()["id"]
            r2 = await c.delete(
                f"/api/map/custom-layers/{lid}",
                headers=_h(app_ctx["owner_a_token"]),
            )
            assert r2.status_code == 200
            r3 = await c.get("/api/map/custom-layers", headers=_h(app_ctx["owner_a_token"]))
            assert all(lyr["id"] != lid for lyr in r3.json()["layers"])


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

class TestPermissions:
    """Driver lacks `can_manage_poi_layers` and is rejected on every write."""

    async def test_driver_cannot_create(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            r = await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["driver_a_token"]),
                json={
                    "label": "x", "color": "#000000", "icon": "📍",
                    "source_type": "csv", "default_on": False,
                },
            )
            assert r.status_code == 403

    async def test_driver_cannot_patch(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            owned = await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["owner_a_token"]),
                json={
                    "label": "owner-made", "color": "#000000", "icon": "📍",
                    "source_type": "csv", "default_on": False,
                },
            )
            lid = owned.json()["id"]
            r = await c.patch(
                f"/api/map/custom-layers/{lid}",
                headers=_h(app_ctx["driver_a_token"]),
                json={"label": "hijacked"},
            )
            assert r.status_code == 403

    async def test_driver_can_list(self, app_ctx):
        """Drivers have `can_location_own` — they can list layers for their map
        view (e.g. nearby fuel stops, weigh stations).  Read is open to all
        map-capable roles; only write operations require `can_manage_poi_layers`."""
        async with _client(app_ctx["app"]) as c:
            r = await c.get("/api/map/custom-layers", headers=_h(app_ctx["driver_a_token"]))
            assert r.status_code == 200


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------

class TestTenantIsolation:
    async def test_account_b_cannot_see_account_a_layers(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["owner_a_token"]),
                json={
                    "label": "A-only", "color": "#7c3aed", "icon": "📍",
                    "source_type": "csv", "default_on": False,
                },
            )
            r = await c.get("/api/map/custom-layers", headers=_h(app_ctx["owner_b_token"]))
            assert r.status_code == 200
            assert all(lyr["label"] != "A-only" for lyr in r.json()["layers"])

    async def test_account_b_cannot_patch_account_a_layer(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            owned = await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["owner_a_token"]),
                json={
                    "label": "A-private", "color": "#7c3aed", "icon": "📍",
                    "source_type": "csv", "default_on": False,
                },
            )
            lid = owned.json()["id"]
            r = await c.patch(
                f"/api/map/custom-layers/{lid}",
                headers=_h(app_ctx["owner_b_token"]),
                json={"label": "stolen"},
            )
            assert r.status_code == 404


# ---------------------------------------------------------------------------
# Overpass validator
# ---------------------------------------------------------------------------

class TestOverpassValidator:
    @pytest.mark.parametrize("bad_query", [
        "",                                          # empty
        "out body;",                                 # plain `out`
        "rel[type=boundary]",                        # wrong opener
        'node["amenity"="fuel"]; out;',              # statement separator
        'node["amenity"="fuel"](recurse)',           # recurse
        'node["amenity"="fuel"] /* comment */',      # comments
        'node["amenity"="fuel"] // line',            # line comments
        'way["highway"="primary"](->.x;)',           # var assignment
    ])
    async def test_invalid_query_rejected(self, app_ctx, bad_query):
        async with _client(app_ctx["app"]) as c:
            r = await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["owner_a_token"]),
                json={
                    "label": "bad", "color": "#000000", "icon": "📍",
                    "source_type": "overpass",
                    "overpass_query": bad_query,
                    "default_on": False,
                },
            )
            assert r.status_code == 422

    async def test_valid_query_accepted(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            r = await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["owner_a_token"]),
                json={
                    "label": "good", "color": "#000000", "icon": "📍",
                    "source_type": "overpass",
                    "overpass_query": 'node["amenity"="charging_station"]',
                    "default_on": False,
                },
            )
            assert r.status_code == 201


# ---------------------------------------------------------------------------
# CSV upload
# ---------------------------------------------------------------------------

class TestCsvUpload:
    async def test_csv_happy_path(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            owned = await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["owner_a_token"]),
                json={
                    "label": "Yards", "color": "#10b981", "icon": "🅿️",
                    "source_type": "csv", "default_on": False,
                },
            )
            lid = owned.json()["id"]
            csv_text = (
                "name,lat,lng,brand\n"
                "Yard 1,41.5,-93.6,Acme\n"
                "Yard 2,41.6,-93.7,Acme\n"
                "Bad row,not_a_number,oops,Acme\n"   # skipped
            )
            r = await c.post(
                f"/api/map/custom-layers/{lid}/csv",
                headers=_h(app_ctx["owner_a_token"]),
                json={"csv": csv_text},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["inserted"] == 2
            assert data["skipped"]  == 1

    async def test_csv_missing_columns_rejected(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            owned = await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["owner_a_token"]),
                json={
                    "label": "Bad CSV", "color": "#10b981", "icon": "🅿️",
                    "source_type": "csv", "default_on": False,
                },
            )
            lid = owned.json()["id"]
            r = await c.post(
                f"/api/map/custom-layers/{lid}/csv",
                headers=_h(app_ctx["owner_a_token"]),
                json={"csv": "name,brand\nA,X\n"},
            )
            assert r.status_code == 422

    async def test_csv_on_overpass_layer_rejected(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            owned = await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["owner_a_token"]),
                json={
                    "label": "OP Layer", "color": "#10b981", "icon": "📍",
                    "source_type": "overpass",
                    "overpass_query": 'node["amenity"="fuel"]',
                    "default_on": False,
                },
            )
            lid = owned.json()["id"]
            r = await c.post(
                f"/api/map/custom-layers/{lid}/csv",
                headers=_h(app_ctx["owner_a_token"]),
                json={"csv": "lat,lng\n1.0,2.0\n"},
            )
            assert r.status_code == 422


# ---------------------------------------------------------------------------
# Pin-drop discovery (network mocked)
# ---------------------------------------------------------------------------

def _mk_overpass_session(overpass_response: dict) -> MagicMock:
    """Build an aiohttp.ClientSession-shaped mock returning `overpass_response`.

    The endpoint code does:  async with session.post(...) as resp: await resp.json()
    """
    resp = MagicMock()
    resp.status = 200
    resp.json   = AsyncMock(return_value=overpass_response)

    post_cm = MagicMock()
    post_cm.__aenter__ = AsyncMock(return_value=resp)
    post_cm.__aexit__  = AsyncMock(return_value=None)

    session = MagicMock()
    session.post = MagicMock(return_value=post_cm)
    return session


class TestPinDrop:
    async def test_pin_drop_finds_brand(self, app_ctx):
        # Overpass returns one node with brand=Pilot at almost the same coords.
        overpass_data = {
            "elements": [
                {
                    "type": "node",
                    "id": 1,
                    "lat": 41.5868,
                    "lon": -93.6250,
                    "tags": {"brand": "Pilot", "amenity": "fuel"},
                }
            ]
        }
        with patch(
            "interfaces.api.routes.maps._get_http_session",
            new=AsyncMock(return_value=_mk_overpass_session(overpass_data)),
        ):
            async with _client(app_ctx["app"]) as c:
                r = await c.post(
                    "/api/map/custom-layers/from-pin",
                    headers=_h(app_ctx["owner_a_token"]),
                    json={"lat": 41.5868, "lng": -93.6250},
                )
                assert r.status_code == 201, r.text
                data = r.json()
                assert data["discovered_brand"] == "Pilot"
                assert "Pilot" in data["label"]

    async def test_pin_drop_no_brand_returns_404(self, app_ctx):
        # Empty Overpass response → 404 with hint about Geofences.
        with patch(
            "interfaces.api.routes.maps._get_http_session",
            new=AsyncMock(return_value=_mk_overpass_session({"elements": []})),
        ):
            async with _client(app_ctx["app"]) as c:
                r = await c.post(
                    "/api/map/custom-layers/from-pin",
                    headers=_h(app_ctx["owner_a_token"]),
                    json={"lat": 0.0, "lng": 0.0},
                )
                assert r.status_code == 404
                assert "geofence" in r.json()["detail"].lower()

    async def test_pin_drop_requires_admin(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            r = await c.post(
                "/api/map/custom-layers/from-pin",
                headers=_h(app_ctx["driver_a_token"]),
                json={"lat": 0.0, "lng": 0.0},
            )
            assert r.status_code == 403


# ---------------------------------------------------------------------------
# USA-only territory clipping
# ---------------------------------------------------------------------------

class TestUsaClipping:
    """The /pois endpoint must clip the requested viewport to US bounds so
    Mexico, Canada and the Caribbean never appear in any POI layer."""

    async def test_bbox_entirely_in_mexico_returns_empty(self, app_ctx):
        # Mexico City area bbox — fully south of the US border.
        async with _client(app_ctx["app"]) as c:
            r = await c.get(
                "/api/map/pois?type=fuel_station&bbox=18.5,-100.5,20.0,-98.5",
                headers=_h(app_ctx["owner_a_token"]),
            )
            assert r.status_code == 200
            assert r.json() == {"type": "FeatureCollection", "features": []}

    async def test_bbox_entirely_in_canada_returns_empty(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            r = await c.get(
                # Toronto-ish bbox — fully north of CONUS clip.
                "/api/map/pois?type=fuel_station&bbox=50.0,-80.0,52.0,-78.0",
                headers=_h(app_ctx["owner_a_token"]),
            )
            assert r.status_code == 200
            assert r.json() == {"type": "FeatureCollection", "features": []}

    async def test_clip_helper_intersects_overlapping_bbox(self):
        """Sanity-check the helper directly so we don't depend on Overpass
        for the intersection-math assertion."""
        from interfaces.api.routes.maps import _clip_bbox_to_usa
        # Border viewport (Detroit↔Windsor): bbox extends north into Canada.
        s, w, n, e = 41.5, -83.5, 43.5, -82.0
        clipped = _clip_bbox_to_usa(s, w, n, e)
        assert clipped is not None
        cs, cw, cn, ce = clipped
        assert cs == 41.5
        assert cw == -83.5
        assert cn == 43.5  # within CONUS top
        assert ce == -82.0
