"""Payroll scheduled jobs.

The monthly job runs on the 1st of every month at 02:00 UTC.  For each
account with ``payroll_enabled = 1`` it computes a *draft* run for the
prior calendar month, leaving the run for an admin to review and
finalize via the dashboard.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timezone

from infra.platform import get_platform_db

from . import service as svc
from .service import PayrollDisabledError

logger = logging.getLogger(__name__)


def _prior_month_window(today: date) -> tuple[date, date]:
    """Return (first_day, last_day) of the calendar month preceding *today*."""
    if today.month == 1:
        year = today.year - 1
        month = 12
    else:
        year = today.year
        month = today.month - 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


async def run_monthly_payroll_job(_app=None) -> None:
    """Compute draft payroll runs for the previous month for every
    payroll-enabled account.  Idempotent enough — re-running on the
    same day will create another draft, so admins should only see
    one fresh draft per scheduled execution.
    """
    today = datetime.now(timezone.utc).date()
    period_start, period_end = _prior_month_window(today)
    pdb = get_platform_db()
    accounts = await pdb.list_accounts(active_only=True)

    created = 0
    skipped = 0
    failed = 0
    for acc in accounts:
        if not getattr(acc, "payroll_enabled", False):
            skipped += 1
            continue
        try:
            run_id = await svc.create_run(
                acc.id, user_id=0,
                period_start=period_start, period_end=period_end,
            )
            logger.info(
                "monthly payroll: created draft run %d for account %d "
                "(%s..%s)",
                run_id, acc.id, period_start, period_end,
            )
            created += 1
        except PayrollDisabledError:
            skipped += 1
        except Exception:
            logger.exception(
                "monthly payroll: failed for account %d", acc.id,
            )
            failed += 1

    logger.info(
        "monthly payroll job done: created=%d skipped=%d failed=%d",
        created, skipped, failed,
    )
