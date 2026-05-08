"""Redis client — optional async connection pool for caching and state.

If REDIS_URL is not set or Redis is unreachable, all operations gracefully
fall back to no-ops so the bot still works without Redis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Awaitable, Callable, Optional, TypeVar

import redis.asyncio as aioredis

T = TypeVar("T")

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:8002/0")
_REDIS_MAX_CONNECTIONS = int(os.getenv("REDIS_MAX_CONNECTIONS", "50"))

_pool: Optional[aioredis.Redis] = None
_available: bool = False


async def init_redis() -> bool:
    """Initialize the Redis connection pool.  Returns True if connected."""
    global _pool, _available

    if not REDIS_URL:
        logger.info("REDIS_URL not set — Redis disabled")
        return False

    try:
        _pool = aioredis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            max_connections=_REDIS_MAX_CONNECTIONS,
            retry_on_timeout=True,
        )
        await _pool.ping()  # type: ignore[misc]
        _available = True
        logger.info("Redis connected: %s (max_connections=%d)", REDIS_URL, _REDIS_MAX_CONNECTIONS)
        return True
    except Exception as e:
        logger.warning(f"Redis unavailable ({e}) — falling back to in-memory")
        _pool = None
        _available = False
        return False


async def close_redis():
    """Gracefully close the Redis connection pool."""
    global _pool, _available
    if _pool:
        await _pool.aclose()
        _pool = None
        _available = False


def is_available() -> bool:
    """Check if Redis is connected and usable."""
    return _available


# ── Key-Value helpers (JSON serialized) ──────────────────────────

async def get(key: str) -> Optional[Any]:
    """Get a JSON-decoded value from Redis.  Returns None on miss or error."""
    if not _available or not _pool:
        return None
    try:
        raw = await _pool.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug(f"Redis GET {key}: {e}")
        return None


async def cache_set(key: str, value: Any, ttl: int = 120):
    """Store a JSON-encoded value in Redis with a TTL (seconds)."""
    if not _available or not _pool:
        return
    try:
        await _pool.setex(key, ttl, json.dumps(value, default=str))
    except Exception as e:
        logger.debug(f"Redis SET {key}: {e}")


# Backward-compat alias: `from adapters.cache.redis import set as redis_set`
# We can't name this `set` at module level (shadows builtins.set inside this file),
# so callers should import as `cache_set` or use `redis_set` alias on import side.


async def delete(key: str):
    """Delete a key from Redis."""
    if not _available or not _pool:
        return
    try:
        await _pool.delete(key)
    except Exception as e:
        logger.debug(f"Redis DEL {key}: {e}")


async def exists(key: str) -> bool:
    """Check if a key exists in Redis."""
    if not _available or not _pool:
        return False
    try:
        return bool(await _pool.exists(key))
    except Exception:
        return False


async def setex_flag(key: str, ttl: int):
    """Set a flag key with TTL (for cooldowns).  Value is always '1'."""
    if not _available or not _pool:
        return
    try:
        await _pool.setex(key, ttl, "1")
    except Exception as e:
        logger.debug(f"Redis SETEX flag {key}: {e}")


# ── Rate limiting ─────────────────────────────────────────────────

async def rate_limit_check(key: str, window_secs: int, max_requests: int) -> bool:
    """Sliding-window rate limit using Redis INCR + EXPIRE.

    Returns True if the request is allowed (under the limit).
    Returns True (allow) on any Redis error — fail open so users are
    never blocked due to a Redis outage.

    Uses a fixed window keyed by floor(now / window_secs) so limits reset
    cleanly at window boundaries.  Key format: ``<prefix>:<window_bucket>``.
    """
    if not _available or not _pool:
        return True  # no Redis — allow and let the in-memory fallback handle it
    try:
        import time as _time
        bucket = int(_time.time()) // window_secs
        full_key = f"{key}:{bucket}"
        count = await _pool.incr(full_key)  # type: ignore[misc]
        if count == 1:
            # First request in this window — set expiry
            await _pool.expire(full_key, window_secs * 2)  # type: ignore[misc]
        return count <= max_requests
    except Exception as e:
        logger.debug("Redis rate_limit_check %s: %s", key, e)
        return True  # fail open


# ── Distributed lock ──────────────────────────────────────────────

async def acquire_lock(name: str, ttl_secs: int = 60) -> bool:
    """Try to acquire a distributed lock.  Returns True if acquired.

    Uses SET NX EX so only one caller wins across all processes.
    Key: ``lock:{name}``.
    """
    if not _available or not _pool:
        return True  # no Redis — act as if lock acquired (single-process safe)
    try:
        result = await _pool.set(f"lock:{name}", "1", nx=True, ex=ttl_secs)
        return result is not None
    except Exception as e:
        logger.debug("Redis acquire_lock %s: %s", name, e)
        return True  # fail open


async def heartbeat_lock(name: str, ttl_secs: int = 60) -> bool:
    """Refresh the TTL of an already-held lock.  Returns True if still held."""
    if not _available or not _pool:
        return True
    try:
        return bool(await _pool.expire(f"lock:{name}", ttl_secs))  # type: ignore[misc]
    except Exception as e:
        logger.debug("Redis heartbeat_lock %s: %s", name, e)
        return True


async def release_lock(name: str):
    """Release a distributed lock."""
    if not _available or not _pool:
        return
    try:
        await _pool.delete(f"lock:{name}")
    except Exception as e:
        logger.debug("Redis release_lock %s: %s", name, e)


# ── Set helpers (for fault code tracking) ────────────────────────

async def sadd(key: str, *members: str, ttl: int = 86400):
    """Add members to a Redis SET and reset TTL."""
    if not _available or not _pool:
        return
    try:
        if members:
            await _pool.sadd(key, *members)  # type: ignore[misc]
            await _pool.expire(key, ttl)
    except Exception as e:
        logger.debug(f"Redis SADD {key}: {e}")


async def smembers(key: str) -> set[str]:
    """Get all members of a Redis SET."""
    if not _available or not _pool:
        return set()
    try:
        result = await _pool.smembers(key)  # type: ignore[misc]
        return result
    except Exception:
        return set()


async def sset(key: str, members: set, ttl: int = 86400):
    """Replace a Redis SET with new members."""
    if not _available or not _pool:
        return
    try:
        pipe = _pool.pipeline()
        pipe.delete(key)
        if members:
            pipe.sadd(key, *members)
            pipe.expire(key, ttl)
        await pipe.execute()
    except Exception as e:
        logger.debug(f"Redis SSET {key}: {e}")


# ── List helpers (for active messages) ───────────────────────────

async def rpush(key: str, *values: str, ttl: int = 3600):
    """Append values to a Redis LIST and reset TTL."""
    if not _available or not _pool:
        return
    try:
        if values:
            await _pool.rpush(key, *values)  # type: ignore[misc]
            await _pool.expire(key, ttl)
    except Exception as e:
        logger.debug(f"Redis RPUSH {key}: {e}")


async def lrange(key: str, start: int = 0, end: int = -1) -> list[str]:
    """Get a range of values from a Redis LIST."""
    if not _available or not _pool:
        return []
    try:
        return await _pool.lrange(key, start, end)  # type: ignore[misc]
    except Exception:
        return []


async def list_delete(key: str):
    """Delete a Redis LIST key."""
    await delete(key)


# ── Single-flight + Stale-While-Revalidate ───────────────────────
#
# Cache-stampede protection. When a popular key expires under load, the
# default behaviour is that every concurrent request misses cache and
# triggers the cold-path compute in parallel — N requests, N expensive
# fetches. Single-flight makes only ONE request do the work; the rest
# wait for the result. SWR makes "fresh enough" the default — when the
# cache exists but is past its soft-TTL, we return the stale value
# immediately and refresh in the background.
#
# Storage shape:
#   key      → JSON {"v": <payload>, "t": <unix_seconds_set_at>}
#   lock:key → "1" with NX EX (acts as the in-flight marker)
#
# Hard TTL on the value is `stale_after + max_stale` so even if the
# refresh fails for a while the next reader still gets *something*
# rather than nothing.

# In-process single-flight registry: collapses concurrent requests
# inside the same process even before they hit Redis. The Redis lock
# above handles the cross-process case.
_inflight: dict[str, asyncio.Future] = {}
_inflight_lock = asyncio.Lock()


async def get_or_compute(
    key: str,
    fetch: Callable[[], Awaitable[T]],
    *,
    fresh_for: int = 120,
    max_stale: int = 600,
    lock_ttl: int = 30,
) -> T:
    """Cache wrapper with stale-while-revalidate + single-flight.

    Behaviour:
      * Fresh (age < fresh_for) → return cached value, no refresh.
      * Stale (fresh_for ≤ age < fresh_for + max_stale) → return cached
        value immediately AND kick off a background refresh (single
        process-wide, single-flight via Redis lock).
      * Cold miss → call ``fetch()`` synchronously, populate cache,
        return result. Concurrent cold-misses on the same key share a
        single in-flight future so only one ``fetch()`` runs.

    The cache stores ``{"v": <payload>, "t": <unix_seconds>}`` so we can
    compute age on the read side without depending on Redis TTL precision.
    Hard TTL = ``fresh_for + max_stale`` so dead data eventually expires.
    """
    # Phase 6 observability — record fresh/stale/miss/computed per key.
    from infra import observability as _obs

    now = int(time.time())

    # Fast path — single Redis GET, return if fresh or kick refresh if stale.
    cached = await get(key)
    if cached and isinstance(cached, dict) and "v" in cached and "t" in cached:
        age = now - int(cached["t"])
        if age < fresh_for:
            _obs.record_cache(key, "fresh")
            return cached["v"]  # type: ignore[no-any-return]
        if age < fresh_for + max_stale:
            _obs.record_cache(key, "stale")
            # Stale but usable — fire-and-forget background refresh.
            asyncio.create_task(_refresh_in_background(key, fetch, fresh_for, max_stale, lock_ttl))
            return cached["v"]  # type: ignore[no-any-return]

    _obs.record_cache(key, "miss")

    # Cold miss — collapse concurrent callers in this process.
    async with _inflight_lock:
        existing = _inflight.get(key)
    if existing is not None:
        _obs.record_single_flight_collapsed(key)
        return await existing  # type: ignore[no-any-return]

    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    async with _inflight_lock:
        # Re-check after acquiring lock — another task may have started.
        existing = _inflight.get(key)
        if existing is not None:
            _obs.record_single_flight_collapsed(key)
            return await existing  # type: ignore[no-any-return]
        _inflight[key] = fut

    try:
        # Cross-process single-flight via Redis lock. If another worker
        # is already computing, briefly wait then re-read the cache. We
        # intentionally do NOT block forever — if the lock holder dies
        # we'd rather compute ourselves than hang the request.
        got_lock = await acquire_lock(f"sf:{key}", ttl_secs=lock_ttl)
        if not got_lock:
            for _ in range(int(lock_ttl * 2)):  # poll up to lock_ttl seconds
                await asyncio.sleep(0.5)
                cached = await get(key)
                if cached and isinstance(cached, dict) and "v" in cached:
                    fut.set_result(cached["v"])
                    return cached["v"]  # type: ignore[no-any-return]
            # Lock holder didn't finish — fall through and compute.

        try:
            value = await fetch()
            await cache_set(key, {"v": value, "t": int(time.time())},
                            ttl=fresh_for + max_stale)
            _obs.record_cache(key, "computed")
            fut.set_result(value)
            return value
        finally:
            if got_lock:
                await release_lock(f"sf:{key}")
    except BaseException as e:
        if not fut.done():
            fut.set_exception(e)
        raise
    finally:
        async with _inflight_lock:
            _inflight.pop(key, None)


async def _refresh_in_background(
    key: str,
    fetch: Callable[[], Awaitable[Any]],
    fresh_for: int,
    max_stale: int,
    lock_ttl: int,
):
    """Background refresher fired by SWR. Single-flight via Redis lock so
    multiple stale reads don't all kick off duplicate refreshes."""
    if not await acquire_lock(f"swr:{key}", ttl_secs=lock_ttl):
        return  # someone else is already refreshing
    try:
        value = await fetch()
        await cache_set(key, {"v": value, "t": int(time.time())},
                        ttl=fresh_for + max_stale)
    except Exception as e:
        logger.debug("SWR refresh failed for %s: %s", key, e)
    finally:
        await release_lock(f"swr:{key}")
