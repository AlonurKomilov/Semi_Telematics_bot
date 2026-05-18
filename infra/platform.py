"""Platform-level database access.

Owns the ``Database`` singleton — a single shared DB holding both
platform-scope tables (accounts, users, invites, billing) and
tenant-scope tables (companies, maintenance, work_orders, alerts).
Tenant isolation is enforced at the SQL layer via ``account_id``
filtering on every per-tenant query, not at the file/process level.

Usage after ``initialize()``::

    platform = get_platform_db()           # users, accounts, invites, …
    tenant   = await get_tenant_db(42)     # companies, alerts, …

Both helpers return the same ``Database`` instance today.  They exist
as distinct names so callers express *intent* — "I'm doing
cross-account work" vs "I'm acting on behalf of account N" — and so
future schema-level multi-tenancy (e.g. per-tenant Postgres schemas)
can swap behaviour without touching call sites.
"""

from __future__ import annotations

import logging

from adapters.storage import Database

logger = logging.getLogger(__name__)

_db: Database | None = None


async def initialize():
    """Initialize the database layer.  Must be called once at startup
    before any DB access — typically from ``infra.startup.initialize``.

    The Database class reads ``DATABASE_URL`` from the environment;
    we don't pass a path because the SQLite branch was retired and
    the constructor's ``path`` argument is now a backwards-compat
    no-op.
    """
    global _db
    _db = Database()
    await _db.initialize()
    logger.info("Database initialized")


def get_db() -> Database:
    """Get the ``Database`` singleton.  Same instance returned by
    ``get_platform_db`` and ``get_tenant_db`` — the three names exist
    to express the caller's scope intent, not to select a backend."""
    assert _db is not None, "infra.platform not initialized — call infra.startup.initialize() first"
    return _db


def get_platform_db() -> Database:
    """Cross-account queries — users, accounts, invites, billing,
    AI usage logs.  Returns the same instance as ``get_db()``."""
    return get_db()


async def get_tenant_db(account_id: int) -> Database:
    """Account-scoped queries — companies, maintenance, work_orders,
    alerts, parking, etc.  Returns the same instance as ``get_db()``;
    the ``account_id`` parameter is kept on the signature so callers
    advertise *which* tenant they're acting on (helpful when reading
    the call site).  All per-tenant queries internally filter by
    ``account_id`` via the mixin methods.
    """
    # account_id intentionally unused at this layer — kept on the
    # signature so the API contract reads correctly and so future
    # per-tenant routing (PG schemas / sharded DBs) can be added
    # without changing call sites.
    _ = account_id
    return get_db()


def get_router():
    """Backward-compatible shim exposed for callers that still use the
    router idiom (e.g. ``interfaces/api/deps.py``).  Returns an object
    with the same ``.platform`` property and ``.get_tenant()`` coro
    the old ``LegacyRouter`` exposed — wrapping the singleton so no
    caller needs to change.

    New code should call ``get_platform_db()`` / ``get_tenant_db()``
    directly; this shim exists so we can retire the router idiom
    incrementally without one mega-PR.
    """
    return _RouterShim()


class _RouterShim:
    """Thin façade preserving the old router API on top of the
    singleton Database.  Two methods, both delegate."""

    @property
    def platform(self) -> Database:
        return get_db()

    async def get_tenant(self, account_id: int) -> Database:
        _ = account_id
        return get_db()


async def close():
    """Close the underlying database connection.  Called from
    ``infra.startup.shutdown`` at graceful shutdown."""
    global _db
    if _db:
        await _db.close()
        _db = None
    logger.info("Database connections closed")
