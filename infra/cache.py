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
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
    TimeoutError as RedisTimeoutError,
)

T = TypeVar("T")

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:8002/0")
_REDIS_MAX_CONNECTIONS = int(os.getenv("REDIS_MAX_CONNECTIONS", "50"))

# Sentinel topology — comma-separated host:port list + master name.
# When both are set, Sentinel mode is used and REDIS_URL is ignored for
# the master connection (the auth/db come from REDIS_URL components if
# present, but the host is resolved via Sentinel).  Defaults: not used.
#
# Example .env for HA:
#   REDIS_SENTINELS=sentinel-1:26379,sentinel-2:26379,sentinel-3:26379
#   REDIS_MASTER_NAME=mymaster
#   REDIS_URL=redis://:s3cr3t@unused/0   ← auth + db come from here
#: Read deadline, not just connect.  ``socket_connect_timeout`` alone
#: covers the handshake; a Redis that ACCEPTS and then stops answering
#: (a BGSAVE fork stall, swap thrash, an iptables DROP after ESTABLISHED,
#: a fenced Sentinel master) leaves every await hanging forever, because
#: redis-py's read falls to an un-timed branch when ``socket_timeout`` is
#: None.  The worst instance is boot: ``init_redis`` awaits ``ping()``
#: inside the API's lifespan, so no gunicorn worker ever reaches a
#: serving state and the 90s worker timeout turns it into a restart loop
#: — which is the opposite of this module's promise that "all operations
#: gracefully fall back to no-ops".
_REDIS_SOCKET_TIMEOUT = float(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))
#: Let redis-py notice a connection that died under it, so a pooled
#: socket is re-established rather than handed out dead.
_REDIS_HEALTH_CHECK_S = int(os.getenv("REDIS_HEALTH_CHECK_INTERVAL", "30"))

_REDIS_SENTINELS = os.getenv("REDIS_SENTINELS", "").strip()
_REDIS_MASTER_NAME = os.getenv("REDIS_MASTER_NAME", "mymaster").strip()

_pool: Optional[aioredis.Redis] = None
_available: bool = False


def _parse_sentinels() -> list[tuple[str, int]]:
    """Parse REDIS_SENTINELS into a list of (host, port) tuples."""
    out: list[tuple[str, int]] = []
    for entry in _REDIS_SENTINELS.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            host, port_str = entry.rsplit(":", 1)
            try:
                out.append((host, int(port_str)))
            except ValueError:
                logger.warning("Skipping malformed REDIS_SENTINELS entry: %s", entry)
        else:
            out.append((entry, 26379))  # default Sentinel port
    return out


async def init_redis() -> bool:
    """Initialize the Redis connection pool.  Returns True if connected.

    Picks between two modes:

    * **Sentinel** (production HA): when ``REDIS_SENTINELS`` is set, uses
      ``redis.asyncio.sentinel.Sentinel`` to resolve the current master.
      Auth + DB-index are taken from ``REDIS_URL`` if present so the same
      env can carry credentials.  Failover takes ~30s — operations during
      that window may raise transient ``ConnectionError`` which the
      caller fail-open path absorbs.

    * **Direct** (dev / single-node): uses ``aioredis.from_url(REDIS_URL)``.
      No failover; the previous default behavior.

    Either mode falls back to "Redis disabled" on connection failure so
    the bot keeps running with in-memory cache.
    """
    global _pool, _available

    sentinel_list = _parse_sentinels()

    if sentinel_list:
        try:
            from redis.asyncio.sentinel import Sentinel
            # Extract auth + db from REDIS_URL if it carries them.
            # ``redis://:password@host/0`` → password='password', db=0.
            # Sentinel.master_for accepts these directly.
            from urllib.parse import urlparse
            parsed = urlparse(REDIS_URL or "redis://:@unused/0")
            password = parsed.password
            db = 0
            if parsed.path and parsed.path != "/":
                try:
                    db = int(parsed.path.lstrip("/"))
                except ValueError:
                    db = 0

            sentinel = Sentinel(
                sentinel_list,
                socket_connect_timeout=5,
                socket_timeout=_REDIS_SOCKET_TIMEOUT,
                password=password,
            )
            _pool = sentinel.master_for(
                _REDIS_MASTER_NAME,
                db=db,
                password=password,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=_REDIS_SOCKET_TIMEOUT,
                health_check_interval=_REDIS_HEALTH_CHECK_S,
                max_connections=_REDIS_MAX_CONNECTIONS,
                retry_on_timeout=True,
            )
            await _pool.ping()  # type: ignore[misc]
            _available = True
            logger.info(
                "Redis connected via Sentinel (master=%s, sentinels=%d, max_conn=%d)",
                _REDIS_MASTER_NAME, len(sentinel_list), _REDIS_MAX_CONNECTIONS,
            )
            return True
        except Exception as e:
            logger.warning(
                "Sentinel mode failed (%s) — falling back to in-memory", e,
            )
            _pool = None
            _available = False
            return False

    if not REDIS_URL:
        logger.info("REDIS_URL not set — Redis disabled")
        return False

    try:
        _pool = aioredis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=_REDIS_SOCKET_TIMEOUT,
            health_check_interval=_REDIS_HEALTH_CHECK_S,
            max_connections=_REDIS_MAX_CONNECTIONS,
            retry_on_timeout=True,
        )
        await _pool.ping()  # type: ignore[misc]
        _available = True
        logger.info("Redis connected: %s (max_connections=%d)", REDIS_URL, _REDIS_MAX_CONNECTIONS)
        return True
    except Exception as e:
        _note_down(e)
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


#: How long to wait before re-testing a Redis that went away.  A failed
#: probe is cheap; a stampede of them is not.
_REPROBE_COOLDOWN_S = float(os.getenv("REDIS_REPROBE_SECONDS", "10"))
_last_probe: float = 0.0
#: A STRONG reference to the in-flight probe.  asyncio holds only a weak
#: one, so a fire-and-forget task can be collected before it ever runs —
#: which is exactly what happened: the probe was scheduled, never
#: executed, and Redis stayed marked down while being perfectly healthy.
#: Keeping one also means only one probe is ever in flight.
_probe_task: "asyncio.Task | None" = None


def _note_down(exc: BaseException) -> None:
    """Flip ``_available`` when a call fails for a CONNECTION reason.

    ``_available`` used to be a one-time boot verdict — set in
    ``init_redis`` and never touched again.  So a Redis that died AFTER
    startup left ``is_available()`` returning True forever: every
    consumer stayed on the Redis branch, each helper's ``except``
    returned its neutral value, and callers read that neutral value as
    fact — ``exists()`` False became "no flag set", which downstream is
    "not in breach", "not revoked", "not yet alerted".  The carefully
    written in-process fallbacks all over this codebase became
    unreachable dead code at precisely the moment they existed for.

    Only connection-shaped errors count.  A WRONGTYPE or a decode error
    means Redis is fine and the caller is not, and marking the whole
    cache down for that would be its own outage.
    """
    global _available
    if isinstance(exc, (RedisConnectionError, RedisTimeoutError, OSError)):
        if _available:
            logger.warning("Redis unreachable (%s) — falling back until it returns",
                           type(exc).__name__)
        _available = False


async def _reprobe() -> None:
    """One attempt to bring Redis back."""
    global _available
    try:
        if _pool is None:
            await init_redis()
            return
        await _pool.ping()
        if not _available:
            logger.info("Redis reachable again — re-enabled")
        _available = True
    except Exception as e:
        _note_down(e)
        pass                            # still down; the cooldown paces us


def _schedule_reprobe() -> None:
    """Ask for a re-probe without blocking a sync caller.

    ``is_available()`` is sync and called from everywhere, so it cannot
    await.  And it cannot be the consumers' job either: once the flag is
    False they take their fallback and never touch Redis again, so
    nothing would ever discover that it came back.  Firing a background
    task from the availability check itself is what closes that loop —
    the answer this call returns is stale by design, and the next one is
    right.
    """
    global _last_probe, _probe_task
    if _probe_task is not None and not _probe_task.done():
        return                          # one in flight is enough
    now = time.monotonic()
    if now - _last_probe < _REPROBE_COOLDOWN_S:
        return
    _last_probe = now
    try:
        _probe_task = asyncio.get_running_loop().create_task(_reprobe())
    except RuntimeError:
        pass            # no loop in this context; the next async caller tries


def is_available() -> bool:
    """Check if Redis is connected and usable.

    A False answer also ASKS whether that is still true.  Consumers
    branch on this and take their in-process fallback when it is False,
    so once it flips nothing would ever touch Redis again — and nothing
    would ever discover it came back.  The probe is fired in the
    background: this answer is stale by design, the next one is right.
    """
    if not _available:
        _schedule_reprobe()
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
        _note_down(e)
        logger.debug(f"Redis GET {key}: {e}")
        return None


async def cache_set(key: str, value: Any, ttl: int = 120):
    """Store a JSON-encoded value in Redis with a TTL (seconds)."""
    if not _available or not _pool:
        return
    try:
        await _pool.setex(key, ttl, json.dumps(value, default=str))
    except Exception as e:
        _note_down(e)
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
        _note_down(e)
        logger.debug(f"Redis DEL {key}: {e}")


async def exists(key: str) -> bool:
    """Check if a key exists in Redis."""
    if not _available or not _pool:
        return False
    try:
        return bool(await _pool.exists(key))
    except Exception as e:
        _note_down(e)
        return False


async def scan_keys(pattern: str, batch: int = 500) -> list[str]:
    """Return every key matching ``pattern``.

    Uses non-blocking SCAN (not KEYS) so a sweep across thousands of
    keys doesn't stall the server.  Decodes bytes → str so callers
    don't have to.  Returns an empty list on failure rather than
    raising — sweep workers shouldn't crash on transient Redis errors.
    """
    if not _available or not _pool:
        return []
    try:
        out: list[str] = []
        async for k in _pool.scan_iter(match=pattern, count=batch):
            if isinstance(k, bytes):
                out.append(k.decode("utf-8", errors="replace"))
            else:
                out.append(str(k))
        return out
    except Exception as e:
        _note_down(e)
        logger.warning("Redis SCAN %s: %s", pattern, e)
        return []


async def setex_flag(key: str, ttl: int):
    """Set a flag key with TTL (for cooldowns).  Value is always '1'."""
    if not _available or not _pool:
        return
    try:
        await _pool.setex(key, ttl, "1")
    except Exception as e:
        _note_down(e)
        logger.debug(f"Redis SETEX flag {key}: {e}")


# ── Counters (metering) ──────────────────────────────────────────

async def incr(key: str, ttl: int) -> None:
    """Best-effort counter increment with a TTL refresh on first write.

    Built for request metering: Redis is the ONE store all gunicorn
    workers share, so counters here are platform-true where in-process
    counters are per-worker fragments.  Silently drops the increment
    when Redis is down — metering must never add failure modes to the
    request path.
    """
    if not _available or not _pool:
        return
    try:
        count = await _pool.incr(key)  # type: ignore[misc]
        if count == 1:
            await _pool.expire(key, ttl)  # type: ignore[misc]
    except Exception as e:
        _note_down(e)
        logger.debug("Redis INCR %s: %s", key, e)


async def hincrby(key: str, field: str, ttl: int, amount: int = 1) -> None:
    """Best-effort hash-field increment (per-account metering buckets)."""
    if not _available or not _pool:
        return
    try:
        await _pool.hincrby(key, field, amount)  # type: ignore[misc]
        await _pool.expire(key, ttl)  # type: ignore[misc]
    except Exception as e:
        _note_down(e)
        logger.debug("Redis HINCRBY %s.%s: %s", key, field, e)


async def get_int(key: str) -> int | None:
    """Read a raw counter as int.  None on miss/error (vs 0 = counted zero)."""
    if not _available or not _pool:
        return None
    try:
        raw = await _pool.get(key)
        return int(raw) if raw is not None else None
    except Exception as e:
        _note_down(e)
        logger.debug("Redis GET int %s: %s", key, e)
        return None


async def hgetall_int(key: str) -> dict[str, int]:
    """Read a counter hash as {field: int}.  Empty dict on miss/error."""
    if not _available or not _pool:
        return {}
    try:
        raw = await _pool.hgetall(key)  # type: ignore[misc]
        out: dict[str, int] = {}
        for k, v in (raw or {}).items():
            kk = k.decode() if isinstance(k, bytes) else str(k)
            try:
                out[kk] = int(v)
            except (TypeError, ValueError):
                continue
        return out
    except Exception as e:
        _note_down(e)
        logger.debug("Redis HGETALL %s: %s", key, e)
        return {}


async def llen(key: str) -> int | None:
    """List length (e.g. the arq job queue).  None on miss/error."""
    if not _available or not _pool:
        return None
    try:
        return int(await _pool.llen(key))  # type: ignore[misc]
    except Exception as e:
        _note_down(e)
        logger.debug("Redis LLEN %s: %s", key, e)
        return None


async def used_memory_mb() -> float | None:
    """Redis used_memory from INFO, in MB.  None when unavailable."""
    if not _available or not _pool:
        return None
    try:
        info = await _pool.info("memory")  # type: ignore[misc]
        used = info.get("used_memory")
        return round(float(used) / (1024 * 1024), 1) if used else None
    except Exception as e:
        _note_down(e)
        logger.debug("Redis INFO memory: %s", e)
        return None


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
        _note_down(e)
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
        _note_down(e)
        logger.debug("Redis acquire_lock %s: %s", name, e)
        return True  # fail open


async def heartbeat_lock(name: str, ttl_secs: int = 60) -> bool:
    """Refresh the TTL of an already-held lock.  Returns True if still held."""
    if not _available or not _pool:
        return True
    try:
        return bool(await _pool.expire(f"lock:{name}", ttl_secs))  # type: ignore[misc]
    except Exception as e:
        _note_down(e)
        logger.debug("Redis heartbeat_lock %s: %s", name, e)
        return True


async def release_lock(name: str):
    """Release a distributed lock."""
    if not _available or not _pool:
        return
    try:
        await _pool.delete(f"lock:{name}")
    except Exception as e:
        _note_down(e)
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
        _note_down(e)
        logger.debug(f"Redis SADD {key}: {e}")


async def smembers(key: str) -> set[str]:
    """Get all members of a Redis SET."""
    if not _available or not _pool:
        return set()
    try:
        result = await _pool.smembers(key)  # type: ignore[misc]
        return result
    except Exception as e:
        _note_down(e)
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
        _note_down(e)
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
        _note_down(e)
        logger.debug(f"Redis RPUSH {key}: {e}")


async def lrange(key: str, start: int = 0, end: int = -1) -> list[str]:
    """Get a range of values from a Redis LIST."""
    if not _available or not _pool:
        return []
    try:
        return await _pool.lrange(key, start, end)  # type: ignore[misc]
    except Exception as e:
        _note_down(e)
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
        _note_down(e)
        logger.debug("SWR refresh failed for %s: %s", key, e)
    finally:
        await release_lock(f"swr:{key}")
