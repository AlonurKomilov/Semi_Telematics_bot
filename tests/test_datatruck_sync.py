"""Datatruck sync engine — normalizers, pagination, runner, route.

No live network: the client's ``_get``/``_get_url`` are stubbed with
canned page envelopes; the engine tests stub the platform/tenant DBs
and the provider resolver.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.telematics.datatruck.client import DatatruckClient
from capabilities.integrations.datatruck.sync import (
    RESOURCES,
    _norm_driver,
    _norm_order,
    _norm_truck,
    _norm_work_order,
)


# ── Normalizers ───────────────────────────────────────────────


def test_norm_driver_snake_case_and_fallbacks():
    rec = {
        "id": 8, "first_name": "Claude", "last_name": "Safari",
        "phone": "+1 555 0100", "email": "c@x.com",
        "status": {"id": 1, "name": "active"},
        "cdl_class": "A",  # un-promoted — must survive in payload
    }
    out = _norm_driver(rec)
    assert out["external_id"] == "8"
    assert out["display_name"] == "Claude Safari"
    assert out["status"] == "active"  # nested dict → name
    assert out["payload"]["cdl_class"] == "A"


def test_norm_driver_camel_case_fallbacks():
    out = _norm_driver({
        "id": "drv_9", "firstName": "Ana", "lastName": "Gomez",
        "phoneNumber": "+1 555 0101",
    })
    assert out["first_name"] == "Ana"
    assert out["phone"] == "+1 555 0101"
    assert out["display_name"] == "Ana Gomez"


def test_norm_truck_matches_live_probe_shape():
    # Field names confirmed against a live tenant probe (2026-06-10).
    rec = {
        "id": 443, "unit_number": "247", "plate_number": "PXF8448",
        "vin": "3AKJJHDR7VSXC469", "year": "2027",
        "odometer": "558396.9",
        "operator": {"id": 12, "name": "ELIERE KALIHUNGU"},
    }
    out = _norm_truck(rec)
    assert out["external_id"] == "443"
    assert out["unit_number"] == "247"
    assert out["year"] == 2027          # str → int coercion
    assert out["odometer"] == pytest.approx(558396.9)
    assert out["operator_name"] == "ELIERE KALIHUNGU"


def test_norm_order_nested_locations_and_ids():
    rec = {
        "id": "ord_1", "status": "delivered",
        "origin": {"city": "Columbus", "state": "OH"},
        "destination": "Chicago, IL",
        "driver": {"id": 8, "name": "X"},
        "truck": 443,
        "total_rate": "2150.00",
    }
    out = _norm_order(rec)
    assert out["origin"] == "Columbus"          # dict → city
    assert out["destination"] == "Chicago, IL"  # scalar passthrough
    assert out["driver_external_id"] == "8"     # nested → id
    assert out["truck_external_id"] == "443"    # scalar id
    assert out["total_rate"] == pytest.approx(2150.0)
    assert out["order_number"] == "ord_1"       # falls back to id


def test_norm_work_order_vehicle_unit_extraction():
    out = _norm_work_order({
        "id": 3, "status": "open",
        "truck": {"id": 443, "unit_number": "247"},
        "total_cost": 480.25,
    })
    assert out["vehicle_unit"] == "247"
    assert out["number"] == "3"  # falls back to id


def test_every_resource_normalizer_survives_empty_record():
    """A degenerate upstream record must normalize without raising —
    external_id ends up '' and the upsert layer skips it."""
    for spec in RESOURCES.values():
        out = spec.normalize({})
        assert out["external_id"] == ""
        assert out["payload"] == {}


# ── Client pagination ─────────────────────────────────────────


def _page(base: str, *, count: int, results: list, next_page: int | None):
    return {
        "count": count,
        "next": f"{base}drivers/list/?page={next_page}" if next_page else None,
        "previous": None,
        "results": results,
    }


@pytest.mark.asyncio
async def test_iter_pages_follows_next_until_exhausted():
    c = DatatruckClient(company_subdomain="premier", api_token="t")
    base = c.base_url
    pages = [
        _page(base, count=25, results=[{"id": i} for i in range(10)], next_page=2),
        _page(base, count=25, results=[{"id": i} for i in range(10, 20)], next_page=3),
        _page(base, count=25, results=[{"id": i} for i in range(20, 25)], next_page=None),
    ]
    calls: list[str] = []

    async def fake_get(path, params=None):
        calls.append(f"path:{path}")
        return 200, pages[0]

    async def fake_get_url(url, params=None):
        calls.append(f"url:{url}")
        n = int(url.rsplit("page=", 1)[1])
        return 200, pages[n - 1]

    c._get = fake_get          # type: ignore[method-assign]
    c._get_url = fake_get_url  # type: ignore[method-assign]

    seen = [p async for p in c.iter_pages("drivers/list/")]
    assert len(seen) == 3
    assert sum(len(p["results"]) for p in seen) == 25
    # First page via path, rest via absolute next links.
    assert calls[0].startswith("path:")
    assert all(x.startswith("url:") for x in calls[1:])


@pytest.mark.asyncio
async def test_iter_pages_respects_max_pages_cap():
    c = DatatruckClient(company_subdomain="premier", api_token="t")
    base = c.base_url

    async def fake_get(path, params=None):
        return 200, _page(base, count=10_000, results=[{"id": 1}], next_page=2)

    async def fake_get_url(url, params=None):
        n = int(url.rsplit("page=", 1)[1])
        return 200, _page(base, count=10_000, results=[{"id": n}], next_page=n + 1)

    c._get = fake_get          # type: ignore[method-assign]
    c._get_url = fake_get_url  # type: ignore[method-assign]

    seen = [p async for p in c.iter_pages("orders/", max_pages=5)]
    assert len(seen) == 5  # capped, not 1000+


@pytest.mark.asyncio
async def test_iter_pages_raises_on_http_error():
    c = DatatruckClient(company_subdomain="premier", api_token="t")

    async def fake_get(path, params=None):
        return 500, "<upstream broke>"

    c._get = fake_get  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="HTTP 500"):
        async for _ in c.iter_pages("drivers/list/"):
            pass


@pytest.mark.asyncio
async def test_get_url_refuses_foreign_hosts():
    """The ``next`` link follower must never leave the tenant host —
    a poisoned pagination envelope can't redirect our authed session
    elsewhere."""
    c = DatatruckClient(company_subdomain="premier", api_token="t")
    with pytest.raises(ValueError, match="refusing non-tenant URL"):
        await c._get_url("https://evil.example.com/api/v1/openapi/x/")


# ── Engine ────────────────────────────────────────────────────


def _integration_stub(*, status="connected", enabled=True):
    ai = MagicMock()
    ai.status = status
    ai.feature_toggles = {
        spec.capability: {"enabled": enabled} for spec in RESOURCES.values()
    }
    return ai


@pytest.mark.asyncio
async def test_sync_resource_happy_path(monkeypatch):
    from capabilities.integrations.datatruck import sync as sync_mod

    db = MagicMock()
    db.get_account_integration = AsyncMock(return_value=_integration_stub())
    monkeypatch.setattr(sync_mod, "get_platform_db", lambda: db)

    tenant = MagicMock()
    tenant.upsert_datatruck_drivers = AsyncMock(side_effect=lambda a, rows: len(rows))
    monkeypatch.setattr(
        sync_mod, "get_tenant_db", AsyncMock(return_value=tenant),
    )

    base = "https://premier.datatruck.io/api/v1/openapi/"

    async def fake_iter_pages(path, params=None, *, max_pages=50):
        yield _page(base, count=12, results=[{"id": i} for i in range(10)], next_page=2)
        yield _page(base, count=12, results=[{"id": i} for i in range(10, 12)], next_page=None)

    client = MagicMock()
    client.iter_pages = fake_iter_pages
    provider = MagicMock()
    provider.client = client
    monkeypatch.setattr(
        sync_mod, "get_telematics_client", AsyncMock(return_value=provider),
    )

    published: list[dict] = []

    async def fake_publish(account_id, resource, payload):
        published.append(dict(payload))

    monkeypatch.setattr(sync_mod, "_publish", fake_publish)

    status = await sync_mod.sync_resource(42, "drivers", triggered_by=7)
    assert status["state"] == "completed"
    assert status["pages_done"] == 2
    assert status["records_written"] == 12
    assert status["total_upstream"] == 12
    assert status["finished_at"]
    # running → per-page heartbeats → final completed
    assert published[0]["state"] == "running"
    assert published[-1]["state"] == "completed"


@pytest.mark.asyncio
async def test_sync_resource_refuses_disabled_toggle(monkeypatch):
    from capabilities.integrations.datatruck import sync as sync_mod

    db = MagicMock()
    db.get_account_integration = AsyncMock(
        return_value=_integration_stub(enabled=False),
    )
    monkeypatch.setattr(sync_mod, "get_platform_db", lambda: db)
    monkeypatch.setattr(sync_mod, "_publish", AsyncMock())

    status = await sync_mod.sync_resource(42, "drivers")
    assert status["state"] == "failed"
    assert "toggle is disabled" in status["error"]


@pytest.mark.asyncio
async def test_sync_resource_refuses_disconnected_integration(monkeypatch):
    from capabilities.integrations.datatruck import sync as sync_mod

    db = MagicMock()
    db.get_account_integration = AsyncMock(return_value=None)
    monkeypatch.setattr(sync_mod, "get_platform_db", lambda: db)
    monkeypatch.setattr(sync_mod, "_publish", AsyncMock())

    status = await sync_mod.sync_resource(42, "drivers")
    assert status["state"] == "failed"
    assert "not connected" in status["error"]


@pytest.mark.asyncio
async def test_sync_resource_unknown_resource_raises():
    from capabilities.integrations.datatruck.sync import sync_resource
    with pytest.raises(ValueError, match="unknown datatruck resource"):
        await sync_resource(42, "nonexistent")


# ── Route preflight ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_sync_unknown_resource_404(monkeypatch):
    from capabilities.integrations.datatruck import router as router_mod
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await router_mod.trigger_sync(
            "nonsense", user={"account_id": 42, "id": 1},
        )
    assert exc.value.status_code == 404
    assert "unknown resource" in exc.value.detail


@pytest.mark.asyncio
async def test_trigger_sync_refuses_duplicate_run(monkeypatch):
    from capabilities.integrations.datatruck import router as router_mod
    from fastapi import HTTPException

    db = MagicMock()
    db.get_account_integration = AsyncMock(return_value=_integration_stub())
    monkeypatch.setattr(router_mod, "get_platform_db", lambda: db)
    monkeypatch.setattr(
        router_mod, "get_sync_status",
        AsyncMock(return_value={"state": "running"}),
    )

    with pytest.raises(HTTPException) as exc:
        await router_mod.trigger_sync(
            "drivers", user={"account_id": 42, "id": 1},
        )
    assert exc.value.status_code == 409
    assert "already running" in exc.value.detail


@pytest.mark.asyncio
async def test_trigger_sync_queues_and_spawns(monkeypatch):
    from capabilities.integrations.datatruck import router as router_mod

    db = MagicMock()
    db.get_account_integration = AsyncMock(return_value=_integration_stub())
    monkeypatch.setattr(router_mod, "get_platform_db", lambda: db)
    monkeypatch.setattr(
        router_mod, "get_sync_status", AsyncMock(return_value=None),
    )
    monkeypatch.setattr(router_mod, "audit", AsyncMock())

    spawned: list = []
    monkeypatch.setattr(
        router_mod, "spawn_background",
        lambda coro: spawned.append(coro) or coro.close(),
    )

    out = await router_mod.trigger_sync(
        "trucks", user={"account_id": 42, "id": 1},
    )
    assert out["state"] == "queued"
    assert out["resource"] == "trucks"
    assert len(spawned) == 1
