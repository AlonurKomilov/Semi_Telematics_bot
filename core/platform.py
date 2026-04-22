"""Platform-level database access.

Owns the router singleton and provides helpers for platform-scope
(cross-account) and tenant-scope (per-account) database access.

Usage after initialize():
    platform = get_platform_db()           # users, accounts, invites
    tenant   = await get_tenant_db(42)     # companies, alerts, maintenance
"""

from __future__ import annotations

import logging
import os
from typing import Union

from adapters.storage import Database, TenantRouter, LegacyRouter

from .config import DATABASE_PATH, MULTI_TENANT

logger = logging.getLogger(__name__)

_db: Database | None = None
_router: Union[TenantRouter, LegacyRouter] | None = None


async def initialize():
    """Initialize the database layer (platform DB + tenant router).

    Must be called once at startup before any DB access.
    """
    global _db, _router

    _db = Database(DATABASE_PATH)
    await _db.initialize()

    if MULTI_TENANT:
        data_dir = os.path.dirname(DATABASE_PATH) or "data"
        _router = TenantRouter(
            os.path.join(data_dir, "platform.db"),
            os.path.join(data_dir, "tenants"),
        )
        await _router.initialize()
    else:
        _router = LegacyRouter(_db)

    logger.info("Database initialized (multi_tenant=%s)", MULTI_TENANT)


def get_db() -> Database:
    """Get the legacy Database singleton (all tables in one file)."""
    assert _db is not None, "core.platform not initialized — call core.startup.initialize() first"
    return _db


def get_router() -> Union[TenantRouter, LegacyRouter]:
    """Get the tenant router instance."""
    assert _router is not None, "core.platform not initialized — call core.startup.initialize() first"
    return _router


def get_platform_db():
    """Get the platform database for cross-account queries (users, accounts, invites)."""
    assert _router is not None, "core.platform not initialized — call core.startup.initialize() first"
    return _router.platform


async def get_tenant_db(account_id: int):
    """Get the tenant database for account-scoped queries (companies, alerts, etc.)."""
    assert _router is not None, "core.platform not initialized — call core.startup.initialize() first"
    return await _router.get_tenant(account_id)


async def close():
    """Close all database connections."""
    global _db, _router
    if _router:
        await _router.close()
        _router = None
    if _db:
        await _db.close()
        _db = None
    logger.info("Database connections closed")
