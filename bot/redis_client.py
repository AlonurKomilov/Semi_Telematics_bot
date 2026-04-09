"""Redis client — optional async connection pool for caching and state.

If REDIS_URL is not set or Redis is unreachable, all operations gracefully
fall back to no-ops so the bot still works without Redis.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:8002/0")

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
        )
        await _pool.ping()
        _available = True
        logger.info(f"Redis connected: {REDIS_URL}")
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


async def set(key: str, value: Any, ttl: int = 120):
    """Store a JSON-encoded value in Redis with a TTL (seconds)."""
    if not _available or not _pool:
        return
    try:
        await _pool.setex(key, ttl, json.dumps(value, default=str))
    except Exception as e:
        logger.debug(f"Redis SET {key}: {e}")


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


# ── Set helpers (for fault code tracking) ────────────────────────

async def sadd(key: str, *members: str, ttl: int = 86400):
    """Add members to a Redis SET and reset TTL."""
    if not _available or not _pool:
        return
    try:
        if members:
            await _pool.sadd(key, *members)
            await _pool.expire(key, ttl)
    except Exception as e:
        logger.debug(f"Redis SADD {key}: {e}")


async def smembers(key: str) -> set[str]:
    """Get all members of a Redis SET."""
    if not _available or not _pool:
        return set()
    try:
        return await _pool.smembers(key)
    except Exception:
        return set()


async def sset(key: str, members: set[str], ttl: int = 86400):
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
            await _pool.rpush(key, *values)
            await _pool.expire(key, ttl)
    except Exception as e:
        logger.debug(f"Redis RPUSH {key}: {e}")


async def lrange(key: str, start: int = 0, end: int = -1) -> list[str]:
    """Get a range of values from a Redis LIST."""
    if not _available or not _pool:
        return []
    try:
        return await _pool.lrange(key, start, end)
    except Exception:
        return []


async def list_delete(key: str):
    """Delete a Redis LIST key."""
    await delete(key)
