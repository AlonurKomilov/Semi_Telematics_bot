"""Per-account fault isolation — timeout + error containment.

Each scheduled job wraps per-account work in ``run_account_job()``
so that one account's failure (timeout, crash, API hang) cannot block
processing of other accounts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Coroutine, Any

logger = logging.getLogger(__name__)

# Maximum time (seconds) a single account's work is allowed to take
# inside a scheduled job before being cancelled.
ACCOUNT_JOB_TIMEOUT = 120
# Camera checks download snapshots + run Gemini Vision per vehicle — needs more time
# Raised from 600s.  The camera job is ~360 vision calls (78 vehicles x
# several cameras) at a concurrency of 3, and successful calls average
# 6.3s / p95 14.5s — so a clean run is 360 x 6.3 / 3 = ~750s and does not
# fit in 600.  It USUALLY finished only because a third of the calls fail
# fast on quota (see the semaphore note in features/cameras/alert.py):
# the job was fitting inside its budget by not doing the work.  900s
# covers a run at measured latency; it does not need to cover one where
# every call succeeds at p95, because the quota makes that impossible
# today anyway.
CAMERA_JOB_TIMEOUT = 900
# Scheduled-Reports generation iterates through every subscriber,
# queries the warehouse, renders a PDF, and delivers it via Telegram
# and/or email — that loop can legitimately take well over 2 min on
# accounts with many subscribers or large windows.  Match the
# camera-job budget.
SCHEDULED_REPORTS_JOB_TIMEOUT = 600


async def run_account_job(
    coro: Coroutine[Any, Any, None],
    *,
    account_id: int,
    job_name: str,
    timeout: int | None = None,
    tenant_db: Any = None,
) -> bool:
    """Run a per-account coroutine with timeout and fault isolation.

    Args:
        coro: The coroutine to execute (e.g. ``_check_faults_account(...)``).
        account_id: Account being processed (for logging).
        job_name: Human-readable job name (for logging).
        timeout: Override the default timeout in seconds.
        tenant_db: Optional tenant Database — when provided, the job runs
            inside ``tenant_db.with_account(account_id)`` so Postgres RLS
            policies see the right ``app.account_id`` for the duration.
            Pass it from callers that already have the db handle on
            hand (most scheduler jobs); pass ``None`` for jobs that
            don't touch the tenant DB at all.

    Returns:
        True if the job completed successfully, False on error/timeout.
    """
    job_timeout = timeout if timeout is not None else ACCOUNT_JOB_TIMEOUT
    try:
        async with asyncio.timeout(job_timeout):
            if tenant_db is not None:
                async with tenant_db.with_account(account_id):
                    await coro
            else:
                await coro
        return True
    except TimeoutError:
        logger.error(
            "%s timed out for account %d after %ds",
            job_name, account_id, job_timeout,
        )
        try:
            from infra.error_reporter import report_error
            # A REAL exception, not None.  ``report_error(None)`` renders
            # as "UnknownError: (no exception object)", so the alert read
            # as something inexplicable when a timeout is the most
            # precisely-known failure there is — the title already said
            # so while the body shrugged.  ``asyncio.timeout`` raises a
            # bare TimeoutError with no message, hence the constructed
            # one: it carries the job, the budget and the account into
            # the body where a reader is looking.
            asyncio.create_task(report_error(
                TimeoutError(
                    f"{job_name} exceeded its {job_timeout}s budget "
                    f"for account {account_id}"
                ),
                source="scheduler",
                job_name=f"{job_name} (timeout after {job_timeout}s)",
                account_id=account_id,
            ))
        except Exception:
            pass
        return False
    except Exception as exc:
        logger.error(
            "%s failed for account %d",
            job_name, account_id,
            exc_info=True,
        )
        try:
            from infra.error_reporter import report_error
            asyncio.create_task(report_error(
                exc,
                source="scheduler",
                job_name=job_name,
                account_id=account_id,
            ))
        except Exception:
            pass
        return False
