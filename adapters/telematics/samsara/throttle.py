"""Rate-limit safety primitives for high-volume Samsara work.

Two independent concerns live here:

  * ``SamsaraThrottle`` — a token-bucket throttle that paces calls
    to Samsara when used.  The live ingest jobs DON'T use it — they
    run at their own infrequent cadences and never burst.  The
    backfill capability (M5) and any future bulk operation acquire a
    token before each Samsara call to stay well under Samsara's
    documented rate limits.

  * ``backfill_account_lock`` — a single asyncio.Lock that's held
    while a backfill is running for any account.  Acts as a serial
    queue across the platform so two account backfills don't
    overlap.  Per-account isolation is already enforced at the API-
    key level (Account A's backfill consumes Account A's Samsara
    quota), but Samsara's API-key system can still exhibit
    cross-account effects under our own infrastructure (gunicorn
    worker saturation, asyncpg pool contention) — the global lock
    prevents those at the cost of slightly slower account-wide
    onboarding.

Why a token bucket rather than a simple delay
---------------------------------------------
Samsara's documented limit is RPS sustained with short burst
tolerance.  A naive ``await asyncio.sleep(1 / rps)`` between calls
wastes the burst budget — if we just did 5 quick calls, we should
still be able to do another 3 immediately without waiting.  The
token-bucket model captures this: the bucket accumulates tokens at
the configured rate, drains by 1 per call, and the burst capacity is
the bucket's maximum.

Defaults
--------
1.0 RPS sustained, 3-token burst.  Samsara's documented limit on
``/fleet/vehicles/stats/history`` (the heaviest endpoint we touch) is
~5 RPS.  We deliberately leave 80 % headroom for the live ingest
jobs that run alongside any backfill, and to absorb spikes when the
upstream answer takes longer than expected.

Tunability
----------
Three env vars drive the defaults; ops can re-tune without code
changes when scaling account-wide rollouts:

  * ``SAMSARA_BACKFILL_RPS``               — sustained rate per second
  * ``SAMSARA_BACKFILL_BURST``             — bucket max capacity
  * ``SAMSARA_BACKFILL_DAY_CHUNK_SEC``     — cooldown between day chunks
"""

from __future__ import annotations

import asyncio
import logging
import time

from infra.config import (
    SAMSARA_BACKFILL_BURST,
    SAMSARA_BACKFILL_RPS,
)

logger = logging.getLogger(__name__)


class SamsaraThrottle:
    """Token-bucket throttle for paced Samsara calls.

    Usage::

        await throttle.acquire()
        response = await client.get_something()

    ``acquire`` returns when a token is available, sleeping just
    long enough to honour the configured rate.  Concurrent callers
    serialize through a per-instance lock so the bucket math stays
    consistent under multiple ``asyncio`` tasks.

    The throttle is NOT thread-safe — ``asyncio.Lock`` is event-loop-
    bound.  Every Samsara call goes through a single event loop in
    our deployment, so this is the right primitive.
    """

    def __init__(
        self,
        *,
        rate_per_sec: float = 1.0,
        burst: int = 3,
        clock=None,
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError(
                f"SamsaraThrottle rate_per_sec must be > 0, got {rate_per_sec}"
            )
        if burst < 1:
            raise ValueError(
                f"SamsaraThrottle burst must be >= 1, got {burst}"
            )
        self._rate = float(rate_per_sec)
        self._burst = int(burst)
        # The bucket starts full so the first ``burst`` calls go
        # through with no wait.  This is what operators expect from a
        # fresh process — the burst budget exists to absorb the
        # opening rush.
        self._tokens = float(burst)
        # ``clock`` injected for tests so monotonic time can be faked
        # without sleeping for real.
        self._now = clock or time.monotonic
        self._last_refill = self._now()
        self._lock = asyncio.Lock()
        # Light observability — operator can ``await throttle.stats()``
        # via the console route when debugging slow backfills.
        self._acquired = 0
        self._waited_sec_total = 0.0

    @property
    def rate_per_sec(self) -> float:
        return self._rate

    @property
    def burst(self) -> int:
        return self._burst

    def stats(self) -> dict[str, float]:
        """Snapshot of throttle counters since process start.

        Returns ``acquired``, ``waited_sec_total``, ``tokens_now``,
        and ``rate_per_sec`` so the operator console can render the
        live state of the throttle without poking internals.
        """
        return {
            "acquired": float(self._acquired),
            "waited_sec_total": round(self._waited_sec_total, 3),
            "tokens_now": round(self._tokens, 3),
            "rate_per_sec": self._rate,
            "burst": float(self._burst),
        }

    async def acquire(self) -> None:
        """Block until a token is available, then consume one.

        Sleeps via ``asyncio.sleep`` so other tasks on the event loop
        keep making progress.  Sleep duration is computed from the
        deficit — ``tokens_needed / rate`` — rather than a fixed
        interval, so callers waiting on a near-full bucket sleep
        only milliseconds.
        """
        async with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                self._acquired += 1
                return
            deficit = 1.0 - self._tokens
            sleep_for = deficit / self._rate
            # Release the lock during the sleep so other coroutines
            # CAN block on it but can't proceed in parallel — they'll
            # find the bucket still drained when they wake.
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
            self._waited_sec_total += sleep_for
        async with self._lock:
            self._refill()
            # After sleeping we should have just enough; cap negative
            # rounding errors at zero so ``acquired`` never goes
            # negative.
            self._tokens = max(0.0, self._tokens - 1.0)
            self._acquired += 1

    def _refill(self) -> None:
        """Add accumulated tokens based on elapsed wall clock.

        Called under ``self._lock`` only.  Capped at ``burst`` so the
        bucket can't overflow during long idle periods.
        """
        now = self._now()
        elapsed = max(0.0, now - self._last_refill)
        if elapsed <= 0:
            return
        self._tokens = min(float(self._burst), self._tokens + elapsed * self._rate)
        self._last_refill = now


# Module-level singletons.  The backfill capability uses these
# directly; tests can substitute alternates by monkeypatching.

samsara_backfill_throttle = SamsaraThrottle(
    rate_per_sec=SAMSARA_BACKFILL_RPS,
    burst=SAMSARA_BACKFILL_BURST,
)


# Global serial lock across all account backfills.
#
# Why one lock for the whole platform:
#   * Samsara rate limits are per-organization (Account A's limits
#     don't apply to Account B), so the lock isn't a Samsara safety
#     concern.
#   * It IS an internal-infra safety concern — gunicorn worker
#     saturation, asyncpg pool contention, and Redis cache traffic
#     from many concurrent backfills can interact badly.  Serializing
#     keeps the blast radius of any one backfill bounded.
#   * Fleet onboarding cadence (a few accounts per day at most for
#     this product) doesn't need parallel backfills.  When it does,
#     we'll add a bounded semaphore alongside this lock.
backfill_account_lock = asyncio.Lock()
