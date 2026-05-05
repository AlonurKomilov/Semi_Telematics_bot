"""TenantRegistry — manages TenantContext lifecycle.

Lazy-initializes TenantContext instances on first access and caches them.
Supports LRU eviction when the number of active tenants exceeds max_tenants,
ensuring memory stays bounded as the number of accounts grows.
"""

from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict

from .tenant import TenantContext

logger = logging.getLogger(__name__)

# Maximum number of tenant contexts to keep in memory.
# Beyond this, the least-recently-used context is evicted.
_DEFAULT_MAX_TENANTS = int(os.getenv("MAX_ACTIVE_TENANTS", "200"))


class TenantRegistry:
    """Manages per-account TenantContext instances with LRU eviction.

    Lazy-initializes contexts on first access and caches them by account_id.
    When the cache exceeds max_tenants, the least-recently-accessed context
    is evicted and its resources are closed.
    Call close_all() during shutdown to release all resources.
    """

    def __init__(self, max_tenants: int = _DEFAULT_MAX_TENANTS):
        self._tenants: OrderedDict[int, TenantContext] = OrderedDict()
        self._max_tenants = max_tenants
        self._access_times: dict[int, float] = {}

    async def get(self, account_id: int) -> TenantContext:
        """Get or create a TenantContext for the given account."""
        if account_id in self._tenants:
            # Move to end (most recently used)
            self._tenants.move_to_end(account_id)
            self._access_times[account_id] = time.monotonic()
            return self._tenants[account_id]

        # Import here to avoid circular imports at module level
        from . import platform as _platform

        tenant_db = await _platform.get_tenant_db(account_id)
        ctx = TenantContext(account_id, tenant_db)
        self._tenants[account_id] = ctx
        self._access_times[account_id] = time.monotonic()
        logger.info("TenantContext created for account %d (active: %d)", account_id, len(self._tenants))

        # Evict LRU if over capacity
        await self._evict_if_needed()

        return ctx

    async def _evict_if_needed(self):
        """Evict the least-recently-used tenant context if over capacity."""
        while len(self._tenants) > self._max_tenants:
            evict_id, evict_ctx = self._tenants.popitem(last=False)
            idle = time.monotonic() - self._access_times.pop(evict_id, time.monotonic())
            try:
                await evict_ctx.close()
            except Exception:
                logger.exception("Error evicting TenantContext %d", evict_id)
            try:
                # Also release the cached Samsara client so its HTTP sessions are closed
                from infra.services import invalidate_client
                await invalidate_client(evict_id)
            except Exception:
                logger.exception("Error releasing Samsara client for evicted account %d", evict_id)
            logger.info("TenantContext %d evicted (%.0fs idle)", evict_id, idle)

    async def invalidate(self, account_id: int):
        """Remove and close a tenant context (e.g., after config change)."""
        ctx = self._tenants.pop(account_id, None)
        self._access_times.pop(account_id, None)
        if ctx:
            await ctx.close()
            logger.info("TenantContext invalidated for account %d", account_id)

    async def close_all(self):
        """Close all tenant contexts — call during shutdown."""
        for ctx in self._tenants.values():
            try:
                await ctx.close()
            except Exception:
                logger.exception("Error closing TenantContext %d", ctx.account_id)
        self._tenants.clear()
        self._access_times.clear()
        logger.info("All TenantContexts closed")

    @property
    def active_accounts(self) -> list[int]:
        """List currently active account IDs."""
        return list(self._tenants.keys())

    def __contains__(self, account_id: int) -> bool:
        return account_id in self._tenants

    def __len__(self) -> int:
        return len(self._tenants)
