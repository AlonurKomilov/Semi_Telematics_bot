"""ARQ background job functions.

Each function takes ``(ctx, ...args)`` and runs in the worker process.
``ctx`` is the ARQ context dict (job_id, enqueue_time, redis pool, etc.).
Return value is JSON-serialised into the job's result so the API
status endpoint can show it.

Adding a new job:
  1. Define the function here.
  2. Append it to ``capabilities.jobs.worker.WorkerSettings.functions``.
  3. Restart the worker process — the API can enqueue it immediately
     after via ``infra.jobs.enqueue("name", *args)``.

Conventions:
  * Each function logs at INFO when it starts + finishes (with timing).
  * Functions are idempotent where possible — ARQ retries failed jobs.
  * Heavy CPU work (PDF generation, large CSV) wraps in
    ``asyncio.to_thread`` so other jobs in the same worker keep
    making progress.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


# ── Health check ──────────────────────────────────────────────

async def ping(ctx: dict) -> str:
    """Smoke-test job — returns ``"pong"``. Used by the runbook to
    verify a fresh worker can pick up jobs end-to-end.

    Run from a Python REPL on the API box::

        from infra import jobs
        await jobs.init_jobs()
        job = await jobs.enqueue("ping")
        print(await jobs.get_job_status(job.job_id))
    """
    job_id = ctx.get("job_id", "?")
    logger.info("ping job %s", job_id)
    return "pong"


# ── Cache pre-warmer ──────────────────────────────────────────
#
# Highest-leverage job: nightly pre-warm of the SWR scorecards cache
# so the first user of the morning gets an instant response instead
# of paying the cold Samsara fan-out cost.
#
# Wired by the daily cron in ``capabilities.jobs.worker.WorkerSettings.cron_jobs``
# (06:00 UTC every day) — fans out one ARQ job per active account.
# That parallelises the work across worker concurrency and keeps any
# single account's failure isolated.

async def precompute_scorecards(
    ctx: dict,
    account_id: int,
    days: int = 7,
    subject: str = "vehicle",
) -> dict[str, Any]:
    """Pre-warm the SWR scorecards cache for one account.

    Calls the same code path the dashboard request uses, so the
    result lands in Redis under the exact same cache key the API will
    later read. Returns a small status dict for the job result.
    """
    from infra import observability as _obs

    t0 = time.perf_counter()
    job_id = ctx.get("job_id", "?")
    logger.info("precompute_scorecards start acct=%d days=%d subject=%s job_id=%s",
                account_id, days, subject, job_id)

    status = "success"
    try:
        from infra.platform import get_tenant_db
        from infra.services import get_platform_db
        from infra import cache as _redis_cache
        from interfaces.api.routes.safety import _build_scorecards_payload
        from interfaces.api.deps import get_user_company_codes

        tenant = await get_tenant_db(account_id)
        platform_db = get_platform_db()
        allowed = await get_user_company_codes({"account_id": account_id})

        async def _compute() -> dict:
            return await _build_scorecards_payload(
                account_id=account_id,
                subject=subject,
                days=days,
                company=None,
                vehicle_nums=None,
                allowed=allowed,
                tenant=tenant,
                platform_db=platform_db,
            )

        cache_key = (
            f"scorecards:composite:{account_id}:{subject}:{days}:_"
        )
        await _redis_cache.get_or_compute(
            cache_key, _compute,
            fresh_for=120, max_stale=600, lock_ttl=45,
        )

        ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info("precompute_scorecards done acct=%d in %sms", account_id, ms)
        return {"account_id": account_id, "days": days, "subject": subject, "ms": ms}

    except Exception:
        status = "failed"
        ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.exception("precompute_scorecards FAILED acct=%d after %sms", account_id, ms)
        # Re-raise so ARQ records the job as failed and retries kick in.
        raise
    finally:
        _obs.record_arq_job(
            function="precompute_scorecards",
            status=status,
            seconds=time.perf_counter() - t0,
        )


async def fanout_precompute_scorecards(ctx: dict, days: int = 7) -> dict[str, Any]:
    """Cron-fired fanout: enqueue one ``precompute_scorecards`` job per
    active account. Cheap top-level job that the cron triggers, then
    each per-account job runs concurrently across worker slots.
    """
    from infra import observability as _obs

    t0 = time.perf_counter()
    status = "success"
    logger.info("fanout_precompute_scorecards start days=%d", days)

    try:
        from infra.platform import get_db
        accounts = await get_db().list_accounts(active_only=True)
    except Exception:
        status = "failed"
        _obs.record_arq_job(function="fanout_precompute_scorecards",
                            status=status, seconds=time.perf_counter() - t0)
        logger.exception("fanout_precompute_scorecards: failed to list accounts")
        raise

    pool = ctx.get("redis")  # ARQ injects the pool into ctx
    if pool is None:
        # Fall back to the module-level pool if the worker didn't set it.
        from infra import jobs as _jobs
        if not _jobs.is_available():
            await _jobs.init_jobs()
        pool = _jobs._pool  # type: ignore[attr-defined]

    enqueued = 0
    if pool is not None:
        for acc in accounts:
            try:
                await pool.enqueue_job(
                    "precompute_scorecards", acc.id, days,
                    _job_id=f"prewarm:{acc.id}:{days}",
                )
                enqueued += 1
            except Exception:
                logger.exception("fanout_precompute_scorecards: enqueue failed acct=%d", acc.id)

    ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info("fanout_precompute_scorecards done enqueued=%d in %sms", enqueued, ms)
    _obs.record_arq_job(function="fanout_precompute_scorecards",
                        status=status, seconds=time.perf_counter() - t0)
    return {"enqueued": enqueued, "total_accounts": len(accounts), "days": days, "ms": ms}
