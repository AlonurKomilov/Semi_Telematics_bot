"""Auto-created incentive runs — the period ends, the draft appears.

Phase 4a of the incentive system: a daily sweep that creates the DRAFT
run for every account whose configured period just completed, then
tells the people who manage payouts that it is waiting for review.

WHAT IT NEVER DOES: finalize.  The human review step — inactive days,
extras, exceptions — is the point of the draft stage; automation only
removes the "remember to click New run" chore.  A manually-created run
for the same period always wins: the sweep skips any period an
existing run overlaps.

Period rules (account timezone questions deliberately avoided — loads
carry calendar dates, and the sweep runs at 12:00 UTC when the prior
calendar day is over in every US timezone):
  * weekly  — the last full Monday–Sunday week (the customer's own
    settlement weeks run Mon–Sun).
  * monthly — the previous calendar month.
  * custom N — the N days after the latest existing run; no runs yet
    means no anchor, so the chain starts manually.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from capabilities.notifications.categories import (
    TARGETED, NotificationCategory, register_category,
)
from capabilities.notifications.channels import NotificationContent
from capabilities.notifications.service import notify_user
from features.kpi.dispatch import runs as runs_service
from infra.platform import get_tenant_db

logger = logging.getLogger(__name__)

RUN_CREATED = "kpi.incentive_run_created"

register_category(NotificationCategory(
    key=RUN_CREATED,
    label="An incentive run draft is ready for review",
    kind=TARGETED,
))


def completed_period(
    cadence: str,
    custom_days: int | None,
    today: date,
    latest_end: str | None,
) -> tuple[str, str] | None:
    """The most recent COMPLETED period as (start, end) ISO dates, or
    None when there is nothing due.  Pure — the tests drive ``today``."""
    if cadence == "monthly":
        first_of_this = today.replace(day=1)
        end = first_of_this - timedelta(days=1)
        return end.replace(day=1).isoformat(), end.isoformat()
    if cadence == "custom":
        if not custom_days or not latest_end:
            return None                    # no anchor — started manually
        start = date.fromisoformat(latest_end[:10]) + timedelta(days=1)
        end = start + timedelta(days=custom_days - 1)
        if end >= today:
            return None                    # period still running
        return start.isoformat(), end.isoformat()
    # weekly (the default): last full Mon–Sun strictly before today.
    last_sunday = today - timedelta(days=today.isoweekday())
    return (last_sunday - timedelta(days=6)).isoformat(), last_sunday.isoformat()


async def create_due_run(account_id: int, *, today: date) -> int | None:
    """Create this account's due draft, if any.  Returns the new run id,
    or None when nothing was due (unconfigured, already covered, or a
    custom chain with no anchor)."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return None
    config = await tenant.get_kpi_incentive_config(account_id)
    if config is None or not config.get("tiers"):
        return None

    existing = await tenant.list_kpi_runs(account_id)
    latest_end = max((r["period_end"] for r in existing), default=None)
    period = completed_period(
        config.get("calc_cadence", "weekly"),
        config.get("calc_custom_days"),
        today,
        latest_end,
    )
    if period is None:
        return None
    start, end = period
    # A run that OVERLAPS the period covers it — a manual run for the
    # same week (even off by a day) must not be doubled by the sweep.
    if any(r["period_start"] <= end and r["period_end"] >= start
           for r in existing):
        return None

    run_id = await runs_service.create_run(
        account_id, period_start=start, period_end=end, created_by=0,
    )
    logger.info("kpi auto-run: account=%s period=%s..%s run=%s",
                account_id, start, end, run_id)

    # Tell the payout managers — non-fatal: the draft exists either way.
    try:
        detail = await runs_service.get_run_detail(account_id, run_id)
        total = sum(float(v) for v in detail["payouts"].values())
        for u in await tenant.list_account_users(account_id):
            if u.role not in ("owner", "admin"):
                continue
            await notify_user(
                tenant, account_id, u.id,
                NotificationContent(
                    title=f"Incentive run {start} – {end} is ready for review",
                    body=(f"{len(detail['rows'])} truck rows, "
                          f"${total:,.2f} at current inputs. Review days "
                          "and extras, then finalize."),
                    category=RUN_CREATED,
                    severity="info",
                    url="/kpi/incentives",
                    meta={"context": "Dispatcher Incentives"},
                ),
            )
    except Exception:
        logger.exception("kpi auto-run: notification failed account=%s",
                         account_id)
    return run_id


async def job_create_due_runs(_app=None, *, today: date | None = None) -> None:
    """The daily sweep (12:00 UTC) — every active account, bounded
    fan-out, one bad tenant never blocks the rest."""
    from capabilities.data_lifecycle._common import for_each_active_account

    day = today or date.today()

    async def _one(account_id: int) -> None:
        try:
            await create_due_run(account_id, today=day)
        except runs_service.RunError as e:
            # Config-shaped refusals (e.g. tenant hiccup) — log, not raise.
            logger.warning("kpi auto-run skipped account=%s: %s",
                           account_id, e)

    await for_each_active_account(_one)
