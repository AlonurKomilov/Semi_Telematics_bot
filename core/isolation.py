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


async def run_account_job(
    coro: Coroutine[Any, Any, None],
    *,
    account_id: int,
    job_name: str,
) -> bool:
    """Run a per-account coroutine with timeout and fault isolation.

    Args:
        coro: The coroutine to execute (e.g. ``_check_faults_account(...)``).
        account_id: Account being processed (for logging).
        job_name: Human-readable job name (for logging).

    Returns:
        True if the job completed successfully, False on error/timeout.
    """
    try:
        async with asyncio.timeout(ACCOUNT_JOB_TIMEOUT):
            await coro
        return True
    except TimeoutError:
        logger.error(
            "%s timed out for account %d after %ds",
            job_name, account_id, ACCOUNT_JOB_TIMEOUT,
        )
        return False
    except Exception:
        logger.error(
            "%s failed for account %d",
            job_name, account_id,
            exc_info=True,
        )
        return False
