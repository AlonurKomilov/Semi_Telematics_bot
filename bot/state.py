"""Mutable runtime state: database singleton, client cache, and bounded caches.

This module owns all mutable state that was previously in bot/config.py.
Pure env-var configuration stays in bot/config.py (read-only).
For backward compatibility, bot/config.py re-exports everything from here.
"""

import asyncio
import os
import time

from cachetools import LRUCache

from typing import Union

from database import Database, TenantRouter, LegacyRouter
from samsara_client import MultiCompanyClient, build_multi_company_client

from bot.config import DATABASE_PATH, SAMSARA_BASE_URL, RATE_LIMIT_SECONDS

# ── Database ─────────────────────────────────────────────────────

# Feature flag: set MULTI_TENANT_DB=1 to use per-tenant SQLite databases
MULTI_TENANT = bool(os.getenv("MULTI_TENANT_DB"))

db = Database(DATABASE_PATH)

router: Union[TenantRouter, LegacyRouter]
if MULTI_TENANT:
    _data_dir = os.path.dirname(DATABASE_PATH) or "data"
    router = TenantRouter(
        os.path.join(_data_dir, "platform.db"),
        os.path.join(_data_dir, "tenants"),
    )
else:
    router = LegacyRouter(db)

# ── In-memory caches (bounded to prevent unbounded growth) ───────

_client_cache: dict[int, MultiCompanyClient] = {}
_known_faults = LRUCache(maxsize=10_000)      # "acct:ORG:vid" → set(codes)
_active_messages = LRUCache(maxsize=5_000)     # (chat_id, user_id) → [msg_ids]

# Rate limiter: (user_id, command) → last_use_timestamp
_rate_limits = LRUCache(maxsize=10_000)


def check_rate_limit(user_id: int, command: str) -> bool:
    """Return True if the command is allowed (not rate-limited).

    Returns False if the user should be throttled.
    """
    key = (user_id, command)
    now = time.time()
    last = _rate_limits.get(key, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return False
    _rate_limits[key] = now
    return True


# ── Client cache helpers ─────────────────────────────────────────

_client_lock = asyncio.Lock()

async def get_client(account_id: int) -> MultiCompanyClient:
    """Get or build a MultiCompanyClient for an account."""
    if account_id in _client_cache:
        return _client_cache[account_id]
    async with _client_lock:
        # Double-check after acquiring lock
        if account_id in _client_cache:
            return _client_cache[account_id]
        companies = await db.get_account_companies(account_id)
        client = build_multi_company_client(companies, SAMSARA_BASE_URL)
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
    companies = await db.get_account_companies(account_id)
    return [o.code for o in companies]
