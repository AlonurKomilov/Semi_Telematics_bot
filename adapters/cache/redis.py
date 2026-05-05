"""Backward-compat shim — real implementation moved to infra.cache."""
from infra.cache import *  # noqa: F401, F403
from infra.cache import (  # noqa: F401
    init_redis, close_redis, is_available,
    get, cache_set, delete, exists,
    setex_flag, rate_limit_check,
    acquire_lock, heartbeat_lock, release_lock,
    sadd, smembers, sset,
    rpush, lrange, list_delete,
)
