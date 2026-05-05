"""TenantContext — per-account isolated runtime state.

Each account gets its own TenantContext holding:
  - TenantDB reference (companies, alerts, maintenance, etc.)
  - Samsara MultiCompanyClient (lazily built)
  - company_display / org_ids dictionaries (per-tenant, not global)
  - LRU caches (known faults, active messages, rate limits)
  - Redis key prefix for namespaced caching

This replaces the module-level globals in bot/state.py and the
shared COMPANY_DISPLAY / ORG_IDS dicts in samsara_client.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from cachetools import LRUCache

from adapters.samsara.client import MultiCompanyClient, build_multi_company_client

from .config import SAMSARA_BASE_URL, RATE_LIMIT_SECONDS

if TYPE_CHECKING:
    from adapters.storage.tenant_db import TenantDB

logger = logging.getLogger(__name__)


class TenantContext:
    """Per-account isolated runtime state.

    Created and managed by TenantRegistry.  One instance per active account.
    """

    __slots__ = (
        "account_id",
        "db",
        "samsara",
        "company_display",
        "org_ids",
        "known_faults",
        "active_messages",
        "rate_limits",
        "redis_prefix",
        "_client_lock",
    )

    def __init__(self, account_id: int, db: TenantDB):
        self.account_id = account_id
        self.db = db
        self.samsara: MultiCompanyClient | None = None
        self.company_display: dict[str, str] = {}
        self.org_ids: dict[str, str] = {}
        self.known_faults: LRUCache = LRUCache(maxsize=10_000)
        self.active_messages: LRUCache = LRUCache(maxsize=5_000)
        self.rate_limits: LRUCache = LRUCache(maxsize=10_000)
        self.redis_prefix = f"t:{account_id}:"
        self._client_lock = asyncio.Lock()

    async def ensure_client(self, platform_db) -> MultiCompanyClient:
        """Lazily build or return the Samsara client for this account.

        Args:
            platform_db: The platform database (needed to look up companies).

        Returns:
            The MultiCompanyClient for this account's companies.
        """
        if self.samsara is not None:
            return self.samsara
        async with self._client_lock:
            # Double-check after acquiring lock
            if self.samsara is not None:
                return self.samsara
            companies = await platform_db.get_account_companies(self.account_id)
            client = build_multi_company_client(companies, SAMSARA_BASE_URL, account_id=self.account_id)
            await client.prefetch_org_ids()
            # Populate per-tenant display names and org IDs
            for co in companies:
                self.company_display[co.code] = co.display_name or co.code
            self.org_ids = client.org_ids
            self.samsara = client
            return client

    async def invalidate_client(self):
        """Drop cached client — call after adding/removing companies."""
        old = self.samsara
        self.samsara = None
        self.company_display.clear()
        self.org_ids.clear()
        if old:
            await old.close()

    def check_rate_limit(self, user_id: int, command: str) -> bool:
        """Return True if the command is allowed (not rate-limited).

        Sync version — uses in-memory LRUCache only.
        Use check_rate_limit_async() in async contexts to also enforce
        via Redis (cross-process, survives restarts).
        """
        key = (user_id, command)
        now = time.time()
        last = self.rate_limits.get(key, 0)
        if now - last < RATE_LIMIT_SECONDS:
            return False
        self.rate_limits[key] = now
        return True

    async def check_rate_limit_async(self, user_id: int, command: str) -> bool:
        """Async rate limit: Redis first, in-memory LRUCache fallback.

        Redis key: ``t:{account_id}:rl:{user_id}:{command}``
        """
        import infra.cache as _redis_cache
        if _redis_cache.is_available():
            rl_key = f"{self.redis_prefix}rl:{user_id}:{command}"
            allowed = await _redis_cache.rate_limit_check(rl_key, RATE_LIMIT_SECONDS, 1)
            if not allowed:
                return False
            # Keep in-memory in sync so sync callers stay consistent
            self.rate_limits[(user_id, command)] = time.time()
            return True

        # In-memory fallback
        key = (user_id, command)
        now = time.time()
        last = self.rate_limits.get(key, 0)
        if now - last < RATE_LIMIT_SECONDS:
            return False
        self.rate_limits[key] = now
        return True

    # ── Redis helpers (namespaced) ────────────────────────────────

    def redis_key(self, key: str) -> str:
        """Prefix a key with the tenant namespace: ``t:{account_id}:{key}``."""
        return f"{self.redis_prefix}{key}"

    async def close(self):
        """Release resources held by this tenant context."""
        if self.samsara:
            await self.samsara.close()
            self.samsara = None
        logger.info("TenantContext %d closed", self.account_id)

    def __repr__(self) -> str:
        return f"<TenantContext account_id={self.account_id}>"
