"""Tests for the four IntegrationCard fixes:

  * Fix A — ``SamsaraProvider.test_connection`` uses ``/me`` instead
    of ``get_fleet_overview`` so it's fast even while a backfill is
    hammering the rate-limited stats endpoints.
  * Fix B — The route wraps ``test_connection`` in
    ``asyncio.wait_for(timeout=12)`` and returns HTTP 504 with a
    useful message when the upstream is slow.
  * Fix D — ``account_integrations.last_backfill_at`` is populated
    when M5 backfill completes; surfaces in the API serializer.
  * Fix C — Backend portion (the dashboard gating logic is tested
    via vitest separately).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.storage import Database


# ── Fix A: provider.test_connection uses a lean DIRECT /me probe ──
#
# The lean probe bypasses the shared client stack entirely:
#   * Fresh aiohttp.ClientSession per call
#   * No breaker, no 429 retry loop, no shared keepalive
#   * 3-second hard ceiling via ClientTimeout(total=3)
# These tests verify that contract via a small aiohttp-session mock
# rather than mocking get_org_id (which the new probe never calls).


class _FakeResponse:
    """Async context manager that produces a fake aiohttp response."""

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
    """Async context manager that produces a fake ClientSession.

    Records every URL hit on the ``calls`` class list so tests can
    assert "1 call per company" without iffy reach-into-aiohttp
    monkey-patching.  ``responses`` maps URL → _FakeResponse;
    requests to unmapped URLs raise to surface test mistakes."""

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
    """Replace ``aiohttp.ClientSession(...)`` with one that returns
    canned responses, so the test never touches the network."""
    import aiohttp
    _FakeSession.calls = []

    def _ctor(*_args, **_kwargs):
        return _FakeSession(responses)

    monkeypatch.setattr(aiohttp, "ClientSession", _ctor)


@pytest.mark.asyncio
async def test_test_connection_probes_each_company_with_direct_me_call(monkeypatch):
    """The probe must hit ``/me`` exactly once per configured company
    and never call ``get_org_id`` / ``get_fleet_overview`` (which
    would route through the breaker + retry stack we're trying to
    bypass)."""
    from adapters.telematics.samsara.provider import SamsaraProvider

    company_a = MagicMock()
    company_a.api_key = "tok_a"
    company_a.base_url = "https://api.samsara.com"
    company_a.get_org_id = AsyncMock(side_effect=AssertionError(
        "lean probe must NOT call get_org_id"))
    company_a.get_fleet_overview = AsyncMock(side_effect=AssertionError(
        "lean probe must NOT call get_fleet_overview"))

    company_b = MagicMock()
    company_b.api_key = "tok_b"
    company_b.base_url = "https://api.samsara.com"
    company_b.get_org_id = AsyncMock(side_effect=AssertionError(
        "lean probe must NOT call get_org_id"))

    multi = MagicMock()
    multi.clients = {"AAA": company_a, "BBB": company_b}

    _install_fake_session(monkeypatch, {
        "https://api.samsara.com/me": _FakeResponse(
            200, {"data": {"id": "org_a"}},
        ),
    })

    provider = SamsaraProvider(multi)
    status = await provider.test_connection({})
    assert status.ok is True
    assert "2 companies reachable" in status.message
    assert status.provider_account_id == "org_a"
    # Both companies were probed.
    assert _FakeSession.calls.count("https://api.samsara.com/me") == 2


@pytest.mark.asyncio
async def test_test_connection_reports_401_as_credentials_rejected(monkeypatch):
    """A 401 on /me means the API key was revoked / never valid.
    The probe must surface this as a clean error message including
    the company code so the operator knows which token to rotate."""
    from adapters.telematics.samsara.provider import SamsaraProvider

    co_ok = MagicMock()
    co_ok.api_key = "tok_good"
    co_ok.base_url = "https://api.samsara.com"

    co_bad = MagicMock()
    co_bad.api_key = "tok_revoked"
    co_bad.base_url = "https://api.samsara.com"

    multi = MagicMock()
    multi.clients = {"OK": co_ok, "BAD": co_bad}

    # Same URL for both companies (only auth header differs), so we
    # can't distinguish in the FakeSession — emulate by returning the
    # error response.  Real behaviour: gather runs both in parallel;
    # whichever fails first surfaces.  We test the FAILURE-MAPPING
    # contract: 401 → "credentials rejected" + company code prefix.
    _install_fake_session(monkeypatch, {
        "https://api.samsara.com/me": _FakeResponse(401),
    })

    provider = SamsaraProvider(multi)
    status = await provider.test_connection({})
    assert status.ok is False
    assert "credentials rejected" in status.message
    assert "HTTP 401" in status.message


@pytest.mark.asyncio
async def test_test_connection_reports_timeout_within_budget(monkeypatch):
    """If the probe hits the 3s timeout, the error message must say
    so explicitly — operators need to know whether to retry (3s
    timeout = transient) vs reconfigure (401 = bad creds)."""
    import asyncio as _aio
    from adapters.telematics.samsara.provider import SamsaraProvider

    co = MagicMock()
    co.api_key = "tok"
    co.base_url = "https://api.samsara.com"

    multi = MagicMock()
    multi.clients = {"X": co}

    class _TimeoutSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        def get(self, _url):
            class _GetCtx:
                async def __aenter__(_self):
                    raise _aio.TimeoutError()
                async def __aexit__(_self, *args):
                    return None
            return _GetCtx()

    import aiohttp
    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *_a, **_k: _TimeoutSession(),
    )

    provider = SamsaraProvider(multi)
    status = await provider.test_connection({})
    assert status.ok is False
    assert "timeout after 3s" in status.message
    assert "X:" in status.message  # company code prefix


@pytest.mark.asyncio
async def test_test_connection_returns_error_when_no_companies_configured():
    from adapters.telematics.samsara.provider import SamsaraProvider

    multi = MagicMock()
    multi.clients = {}
    provider = SamsaraProvider(multi)

    status = await provider.test_connection({})
    assert status.ok is False
    assert "no companies" in status.message.lower()


# ── Fix B: route wraps test_connection in wait_for ─────────────


@pytest.mark.asyncio
async def test_route_returns_504_when_test_connection_exceeds_12s(monkeypatch):
    """The route handler MUST cap the upstream call at 12s so the
    frontend never sees the bare 30s ABORT from the fetch timeout —
    a clean 504 with a useful message is better UX."""
    from capabilities.integrations import router as integrations_module

    # Mock the provider's test_connection to hang forever.
    hanging_provider = MagicMock()

    async def _hang(*_a, **_kw):
        await asyncio.sleep(100)
        return None

    hanging_provider.test_connection = _hang

    monkeypatch.setattr(
        integrations_module, "get_telematics_client",
        AsyncMock(return_value=hanging_provider),
    )

    db = MagicMock()
    integration = MagicMock()
    integration.credentials = {"api_token": "test"}
    db.get_account_integration = AsyncMock(return_value=integration)
    db.record_integration_health_check = AsyncMock()
    monkeypatch.setattr(
        integrations_module, "get_platform_db", lambda: db,
    )

    # Speed up the wait_for so the test doesn't actually wait 12s.
    original_wait_for = asyncio.wait_for

    async def fast_wait_for(coro, timeout):
        # Force immediate timeout regardless of requested seconds —
        # keeps test fast while exercising the timeout path.
        return await original_wait_for(coro, timeout=0.05)

    monkeypatch.setattr(
        "capabilities.integrations.router.asyncio.wait_for", fast_wait_for,
    )

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await integrations_module.test_connection_action(
            "samsara",
            user={"account_id": 42, "id": 1},
        )
    assert exc_info.value.status_code == 504
    assert "slow" in str(exc_info.value.detail).lower() \
        or "backfill" in str(exc_info.value.detail).lower()
    # Health-check column must be marked as failed.
    db.record_integration_health_check.assert_awaited_once()
    call_kwargs = db.record_integration_health_check.call_args.kwargs
    assert call_kwargs["ok"] is False


# ── Fix D: last_backfill_at column ─────────────────────────────


@pytest.mark.asyncio
async def test_record_integration_backfill_completion_sets_timestamp(seeded_db):
    """After M5 completes, the timestamp column must be populated so
    the dashboard can show "Last backfill: X" without depending on
    the 24h Redis status badge."""
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    # Seed an integration row.
    await db.upsert_account_integration(
        account.id, "samsara", credentials={"api_token": "x"},
    )
    pre = await db.get_account_integration(account.id, "samsara")
    assert pre is not None
    assert pre.last_backfill_at == ""

    await db.record_integration_backfill_completion(account.id, "samsara")

    post = await db.get_account_integration(account.id, "samsara")
    assert post is not None
    assert post.last_backfill_at != ""
    # Roughly an ISO timestamp.
    assert "T" in post.last_backfill_at


@pytest.mark.asyncio
async def test_serializer_exposes_last_backfill_at(seeded_db):
    """API surface check — the route's _serialize_integration helper
    must include the new field so the dashboard can read it."""
    from capabilities.integrations.router import _serialize_integration

    db: Database = seeded_db["db"]
    account = seeded_db["account"]
    await db.upsert_account_integration(
        account.id, "samsara", credentials={"api_token": "x"},
    )
    await db.record_integration_backfill_completion(account.id, "samsara")
    ai = await db.get_account_integration(account.id, "samsara")

    payload = _serialize_integration(ai)
    assert "last_backfill_at" in payload
    assert payload["last_backfill_at"] != ""


@pytest.mark.asyncio
async def test_upsert_preserves_last_backfill_at_on_reconnect(seeded_db):
    """Re-connecting must not wipe the existing backfill timestamp.
    Otherwise re-authing would make the dashboard think there's no
    fresh data and trigger another full backfill."""
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    await db.upsert_account_integration(
        account.id, "samsara", credentials={"api_token": "x"},
    )
    await db.record_integration_backfill_completion(account.id, "samsara")
    before = await db.get_account_integration(account.id, "samsara")
    assert before is not None
    original_ts = before.last_backfill_at
    assert original_ts != ""

    # Reconnect — same as a credential rotation.
    await db.upsert_account_integration(
        account.id, "samsara", credentials={"api_token": "y"},
    )
    after = await db.get_account_integration(account.id, "samsara")
    assert after is not None
    assert after.last_backfill_at == original_ts
