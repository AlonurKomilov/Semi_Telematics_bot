"""Mutable runtime state: database singleton, client cache, and bounded caches.

This module owns all mutable state that was previously in bot/config.py.
Pure env-var configuration stays in bot/config.py (read-only).
For backward compatibility, bot/config.py re-exports everything from here.

NOTE (Phase 8): ``db`` and ``router`` are no longer created at import
time.  They are lazy proxies that delegate to ``core.platform`` after
``core.startup.initialize()`` has been called.
"""

import os

from typing import Union

from adapters.storage import Database, TenantRouter, LegacyRouter

# Service functions (canonical home: core.services).
# Re-exported here so ``from interfaces.bot.state import get_client`` keeps working.
from infra.services import (  # noqa: F401
    _client_cache,
    _rate_limits,
    check_rate_limit,
    get_client,
    invalidate_client,
    get_user_company_codes,
    get_platform_db,
    get_tenant_db,
)

from infra.config import SAMSARA_BASE_URL, RATE_LIMIT_SECONDS  # noqa: F401

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
    from infra.platform import get_db
    return get_db()


def _get_router() -> Union[TenantRouter, LegacyRouter]:
    """Return the platform router via core.platform (lazy)."""
    from infra.platform import get_router
    return get_router()


class _LazyDB:
    """Transparent proxy that forwards attribute access to core.platform._db.

    This lets existing code like ``from interfaces.bot.state import db; db.some_method()``
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

# ── Shared caches (re-exported for backward compat) ──────────────
from infra.services import _active_messages  # noqa: F401, E402
