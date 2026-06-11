"""Per-company integration controls: test_company + Redis-backed
company_health + aggregate "N of M healthy" summary + loosened
Run-now preflight.

Covers:
  * ``SamsaraProvider.test_company(code)`` returns per-company probe
    result via the lean direct-/me path.
  * ``capabilities.integrations.company_health.{set,get,list}`` round-
    trip + TTL behaviour (TTL itself can't be tested without time
    travel; we verify the key gets written and read).
  * ``any_company_healthy`` + ``summarise_health`` semantics.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Per-company probe (SamsaraProvider.test_company) ───────────


class _FakeResponse:
    def __init__(self, status: int, body: dict | None = None):
        self.status = status
        self._body = body or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def json(self):
        return self._body


class _FakeSession:
    calls: list[str] = []

    def __init__(self, responses: dict[str, _FakeResponse]):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def get(self, url: str):
        _FakeSession.calls.append(url)
        if url not in self._responses:
            raise AssertionError(f"unexpected GET to {url}")
        return self._responses[url]


def _install_fake_session(monkeypatch, responses: dict[str, _FakeResponse]):
    import aiohttp
    _FakeSession.calls = []
    monkeypatch.setattr(
        aiohttp, "ClientSession",
        lambda *_a, **_k: _FakeSession(responses),
    )


@pytest.mark.asyncio
async def test_test_company_returns_ok_with_org_id(monkeypatch):
    from adapters.telematics.samsara.provider import SamsaraProvider

    co = MagicMock()
    co.api_key = "tok_cft"
    co.base_url = "https://api.samsara.com"

    multi = MagicMock()
    multi.clients = {"CFT": co, "G1": MagicMock()}

    _install_fake_session(monkeypatch, {
        "https://api.samsara.com/me": _FakeResponse(
            200, {"data": {"id": "samsara_org_42"}},
        ),
    })

    provider = SamsaraProvider(multi)
    status = await provider.test_company("CFT")
    assert status.ok is True
    assert status.provider_account_id == "samsara_org_42"
    # Exactly ONE /me call — only the requested company is probed.
    assert _FakeSession.calls == ["https://api.samsara.com/me"]


@pytest.mark.asyncio
async def test_test_company_returns_error_for_unknown_company():
    from adapters.telematics.samsara.provider import SamsaraProvider

    multi = MagicMock()
    multi.clients = {"CFT": MagicMock()}
    provider = SamsaraProvider(multi)

    status = await provider.test_company("NONEXISTENT")
    assert status.ok is False
    assert "no such company" in status.message.lower()


@pytest.mark.asyncio
async def test_test_company_reports_401(monkeypatch):
    from adapters.telematics.samsara.provider import SamsaraProvider

    co = MagicMock()
    co.api_key = "tok_revoked"
    co.base_url = "https://api.samsara.com"

    multi = MagicMock()
    multi.clients = {"X": co}

    _install_fake_session(monkeypatch, {
        "https://api.samsara.com/me": _FakeResponse(401),
    })

    provider = SamsaraProvider(multi)
    status = await provider.test_company("X")
    assert status.ok is False
    assert "credentials rejected" in status.message
    assert "HTTP 401" in status.message


# ── company_health Redis-backed storage ────────────────────────


def _make_redis_stub():
    store: dict[str, dict] = {}

    async def fake_get(key):
        return store.get(key)

    async def fake_set(key, value, ttl=120):
        store[key] = dict(value) if isinstance(value, dict) else value

    async def fake_scan(pattern, batch=500):
        prefix = pattern.rstrip("*")
        return [k for k in store.keys() if k.startswith(prefix)]

    def fake_available():
        return True

    return store, fake_get, fake_set, fake_scan, fake_available


@pytest.mark.asyncio
async def test_set_and_get_company_health_round_trip(monkeypatch):
    from capabilities.integrations import company_health

    store, fake_get, fake_set, fake_scan, fake_avail = _make_redis_stub()
    monkeypatch.setattr(company_health._redis, "is_available", fake_avail)
    monkeypatch.setattr(company_health._redis, "get", fake_get)
    monkeypatch.setattr(company_health._redis, "cache_set", fake_set)

    await company_health.set_company_health(
        42, "samsara", "CFT", ok=True, message="reachable", elapsed_ms=187,
    )
    h = await company_health.get_company_health(42, "samsara", "CFT")
    assert h is not None
    assert h["ok"] is True
    assert h["message"] == "reachable"
    assert h["elapsed_ms"] == 187
    assert "checked_at" in h
    # Roughly now.
    assert abs(h["checked_at"] - time.time()) < 5


@pytest.mark.asyncio
async def test_list_company_health_returns_only_this_provider(monkeypatch):
    from capabilities.integrations import company_health

    store, fake_get, fake_set, fake_scan, fake_avail = _make_redis_stub()
    monkeypatch.setattr(company_health._redis, "is_available", fake_avail)
    monkeypatch.setattr(company_health._redis, "get", fake_get)
    monkeypatch.setattr(company_health._redis, "cache_set", fake_set)
    monkeypatch.setattr(company_health._redis, "scan_keys", fake_scan)

    await company_health.set_company_health(42, "samsara", "CFT", ok=True)
    await company_health.set_company_health(42, "samsara", "G1", ok=False, message="HTTP 401")
    # Different account — must not appear.
    await company_health.set_company_health(99, "samsara", "Z9", ok=True)
    # Different provider — must not appear.
    await company_health.set_company_health(42, "motive", "AAA", ok=True)

    result = await company_health.list_company_health(42, "samsara")
    assert set(result.keys()) == {"CFT", "G1"}
    assert result["CFT"]["ok"] is True
    assert result["G1"]["ok"] is False


# ── Aggregate summary semantics ───────────────────────────────


def test_any_company_healthy_with_empty_map_allows_through():
    """Empty health map = untested = treated as "unknown but allowed".
    We don't want to block backfills just because the operator
    hasn't run per-company tests yet."""
    from capabilities.integrations.company_health import any_company_healthy
    assert any_company_healthy({}) is True


def test_any_company_healthy_with_one_ok_returns_true():
    from capabilities.integrations.company_health import any_company_healthy
    assert any_company_healthy({
        "CFT": {"ok": True},
        "G1":  {"ok": False},
        "OSY": {"ok": False},
    }) is True


def test_any_company_healthy_with_all_failing_returns_false():
    from capabilities.integrations.company_health import any_company_healthy
    assert any_company_healthy({
        "CFT": {"ok": False},
        "G1":  {"ok": False},
    }) is False


def test_summarise_health_unknown_when_no_records():
    from capabilities.integrations.company_health import summarise_health
    s = summarise_health({}, total_companies=5)
    assert s["status"] == "unknown"
    assert s["healthy"] == 0
    assert s["untested"] == 5


def test_summarise_health_healthy_when_all_ok():
    from capabilities.integrations.company_health import summarise_health
    s = summarise_health({
        "A": {"ok": True}, "B": {"ok": True}, "C": {"ok": True},
    }, total_companies=3)
    assert s["status"] == "healthy"
    assert s["healthy"] == 3
    assert s["untested"] == 0


def test_summarise_health_degraded_when_mixed():
    from capabilities.integrations.company_health import summarise_health
    s = summarise_health({
        "A": {"ok": True}, "B": {"ok": False},
    }, total_companies=5)
    assert s["status"] == "degraded"
    assert s["healthy"] == 1
    assert s["untested"] == 3


def test_summarise_health_error_when_all_failing():
    from capabilities.integrations.company_health import summarise_health
    s = summarise_health({
        "A": {"ok": False}, "B": {"ok": False},
    }, total_companies=2)
    assert s["status"] == "error"
    assert s["healthy"] == 0


# ── Route: loosened preflight ────────────────────────────────


@pytest.mark.asyncio
async def test_run_now_preflight_passes_when_any_company_healthy(monkeypatch):
    """The Run-now trigger MUST pass even when ai.status='error'
    if at least one company has been tested and is healthy.
    Otherwise one bad company blocks backfill for 4 good ones."""
    from capabilities.integrations import router as integrations_module
    from capabilities.integrations import company_health

    # Fake "error" status integration
    integ = MagicMock()
    integ.status = "error"
    integ.credentials = {"companies": {"CFT": "t"}}
    integ.feature_toggles = {}

    db = MagicMock()
    db.get_account_integration = AsyncMock(return_value=integ)
    monkeypatch.setattr(integrations_module, "get_platform_db", lambda: db)

    # The loosened preflight also calls ``get_tenant_db`` from
    # ``infra.platform`` to filter stale health records by current
    # company codes.  Stub it so the test doesn't need a real
    # tenant DB.
    tenant_stub = MagicMock()
    cft = MagicMock(); cft.code = "CFT"
    tenant_stub.get_account_companies = AsyncMock(return_value=[cft])

    async def fake_get_tenant_db(_acct_id):
        return tenant_stub

    import infra.platform as _platform_module
    monkeypatch.setattr(_platform_module, "get_tenant_db", fake_get_tenant_db)

    # Health map shows 1 of 5 healthy → preflight should pass.
    store, fake_get, fake_set, fake_scan, fake_avail = _make_redis_stub()
    monkeypatch.setattr(company_health._redis, "is_available", fake_avail)
    monkeypatch.setattr(company_health._redis, "get", fake_get)
    monkeypatch.setattr(company_health._redis, "cache_set", fake_set)
    monkeypatch.setattr(company_health._redis, "scan_keys", fake_scan)
    await company_health.set_company_health(42, "samsara", "CFT", ok=True)

    # Spawn-background spy.
    spawned: list = []
    monkeypatch.setattr(
        integrations_module, "_spawn_background",
        lambda coro: spawned.append(coro) or coro.close(),
    )
    monkeypatch.setattr(
        integrations_module, "_audit",
        AsyncMock(),
    )
    # backfill_status preflight = no running record.
    monkeypatch.setattr(
        integrations_module, "get_backfill_status",
        AsyncMock(return_value=None),
    )

    body = integrations_module.BackfillHistoryRequest(days=30)
    result = await integrations_module.trigger_backfill_history(
        "samsara", body, user={"account_id": 42, "id": 1},
    )
    # Preflight passed → spawned a task → returned queued.
    assert result["state"] == "queued"
    assert len(spawned) == 1


# ── Cold-cache deadlock regression ────────────────────────────


@pytest.mark.asyncio
async def test_get_telematics_client_no_self_deadlock_on_cold_cache(monkeypatch):
    """Calling get_telematics_client when BOTH _client_cache and
    _provider_cache are empty must not deadlock.

    The historical bug: get_telematics_client took _client_lock,
    then nested into get_client which tried to acquire the same
    non-reentrant lock — wedging until the route's wait_for budget
    (8s/12s) expired, which surfaced to the user as a 504 with
    "upstream slow — backfill or rate limit in progress".  This
    test exercises the exact cold-cache shape that triggered it.
    """
    from infra import services

    # Reset both caches so the lock paths get exercised.
    services._client_cache.clear()
    services._provider_cache.clear()

    # Stub the platform DB + tenant DB + build_multi_company_client
    # so we never touch I/O.  The point is the lock contract, not
    # the construction details.
    fake_companies: list = []
    tenant_stub = MagicMock()
    tenant_stub.get_account_companies = AsyncMock(return_value=fake_companies)
    monkeypatch.setattr(services, "get_db", lambda: tenant_stub)

    platform_stub = MagicMock()
    platform_stub.get_account_integration = AsyncMock(return_value=None)
    import infra.platform as _pmod
    monkeypatch.setattr(_pmod, "get_platform_db", lambda: platform_stub)

    fake_client = MagicMock()
    fake_client.prefetch_org_ids = AsyncMock()
    fake_client.close = AsyncMock()
    monkeypatch.setattr(
        services, "build_multi_company_client",
        lambda *a, **k: fake_client,
    )

    # If the deadlock returns, asyncio.wait_for will fire.  Give it
    # 2 seconds — plenty for the legit path, definitely shorter than
    # the bug's 8s/12s wedge.
    import asyncio as _asyncio
    provider = await _asyncio.wait_for(
        services.get_telematics_client(42, "samsara", prefetch=False),
        timeout=2.0,
    )
    assert provider is not None
    assert (42, "samsara") in services._provider_cache

    # Concurrent cold-cache callers shouldn't deadlock either.
    services._client_cache.clear()
    services._provider_cache.clear()
    a, b, c = await _asyncio.wait_for(
        _asyncio.gather(
            services.get_telematics_client(43, "samsara", prefetch=False),
            services.get_telematics_client(43, "samsara", prefetch=False),
            services.get_telematics_client(43, "samsara", prefetch=False),
        ),
        timeout=2.0,
    )
    # All three return the same cached wrapper.
    assert a is b is c


# ── Per-company backfill scoping ──────────────────────────────


def test_redis_key_includes_company_code_when_scoped():
    """Per-company refresh writes to a per-company Redis key so an
    account-wide "Run all" and a row-level "Refresh" can co-exist
    without one clobbering the other's progress."""
    from capabilities.telemetry.history_backfill import _redis_key
    account_key = _redis_key(42, "samsara")
    company_key = _redis_key(42, "samsara", "OSY")
    assert account_key != company_key
    assert "OSY" in company_key
    assert "co=OSY" in company_key  # locked-in shape
    # Empty/None falls back to the account-level shape.
    assert _redis_key(42, "samsara", None) == account_key
    assert _redis_key(42, "samsara", "") == account_key


@pytest.mark.asyncio
async def test_get_stats_history_scopes_to_one_company(monkeypatch):
    """When ``company_code`` is set, get_stats_history walks only
    that company's client.  An unknown code returns the empty dict
    without raising."""
    from adapters.telematics.samsara.provider import SamsaraProvider

    cft = MagicMock()
    cft._get_paginated_history = AsyncMock(return_value={
        "v1": {"gps": [{"time": "2026-01-01T00:00:00Z", "lat": 1, "lng": 2}]},
    })
    g1 = MagicMock()
    g1._get_paginated_history = AsyncMock(return_value={
        "v2": {"gps": [{"time": "2026-01-01T00:00:00Z", "lat": 3, "lng": 4}]},
    })

    multi = MagicMock()
    multi.clients = {"CFT": cft, "G1": g1}
    provider = SamsaraProvider(multi)

    # Scoped to CFT — only CFT's client called, only v1 returned.
    out = await provider.get_stats_history(
        ["gps"], "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z",
        company_code="CFT",
    )
    assert "v1" in out
    assert "v2" not in out
    cft._get_paginated_history.assert_awaited_once()
    g1._get_paginated_history.assert_not_called()

    # Unknown code — empty dict, no client called.
    out = await provider.get_stats_history(
        ["gps"], "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z",
        company_code="DOES_NOT_EXIST",
    )
    assert out == {}

    # Unscoped — every client called.
    cft._get_paginated_history.reset_mock()
    g1._get_paginated_history.reset_mock()
    out = await provider.get_stats_history(
        ["gps"], "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z",
    )
    assert "v1" in out and "v2" in out
    cft._get_paginated_history.assert_awaited_once()
    g1._get_paginated_history.assert_awaited_once()
