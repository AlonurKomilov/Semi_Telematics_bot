"""Scheduler entry for the Retention hub.

Runs the platform pass once, then the tenant pass per active account.
Wired into the scheduler as the ``data_retention`` cron (02:00 UTC); it
replaced the three legacy prune paths (the per-capability telemetry-history
job, the email-webhook cleanup, and the in-line score-events prune) so every
retention window lives in one contract instead of three scattered constants.

Runs for every active account (no capability gate): a prune on a tier with
no rows is a harmless no-op.
"""

from __future__ import annotations

import logging

from . import discover
from .engine import prune_platform_targets, prune_tenant_targets

logger = logging.getLogger(__name__)


async def job_run_retention(_app=None) -> None:
    discover()
    from infra.platform import get_platform_db, get_tenant_db

    try:
        await prune_platform_targets(get_platform_db())
    except Exception:
        logger.exception("retention: platform pass failed")

    async def _for_account(account_id: int) -> None:
        tenant = await get_tenant_db(account_id)
        if tenant is not None:
            await prune_tenant_targets(tenant, account_id)

    # Reuse the warehouse ingestor's active-account iterator (bounded
    # concurrency, per-account error isolation).
    from capabilities.telemetry.ingestor import _for_each_active_account
    await _for_each_active_account(_for_account)
