"""TenantRegistry — manages TenantContext lifecycle.

Lazy-initializes TenantContext instances on first access and caches them.
In later phases, BotRegistry will be added here for per-account Telegram bots.
"""

from __future__ import annotations

import logging

from .tenant import TenantContext

logger = logging.getLogger(__name__)


class TenantRegistry:
    """Manages per-account TenantContext instances.

    Lazy-initializes contexts on first access and caches them by account_id.
    Call close_all() during shutdown to release all resources.
    """

    def __init__(self):
        self._tenants: dict[int, TenantContext] = {}

    async def get(self, account_id: int) -> TenantContext:
        """Get or create a TenantContext for the given account."""
        if account_id in self._tenants:
            return self._tenants[account_id]

        # Import here to avoid circular imports at module level
        from . import platform as _platform

        tenant_db = await _platform.get_tenant_db(account_id)
        ctx = TenantContext(account_id, tenant_db)
        self._tenants[account_id] = ctx
        logger.info("TenantContext created for account %d", account_id)
        return ctx

    async def invalidate(self, account_id: int):
        """Remove and close a tenant context (e.g., after config change)."""
        ctx = self._tenants.pop(account_id, None)
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
        logger.info("All TenantContexts closed")

    @property
    def active_accounts(self) -> list[int]:
        """List currently active account IDs."""
        return list(self._tenants.keys())

    def __contains__(self, account_id: int) -> bool:
        return account_id in self._tenants

    def __len__(self) -> int:
        return len(self._tenants)
