"""Async circuit breaker for the Samsara HTTP client.

Why this exists
───────────────
Samsara outages (or intermittent failures across all companies) were
cascading into gunicorn worker starvation: each ``aiohttp`` call sat
for up to 30–60 s before timing out, and while those workers blocked
on dead requests, pure-internal API endpoints (``POST /maintenance/
tasks``, ``POST /work-orders``, ``PUT /user/preferences``, etc.) had
no worker to serve them.  The dashboard's 30 s client timeout fired
and users saw "signal is aborted without reason" on endpoints that
should always complete in <100 ms.

The fix has two layers:
  1. Per-call timeouts on the aiohttp session were dropped to 5 s
     (15 s for camera-image blobs) — see ``adapters/samsara/client.py``.
  2. This circuit breaker stops *attempting* the call entirely once
     Samsara has clearly gone dark, so the timeout doesn't even need
     to fire.  When the circuit is open, callers get an immediate
     ``SamsaraUnavailable`` and can fall back to cached/empty data.

State machine
─────────────
    closed   ── failure_threshold consecutive failures in window ──▶  open
    open     ── half_open_after_s elapsed since trip ──▶              half_open
    half_open ── one trial: success ──▶ closed
              ── one trial: failure ──▶ open  (reset window)

A circuit instance is module-global per process via the
``samsara_breaker`` singleton — Samsara is a single upstream, so one
breaker covers every client/company call.  Per-company isolation is
not needed because the failure modes we've seen are uniformly
account-wide (whole API key is rate-limited or whole region is down).

Telemetry
─────────
State transitions log at WARNING when opening (with the last error
preview) and INFO when closing — single line each, no log spam.
``state_snapshot()`` returns the current breaker state for the
``/api/health/deps`` endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TypeVar

import aiohttp

logger = logging.getLogger(__name__)


T = TypeVar("T")


class SamsaraUnavailable(aiohttp.ClientError):
    """Raised when the Samsara circuit breaker is open.

    Inherits from ``aiohttp.ClientError`` so every existing caller's
    ``except aiohttp.ClientError`` block catches it without modification.
    Behaviourally identical to a network failure from the caller's
    perspective — they fall back to cached/empty data — but skips the
    network round-trip entirely so the gunicorn worker pool isn't
    blocked waiting for a dead upstream.
    """
    pass


@dataclass
class _BreakerState:
    """Mutable state for the breaker.  Lives inside ``SamsaraCircuitBreaker``
    so tests can construct independent instances; the singleton at the
    bottom of the module is what production code uses.
    """
    # Configuration
    failure_threshold: int = 5
    window_seconds: float = 60.0
    half_open_after_s: float = 30.0

    # Live state
    state: str = "closed"                # "closed" | "open" | "half_open"
    consecutive_failures: int = 0
    last_failure_at: float = 0.0
    opened_at: float = 0.0
    last_error_preview: str = ""
    # Rolling failure count window — list of timestamps within
    # the last ``window_seconds``.
    recent_failures: list[float] = field(default_factory=list)

    # Lifetime counters for /api/health/deps
    total_calls: int = 0
    total_failures: int = 0
    total_open_transitions: int = 0


class SamsaraCircuitBreaker:
    """Async-safe circuit breaker.  One instance per upstream service."""

    def __init__(
        self,
        failure_threshold: int = 5,
        window_seconds: float = 60.0,
        half_open_after_s: float = 30.0,
    ):
        self._state = _BreakerState(
            failure_threshold=failure_threshold,
            window_seconds=window_seconds,
            half_open_after_s=half_open_after_s,
        )
        self._lock = asyncio.Lock()

    # ── Public API ───────────────────────────────────────────────

    async def call(
        self,
        fn: Callable[..., Awaitable[T]],
        *args,
        **kwargs,
    ) -> T:
        """Run ``fn(*args, **kwargs)`` through the breaker.

        Raises ``SamsaraUnavailable`` instantly when the circuit is
        open (no HTTP call attempted).  When the circuit is closed or
        half-open, runs the call; counts the outcome toward the
        rolling window; transitions state on threshold crossings.
        """
        await self._maybe_transition_half_open()

        if self._state.state == "open":
            raise SamsaraUnavailable(
                f"Samsara circuit breaker open; last error: "
                f"{self._state.last_error_preview!r}"
            )

        self._state.total_calls += 1
        try:
            result = await fn(*args, **kwargs)
        except _RetriableError as e:
            # The caller used ``record_failure_only`` semantics — raise
            # passes through but we still count it as a failure.
            await self._record_failure(repr(e))
            raise
        except Exception as e:
            # Permission errors / 4xx aren't infrastructure failures;
            # they signal a client bug, not Samsara being down.  Don't
            # let them trip the breaker.  We import here to avoid a
            # circular import (client → breaker → client).
            from adapters.samsara.client import SamsaraPermissionError
            if isinstance(e, SamsaraPermissionError):
                raise
            await self._record_failure(f"{type(e).__name__}: {e}")
            raise
        else:
            await self._record_success()
            return result

    def is_open(self) -> bool:
        """Quick state check without modifying state."""
        return self._state.state == "open"

    def state_snapshot(self) -> dict:
        """Return current breaker state for ``/api/health/deps``."""
        s = self._state
        now = time.monotonic()
        return {
            "state": s.state,
            "consecutive_failures": s.consecutive_failures,
            "last_failure_age_s": (
                round(now - s.last_failure_at, 1) if s.last_failure_at else None
            ),
            "last_error_preview": s.last_error_preview,
            "open_since_age_s": (
                round(now - s.opened_at, 1)
                if s.state == "open" and s.opened_at else None
            ),
            "total_calls": s.total_calls,
            "total_failures": s.total_failures,
            "total_open_transitions": s.total_open_transitions,
            "config": {
                "failure_threshold": s.failure_threshold,
                "window_seconds": s.window_seconds,
                "half_open_after_s": s.half_open_after_s,
            },
        }

    def reset(self) -> None:
        """Force the breaker back to ``closed`` and zero the counters.

        Intended for tests only; production code never resets manually.
        """
        self._state = _BreakerState(
            failure_threshold=self._state.failure_threshold,
            window_seconds=self._state.window_seconds,
            half_open_after_s=self._state.half_open_after_s,
        )

    # ── Internal state transitions ───────────────────────────────

    async def _record_success(self) -> None:
        async with self._lock:
            if self._state.state == "half_open":
                logger.info(
                    "Samsara circuit breaker closing after successful trial "
                    "(was open for %.0fs)",
                    time.monotonic() - self._state.opened_at,
                )
            self._state.state = "closed"
            self._state.consecutive_failures = 0
            self._state.recent_failures = []
            self._state.opened_at = 0.0

    async def _record_failure(self, error_preview: str) -> None:
        async with self._lock:
            now = time.monotonic()
            self._state.total_failures += 1
            self._state.consecutive_failures += 1
            self._state.last_failure_at = now
            self._state.last_error_preview = error_preview[:200]

            # Drop failures older than window_seconds out of the rolling list.
            cutoff = now - self._state.window_seconds
            self._state.recent_failures = [
                t for t in self._state.recent_failures if t >= cutoff
            ]
            self._state.recent_failures.append(now)

            already_open = self._state.state == "open"
            if (
                self._state.state in ("closed", "half_open")
                and len(self._state.recent_failures) >= self._state.failure_threshold
            ):
                self._state.state = "open"
                self._state.opened_at = now
                self._state.total_open_transitions += 1
                if not already_open:
                    logger.warning(
                        "Samsara circuit breaker OPEN after %d failures in %.0fs window. "
                        "Last error: %s",
                        len(self._state.recent_failures),
                        self._state.window_seconds,
                        self._state.last_error_preview,
                    )

    async def _maybe_transition_half_open(self) -> None:
        if self._state.state != "open":
            return
        async with self._lock:
            if self._state.state != "open":
                return
            elapsed = time.monotonic() - self._state.opened_at
            if elapsed >= self._state.half_open_after_s:
                self._state.state = "half_open"
                logger.info(
                    "Samsara circuit breaker entering half-open after %.0fs",
                    elapsed,
                )


class _RetriableError(Exception):
    """Internal marker — not raised externally.  Reserved for future
    integrations (e.g. retry-then-trip semantics on specific status codes).
    """
    pass


# ── Process-global singleton ─────────────────────────────────────
# One Samsara upstream → one breaker.  All ``SamsaraClient`` instances
# (one per company API key) share this so the breaker measures the
# health of Samsara-as-a-service, not of any single company's key.

samsara_breaker = SamsaraCircuitBreaker()
