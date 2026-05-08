"""Tenant router — routes DB access to the correct per-tenant database.

Two implementations:
  - TenantRouter: production multi-DB router (platform.db + tenant_N.db)
  - LegacyRouter: wraps the existing single Database for backward compat

Usage:
    # Multi-tenant mode:
    router = TenantRouter("data/platform.db", "data/tenants/")
    await router.initialize()
    platform = router.platform
    tenant = await router.get_tenant(account_id)

    # Legacy single-DB mode:
    router = LegacyRouter(db)
    platform = router.platform  # same db instance
    tenant = await router.get_tenant(account_id)  # same db instance
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from .platform import PlatformDB
from .tenant import TenantDB

if TYPE_CHECKING:
    from .core import _DatabaseCore
    from . import Database

logger = logging.getLogger(__name__)


class TenantRouter:
    """Routes DB access to per-tenant SQLite databases.

    Future-proof: swap this class to route to remote hosts (Option C)
    without changing any caller code.
    """

    def __init__(self, platform_path: str, tenants_dir: str):
        self._platform_path = platform_path
        self._tenants_dir = tenants_dir
        self._platform: PlatformDB | None = None
        self._tenants: dict[int, TenantDB] = {}

    async def initialize(self):
        """Initialize the platform DB. Tenant DBs are lazy-loaded."""
        os.makedirs(self._tenants_dir, exist_ok=True)
        self._platform = PlatformDB(self._platform_path)
        await self._platform.initialize()

    @property
    def platform(self) -> PlatformDB:
        assert self._platform is not None, "TenantRouter not initialized"
        return self._platform

    async def get_tenant(self, account_id: int) -> TenantDB:
        """Get or create a TenantDB for the given account."""
        if account_id in self._tenants:
            return self._tenants[account_id]

        path = os.path.join(self._tenants_dir, f"tenant_{account_id}.db")
        tenant = TenantDB(path, account_id)
        await tenant.initialize()
        self._tenants[account_id] = tenant
        return tenant

    async def close(self):
        """Close all database connections."""
        if self._platform:
            await self._platform.close()
        for tenant in self._tenants.values():
            await tenant.close()
        self._tenants.clear()
        logger.info("TenantRouter: all connections closed")


class LegacyRouter:
    """Wraps the existing single Database instance for backward compatibility.

    Both `platform` and `get_tenant()` return the same Database instance,
    since all tables live in one file. This enables gradual migration:
    code can be written against the router interface while still using
    the old single-DB layout.
    """

    def __init__(self, db: Database):
        self._db = db

    async def initialize(self):
        """No-op — the underlying Database is already initialized."""
        pass

    @property
    def platform(self) -> Database:
        return self._db

    async def get_tenant(self, account_id: int) -> Database:
        """Return the same DB instance regardless of account_id."""
        return self._db

    async def close(self):
        """Close the underlying database."""
        await self._db.close()
