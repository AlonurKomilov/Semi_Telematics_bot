"""PTI cron entry points.

Four scheduled jobs (registered by ``interfaces/bot/scheduler.py`` in
Step 5 of the PTI rollout):

  * ``job_pti_spawn_weekly``        — daily 06:00 local; creates next
                                       weekly inspection per driver
  * ``job_pti_remind_due_soon``     — hourly tick; notifies drivers
                                       whose inspection is due <24h
  * ``job_pti_escalate_overdue``    — daily 09:00 local; admin digest
                                       of overdue inspections
  * ``job_pti_fleet_digest``        — daily 09:00 local; fleet summary
                                       of awaiting-review counts

Each job is wrapped in defensive try/except so one bad tenant doesn't
crash the scheduler thread.  Cross-account fan-out uses the same
``_for_each_active_account`` helper the telemetry ingestor uses, so
concurrency is bounded by ``INGEST_MAX_CONCURRENT_ACCOUNTS``.

Notification dispatch lives in ``interfaces/bot/pti.py`` (Step 9).
This file only fans out the data + calls the bot module's notifier
when wired.  Keeping the cron callable from outside the bot module
means future surfaces (e.g. a dashboard "force-spawn now" button) can
re-use the same plumbing.
"""

from __future__ import annotations

import logging

from infra.services import get_platform_db, get_tenant_db
from capabilities.localization.tz import is_local_hour
from features.inspections import service as pti_service
from features.inspections.formatter import fleet_digest_line

logger = logging.getLogger(__name__)


# Defaults match the plan §9.3.  Override via env in non-production
# environments if you want the spawn cron to fire at a different
# local hour.
PTI_SPAWN_LOCAL_HOUR: int = 6
PTI_DIGEST_LOCAL_HOUR: int = 9


# ── Single-account jobs ────────────────────────────────────────────


async def _spawn_for_account(account_id: int) -> int:
    """Run the spawner for one account.  Returns the count spawned."""
    if not await is_local_hour(account_id, PTI_SPAWN_LOCAL_HOUR):
        return 0
    tenant_db = await get_tenant_db(account_id)
    if tenant_db is None:
        return 0
    try:
        spawned = await pti_service.spawn_weekly_inspections(
            account_id, tenant_db,
        )
        return len(spawned)
    except Exception:
        logger.exception("pti spawn failed for acct=%d", account_id)
        return 0


async def _remind_for_account(account_id: int) -> int:
    """Notify drivers whose inspection lands in the next 24h.  Returns
    count of drivers pinged (or skipped — caller doesn't differentiate
    yet)."""
    tenant_db = await get_tenant_db(account_id)
    if tenant_db is None:
        return 0
    try:
        due_soon = await pti_service.list_due_soon(account_id, tenant_db)
        if not due_soon:
            return 0
        # The actual notification dispatch lands in interfaces/bot/pti.py
        # (Step 9); for now log so deploy-time smoke can confirm the
        # cron is finding rows.
        logger.info(
            "pti due_soon acct=%d count=%d",
            account_id, len(due_soon),
        )
        try:
            from interfaces.bot.pti import notify_due_soon  # type: ignore[import-not-found]
            await notify_due_soon(account_id, due_soon)
        except ImportError:
            # Bot module not wired yet (Step 9 hasn't landed) — silent.
            pass
        return len(due_soon)
    except Exception:
        logger.exception("pti remind-due-soon failed for acct=%d", account_id)
        return 0


async def _escalate_for_account(account_id: int) -> int:
    if not await is_local_hour(account_id, PTI_DIGEST_LOCAL_HOUR):
        return 0
    tenant_db = await get_tenant_db(account_id)
    if tenant_db is None:
        return 0
    try:
        overdue = await pti_service.mark_overdue_inspections(
            account_id, tenant_db,
        )
        if not overdue:
            return 0
        logger.info(
            "pti overdue acct=%d count=%d",
            account_id, len(overdue),
        )
        try:
            from interfaces.bot.pti import notify_overdue_to_admin  # type: ignore[import-not-found]
            await notify_overdue_to_admin(account_id, overdue)
        except ImportError:
            pass
        return len(overdue)
    except Exception:
        logger.exception("pti escalate-overdue failed for acct=%d", account_id)
        return 0


async def _digest_for_account(account_id: int) -> str:
    """Build + send the daily fleet digest line for one account.
    Returns the digest string (or empty when nothing to report)."""
    if not await is_local_hour(account_id, PTI_DIGEST_LOCAL_HOUR):
        return ""
    tenant_db = await get_tenant_db(account_id)
    if tenant_db is None:
        return ""
    try:
        # Submitted-and-awaiting-review = the fleet's actionable queue.
        awaiting = await tenant_db.list_inspections_for_account(
            account_id, status="submitted", page_size=500,
        )
        overdue = await pti_service.mark_overdue_inspections(
            account_id, tenant_db,
        )
        # needs_service review status in the last 7 days.
        ns = await tenant_db.list_inspections_for_account(
            account_id, review_status="needs_service",
            days=7, page_size=500,
        )
        counts = {
            "awaiting_review":   int(awaiting.get("total") or 0),
            "overdue":           len(overdue),
            "needs_service_7d":  int(ns.get("total") or 0),
        }
        line = fleet_digest_line(counts)
        if line == "No PTI activity":
            return ""
        logger.info("pti fleet digest acct=%d %s", account_id, line)
        try:
            from interfaces.bot.pti import send_fleet_digest  # type: ignore[import-not-found]
            await send_fleet_digest(account_id, counts, line)
        except ImportError:
            pass
        return line
    except Exception:
        logger.exception("pti fleet-digest failed for acct=%d", account_id)
        return ""


# ── Scheduler entry points ─────────────────────────────────────────


async def _for_each_active_account(coro_factory) -> None:
    """Bounded fan-out — same pattern as ``capabilities.telemetry.ingestor``.

    Kept in this module (rather than imported) to avoid a circular
    dependency between PTI jobs and the telemetry layer.
    """
    import asyncio
    import os
    max_concurrent = int(os.getenv("INGEST_MAX_CONCURRENT_ACCOUNTS", "5"))
    try:
        accounts = await get_platform_db().list_accounts(active_only=True)
    except Exception:
        logger.exception("pti: list_accounts failed")
        return
    if not accounts:
        return
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run(account):
        async with semaphore:
            try:
                await coro_factory(account.id)
            except Exception:
                logger.exception(
                    "pti job failed for acct=%d", account.id,
                )

    await asyncio.gather(*(_run(a) for a in accounts))


async def job_pti_spawn_weekly(_app=None) -> None:
    """Hourly tick that gates each account on its local 06:00."""
    await _for_each_active_account(_spawn_for_account)


async def job_pti_remind_due_soon(_app=None) -> None:
    """Hourly tick — drivers with inspections due in <24h."""
    await _for_each_active_account(_remind_for_account)


async def job_pti_escalate_overdue(_app=None) -> None:
    """Hourly tick — admin digest gated on local 09:00."""
    await _for_each_active_account(_escalate_for_account)


async def job_pti_fleet_digest(_app=None) -> None:
    """Hourly tick — fleet status digest gated on local 09:00."""
    await _for_each_active_account(_digest_for_account)
