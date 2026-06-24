"""Scheduler entry for the Retention hub.

Runs the platform pass once, then the tenant pass per active account.
Wired into the scheduler as the ``data_retention`` cron (02:00 UTC); it
replaced the three legacy prune paths (the per-capability telemetry-history
job, the email-webhook cleanup, and the in-line score-events prune) so every
retention window lives in one contract instead of three scattered constants.

Runs for every active account (no capability gate): a prune on a tier with
no rows is a harmless no-op.  After the passes, one summary row per target is
recorded (``record_retention_runs``) so the operator console can show
last-run + rows-deleted.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import discover
from .engine import prune_platform_targets, prune_tenant_targets
from .registry import resolve

logger = logging.getLogger(__name__)


async def job_run_retention(_app=None) -> None:
    discover()
    from infra.platform import get_platform_db, get_tenant_db

    platform_db = get_platform_db()
    platform_deleted: dict[str, int] = {}
    try:
        platform_deleted = await prune_platform_targets(platform_db)
    except Exception:
        logger.exception("retention: platform pass failed")

    # Accumulate per-target totals + accounts-touched across the tenant pass.
    tenant_deleted: dict[str, int] = {}
    tenant_accounts: dict[str, int] = {}

    async def _for_account(account_id: int) -> None:
        tenant = await get_tenant_db(account_id)
        if tenant is None:
            return
        per_target = await prune_tenant_targets(tenant, account_id)
        # No await between these reads/writes → safe under the bounded
        # concurrent fan-out below.
        for key, deleted in per_target.items():
            tenant_deleted[key] = tenant_deleted.get(key, 0) + deleted
            tenant_accounts[key] = tenant_accounts.get(key, 0) + 1

    # The shared active-account iterator (bounded concurrency, per-account
    # error isolation) — lives in the lifecycle family, not the ingestor.
    from .._common import for_each_active_account
    await for_each_active_account(_for_account)

    # Record one summary row per resolved target (incl. zero-delete runs,
    # so the console shows the job ran even when nothing aged out).
    ran_at = datetime.now(timezone.utc).isoformat()
    records = []
    for r in resolve():
        key = r.target.key
        if r.target.scope == "platform":
            records.append({
                "target_key": key, "scope": "platform", "keep_days": r.keep_days,
                "rows_deleted": platform_deleted.get(key, 0), "accounts": 0,
            })
        else:
            records.append({
                "target_key": key, "scope": "tenant", "keep_days": r.keep_days,
                "rows_deleted": tenant_deleted.get(key, 0),
                "accounts": tenant_accounts.get(key, 0),
            })
    try:
        await platform_db.record_retention_runs(ran_at, records)
    except Exception:
        logger.exception("retention: recording run telemetry failed")
