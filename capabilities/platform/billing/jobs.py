"""Scheduled billing jobs.

Currently:

  - ``run_monthly_billing_snapshots`` — on the 1st of each month, writes
    one ``billing_usage_snapshots`` row per active account capturing
    what the previous calendar month's invoice was for.  The row is the
    source of truth for finance reports and dashboard history; it's
    idempotent on (account_id, period_start) so a re-run of the job
    after a transient failure is safe.

The job lives here (not in ``features/vehicles/warehouse``) because the work
is purely billing — Samsara is already long-since polled for activity
by the time this fires (02:30 UTC vs the ingest job's 60s cadence).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _platform_router():
    """Resolve the platform router lazily.

    Importing the function (``from infra.platform import get_router``)
    binds the reference at module-load time, which means tests that
    swap ``infra.platform.get_router`` via monkeypatch don't see the
    swap.  Reading the attribute at call time is one extra dict lookup
    per call and makes the seam testable.
    """
    from infra import platform as _ip
    return _ip.get_router()


def _previous_month_window(now: datetime | None = None) -> tuple[str, str]:
    """Return ISO-8601 (period_start, period_end) for the prior calendar month.

    ``period_start`` is the first day of last month at 00:00:00 UTC.
    ``period_end`` is the first day of THIS month at 00:00:00 UTC
    (half-open interval; matches Stripe's invoice period convention).
    Independent of the caller's clock so tests can pin the time.
    """
    now = now or datetime.now(timezone.utc)
    if now.month == 1:
        prev_year, prev_month = now.year - 1, 12
    else:
        prev_year, prev_month = now.year, now.month - 1
    period_start = datetime(prev_year, prev_month, 1, tzinfo=timezone.utc)
    period_end   = datetime(now.year,  now.month,  1, tzinfo=timezone.utc)
    return period_start.isoformat(), period_end.isoformat()


async def snapshot_account_billing(
    account_id: int,
    *,
    now: datetime | None = None,
) -> int | None:
    """Compute + persist one account's monthly billing snapshot.

    Returns the new (or existing — idempotent) snapshot row id, or
    None when the account has no subscription configured.  Reads
    activity from ``vehicle_state.captured_at`` via ``compute_billing``
    so the recorded extras count matches what Stripe was billing the
    customer during the period.
    """
    router = _platform_router()
    platform_db = router.platform
    sub = await platform_db.get_subscription(account_id)
    if not sub:
        return None
    billing = await platform_db.compute_billing(account_id)
    period_start, period_end = _previous_month_window(now)
    # User count + AI queries snapshotted alongside so the row is a
    # self-contained record of "what this account looked like at the
    # close of the billing period".  Both lookups are best-effort —
    # finance reports tolerate a zero better than they tolerate a
    # missing snapshot.
    try:
        user_count = await platform_db.count_account_users(account_id)
    except Exception:
        logger.exception("snapshot_account_billing: count_account_users failed acct=%d", account_id)
        user_count = 0
    try:
        ai_stats = await platform_db.get_ai_usage_stats(account_id, days=31)
        ai_queries = int((ai_stats or {}).get("total", 0))
    except Exception:
        logger.exception("snapshot_account_billing: get_ai_usage_stats failed acct=%d", account_id)
        ai_queries = 0
    return await platform_db.record_usage_snapshot(
        account_id=account_id,
        period_start=period_start,
        period_end=period_end,
        # ``vehicle_count`` keeps the raw Samsara count for backwards
        # compat; the activity-based ``active_vehicles`` is what drives
        # the extras / amount_due math on this row.
        vehicle_count=int(sub.get("vehicle_count") or 0),
        user_count=user_count,
        ai_queries=ai_queries,
        base_vehicles=billing["included"],
        monthly_base_cents=billing["base_cents"],
        extra_vehicle_cents=billing["extra_unit_cents"],
        active_vehicles=billing["active_vehicles"],
        inactive_vehicles=billing["inactive_vehicles"],
    )


async def run_monthly_billing_snapshots(_app=None) -> dict:
    """Snapshot every active account's billing for the just-closed month.

    Designed for APScheduler's ``cron`` trigger on day=1, hour=2,
    minute=30.  Each account is processed independently — one tenant
    erroring out doesn't block the rest, and the per-account
    snapshotter is idempotent so an APScheduler retry storm produces
    the same single row.

    Returns a small summary for logs / metrics: how many accounts
    succeeded vs failed.  The ``_app`` arg is unused but kept so the
    job can be registered identically to bot-aware jobs.
    """
    platform_db = _platform_router().platform
    try:
        accounts = await platform_db.list_accounts(active_only=True)
    except Exception:
        logger.exception("run_monthly_billing_snapshots: list_accounts failed")
        return {"succeeded": 0, "failed": 0}
    succeeded = 0
    failed = 0
    for acc in accounts:
        try:
            snap_id = await snapshot_account_billing(acc.id)
            if snap_id is not None:
                succeeded += 1
        except Exception:
            failed += 1
            logger.exception(
                "run_monthly_billing_snapshots: snapshot failed acct=%d", acc.id,
            )
    logger.info(
        "monthly billing snapshots: %d succeeded, %d failed, %d skipped (no subscription)",
        succeeded, failed, max(0, len(accounts) - succeeded - failed),
    )
    return {"succeeded": succeeded, "failed": failed, "total": len(accounts)}


__all__ = [
    "run_monthly_billing_snapshots",
    "snapshot_account_billing",
    "_previous_month_window",
    "run_comp_expiry_sweep",
]


# ── Comp-expiry sweep ────────────────────────────────────────────


async def run_comp_expiry_sweep(_app=None, *, now: datetime | None = None) -> dict:
    """Daily sweep: expire lapsed comps + ping accounts approaching expiry.

    Two phases:

      1. **Expire**: ``expire_lapsed_comps`` flips ``is_comped`` off for
         any account whose ``comp_expires_at`` has passed, and writes
         an ``expired`` row to ``comp_account_history``.  We then fire
         ``notify_comp_expired`` for each affected account so the
         operator knows their next invoice will charge normally.

      2. **Remind**: scan still-comped accounts whose remaining window
         matches a reminder bucket (7 / 3 / 1 days).  Throttled per
         (account, bucket) via the comp history table: we only fire
         a bucket reminder once per comp window, so renewing a comp
         resets the throttle naturally (the new ``granted`` row marks
         a new window).

    Idempotent: a re-run during the same UTC day is a no-op for both
    phases because the throttle row keys off the bucket and the expire
    helper is itself idempotent.  Returns a small status dict for
    metrics / logs.
    """
    from capabilities.platform.billing.notifications import (
        notify_comp_expired, notify_comp_expiring,
        days_until, reminder_bucket,
    )
    now = now or datetime.now(timezone.utc)
    platform_db = _platform_router().platform

    # Expire lapsed comps and notify their admins
    try:
        expired_ids = await platform_db.expire_lapsed_comps(now=now)
    except Exception:
        logger.exception("run_comp_expiry_sweep: expire_lapsed_comps failed")
        expired_ids = []
    expired_notified = 0
    for acct_id in expired_ids:
        try:
            await notify_comp_expired(acct_id)
            expired_notified += 1
        except Exception:
            logger.exception(
                "run_comp_expiry_sweep: notify_comp_expired failed acct=%d", acct_id,
            )

    # Expire elapsed Pro trials → downgrade to free.  Distinct mechanism
    # from comps: self-serve trials use status='trialing' + trial_ends_at
    # (see BillingMixin.start_trial), so they need their own sweep.  The
    # owner keeps access; they just drop to free-tier limits.
    trials_downgraded = 0
    try:
        downgraded = await platform_db.expire_due_trials(before_iso=now.isoformat())
        trials_downgraded = len(downgraded)
        for r in downgraded:
            try:
                await platform_db.add_platform_audit(
                    "trial_expired",
                    account_id=r["account_id"],
                    actor="scheduler",
                    details="14-day pro trial elapsed — downgraded to free",
                )
            except Exception:
                logger.exception(
                    "run_comp_expiry_sweep: trial-expiry audit failed acct=%s",
                    r["account_id"],
                )
        if trials_downgraded:
            logger.info(
                "run_comp_expiry_sweep: downgraded %d expired trial(s)",
                trials_downgraded,
            )
    except Exception:
        logger.exception("run_comp_expiry_sweep: expire_due_trials failed")

    # Send expiring-soon reminders to comps still in their window
    try:
        accounts = await platform_db.list_accounts(active_only=True)
    except Exception:
        logger.exception("run_comp_expiry_sweep: list_accounts failed")
        return {
            "expired": len(expired_ids), "expired_notified": expired_notified,
            "trials_downgraded": trials_downgraded, "reminded": 0, "checked": 0,
        }

    reminded = 0
    for acc in accounts:
        try:
            sub = await platform_db.get_subscription(acc.id)
        except Exception:
            logger.exception("run_comp_expiry_sweep: get_subscription failed acct=%d", acc.id)
            continue
        if not sub or not sub.get("is_comped"):
            continue
        exp = sub.get("comp_expires_at") or ""
        d = days_until(exp, now)
        bucket = reminder_bucket(d) if d is not None else None
        if bucket is None:
            continue
        already_sent = await _expiring_reminder_already_sent(
            platform_db, acc.id, bucket,
        )
        if already_sent:
            continue
        try:
            await notify_comp_expiring(acc.id, exp, d)
            await platform_db.record_comp_reminder_sent(acc.id, bucket, exp)
            reminded += 1
        except Exception:
            logger.exception(
                "run_comp_expiry_sweep: reminder failed acct=%d bucket=%d",
                acc.id, bucket,
            )

    logger.info(
        "comp expiry sweep: %d expired (%d notified), %d expiring reminders sent "
        "out of %d active accounts",
        len(expired_ids), expired_notified, reminded, len(accounts),
    )
    from infra import observability as _obs
    _obs.record_comp_sweep("expired", count=len(expired_ids))
    _obs.record_comp_sweep("reminder_sent", count=reminded)
    _obs.record_comp_sweep("checked", count=len(accounts))
    return {
        "expired":           len(expired_ids),
        "expired_notified":  expired_notified,
        "trials_downgraded": trials_downgraded,
        "reminded":          reminded,
        "checked":           len(accounts),
    }


async def _expiring_reminder_already_sent(
    platform_db, account_id: int, bucket: int,
) -> bool:
    """Has an expiring-soon reminder for this bucket fired this comp window?

    "This window" is defined as: since the most recent ``granted`` /
    ``renewed`` history row.  Renewing a comp counts as a new window,
    so each renewal allows a fresh round of reminders without a
    schema change.
    """
    # Pull recent history newest-first.  The `granted` / `renewed` row
    # is the upper bound of the previous window; anything before it is
    # irrelevant.  We cap the scan at 50 rows because comp histories
    # are short (one grant, occasional renewals, a few reminders).
    history = await platform_db.get_comp_history(account_id, limit=50)
    for row in history:
        action = row.get("action", "")
        if action in ("granted", "renewed"):
            # Older rows belong to a previous window — they can't be
            # the throttle entry for the current one.
            return False
        if action == "expiring_reminder":
            reason = row.get("reason", "") or ""
            if f"bucket={bucket}d" in reason:
                return True
    return False
