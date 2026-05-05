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
CAMERA_JOB_TIMEOUT = 600


async def run_account_job(
    coro: Coroutine[Any, Any, None],
    *,
    account_id: int,
    job_name: str,
    timeout: int | None = None,
) -> bool:
    """Run a per-account coroutine with timeout and fault isolation.

    Args:
        coro: The coroutine to execute (e.g. ``_check_faults_account(...)``).
        account_id: Account being processed (for logging).
        job_name: Human-readable job name (for logging).
        timeout: Override the default timeout in seconds.

    Returns:
        True if the job completed successfully, False on error/timeout.
    """
    job_timeout = timeout if timeout is not None else ACCOUNT_JOB_TIMEOUT
    try:
        async with asyncio.timeout(job_timeout):
            await coro
        return True
    except TimeoutError:
        logger.error(
            "%s timed out for account %d after %ds",
            job_name, account_id, job_timeout,
        )
        try:
            from infra.error_reporter import report_error
            asyncio.create_task(report_error(
                None,
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
