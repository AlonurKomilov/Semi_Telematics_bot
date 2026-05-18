"""Core platform — shared infrastructure for per-account isolation.

Public API::

    from infra import get_platform_db, get_tenant_db, TenantContext, TenantRegistry
    from infra.config import SAMSARA_BASE_URL, ...
    from infra.startup import initialize, shutdown

Modules:
    config    — shared (non-bot-specific) environment variables
    platform  — database layer: router, platform DB, tenant DB access
    tenant    — TenantContext: per-account isolated state
    registry  — TenantRegistry: manages TenantContext lifecycle
    startup   — initialize() / shutdown() lifecycle functions
"""

from .platform import get_platform_db, get_tenant_db, get_db, get_router
from .services import get_client, invalidate_client, get_user_company_codes, check_rate_limit  # noqa: F401
from .tenant import TenantContext
from .registry import TenantRegistry
from .bot_registry import BotRegistry
from .context import (
    get_company_display,
    get_org_ids,
    set_tenant_display,
)
from .isolation import run_account_job, ACCOUNT_JOB_TIMEOUT

__all__ = [
    "get_platform_db",
    "get_tenant_db",
    "get_db",
    "get_router",
    "TenantContext",
    "TenantRegistry",
    "BotRegistry",
    "get_company_display",
    "get_org_ids",
    "set_tenant_display",
    "run_account_job",
    "ACCOUNT_JOB_TIMEOUT",
]
