"""Core service functions — platform-agnostic business helpers.

Provides Samsara client management, database access helpers, and rate
limiting.  These are the canonical definitions — ``bot.state`` and
``bot.config`` re-export them for backward compatibility.
"""

import asyncio
import time

from cachetools import LRUCache

from adapters.samsara.client import MultiCompanyClient, build_multi_company_client
from infra.config import SAMSARA_BASE_URL, RATE_LIMIT_SECONDS

# Re-export platform DB accessors so non-bot code has a single import.
from infra.platform import get_platform_db, get_tenant_db, get_db  # noqa: F401

# ── Client cache ─────────────────────────────────────────────────

_client_cache: dict[int, MultiCompanyClient] = {}
_client_lock = asyncio.Lock()


async def get_client(account_id: int) -> MultiCompanyClient:
    """Get or build a MultiCompanyClient for an account."""
    if account_id in _client_cache:
        return _client_cache[account_id]
    async with _client_lock:
        # Double-check after acquiring lock
        if account_id in _client_cache:
            return _client_cache[account_id]
        db = get_db()
        companies = await db.get_account_companies(account_id)
        client = build_multi_company_client(companies, SAMSARA_BASE_URL, account_id=account_id)
        await client.prefetch_org_ids()
        _client_cache[account_id] = client
        return client


async def invalidate_client(account_id: int):
    """Drop cached client — call after adding/removing companies."""
    old = _client_cache.pop(account_id, None)
    if old:
        await old.close()


async def get_user_company_codes(account_id: int) -> list[str]:
    """Get sorted company codes for an account."""
    db = get_db()
    companies = await db.get_account_companies(account_id)
    return [o.code for o in companies]


# ── Rate limiting ────────────────────────────────────────────────

_rate_limits = LRUCache(maxsize=10_000)

# ── Shared UI state (used by both bot/ and capabilities/alerting/) ───

_active_messages: dict = LRUCache(maxsize=5_000)  # (account_id, chat_id, user_id) → [msg_ids]


def check_rate_limit(user_id: int, command: str) -> bool:
    """Return True if the command is allowed (not rate-limited).

    Returns False if the user should be throttled.
    In-memory fallback — use check_rate_limit_async() in async contexts
    to also enforce via Redis (cross-process, survives restarts).
    """
    key = (user_id, command)
    now = time.time()
    last = _rate_limits.get(key, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return False
    _rate_limits[key] = now
    return True


async def check_rate_limit_async(user_id: int, command: str) -> bool:
    """Async rate limit check: Redis first, in-memory LRUCache fallback.

    Redis key: ``rl:{user_id}:{command}`` (global, not tenant-scoped —
    use TenantContext.check_rate_limit_async for per-tenant keys).
    """
    import infra.cache as _redis_cache
    if _redis_cache.is_available():
        rl_key = f"rl:{user_id}:{command}"
        allowed = await _redis_cache.rate_limit_check(rl_key, RATE_LIMIT_SECONDS, 1)
        if not allowed:
            return False
        _rate_limits[(user_id, command)] = time.time()
        return True

    # In-memory fallback
    return check_rate_limit(user_id, command)
