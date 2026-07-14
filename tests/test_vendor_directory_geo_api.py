"""Operator API tests for vendor-directory geo (C2 map prerequisite).

Surface covered:
  GET /system/vendor-directory/geocode      — Nominatim proxy (stubbed)
  PUT /system/vendor-directory/{id}/geo     — pin confirm / clear

Auth is ``require_system_owner``; the Nominatim hop is monkeypatched so
no test ever touches the network.
"""

from __future__ import annotations

import os
import sys
import uuid

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def geo_app(pg_db, monkeypatch):
    db = pg_db
    acct = await db.create_account("Geo Operator Co")
    op = await db.create_user(910001, acct.id, role=Role.OWNER)
    non_op = await db.create_user(910002, acct.id, role=Role.OWNER)

    import capabilities.permissions.roles as perms
    monkeypatch.setattr(perms, "SYSTEM_OWNER_IDS", {op.telegram_id})

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = db

    # Isolate the module-global geocode cache between test runs.
    import capabilities.platform.vendor_directory.router as vd_router
    vd_router._geocode_cache.clear()

    from interfaces.api.app import create_api
    app = create_api()

    # UNIQUE name per test: create_directory_entry dedups on the global
    # name_key, so a shared name would converge concurrent tests onto
    # ONE row — their geo writes would race each other's asserts.
    entry = await db.create_directory_entry(
        f"Geo API Shop {uuid.uuid4().hex[:8]}", status="active",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {
            "client": client,
            "db": db,
            "entry": entry,
            "op_token": create_jwt(op.telegram_id, acct.id, "owner"),
            "non_op_token": create_jwt(non_op.telegram_id, acct.id, "owner"),
            "router_mod": vd_router,
            "monkeypatch": monkeypatch,
        }

    _cp._db = _old


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_geo_set_and_clear(geo_app):
    s = geo_app
    eid = s["entry"]["id"]

    r = await s["client"].put(
        f"/api/system/vendor-directory/{eid}/geo",
        json={"lat": 41.85, "lng": -87.65},
        headers=_hdr(s["op_token"]),
    )
    assert r.status_code == 200
    row = await s["db"].get_directory_entry(eid)
    assert row["lat"] == pytest.approx(41.85)
    assert row["lng"] == pytest.approx(-87.65)

    r = await s["client"].put(
        f"/api/system/vendor-directory/{eid}/geo",
        json={"lat": None, "lng": None},
        headers=_hdr(s["op_token"]),
    )
    assert r.status_code == 200
    row = await s["db"].get_directory_entry(eid)
    assert row["lat"] is None and row["lng"] is None


@pytest.mark.asyncio
async def test_geo_partial_pair_and_range_rejected(geo_app):
    s = geo_app
    eid = s["entry"]["id"]
    r = await s["client"].put(
        f"/api/system/vendor-directory/{eid}/geo",
        json={"lat": 41.85, "lng": None},
        headers=_hdr(s["op_token"]),
    )
    assert r.status_code == 422
    # Out-of-range fails Pydantic validation before the handler runs.
    r = await s["client"].put(
        f"/api/system/vendor-directory/{eid}/geo",
        json={"lat": 91.0, "lng": 0.0},
        headers=_hdr(s["op_token"]),
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_geo_unknown_entry_404(geo_app):
    s = geo_app
    r = await s["client"].put(
        "/api/system/vendor-directory/999999/geo",
        json={"lat": 1.0, "lng": 1.0},
        headers=_hdr(s["op_token"]),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_geo_operator_gate(geo_app):
    s = geo_app
    eid = s["entry"]["id"]
    r = await s["client"].put(
        f"/api/system/vendor-directory/{eid}/geo",
        json={"lat": 1.0, "lng": 1.0},
        headers=_hdr(s["non_op_token"]),
    )
    assert r.status_code == 403
    r = await s["client"].get(
        "/api/system/vendor-directory/geocode", params={"q": "1 Main St"},
        headers=_hdr(s["non_op_token"]),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_geocode_proxies_and_caches(geo_app):
    s = geo_app
    calls: list[str] = []

    async def fake_search(q: str) -> list[dict]:
        calls.append(q)
        return [{"lat": 41.85, "lng": -87.65, "label": "1 Main St, Springfield, IL"}]

    s["monkeypatch"].setattr(s["router_mod"], "_nominatim_search", fake_search)

    r = await s["client"].get(
        "/api/system/vendor-directory/geocode",
        params={"q": "1 Main St Springfield"},
        headers=_hdr(s["op_token"]),
    )
    assert r.status_code == 200
    assert r.json()["results"][0]["lat"] == pytest.approx(41.85)

    # Same query again → served from cache, no second upstream call.
    r = await s["client"].get(
        "/api/system/vendor-directory/geocode",
        params={"q": "1 Main St Springfield"},
        headers=_hdr(s["op_token"]),
    )
    assert r.status_code == 200
    assert calls == ["1 Main St Springfield"]


@pytest.mark.asyncio
async def test_geocode_upstream_failure_is_502(geo_app):
    s = geo_app

    async def broken(q: str) -> list[dict]:
        raise RuntimeError("nominatim down")

    s["monkeypatch"].setattr(s["router_mod"], "_nominatim_search", broken)
    r = await s["client"].get(
        "/api/system/vendor-directory/geocode", params={"q": "somewhere USA"},
        headers=_hdr(s["op_token"]),
    )
    assert r.status_code == 502


@pytest.mark.asyncio
async def test_map_pois_vendor_directory_layer(geo_app):
    """End-to-end: geocode-confirmed ACTIVE entries surface as GeoJSON
    on /map/pois?type=vendor_directory for any user with map access —
    identity fields only."""
    s = geo_app
    eid = s["entry"]["id"]
    shop = s["entry"]["name"]
    r = await s["client"].put(
        f"/api/system/vendor-directory/{eid}/geo",
        json={"lat": 41.85, "lng": -87.65},
        headers=_hdr(s["op_token"]),
    )
    assert r.status_code == 200

    # Bust the shared POI cache so this test never sees another test's
    # cached empty FeatureCollection for the same rounded bbox.
    import features.location.pois as pois_mod
    pois_mod._poi_cache.clear()

    # A regular (non-operator) owner reads the layer through the map API.
    r = await s["client"].get(
        "/api/map/pois",
        params={"type": "vendor_directory", "bbox": "41.0,-88.0,42.0,-87.0"},
        headers=_hdr(s["non_op_token"]),
    )
    assert r.status_code == 200
    # Filter to THIS test's shop — never assert global feature counts
    # (concurrent xdist workers may surface their own entries).
    mine = [f for f in r.json()["features"]
            if f["properties"]["name"] == shop]
    assert len(mine) == 1
    props = mine[0]["properties"]
    assert props["name"] == shop
    assert props["_directory"] is True
    lng_, lat_ = mine[0]["geometry"]["coordinates"]
    assert lng_ == pytest.approx(-87.65)
    assert lat_ == pytest.approx(41.85)
    # Identity-only: no audit/account fields in the payload.
    assert "suggested_by_account" not in props
    assert "status" not in props

    # Clearing the pin removes the shop from the layer.
    r = await s["client"].put(
        f"/api/system/vendor-directory/{eid}/geo",
        json={"lat": None, "lng": None},
        headers=_hdr(s["op_token"]),
    )
    assert r.status_code == 200
    pois_mod._poi_cache.clear()
    r = await s["client"].get(
        "/api/map/pois",
        params={"type": "vendor_directory", "bbox": "41.0,-88.0,42.0,-87.0"},
        headers=_hdr(s["non_op_token"]),
    )
    assert r.status_code == 200
    assert all(f["properties"]["name"] != shop
               for f in r.json()["features"])
