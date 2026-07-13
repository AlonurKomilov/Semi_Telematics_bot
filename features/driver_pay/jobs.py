"""Driver Pay scheduled jobs.

The monthly job runs on the 1st of every month at 02:00 UTC.  For each
account with the driver-pay module enabled it computes a *draft* run for the
prior calendar month, leaving the run for an admin to review and
finalize via the dashboard.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timezone

from infra.platform import get_platform_db, get_tenant_db

from . import service as svc
from .service import DriverPayDisabledError

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


async def run_monthly_driver_pay_job(_app=None) -> None:
    """Compute draft driver-pay runs for the previous month for every
    driver-pay-enabled account.  Idempotent enough — re-running on the
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
        from capabilities.permissions.modules import module_enabled
        # Accounting module must be on (driver pay lives under it now)…
        if not module_enabled(getattr(acc, "disabled_modules", ""), "accounting"):
            skipped += 1
            continue
        # …and the account must actually USE driver pay — configured driver
        # pay or bonus rules.  Without a standalone switch, this is the
        # "uses driver pay" signal, so we don't spawn empty drafts for every
        # accounting-enabled account.
        tenant = await get_tenant_db(acc.id)
        if tenant is None:
            skipped += 1
            continue
        has_settings = await tenant.list_driver_pay_settings(acc.id)
        has_rules = await tenant.list_bonus_rules(acc.id, active_only=True)
        if not has_settings and not has_rules:
            skipped += 1
            continue
        try:
            run_id = await svc.create_run(
                acc.id, user_id=0,
                period_start=period_start, period_end=period_end,
            )
            logger.info(
                "monthly driver-pay: created draft run %d for account %d "
                "(%s..%s)",
                run_id, acc.id, period_start, period_end,
            )
            created += 1
        except DriverPayDisabledError:
            skipped += 1
        except Exception:
            logger.exception(
                "monthly driver-pay: failed for account %d", acc.id,
            )
            failed += 1

    logger.info(
        "monthly driver-pay job done: created=%d skipped=%d failed=%d",
        created, skipped, failed,
    )
