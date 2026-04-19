"""Mutable runtime state: database singleton, client cache, and bounded caches.

This module owns all mutable state that was previously in bot/config.py.
Pure env-var configuration stays in bot/config.py (read-only).
For backward compatibility, bot/config.py re-exports everything from here.

NOTE (Phase 8): ``db`` and ``router`` are no longer created at import
time.  They are lazy proxies that delegate to ``core.platform`` after
``core.startup.initialize()`` has been called.
"""

import asyncio
import os
import time

from cachetools import LRUCache

from typing import Union

from database import Database, TenantRouter, LegacyRouter
from samsara_client import MultiCompanyClient, build_multi_company_client

from core.config import SAMSARA_BASE_URL, RATE_LIMIT_SECONDS

# ── Database ─────────────────────────────────────────────────────
# Lazy delegation to core.platform — unified DB layer (Phase 8).
# The old module-level `db = Database(...)` is replaced by a property-
# like accessor.  Most callers still import ``db`` from here or from
# bot.config and call ``db.xxx()`` — those keep working because after
# ``core.startup.initialize()`` runs, ``core.platform._db`` is ready.

# Feature flag (still read from env for backward compat)
MULTI_TENANT = bool(os.getenv("MULTI_TENANT_DB"))


def _get_db() -> Database:
    """Return the platform Database via core.platform (lazy)."""
    from core.platform import get_db
    return get_db()


def _get_router() -> Union[TenantRouter, LegacyRouter]:
    """Return the platform router via core.platform (lazy)."""
    from core.platform import get_router
    return get_router()


class _LazyDB:
    """Transparent proxy that forwards attribute access to core.platform._db.

    This lets existing code like ``from bot.state import db; db.some_method()``
    keep working without changes, while the actual Database instance is
    created in ``core.startup.initialize()`` instead of at import time.
    """

    def __getattr__(self, name):
        return getattr(_get_db(), name)


class _LazyRouter:
    """Transparent proxy for the tenant router."""

    @property
    def platform(self):
        return _get_router().platform

    async def get_tenant(self, account_id: int):
        return await _get_router().get_tenant(account_id)

    async def initialize(self):
        # Already initialized by core.startup — no-op.
        pass

    async def close(self):
        # Close managed by core.startup.shutdown() — no-op.
        pass

    def __getattr__(self, name):
        return getattr(_get_router(), name)


db = _LazyDB()       # type: ignore[assignment]
router = _LazyRouter()  # type: ignore[assignment]

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
    companies = await db.get_account_companies(account_id)
    return [o.code for o in companies]


# ── Multi-tenant DB helpers ──────────────────────────────────────


def get_platform_db():
    """Get the platform database for cross-account queries (users, accounts).

    In legacy mode returns the same ``db`` singleton.
    In multi-tenant mode returns the dedicated PlatformDB.
    """
    return router.platform


async def get_tenant_db(account_id: int):
    """Get the tenant database for account-scoped queries (companies, alerts, …).

    In legacy mode returns the same ``db`` singleton.
    In multi-tenant mode returns a per-account TenantDB.
    """
    return await router.get_tenant(account_id)
