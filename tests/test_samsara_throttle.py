"""Smoke tests for the M4 rate-limit safety primitives.

Covers:

  * ``SamsaraThrottle`` token-bucket behaviour with a fake clock so
    the tests don't actually sleep.
  * The module-level singleton + the global backfill lock exist and
    have sane defaults.
  * The ``Retry-After`` header parser handles missing / numeric /
    out-of-bounds / unparseable values without crashing the caller.
  * The 429-aware retry loop in ``_get_raw`` sleeps when a 429 comes
    back, exits cleanly on the eventual 200, and gives up after the
    configured retry budget.

The HTTP retry tests use a stub aiohttp session because spinning up
a real Samsara mock server would be heavier than needed for this
behavioural change.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.telematics.samsara.throttle import (
    SamsaraThrottle,
    backfill_account_lock,
    samsara_backfill_throttle,
)
from adapters.telematics.samsara.client import (
    SamsaraClient,
    SamsaraPermissionError,
    _parse_retry_after,
    _sleep_with_jitter,
)


# ── Retry-After parser ───────────────────────────────────────────


def test_parse_retry_after_handles_missing_header():
    assert _parse_retry_after(None, default_sec=2.0) == 2.0
    assert _parse_retry_after("", default_sec=2.0) == 2.0


def test_parse_retry_after_handles_integer_seconds():
    assert _parse_retry_after("5", default_sec=2.0) == 5.0


def test_parse_retry_after_handles_float_seconds():
    """Samsara documents integer seconds but we accept floats too."""
    assert _parse_retry_after("3.5", default_sec=2.0) == 3.5


def test_parse_retry_after_clamps_pathologically_long_values():
    """A buggy upstream claiming 'sleep 24 hours' must not actually
    cause us to do so."""
    assert _parse_retry_after("999999", default_sec=2.0) == 60.0


def test_parse_retry_after_clamps_zero_to_minimum():
    """Even if upstream says 0, sleep at least 0.5 s — no busy-spin."""
    assert _parse_retry_after("0", default_sec=2.0) == 0.5


def test_parse_retry_after_falls_back_on_unparseable():
    assert _parse_retry_after("not-a-number", default_sec=2.0) == 2.0


@pytest.mark.asyncio
async def test_sleep_with_jitter_sleeps_at_least_the_base():
    """The jitter is +0..10 %, so the actual sleep is always >= base
    and <= base * 1.1."""
    with patch(
        "adapters.telematics.samsara.client.asyncio.sleep",
        new_callable=AsyncMock,
    ) as fake_sleep:
        await _sleep_with_jitter(1.0)
    actual = fake_sleep.call_args.args[0]
    assert 1.0 <= actual <= 1.1


# ── Token-bucket throttle ─────────────────────────────────────────


class _FakeClock:
    """Manual clock for deterministic throttle math.  ``advance``
    moves time forward; ``__call__`` is the ``time.monotonic``-shaped
    accessor the throttle uses."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_throttle_full_bucket_allows_burst_immediately(monkeypatch):
    """Fresh throttle = ``burst`` tokens available; the first
    ``burst`` acquires return without sleeping."""
    clock = _FakeClock()
    throttle = SamsaraThrottle(rate_per_sec=1.0, burst=3, clock=clock)

    sleep_calls: list[float] = []

    async def _fake_sleep(s):
        sleep_calls.append(s)

    monkeypatch.setattr("asyncio.sleep", _fake_sleep)

    for _ in range(3):
        await throttle.acquire()
    assert sleep_calls == []
    assert throttle.stats()["acquired"] == 3


@pytest.mark.asyncio
async def test_throttle_paces_calls_at_configured_rate(monkeypatch):
    """Beyond the burst budget, each acquire sleeps ~1/rate seconds."""
    clock = _FakeClock()
    throttle = SamsaraThrottle(rate_per_sec=2.0, burst=1, clock=clock)

    sleeps: list[float] = []

    async def _fake_sleep(s):
        sleeps.append(s)
        clock.advance(s)

    monkeypatch.setattr("asyncio.sleep", _fake_sleep)

    # First call: drains the burst, no sleep.
    await throttle.acquire()
    # Next two calls: each waits ~0.5 s at 2 RPS.
    await throttle.acquire()
    await throttle.acquire()

    assert len(sleeps) == 2
    for s in sleeps:
        # Allow small float wobble; should be 0.5 s give or take rounding.
        assert 0.45 <= s <= 0.55


@pytest.mark.asyncio
async def test_throttle_rejects_invalid_config():
    with pytest.raises(ValueError):
        SamsaraThrottle(rate_per_sec=0, burst=1)
    with pytest.raises(ValueError):
        SamsaraThrottle(rate_per_sec=-1, burst=1)
    with pytest.raises(ValueError):
        SamsaraThrottle(rate_per_sec=1, burst=0)


@pytest.mark.asyncio
async def test_throttle_stats_snapshot_shape():
    throttle = SamsaraThrottle(rate_per_sec=1.0, burst=3)
    snap = throttle.stats()
    assert set(snap.keys()) == {
        "acquired", "waited_sec_total", "tokens_now", "rate_per_sec", "burst",
    }
    assert snap["rate_per_sec"] == 1.0
    assert snap["burst"] == 3.0


def test_module_singletons_have_sane_defaults():
    snap = samsara_backfill_throttle.stats()
    assert snap["rate_per_sec"] > 0
    assert snap["burst"] >= 1
    assert isinstance(backfill_account_lock, asyncio.Lock)


# ── 429-aware retry in _get_raw ───────────────────────────────────


def _stub_response(status, json_payload=None, headers=None):
    """Build an aiohttp-shaped async context manager for one response."""
    resp = MagicMock()
    resp.status = status
    resp.headers = headers or {}
    resp.json = AsyncMock(return_value=json_payload or {})
    # raise_for_status mirrors aiohttp: raises on 4xx/5xx, no-op on 2xx.
    if status >= 400:
        import aiohttp
        resp.raise_for_status = MagicMock(side_effect=aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=status,
        ))
    else:
        resp.raise_for_status = MagicMock(return_value=None)

    class _Ctx:
        async def __aenter__(self):
            return resp

        async def __aexit__(self, *exc):
            return None

    return _Ctx()


@pytest.mark.asyncio
async def test_get_raw_retries_429_then_succeeds(monkeypatch):
    """Two 429s then a 200 → final return is the 200's JSON."""
    client = SamsaraClient(api_key="test", base_url="https://api.example.com")

    # Skip the real sleep delay between retries.
    monkeypatch.setattr(
        "adapters.telematics.samsara.client._sleep_with_jitter",
        AsyncMock(),
    )

    responses = [
        _stub_response(429, headers={"Retry-After": "1"}),
        _stub_response(429, headers={"Retry-After": "1"}),
        _stub_response(200, json_payload={"data": [{"id": "v1"}]}),
    ]
    session = MagicMock()
    session.get = MagicMock(side_effect=responses)
    monkeypatch.setattr(client, "_get_session", AsyncMock(return_value=session))

    result = await client._get_raw("/fleet/vehicles")
    assert result == {"data": [{"id": "v1"}]}
    assert session.get.call_count == 3


@pytest.mark.asyncio
async def test_get_raw_exhausts_retries_and_raises(monkeypatch):
    """429 every attempt → raises after the configured max retries."""
    import aiohttp

    client = SamsaraClient(api_key="test", base_url="https://api.example.com")

    monkeypatch.setattr(
        "adapters.telematics.samsara.client._sleep_with_jitter",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "adapters.telematics.samsara.client.SAMSARA_429_MAX_RETRIES", 2,
    )

    # Need 3 responses: 2 retries + 1 final attempt that also 429s.
    responses = [
        _stub_response(429, headers={"Retry-After": "1"}),
        _stub_response(429, headers={"Retry-After": "1"}),
        _stub_response(429, headers={"Retry-After": "1"}),
    ]
    session = MagicMock()
    session.get = MagicMock(side_effect=responses)
    monkeypatch.setattr(client, "_get_session", AsyncMock(return_value=session))

    with pytest.raises(aiohttp.ClientResponseError):
        await client._get_raw("/fleet/vehicles")


@pytest.mark.asyncio
async def test_get_raw_does_not_retry_non_429_errors(monkeypatch):
    """A 403 should raise immediately as SamsaraPermissionError, no
    retry loop, no sleep call."""
    client = SamsaraClient(api_key="test", base_url="https://api.example.com")

    sleep_calls: list = []
    async def _capture(s):
        sleep_calls.append(s)
    monkeypatch.setattr(
        "adapters.telematics.samsara.client._sleep_with_jitter", _capture,
    )

    session = MagicMock()
    session.get = MagicMock(return_value=_stub_response(
        403, json_payload={"message": "forbidden"},
    ))
    monkeypatch.setattr(client, "_get_session", AsyncMock(return_value=session))

    with pytest.raises(SamsaraPermissionError):
        await client._get_raw("/fleet/vehicles")
    assert sleep_calls == []
    assert session.get.call_count == 1


@pytest.mark.asyncio
async def test_get_raw_uses_default_retry_after_when_header_missing(monkeypatch):
    """Missing Retry-After → fall back to ``SAMSARA_429_DEFAULT_RETRY_AFTER_SEC``."""
    client = SamsaraClient(api_key="test", base_url="https://api.example.com")

    sleep_calls: list[float] = []

    async def _capture(s):
        sleep_calls.append(s)

    monkeypatch.setattr(
        "adapters.telematics.samsara.client._sleep_with_jitter", _capture,
    )
    monkeypatch.setattr(
        "adapters.telematics.samsara.client.SAMSARA_429_DEFAULT_RETRY_AFTER_SEC",
        4.0,
    )

    responses = [
        _stub_response(429, headers={}),   # no header
        _stub_response(200, json_payload={"data": []}),
    ]
    session = MagicMock()
    session.get = MagicMock(side_effect=responses)
    monkeypatch.setattr(client, "_get_session", AsyncMock(return_value=session))

    await client._get_raw("/fleet/vehicles")
    assert sleep_calls == [4.0]
