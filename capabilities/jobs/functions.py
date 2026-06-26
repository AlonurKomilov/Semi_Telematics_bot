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
        from features.scorecards.router import _build_scorecards_payload
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


# Windows the dashboard exposes in its DateRangePresets picker.  The
# cron prewarms every (account × window × subject) tuple so any click
# the user makes hits a warm cache.  Today (7) used to be the *only*
# warm window — anyone clicking 14/30/60/90 paid the 30-45 s
# cold-compute cost on Samsara.
_PREWARM_WINDOWS: tuple[int, ...] = (7, 14, 30, 60, 90)
_PREWARM_SUBJECTS: tuple[str, ...] = ("vehicle", "driver")


async def fanout_precompute_scorecards(
    ctx: dict,
    days: int | None = None,
) -> dict[str, Any]:
    """Cron-fired fanout: enqueue one ``precompute_scorecards`` job per
    (active account × window × subject) combination so the SWR cache
    is warm for every common click in the dashboard's date picker.

    *days* — when set, prewarm only that single window (back-compat for
    ad-hoc invocations).  When ``None`` (the cron default), prewarm
    every window in ``_PREWARM_WINDOWS``.

    Each per-account compute runs concurrently across worker slots
    (``max_jobs=10`` by default), so a 10-account fleet × 5 windows ×
    2 subjects = 100 jobs and the cron drains the whole queue in
    roughly ``100 × avg_compute_s / 10``.  With 30 s avg cold compute,
    that's 5 minutes — well within the 2-hour cron interval.
    """
    from infra import observability as _obs

    t0 = time.perf_counter()
    status = "success"
    windows: tuple[int, ...] = (days,) if days else _PREWARM_WINDOWS
    logger.info(
        "fanout_precompute_scorecards start windows=%s subjects=%s",
        windows, _PREWARM_SUBJECTS,
    )

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
            for window in windows:
                for subject in _PREWARM_SUBJECTS:
                    try:
                        await pool.enqueue_job(
                            "precompute_scorecards", acc.id, window,
                            subject=subject,
                            _job_id=f"prewarm:{acc.id}:{window}:{subject}",
                        )
                        enqueued += 1
                    except Exception:
                        logger.exception(
                            "fanout_precompute_scorecards: enqueue failed acct=%d days=%d subject=%s",
                            acc.id, window, subject,
                        )

    ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "fanout_precompute_scorecards done enqueued=%d in %sms (%d accts × %d windows × %d subjects)",
        enqueued, ms, len(accounts), len(windows), len(_PREWARM_SUBJECTS),
    )
    _obs.record_arq_job(function="fanout_precompute_scorecards",
                        status=status, seconds=time.perf_counter() - t0)
    return {
        "enqueued": enqueued,
        "total_accounts": len(accounts),
        "windows": list(windows),
        "subjects": list(_PREWARM_SUBJECTS),
        "ms": ms,
    }


# ── Account backfill (on company-connect / token-rotation) ────────────
#
# Wired from ``interfaces/api/routes/admin.py:add_company`` and
# ``update_company`` (when ``samsara_api_key`` rotates).  Runs in the
# ARQ worker (``4truck-queue.service``) so the API process stays
# responsive for live users — a 90-day backfill on a 100-truck fleet
# can take 30-60s of Samsara round-trips; doing that inline in a FastAPI
# worker would tie up a connection slot for the duration and slow down
# concurrent requests.

async def backfill_account_initial(
    ctx: dict,
    account_id: int,
    days: int = 90,
) -> dict[str, Any]:
    """ARQ-side wrapper that calls the historical backfill for one
    account.  Idempotent — the underlying writers all use
    ``UNIQUE``/``ON CONFLICT DO UPDATE`` so the same account can be
    backfilled multiple times without creating duplicate rows.  Gap-
    aware — sources with sufficient existing coverage are skipped.
    """
    from infra import observability as _obs
    job_id = ctx.get("job_id", "?")
    t0 = time.perf_counter()
    status = "ok"
    try:
        from capabilities.integrations.samsara.backfill import backfill_account_initial as _impl
        result = await _impl(account_id, days=days)
    except Exception:
        status = "error"
        logger.exception("backfill_account_initial: job=%s acct=%d failed", job_id, account_id)
        raise
    finally:
        _obs.record_arq_job(
            function="backfill_account_initial",
            status=status, seconds=time.perf_counter() - t0,
        )
    return result
