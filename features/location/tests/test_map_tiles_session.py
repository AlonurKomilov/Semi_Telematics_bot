"""The platform's Google Map Tiles sessions — one per map type, shared
by every browser, renewed before Google would let them die.

A session is what every tile request must carry.  Creating one is
free of quota and lasts two weeks, and Google says it "can be used
across multiple clients" — so the platform opens one per map type and
hands it out, rather than letting every browser open its own.  These
pin the cache: it does not re-create while fresh, it renews a day
early, it serialises creation per type, and terrain carries the road
layer Google requires of it.
"""
import asyncio

import pytest

from features.location import map_engine as me


@pytest.fixture(autouse=True)
def _clean():
    me.reset_sessions_for_tests()
    yield
    me.reset_sessions_for_tests()


def _fake_creator(calls: list, expiry_in: float = 14 * 86400):
    async def _create(map_type, key):
        calls.append((map_type, key))
        return {"session": f"tok-{map_type}-{len(calls)}", "expiry": int(1_000_000 + expiry_in),
                "tile_size": 256, "image_format": "png"}
    return _create


@pytest.mark.asyncio
async def test_a_fresh_session_is_reused_not_recreated(monkeypatch):
    calls = []
    monkeypatch.setattr(me, "_create_session", _fake_creator(calls))
    a = await me.tile_session("roadmap", "K", now=1_000_000)
    b = await me.tile_session("roadmap", "K", now=1_000_000 + 3600)
    assert a["session"] == b["session"]
    assert calls == [("roadmap", "K")]


@pytest.mark.asyncio
async def test_each_map_type_has_its_own_session(monkeypatch):
    calls = []
    monkeypatch.setattr(me, "_create_session", _fake_creator(calls))
    r = await me.tile_session("roadmap", "K", now=1_000_000)
    s = await me.tile_session("satellite", "K", now=1_000_000)
    assert r["session"] != s["session"]
    assert [c[0] for c in calls] == ["roadmap", "satellite"]


@pytest.mark.asyncio
async def test_a_session_is_renewed_a_day_before_it_expires(monkeypatch):
    """A browser handed a token an hour ago must never watch it die
    mid-view — so the platform stops handing out a token that has less
    than a day left."""
    calls = []
    monkeypatch.setattr(me, "_create_session", _fake_creator(calls, expiry_in=14 * 86400))
    first = await me.tile_session("roadmap", "K", now=1_000_000)
    # 13 days and 1 hour later: under a day left → renewed
    second = await me.tile_session("roadmap", "K", now=1_000_000 + 13 * 86400 + 3600)
    assert second["session"] != first["session"]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_concurrent_askers_share_one_creation(monkeypatch):
    """Twenty browsers opening the map at once must not open twenty
    sessions at Google."""
    calls = []
    slow = _fake_creator(calls)

    async def _create(map_type, key):
        await asyncio.sleep(0.01)
        return await slow(map_type, key)

    monkeypatch.setattr(me, "_create_session", _create)
    results = await asyncio.gather(*(me.tile_session("roadmap", "K", now=1_000_000) for _ in range(20)))
    assert len({r["session"] for r in results}) == 1
    assert len(calls) == 1


def test_terrain_carries_the_road_layer_google_requires():
    assert me.TILE_TYPES["terrain"]["layerTypes"] == ["layerRoadmap"]
    assert "layerTypes" not in me.TILE_TYPES["roadmap"]


@pytest.mark.asyncio
async def test_no_key_means_no_session_not_a_google_call(monkeypatch):
    calls = []
    monkeypatch.setattr(me, "_create_session", _fake_creator(calls))
    with pytest.raises(me.TileSessionError):
        await me.tile_session("roadmap", "")
    assert calls == []


@pytest.mark.asyncio
async def test_an_unknown_type_is_refused_before_anything_is_asked(monkeypatch):
    calls = []
    monkeypatch.setattr(me, "_create_session", _fake_creator(calls))
    with pytest.raises(ValueError):
        await me.tile_session("streetview", "K")
    assert calls == []


def test_the_wire_shape_is_what_leaflet_expands():
    entry = {"session": "tok", "expiry": 123, "tile_size": 256, "image_format": "png"}
    w = me.tile_wire("roadmap", entry, "K")
    assert w["tile_url"] == "https://tile.googleapis.com/v1/2dtiles/{z}/{x}/{y}?session=tok&key=K"
    assert w["viewport_url"].startswith("https://tile.googleapis.com/tile/v1/viewport?session=tok&key=K")
    assert w["tile_size"] == 256 and w["type"] == "roadmap"


class _Tenant:
    def __init__(self, value="osm"):
        self.value = value
        self.writes = []

    async def get_account_setting(self, account_id, key, default=""):
        return self.value

    async def set_account_setting(self, account_id, key, value):
        self.writes.append((key, value)); self.value = value


@pytest.mark.asyncio
async def test_set_engine_normalises_before_it_stores(monkeypatch):
    monkeypatch.setenv(me.ENV_GOOGLE_KEY, "K")
    t = _Tenant()
    out = await me.set_engine(1, t, "  GOOGLE ")
    assert t.writes == [(me.MAP_ENGINE_KEY, "google")]
    assert out["engine"] == "google"
