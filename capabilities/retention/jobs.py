"""Scheduler entry for the Retention hub.

Runs the platform pass once, then the tenant pass per active account.
NOT yet wired into the scheduler — the legacy prune jobs
(``job_prune_telemetry_history``, the email-webhook prune, the in-line
score-events prune) still run.  The cutover is a deliberate step: register
this job, then remove those three so retention isn't double-run.
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
