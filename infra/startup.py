"""Platform lifecycle — initialize and shutdown shared infrastructure.

This is the single entry point for bringing up the platform layer.
Call initialize() FIRST, before starting bots or the API server.

    registry = await infra.startup.initialize()
    # ... start bots, API, scheduler ...
    await infra.startup.shutdown()
"""

from __future__ import annotations

import logging

from . import platform as _platform
from .registry import TenantRegistry

logger = logging.getLogger(__name__)

# Singleton registry — created once at startup
tenant_registry: TenantRegistry | None = None


async def initialize() -> TenantRegistry:
    """Initialize shared platform infrastructure.

    Sets up: encryption, database + router, AI, Redis, key migration.
    Returns the TenantRegistry for use by bots and API.
    """
    global tenant_registry

    # 1. Encryption for secrets at rest (must happen before DB reads)
    from infra.crypto import init_encryption
    init_encryption()

    # 2. Database layer (platform DB + tenant router)
    await _platform.initialize()

    # 3. Give AI client access to the DB for per-account model persistence
    import capabilities.ai as ai
    ai.set_db(_platform.get_db())

    # 4. Migrate plaintext API keys → encrypted (one-time, idempotent)
    await _platform.get_db().migrate_encrypt_api_keys()

    # 5. Initialize Redis (optional — graceful fallback if unavailable)
    import infra.cache as rcache
    await rcache.init_redis()

    # 5b. Initialize ARQ job-queue pool (optional — graceful fallback if
    # arq isn't installed or Redis is unreachable). Lets API handlers
    # call ``infra.jobs.enqueue(...)`` without per-call setup.
    import infra.jobs as rjobs
    await rjobs.init_jobs()

    # 6. Error reporter — Telegram notifications + error_log DB table
    from infra.error_reporter import init_error_reporter
    from infra.config import TELEGRAM_TOKEN, ERROR_REPORT_CHAT_ID
    init_error_reporter(TELEGRAM_TOKEN or "", ERROR_REPORT_CHAT_ID, _platform.get_db())

    # 7. Tenant registry
    tenant_registry = TenantRegistry()

    logger.info("Platform initialized")
    return tenant_registry


async def shutdown():
    """Shut down platform infrastructure.  Call on exit."""
    global tenant_registry

    # Close ARQ pool first so any in-flight enqueue calls don't race
    # with the Redis pool teardown that follows.
    try:
        import infra.jobs as rjobs
        await rjobs.close_jobs()
    except Exception as e:
        logger.warning("ARQ pool close error: %s", e)

    # Close Redis
    try:
        import infra.cache as rcache
        await rcache.close_redis()
    except Exception as e:
        logger.warning("Redis close error: %s", e)

    # Close all tenant contexts (Samsara clients, caches)
    if tenant_registry:
        await tenant_registry.close_all()
        tenant_registry = None

    # Close database connections
    await _platform.close()

    logger.info("Platform shut down")
