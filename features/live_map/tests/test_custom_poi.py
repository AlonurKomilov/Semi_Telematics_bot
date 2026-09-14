"""Tests for the per-tenant custom POI layer feature.

Covers:
  * CRUD over /api/map/custom-layers (overpass + csv source types)
  * Permission gates: only roles with `can_manage_poi_layers` can write
  * Tenant isolation: account A's layer is invisible to account B
  * Overpass validator rejects unsafe tokens (`;`, `out`, recursion, …)
  * CSV upload happy path + invalid-row skipping + size guard
  * Pin-drop discovery (network is mocked)
  * A custom layer whose source refuses is a 502, never an empty layer

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

from adapters.storage import Database, Role
from interfaces.api.auth import create_jwt


# ---------------------------------------------------------------------------
# Fixture — same shape as test_api_routes.db_and_app, scoped to this module.
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def app_ctx(pg_db):
    database = pg_db

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
    _old_db = _cp._db
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

    _cp._db = _old_db
    # database.close() handled by the pg_db fixture's own teardown


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

    async def test_patch_refines_an_overpass_layers_query(self, app_ctx):
        """The only PATCH path that reaches the query validator.

        Every other test that sends an `overpass_query` aims it at a CSV
        layer, which is refused at 422 BEFORE the validator runs — so the
        happy path was unexercised.  A call broken on this line shipped
        green once already: splitting pois.py gave the route a module
        named `overpass` to reach the client through, and the local
        variable of the same name hid it.  Python only says so here.
        """
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
            lid = r.json()["id"]

            refined = 'node["amenity"="car_wash"]["hgv"="designated"]'
            r2 = await c.patch(
                f"/api/map/custom-layers/{lid}",
                headers=_h(app_ctx["owner_a_token"]),
                json={"overpass_query": refined},
            )
            assert r2.status_code == 200, r2.text
            assert r2.json()["overpass_query"] == refined

            # And the validator is still in that path: a query the
            # whitelist refuses must not reach storage.
            r3 = await c.patch(
                f"/api/map/custom-layers/{lid}",
                headers=_h(app_ctx["owner_a_token"]),
                json={"overpass_query": 'out; node["amenity"="fuel"]'},
            )
            assert r3.status_code == 422, r3.text

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
        """Drivers have `can_location_vehicle` — they can list layers for their map
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
            "features.live_map.poi.overpass._get_http_session",
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
            "features.live_map.poi.overpass._get_http_session",
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
# Handed over whole, so the client stops asking per viewport
# ---------------------------------------------------------------------------

class TestWholeLayerHandover:
    """These layers do not move and they are small — the largest is about
    5,400 points, 169 KB gzipped.  So a client can hold one and pan
    locally forever, asking only "is my copy still current".

    Version-and-replace rather than a delta: a delta would have to
    describe DELETIONS, and the import sweeps with a hard DELETE, so a
    client with a stale copy would keep a truck stop that closed.
    Replacing the set wholesale gets that right for free.
    """

    async def _import(self, db, layer, points, stamp):
        await db.upsert_poi_points(layer, points, stamp)
        await db.finish_poi_import(layer, stamp, len(points), ok=True)

    async def test_versions_name_what_is_holdable_and_what_is_not(self, app_ctx):
        db = app_ctx["db"]
        await self._import(db, "shower", [
            {"osm_type": "node", "osm_id": 1, "lat": 41.0, "lng": -93.0,
             "name": "One", "props": {}},
        ], "2026-09-14T02:00:00Z")

        async with _client(app_ctx["app"]) as c:
            r = await c.get("/api/map/poi-versions",
                            headers=_h(app_ctx["owner_a_token"]))
        assert r.status_code == 200, r.text
        versions = r.json()["versions"]
        assert versions["shower"] == "2026-09-14T02:00:00Z"
        # NULL, not a date and not an omission: a layer with no import is
        # a layer the client must fetch the old way.
        assert versions["rest_area"] is None
        # Every built-in layer is named, so a client can decide about all
        # of them from one reply.
        from features.live_map.poi.layers import POI_OVERPASS_QUERIES
        assert set(versions) == set(POI_OVERPASS_QUERIES)

    async def test_a_layer_comes_over_whole_with_the_version_it_is(self, app_ctx):
        db = app_ctx["db"]
        await self._import(db, "truck_parking", [
            {"osm_type": "node", "osm_id": 10, "lat": 41.0, "lng": -93.0,
             "name": "Iowa lot", "props": {"amenity": "parking", "hgv": "yes"}},
            {"osm_type": "node", "osm_id": 11, "lat": 34.0, "lng": -118.0,
             "name": "California lot", "props": {}},
        ], "2026-09-14T02:30:00Z")

        async with _client(app_ctx["app"]) as c:
            r = await c.get("/api/map/poi-set?type=truck_parking",
                            headers=_h(app_ctx["owner_a_token"]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["version"] == "2026-09-14T02:30:00Z"
        # WHOLE: both points, though they are two thousand miles apart —
        # no viewport narrowed this.
        assert len(body["features"]) == 2
        names = {f["properties"]["name"] for f in body["features"]}
        assert names == {"Iowa lot", "California lot"}

    async def test_both_endpoints_describe_a_point_the_same_way(self, app_ctx):
        """One wire shape, two ways in.

        A client that drew one shape for a held layer and another for a
        fetched one would have a bug nobody could see until a popup came
        up empty — which is why point_to_feature is the only place that
        says what a POI looks like.
        """
        db = app_ctx["db"]
        await self._import(db, "weigh_station", [
            {"osm_type": "node", "osm_id": 20, "lat": 41.5, "lng": -93.5,
             "name": "Scale", "props": {"amenity": "weighbridge"}},
        ], "2026-09-14T02:40:00Z")

        async with _client(app_ctx["app"]) as c:
            whole = await c.get("/api/map/poi-set?type=weigh_station",
                                headers=_h(app_ctx["owner_a_token"]))
            boxed = await c.get(
                "/api/map/pois?type=weigh_station&bbox=41.0,-94.0,42.0,-93.0",
                headers=_h(app_ctx["owner_a_token"]))
        assert whole.status_code == 200 and boxed.status_code == 200
        assert whole.json()["features"] == boxed.json()["features"]

    async def test_a_layer_with_no_import_is_refused_not_answered_empty(self, app_ctx):
        """The whole point.  An empty set here would be cached by the
        client and believed — "there are no rest areas in America" — and
        it would keep believing it until the next version changed."""
        async with _client(app_ctx["app"]) as c:
            r = await c.get("/api/map/poi-set?type=rest_area",
                            headers=_h(app_ctx["owner_a_token"]))
        assert r.status_code == 409, r.text
        assert "not been imported" in r.json()["detail"]

    async def test_an_unknown_layer_is_a_404(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            r = await c.get("/api/map/poi-set?type=not_a_layer",
                            headers=_h(app_ctx["owner_a_token"]))
        assert r.status_code == 404

    async def test_a_driver_may_hold_a_layer_but_not_author_one(self, app_ctx):
        """Both new routes ride can_view_poi, the same grant that has
        always shown the overlays — holding a copy is still seeing."""
        db = app_ctx["db"]
        await self._import(db, "def_station", [
            {"osm_type": "node", "osm_id": 30, "lat": 41.0, "lng": -93.0,
             "name": "DEF", "props": {}},
        ], "2026-09-14T02:50:00Z")
        async with _client(app_ctx["app"]) as c:
            for url in ("/api/map/poi-versions", "/api/map/poi-set?type=def_station"):
                r = await c.get(url, headers=_h(app_ctx["driver_a_token"]))
                assert r.status_code == 200, (url, r.text)


# ---------------------------------------------------------------------------
# Served from our own table
# ---------------------------------------------------------------------------

class TestServedFromOurTable:
    """A truck stop does not move, so the map stopped asking for one.

    Measured 2026-09-13: all three reachable Overpass mirrors refused a
    one-node query inside a single hour, and a built-in layer's whole
    answer hung on one of them replying in seconds.  Once a layer has
    been imported it is read from an indexed table instead, and the
    mirror is not in the request at all — which is what the first test
    here actually proves, by making the mirror raise.
    """

    async def test_an_imported_layer_is_served_without_touching_the_mirror(self, app_ctx):
        db = app_ctx["db"]
        await db.upsert_poi_points("fuel_station", [
            {"osm_type": "node", "osm_id": 1, "lat": 41.85, "lng": -87.65,
             "name": "Pilot", "props": {"amenity": "fuel", "brand": "Pilot"}},
            {"osm_type": "node", "osm_id": 2, "lat": 34.05, "lng": -118.24,
             "name": "Love's", "props": {"amenity": "fuel"}},
        ], "2026-09-13T03:40:00Z")
        await db.finish_poi_import("fuel_station", "2026-09-13T03:40:00Z", 2, ok=True)

        async def _must_not_be_called(*_a, **_k):
            raise AssertionError("the mirror was asked for an imported layer")

        with patch("features.live_map.poi.overpass._fetch_overpass",
                   new=_must_not_be_called):
            async with _client(app_ctx["app"]) as c:
                r = await c.get(
                    "/api/map/pois?type=fuel_station&bbox=41.0,-88.0,42.0,-87.0",
                    headers=_h(app_ctx["owner_a_token"]),
                )
        assert r.status_code == 200, r.text
        body = r.json()
        # Only what is in the viewport — the Los Angeles row stays out.
        assert len(body["features"]) == 1, body
        f = body["features"][0]
        assert f["properties"]["name"] == "Pilot"
        assert f["properties"]["brand"] == "Pilot"
        assert f["geometry"]["coordinates"] == [-87.65, 41.85]
        # And the freshness the panels show is OURS now, not a mirror's.
        assert body["source_as_of"] == "2026-09-13T03:40:00Z"

    async def test_a_layer_never_imported_still_asks_the_mirror(self, app_ctx):
        """The whole migration in one test: nothing breaks before the
        first import, and the old path is still there until every layer
        has had one."""
        asked = []

        async def _fake(parts, bbox):
            asked.append(bbox)
            return []

        with patch("features.live_map.poi.overpass._fetch_overpass", new=_fake):
            async with _client(app_ctx["app"]) as c:
                r = await c.get(
                    "/api/map/pois?type=shower&bbox=41.0,-88.0,42.0,-87.0",
                    headers=_h(app_ctx["owner_a_token"]),
                )
        assert r.status_code == 200, r.text
        assert asked, "a layer with no import did not fall through to the mirror"

    async def test_a_failed_import_does_not_switch_the_layer_over(self, app_ctx):
        """`ok=False` leaves no date, and no date means the table is not
        the source yet — otherwise a layer whose first import died would
        be served from an empty table and read as "none in this view"."""
        db = app_ctx["db"]
        await db.finish_poi_import(
            "rest_area", "2026-09-13T03:40:00Z", 0, ok=False, note="Overpass 504")

        asked = []

        async def _fake(parts, bbox):
            asked.append(bbox)
            return []

        with patch("features.live_map.poi.overpass._fetch_overpass", new=_fake):
            async with _client(app_ctx["app"]) as c:
                r = await c.get(
                    "/api/map/pois?type=rest_area&bbox=41.0,-88.0,42.0,-87.0",
                    headers=_h(app_ctx["owner_a_token"]),
                )
        assert r.status_code == 200, r.text
        assert asked, "an empty table was used as the source after a failed import"


# ---------------------------------------------------------------------------
# A source that did not answer
# ---------------------------------------------------------------------------

class TestCustomLayerSourceFailure:
    """The fault this codebase has now shipped four times: a refusal
    arriving as an empty answer.

    The built-in layers were fixed after the owner opened Chicago and
    read "None in this view".  The custom branch kept the old shape —
    `except Exception: features = []` and then a five-minute cache write,
    so an account's OWN layer went blank during an outage and STAYED
    blank for five minutes after it ended.
    """

    async def test_a_csv_layer_does_not_borrow_openstreetmaps_date(self, app_ctx):
        """The panels show "OpenStreetMap · N old" from `source_as_of`.

        A CSV layer is the account's own file and has nothing to do with
        OSM's extract date; the repair-shop directory is our database.
        Stamping OSM's date on either would be a wrong fact rendered
        confidently — omitted is the honest answer, which is the rule the
        rest of this codebase already follows for counts.
        """
        async with _client(app_ctx["app"]) as c:
            r = await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["owner_a_token"]),
                json={
                    "label": "Yards", "color": "#10b981", "icon": "🅿️",
                    "source_type": "csv", "default_on": False,
                },
            )
            lid = r.json()["id"]
            await c.post(
                f"/api/map/custom-layers/{lid}/csv",
                headers=_h(app_ctx["owner_a_token"]),
                json={"csv": "name,lat,lng\nYard 1,41.5,-93.6\n"},
            )
            got = await c.get(
                f"/api/map/pois?type=custom_{lid}&bbox=41.0,-94.0,42.0,-93.0",
                headers=_h(app_ctx["owner_a_token"]),
            )
            assert got.status_code == 200, got.text
            body = got.json()
            assert len(body["features"]) == 1, body
            assert "source_as_of" not in body, (
                "a CSV layer is being stamped with OpenStreetMap's extract date")

    async def test_a_layer_whose_source_refused_is_a_502_and_is_not_cached(self, app_ctx):
        async with _client(app_ctx["app"]) as c:
            r = await c.post(
                "/api/map/custom-layers",
                headers=_h(app_ctx["owner_a_token"]),
                json={
                    "label": "Truck Washes", "color": "#7c3aed", "icon": "🚿",
                    "source_type": "overpass",
                    "overpass_query": 'node["amenity"="car_wash"]["hgv"="yes"]',
                    "default_on": False,
                },
            )
            assert r.status_code == 201, r.text
            lid = r.json()["id"]

            calls = 0

            async def _refuse(_parts, _bbox):
                nonlocal calls
                calls += 1
                raise RuntimeError("Overpass: the server is probably too busy")

            # Patched on the MODULE, which is the only reason this reaches
            # the call site — see test_poi_module_seams.py.
            with patch("features.live_map.poi.overpass._fetch_overpass", new=_refuse):
                url = f"/api/map/pois?type=custom_{lid}&bbox=41.0,-88.0,42.0,-87.0"
                first = await c.get(url, headers=_h(app_ctx["owner_a_token"]))
                assert first.status_code == 502, first.text
                assert "not answering" in first.json()["detail"]

                # And the failure is not remembered: the next request asks
                # again rather than serving five more minutes of it.
                second = await c.get(url, headers=_h(app_ctx["owner_a_token"]))
                assert second.status_code == 502, second.text

            assert calls == 2, f"the second request was served from cache ({calls} fetch)"


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
        from features.live_map.poi.viewport import _clip_bbox_to_usa
        # Border viewport (Detroit↔Windsor): bbox extends north into Canada.
        s, w, n, e = 41.5, -83.5, 43.5, -82.0
        clipped = _clip_bbox_to_usa(s, w, n, e)
        assert clipped is not None
        cs, cw, cn, ce = clipped
        assert cs == 41.5
        assert cw == -83.5
        assert cn == 43.5  # within CONUS top
        assert ce == -82.0
