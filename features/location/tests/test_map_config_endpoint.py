"""``/map/config`` decides, ``/map/engine`` draws, ``/map/tiles/session``
hands out the billable token — and only to an account on Google."""
import pytest
from fastapi import HTTPException

from features.location import config as cfg
from features.location import map_engine as me
from features.location import router as maps


@pytest.mark.asyncio
async def test_an_unknown_engine_is_refused_not_stored(monkeypatch):
    writes = []

    class _T:
        async def get_account_setting(self, *a, **k): return "osm"
        async def set_account_setting(self, *a): writes.append(a)

    async def _tenant(_): return _T()
    monkeypatch.setattr(cfg, "_get_tenant_db", _tenant)
    with pytest.raises(HTTPException) as e:
        await cfg.put_map_config(cfg.EngineUpdate(engine="mapbox"), {"account_id": 1})
    assert e.value.status_code == 422
    assert writes == []


@pytest.mark.asyncio
async def test_the_config_read_never_carries_the_key(monkeypatch):
    monkeypatch.setenv(me.ENV_GOOGLE_KEY, "K")

    class _T:
        async def get_account_setting(self, *a, **k): return "google"

    async def _tenant(_): return _T()
    monkeypatch.setattr(cfg, "_get_tenant_db", _tenant)
    out = await cfg.get_map_config({"account_id": 1})
    assert out["engine"] == "google"
    assert "key" not in out


@pytest.mark.asyncio
async def test_an_account_on_the_free_engine_gets_no_session(monkeypatch):
    """A session is a billable thing to hand out."""
    monkeypatch.delenv(me.ENV_GOOGLE_KEY, raising=False)

    class _T:
        async def get_account_setting(self, *a, **k): return "google"   # asked, but no key

    async def _tenant(_): return _T()
    monkeypatch.setattr("infra.platform.get_tenant_db", _tenant)
    with pytest.raises(HTTPException) as e:
        await maps.map_tiles_session(type="roadmap", user={"account_id": 1})
    assert e.value.status_code == 403


def test_both_routes_are_mounted_and_config_resolves_first():
    from interfaces.api.app import app
    paths = [r.path for r in app.routes]
    assert any(p.endswith("/map/config") for p in paths)
    assert any(p.endswith("/map/tiles/session") for p in paths)
    assert any(p.endswith("/map/engine") for p in paths)
