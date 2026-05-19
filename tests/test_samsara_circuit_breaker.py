"""Tests for the Samsara async circuit breaker.

Covers the state-machine transitions documented at the top of
``adapters/samsara/circuit_breaker.py``:

    closed ── N failures ──▶ open ── timeout ──▶ half_open ── success ──▶ closed
                                              ── failure ──▶ open

Plus the invariants that production code relies on:
  * ``SamsaraUnavailable`` is an ``aiohttp.ClientError`` so existing
    callers catch it transparently.
  * ``SamsaraPermissionError`` (4xx) never trips the breaker — those
    are client bugs, not infrastructure failures.
  * The breaker is async-safe; concurrent callers see consistent state.
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest

from adapters.samsara.circuit_breaker import (
    SamsaraCircuitBreaker,
    SamsaraUnavailable,
)


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def breaker() -> SamsaraCircuitBreaker:
    """A fresh breaker with low thresholds for fast tests."""
    return SamsaraCircuitBreaker(
        failure_threshold=3,
        window_seconds=60.0,
        half_open_after_s=0.05,  # 50 ms so half-open tests don't sleep forever
    )


async def _fail(exc: Exception = aiohttp.ClientError("boom")):
    raise exc


async def _ok(value: str = "ok"):
    return value


# ── Exception inheritance: caller compatibility invariant ───────────

class TestExceptionContract:
    def test_unavailable_is_aiohttp_client_error(self):
        """Every caller has ``except aiohttp.ClientError`` blocks —
        the breaker exception must satisfy them without code changes."""
        assert issubclass(SamsaraUnavailable, aiohttp.ClientError)


# ── State machine ────────────────────────────────────────────────────

class TestStateMachine:
    @pytest.mark.asyncio
    async def test_closed_passes_calls_through(self, breaker):
        result = await breaker.call(_ok, "hello")
        assert result == "hello"
        assert breaker.state_snapshot()["state"] == "closed"

    @pytest.mark.asyncio
    async def test_trips_open_after_threshold_failures(self, breaker):
        for _ in range(3):
            with pytest.raises(aiohttp.ClientError):
                await breaker.call(_fail)
        snap = breaker.state_snapshot()
        assert snap["state"] == "open"
        assert snap["total_open_transitions"] == 1

    @pytest.mark.asyncio
    async def test_open_fast_fails_without_calling_fn(self, breaker):
        # Trip it.
        for _ in range(3):
            with pytest.raises(aiohttp.ClientError):
                await breaker.call(_fail)

        # Now the wrapped fn should NOT run — even a function that
        # would succeed must be skipped.
        called = False

        async def succeeds():
            nonlocal called
            called = True
            return "should not happen"

        with pytest.raises(SamsaraUnavailable):
            await breaker.call(succeeds)
        assert called is False, "open breaker must not invoke the wrapped fn"

    @pytest.mark.asyncio
    async def test_half_open_then_close_on_success(self, breaker):
        for _ in range(3):
            with pytest.raises(aiohttp.ClientError):
                await breaker.call(_fail)
        assert breaker.is_open()

        await asyncio.sleep(0.06)  # exceed half_open_after_s

        # First call after timeout triggers half-open, then closes on success.
        result = await breaker.call(_ok, "recovered")
        assert result == "recovered"
        snap = breaker.state_snapshot()
        assert snap["state"] == "closed"
        assert snap["consecutive_failures"] == 0

    @pytest.mark.asyncio
    async def test_half_open_reopens_on_failure(self, breaker):
        for _ in range(3):
            with pytest.raises(aiohttp.ClientError):
                await breaker.call(_fail)
        await asyncio.sleep(0.06)

        # A failing trial must immediately re-open the breaker.
        with pytest.raises(aiohttp.ClientError):
            await breaker.call(_fail)
        assert breaker.is_open()


# ── Permission errors: must NOT trip the breaker ────────────────────

class TestPermissionErrorPassthrough:
    @pytest.mark.asyncio
    async def test_permission_error_does_not_count_as_failure(self, breaker):
        from adapters.samsara.client import SamsaraPermissionError

        async def perm_denied():
            raise SamsaraPermissionError("403 from Samsara")

        for _ in range(10):
            with pytest.raises(SamsaraPermissionError):
                await breaker.call(perm_denied)

        # Even after 10 permission errors, the breaker is still closed —
        # because 4xx isn't an infra failure.
        snap = breaker.state_snapshot()
        assert snap["state"] == "closed"
        assert snap["consecutive_failures"] == 0


# ── Telemetry snapshot ──────────────────────────────────────────────

class TestSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_reports_counters(self, breaker):
        await breaker.call(_ok)
        with pytest.raises(aiohttp.ClientError):
            await breaker.call(_fail)

        snap = breaker.state_snapshot()
        assert snap["total_calls"] == 2
        assert snap["total_failures"] == 1
        assert snap["consecutive_failures"] == 1
        assert snap["state"] == "closed"
        assert snap["config"]["failure_threshold"] == 3


# ── Async safety: concurrent callers see consistent state ───────────

class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_failures_trip_once(self, breaker):
        # Many concurrent failures should produce exactly one
        # "open" transition, not one per task.
        async def attempt():
            try:
                await breaker.call(_fail)
            except aiohttp.ClientError:
                pass

        await asyncio.gather(*(attempt() for _ in range(20)))

        snap = breaker.state_snapshot()
        assert snap["state"] == "open"
        assert snap["total_open_transitions"] == 1
