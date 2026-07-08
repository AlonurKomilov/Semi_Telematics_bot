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
    _order_params,
    _work_order_params,
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


def test_norm_driver_account_nested_shape():
    # The real drivers-list endpoint (apidocs get_driver_list) nests the
    # person under ``account`` and puts the phone in ``contact_number`` —
    # top-level name keys are absent entirely.
    out = _norm_driver({
        "id": 194,
        "account": {"first_name": "Jean", "last_name": "Bosco",
                    "full_name": "Jean Bosco", "email": "jb@x.com"},
        "contact_number": "+1 555 0102",
        "status": "active",
        "assigned_truck": {"id": 7, "unit_number": "T-12"},
    })
    assert out["display_name"] == "Jean Bosco"
    assert out["first_name"] == "Jean" and out["last_name"] == "Bosco"
    assert out["email"] == "jb@x.com"
    assert out["phone"] == "+1 555 0102"
    assert out["payload"]["assigned_truck"]["unit_number"] == "T-12"


def test_norm_truck_matches_live_probe_shape():
    # Field names confirmed against a live tenant probe (2026-06-10).
    rec = {
        "id": 443, "unit_number": "247", "plate_number": "ABC1234",
        "vin": "1HGTEST0000000001", "year": "2027",
        "odometer": "558396.9",
        "operator": {"id": 12, "name": "SAM OPERATOR"},
    }
    out = _norm_truck(rec)
    assert out["external_id"] == "443"
    assert out["unit_number"] == "247"
    assert out["year"] == 2027          # str → int coercion
    assert out["odometer"] == pytest.approx(558396.9)
    assert out["operator_name"] == "SAM OPERATOR"


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
    assert out["vehicle_type"] == "truck"   # the WO is on a truck
    assert out["number"] == "3"  # falls back to id


def test_norm_work_order_vendor_string_does_not_echo_name_as_phone():
    # List shape: vendor is a bare name string (no contact dict).  The phone
    # must NOT become the vendor name — it stays blank unless a top-level
    # phone field exists.
    out = _norm_work_order({"id": 7, "vendor": "Roadside Fuel Co", "truck": "247"})
    assert out["vendor_name"] == "Roadside Fuel Co"
    assert out["vendor_phone"] == ""        # was the bug: echoed "Roadside Fuel Co"


def test_norm_work_order_rounds_money_to_cents():
    out = _norm_work_order({
        "id": 8, "total_price": 426.9899, "tax_total_price": 12.3456,
        "work_order_tasks": [
            {"custom_task": "Battery", "parts_price": 132.9900,
             "parts_quantity": 4, "total_price": 531.9600},
        ],
    })
    assert out["total_cost"] == 426.99
    assert out["tax_amount"] == 12.35
    assert out["line_items"][0]["unit_cost"] == 132.99
    assert out["line_items"][0]["total"] == 531.96


def test_norm_work_order_v2_detail_shape():
    """Pin the mapping against the live v2 detail serializer — most
    importantly that ``number`` (WO id) and ``invoice_id`` are distinct,
    the vendor object is unwrapped, and line items are normalized."""
    rec = {
        "id": 1599, "number": "WO-0001", "invoice_id": "INV-1001",
        "status": "completed", "note": "front brake job", "notes": [],
        "payment_type": "efs", "tax_total_price": 92.48,
        "total_price": 1270.44, "odometer": 0.0,
        "scheduled_on_date": "2026-06-09T00:00:00Z", "due_date": "2026-07-09",
        "location": "100 Example Rd, Springfield, IL 62701",
        "truck": {"id": 38, "unit_number": "T100", "plate_number": "ABC1234"},
        "trailer": None,
        "vendor": {"id": 1263, "name": "Roadside Repair Co",
                   "contact_number": "(555) 010-0100",
                   "email": "shop@example.com"},
        "work_order_tasks": [
            {"id": 3699, "task": None, "custom_task": "TIRE DISPOSAL FEE",
             "parts_price": 10.0, "parts_quantity": 2, "labor_price": 0.0,
             "labor_hours": 1, "total_price": 20.0},
        ],
    }
    out = _norm_work_order(rec)
    assert out["external_id"] == "1599"
    assert out["number"] == "WO-0001"
    assert out["invoice_number"] == "INV-1001"     # invoice_id, NOT number
    assert out["vehicle_unit"] == "T100"         # truck.unit_number
    assert out["vendor_name"] == "Roadside Repair Co"
    assert out["vendor_phone"] == "(555) 010-0100"
    assert out["vendor_address"] == "100 Example Rd, Springfield, IL 62701"
    assert out["payment_method"] == "efs"
    assert out["note"] == "front brake job"         # the string, not the [] array
    assert out["tax_amount"] == 92.48
    assert out["total_cost"] == 1270.44
    assert out["opened_at"] == "2026-06-09T00:00:00Z"
    assert len(out["line_items"]) == 1
    item = out["line_items"][0]
    assert item["name"] == "TIRE DISPOSAL FEE"
    assert item["quantity"] == 2.0
    assert item["unit_cost"] == 10.0
    assert item["total"] == 20.0


def test_norm_work_order_trailer_unit():
    """When the WO is on a trailer, the unit comes from ``trailer``."""
    out = _norm_work_order({
        "id": 1601, "truck": None,
        "trailer": {"id": 9, "unit_number": "TR200"},
    })
    assert out["vehicle_unit"] == "TR200"
    assert out["vehicle_type"] == "trailer"   # the WO is on a trailer


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
    c = DatatruckClient(company_subdomain="acme", api_token="t")
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
    c = DatatruckClient(company_subdomain="acme", api_token="t")
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
    c = DatatruckClient(company_subdomain="acme", api_token="t")

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
    c = DatatruckClient(company_subdomain="acme", api_token="t")
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

    base = "https://acme.datatruck.io/api/v1/openapi/"

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


# ── History window (days selector) ────────────────────────────


def test_order_params_window():
    from datetime import datetime, timedelta, timezone
    # No window → no filter, preserving the prior full-pagination path.
    assert _order_params(None) is None
    assert _order_params(0) is None
    today = datetime.now(timezone.utc).date()
    p = _order_params(7)
    assert p["from_date"] == (today - timedelta(days=7)).isoformat()
    assert p["to_date"] == (today + timedelta(days=7)).isoformat()


def test_work_order_params_window_defaults_to_90():
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc).date()
    # No window → the prior 90-day default.
    assert _work_order_params()["from_date"] == (
        today - timedelta(days=90)
    ).isoformat()
    # Chosen window narrows the lookback.
    assert _work_order_params(30)["from_date"] == (
        today - timedelta(days=30)
    ).isoformat()


@pytest.mark.asyncio
async def test_sync_resource_passes_days_window_to_date_ranged(monkeypatch):
    """A chosen ``days`` reaches the orders endpoint as from/to params."""
    from datetime import datetime, timedelta, timezone

    from capabilities.integrations.datatruck import sync as sync_mod

    db = MagicMock()
    db.get_account_integration = AsyncMock(return_value=_integration_stub())
    monkeypatch.setattr(sync_mod, "get_platform_db", lambda: db)

    tenant = MagicMock()
    tenant.upsert_datatruck_orders = AsyncMock(side_effect=lambda a, rows: len(rows))
    monkeypatch.setattr(sync_mod, "get_tenant_db", AsyncMock(return_value=tenant))

    captured: dict = {}
    base = "https://acme.datatruck.io/api/v1/openapi/"

    async def fake_iter_pages(path, params=None, *, max_pages=50):
        captured["params"] = params
        yield _page(base, count=1, results=[{"id": 1}], next_page=None)

    client = MagicMock()
    client.iter_pages = fake_iter_pages
    provider = MagicMock()
    provider.client = client
    monkeypatch.setattr(
        sync_mod, "get_telematics_client", AsyncMock(return_value=provider),
    )
    monkeypatch.setattr(sync_mod, "_publish", AsyncMock())

    status = await sync_mod.sync_resource(42, "orders", days=7)
    assert status["state"] == "completed"
    today = datetime.now(timezone.utc).date()
    assert captured["params"]["from_date"] == (today - timedelta(days=7)).isoformat()
    assert captured["params"]["to_date"] == (today + timedelta(days=7)).isoformat()


@pytest.mark.asyncio
async def test_sync_resource_orders_without_window_sends_no_params(monkeypatch):
    """Default (days=None) keeps the prior no-filter orders behaviour."""
    from capabilities.integrations.datatruck import sync as sync_mod

    db = MagicMock()
    db.get_account_integration = AsyncMock(return_value=_integration_stub())
    monkeypatch.setattr(sync_mod, "get_platform_db", lambda: db)

    tenant = MagicMock()
    tenant.upsert_datatruck_orders = AsyncMock(side_effect=lambda a, rows: len(rows))
    monkeypatch.setattr(sync_mod, "get_tenant_db", AsyncMock(return_value=tenant))

    captured: dict = {"params": "unset"}
    base = "https://acme.datatruck.io/api/v1/openapi/"

    async def fake_iter_pages(path, params=None, *, max_pages=50):
        captured["params"] = params
        yield _page(base, count=1, results=[{"id": 1}], next_page=None)

    client = MagicMock()
    client.iter_pages = fake_iter_pages
    provider = MagicMock()
    provider.client = client
    monkeypatch.setattr(
        sync_mod, "get_telematics_client", AsyncMock(return_value=provider),
    )
    monkeypatch.setattr(sync_mod, "_publish", AsyncMock())

    status = await sync_mod.sync_resource(42, "orders")
    assert status["state"] == "completed"
    assert captured["params"] is None


@pytest.mark.asyncio
async def test_work_orders_sync_does_not_call_v2_detail(monkeypatch):
    """Detail-fetch is OFF: an openapi token gets 401 on the v2 detail
    endpoint, so re-fetching per WO would be ~300 wasted rate-gated calls
    per sync.  The sync must enrich purely from the openapi list and never
    touch ``get_path``."""
    from capabilities.integrations.datatruck import sync as sync_mod

    db = MagicMock()
    db.get_account_integration = AsyncMock(return_value=_integration_stub())
    monkeypatch.setattr(sync_mod, "get_platform_db", lambda: db)

    tenant = MagicMock()
    tenant.upsert_datatruck_work_orders = AsyncMock(side_effect=lambda a, rows: len(rows))
    captured: dict = {}

    async def fake_project(account_id, rows, *, source="datatruck", vehicle_lookup=None):
        captured["rows"] = rows
        return len(rows)

    tenant.project_external_work_orders = fake_project
    monkeypatch.setattr(sync_mod, "get_tenant_db", AsyncMock(return_value=tenant))

    base = "https://acme.datatruck.io/api/v1/openapi/"

    async def fake_iter_pages(path, params=None, *, max_pages=50):
        # The openapi list record (vendor/truck as strings, has tasks).
        yield _page(base, count=1, results=[{
            "id": 1599, "number": "WO-0001", "vendor": "Roadside Repair Co",
            "truck": "T100", "total_price": 124.0, "balance": 0.0,
            "paid_amount": 124.0,
            "work_order_tasks": [
                {"custom_task": "Brake pads", "parts_price": 62.0,
                 "parts_quantity": 2, "total_price": 124.0},
            ],
        }], next_page=None)

    get_path = AsyncMock()  # must NOT be called
    client = MagicMock()
    client.iter_pages = fake_iter_pages
    client.get_path = get_path
    provider = MagicMock()
    provider.client = client
    monkeypatch.setattr(
        sync_mod, "get_telematics_client", AsyncMock(return_value=provider),
    )
    monkeypatch.setattr(sync_mod, "_publish", AsyncMock())

    status = await sync_mod.sync_resource(42, "work_orders")
    assert status["state"] == "completed"
    get_path.assert_not_called()              # no wasted v2 calls
    row = captured["rows"][0]
    assert row["vendor_name"] == "Roadside Repair Co"   # from the list string
    assert row["balance"] == 0.0                        # payment signal carried
    assert row["paid_amount"] == 124.0


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


# ── Preview (plan → apply) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_resource_fetches_diffs_and_caches(monkeypatch):
    from capabilities.integrations.datatruck import sync as sync_mod

    tenant = MagicMock()

    async def fake_plan(account_id, rows, *, vehicle_type):
        return {
            "kind": "vehicles", "vehicle_type": vehicle_type,
            "new": [{"unit": r["unit_number"]} for r in rows],
            "enrich": [], "review": [],
            "counts": {"new": len(rows), "enrich": 0, "review": 0,
                       "unchanged": 0, "total": len(rows)},
        }

    tenant.plan_external_vehicles = fake_plan
    monkeypatch.setattr(sync_mod, "get_tenant_db", AsyncMock(return_value=tenant))

    base = "https://acme.datatruck.io/api/v1/openapi/"

    async def fake_iter_pages(path, params=None, *, max_pages=50):
        yield _page(base, count=2, results=[
            {"id": 1, "unit_number": "204"},
            {"id": 2, "unit_number": "305"},
        ], next_page=None)

    client = MagicMock()
    client.iter_pages = fake_iter_pages
    provider = MagicMock()
    provider.client = client
    monkeypatch.setattr(
        sync_mod, "get_telematics_client", AsyncMock(return_value=provider),
    )

    cached: dict = {}
    monkeypatch.setattr(sync_mod._redis, "is_available", lambda: True)

    async def fake_cache_set(key, value, ttl=0):
        cached["key"] = key
        cached["value"] = value

    monkeypatch.setattr(sync_mod._redis, "cache_set", fake_cache_set)

    payload = await sync_mod.run_preview(42, "trucks", "pid-1")
    assert payload["state"] == "ready"
    assert payload["fetched"] == 2
    assert payload["diff"]["counts"]["new"] == 2
    # The fetched rows were cached so apply commits the same data.
    assert len(cached["value"]["rows"]) == 2

    # get_preview strips the bulky rows before handing the diff to the client.
    async def fake_get(key):
        return cached["value"]
    monkeypatch.setattr(sync_mod._redis, "get", fake_get)
    got = await sync_mod.get_preview(42, "trucks", "pid-1")
    assert got["state"] == "ready"
    assert got["diff"]["counts"]["new"] == 2
    assert "rows" not in got


@pytest.mark.asyncio
async def test_run_preview_rejects_non_previewable_resource():
    from capabilities.integrations.datatruck import sync as sync_mod
    # run_preview never raises — a bad resource lands as state='failed'.
    payload = await sync_mod.run_preview(42, "orders", "pid-x")
    assert payload["state"] == "failed"
    assert "does not support preview" in payload["error"]


@pytest.mark.asyncio
async def test_apply_resource_commits_cached_rows(monkeypatch):
    from capabilities.integrations.datatruck import sync as sync_mod

    tenant = MagicMock()
    tenant.upsert_datatruck_trucks = AsyncMock(side_effect=lambda a, rows: len(rows))
    projected: dict = {}

    async def fake_proj(account_id, rows, *, vehicle_type, source):
        projected["rows"] = rows
        return len(rows)

    tenant.project_external_vehicles = fake_proj
    monkeypatch.setattr(sync_mod, "get_tenant_db", AsyncMock(return_value=tenant))
    monkeypatch.setattr(sync_mod, "_publish", AsyncMock())

    monkeypatch.setattr(sync_mod._redis, "is_available", lambda: True)
    rows = [{"external_id": "1", "unit_number": "204"}]

    async def fake_get(key):
        return {"rows": rows, "total_upstream": 1}

    monkeypatch.setattr(sync_mod._redis, "get", fake_get)
    deleted: dict = {}

    async def fake_del(key):
        deleted["key"] = key

    monkeypatch.setattr(sync_mod._redis, "delete", fake_del)

    status = await sync_mod.apply_resource(42, "trucks", "pid123")
    assert status["state"] == "completed"
    assert status["records_written"] == 1
    assert projected["rows"] == rows
    assert deleted["key"]  # the cached preview is consumed (one-shot)


@pytest.mark.asyncio
async def test_apply_resource_expired_preview_fails(monkeypatch):
    from capabilities.integrations.datatruck import sync as sync_mod
    monkeypatch.setattr(sync_mod, "get_tenant_db", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(sync_mod, "_publish", AsyncMock())
    monkeypatch.setattr(sync_mod._redis, "is_available", lambda: True)

    async def fake_get(key):
        return None

    monkeypatch.setattr(sync_mod._redis, "get", fake_get)
    status = await sync_mod.apply_resource(42, "trucks", "missing")
    assert status["state"] == "failed"
    assert "expired" in status["error"]


@pytest.mark.asyncio
async def test_fetch_vehicle_lookup_builds_plate_and_unit_map(monkeypatch):
    from capabilities.integrations.datatruck import sync as sync_mod
    base = "https://acme.datatruck.io/api/v1/openapi/"
    pages = {
        "trucks/list/": _page(base, count=1, results=[
            {"id": 1, "unit_number": "T100", "plate_number": "ABC123"}],
            next_page=None),
        "trailers/list/": _page(base, count=1, results=[
            {"id": 2, "unit_number": "TR200", "plate_number": "XYZ789"}],
            next_page=None),
    }

    async def fake_iter_pages(path, params=None, *, max_pages=50):
        yield pages[path]

    client = MagicMock()
    client.iter_pages = fake_iter_pages
    provider = MagicMock()
    provider.client = client
    monkeypatch.setattr(
        sync_mod, "get_telematics_client", AsyncMock(return_value=provider),
    )

    lookup = await sync_mod._fetch_vehicle_lookup(42)
    # keyed by BOTH plate and unit, case-insensitive; value [unit, type]
    assert lookup["abc123"] == ["T100", "truck"]
    assert lookup["t100"] == ["T100", "truck"]
    assert lookup["xyz789"] == ["TR200", "trailer"]


def test_norm_work_order_assigned_to_string_and_object():
    # openapi list: assigned_to is a bare name string
    out = _norm_work_order({"id": 1, "assigned_to": "Pat Driver"})
    assert out["assigned_to"] == "Pat Driver"
    # v2 detail: assigned_to is a nested user object → full_name
    out2 = _norm_work_order({
        "id": 2,
        "assigned_to": {"id": 9, "full_name": "Pat Driver", "username": "pd"},
    })
    assert out2["assigned_to"] == "Pat Driver"


# ── Scheduler auto-sync jobs ──────────────────────────────────


def test_sync_jobs_cover_every_resource():
    """Every Datatruck resource gets an auto-sync job, so a newly-added
    resource can't silently lack a scheduler runner."""
    from capabilities.integrations.datatruck.sync import SYNC_JOBS

    assert set(SYNC_JOBS) == set(RESOURCES)


@pytest.mark.asyncio
async def test_sync_job_fans_out_gated_by_datatruck_toggle(monkeypatch):
    """A resource's job fans out through the provider-agnostic helper,
    passing that resource's capability + provider_id='datatruck' — so the
    shared gate skips accounts that aren't connected or have the toggle
    off (no Datatruck-specific gating logic duplicated in the job)."""
    import capabilities.integrations.datatruck.sync as dt

    seen: dict = {}

    async def _fake_fanout(capability, coro_factory, *, provider_id="samsara"):
        seen["capability"] = capability
        seen["provider_id"] = provider_id
        seen["factory_name"] = getattr(coro_factory, "__name__", "")

    monkeypatch.setattr(dt, "_for_each_account_with_capability", _fake_fanout)

    await dt.SYNC_JOBS["trucks"]()
    assert seen["capability"] == dt.RESOURCES["trucks"].capability
    assert seen["provider_id"] == "datatruck"
    assert seen["factory_name"] == "datatruck_sync_trucks"


def test_norm_order_settlement_and_other_pay():
    from capabilities.integrations.datatruck.sync import _norm_order
    out = _norm_order({
        "id": 22193, "load_id": "958511703",
        "status": "Delivered",
        "load_pay": "3000.00", "total_other_pay": "450.00",
        "total_pay": "3450.00",
        "trip": {
            "driver__full_name": "Eugene B",
            "settlement__settlement_number": "STL-1044",
            "settlement__status": "Sent",
            "settlement__is_sent": True,
        },
    })
    assert out["total_rate"] == 3450.0       # linehaul + accessorials
    assert out["other_pay"] == 450.0         # the informational split
    assert out["settlement_ref"] == "STL-1044"
    assert out["settlement_status"] == "Sent"
