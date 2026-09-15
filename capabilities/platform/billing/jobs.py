"""Scheduled billing jobs.

Currently:

  - ``run_monthly_billing_snapshots`` — on the 1st of each month, writes
    one ``billing_usage_snapshots`` row per active account capturing
    what the previous calendar month's invoice was for.  The row is the
    source of truth for finance reports and dashboard history; it's
    idempotent on (account_id, period_start) so a re-run of the job
    after a transient failure is safe.

  - ``run_billing_quantity_sync`` — daily, pushes every account's
    billable vehicle count to the payment provider.  The count is ours
    (the vehicle registry), so this job is what keeps Stripe's extras
    quantity honest for an account whose telematics integration is
    paused or gone: the after-ingest sync never fires for it, and a
    truck added or archived by hand in 4truck would otherwise never
    reach the invoice.

The job lives here (not in ``features/vehicles/warehouse``) because the work
is purely billing — Samsara is already long-since polled for activity
by the time this fires (02:30 UTC vs the ingest job's 60s cadence).
"""

from __future__ import annotations

import logging
import asyncio
import os
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


def month_window(month: str) -> tuple[str, str]:
    """``"2026-09"`` → that calendar month, as a half-open ISO interval.

    The operator's way of naming a period: the monthly job bills the
    month that just closed, but an account switched to Absolute 0
    mid-year has months behind it that nobody billed, and the operator
    must be able to say which one.
    """
    try:
        year, mon = (int(part) for part in month.split("-", 1))
        start = datetime(year, mon, 1, tzinfo=timezone.utc)
    except Exception:
        raise ValueError("A month is written YYYY-MM, for example 2026-09.")
    end = datetime(year + (mon == 12), (mon % 12) + 1, 1, tzinfo=timezone.utc)
    return start.isoformat(), end.isoformat()


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
    the billable count from the vehicle registry via ``compute_billing``
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
        # ``vehicle_count`` keeps the subscription's fleet-size column for
        # backwards compat; the registry-based ``active_vehicles`` is what drives
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


async def issue_local_invoice(account_id: int, *, now: datetime | None = None,
                              period: tuple[str, str] | None = None) -> dict | None:
    """Write, render and send one Absolute 0 account's monthly invoice.

    Stripe bills nobody here, so nothing would otherwise be written: no
    invoice, no receipt, no record for an accountant to find.  This
    computes the month the ordinary way, records what was covered, and
    sends the same receipt email everyone else gets — with a PDF this
    process built rather than one Stripe hosted.

    Idempotent through the invoice number, which is derived from the
    period and the account: running the month twice writes one row.
    Returns the row's fields, or None when the account has no Absolute 0
    grant (every other account is Stripe's to bill).
    """
    from capabilities.platform.billing import local_invoice as _inv
    from adapters.storage.account_discounts import ABSOLUTE

    platform_db = _platform_router().platform
    discount = await platform_db.live_account_discount(account_id)
    if not discount or discount.get("kind") != ABSOLUTE:
        return None
    billing = await platform_db.compute_billing(account_id)
    period_start, period_end = period or _previous_month_window(now)
    account = await platform_db.get_account(account_id)
    invoice = _inv.build(
        account_id=account_id, account_name=getattr(account, "name", "") or "",
        billing=billing, discount=discount,
        period_start=period_start, period_end=period_end)
    row = _inv.to_row(invoice)
    await platform_db.record_invoice(account_id, invoice["number"], **row)
    logger.info("Absolute 0 invoice %s written for account %s (covered %s cents)",
                invoice["number"], account_id, invoice["subtotal_cents"])
    await _send_local_invoice(account_id, platform_db, invoice)
    return {"number": invoice["number"], **row}


async def _send_local_invoice(account_id: int, platform_db, invoice: dict) -> bool:
    """Email the invoice we just wrote, with its PDF attached.

    Best-effort, and deliberately after the row: the record of what was
    covered must survive a mail relay that is down.
    """
    from capabilities.platform.billing import local_invoice as _inv
    from capabilities.platform.billing import receipt_email
    if not receipt_email.enabled():
        return False
    try:
        sub = await platform_db.get_subscription(account_id) or {}
        to = str(sub.get("billing_email") or "")
        if not to:
            logger.info("Absolute 0 invoice %s: no address on file", invoice["number"])
            return False
        pdf = await asyncio.to_thread(
            _inv.render_pdf, invoice,
            {"name": (os.getenv("SMTP_FROM_NAME") or "4truck"), "support": receipt_email.reply_to()})
        sent = await asyncio.to_thread(
            receipt_email.send_local, to=to, account_name=invoice.get("account_name") or "",
            invoice=invoice, pdf=pdf)
        if sent:
            await platform_db.mark_receipt_emailed(
                invoice["number"], datetime.now(timezone.utc).isoformat())
        return bool(sent)
    except Exception:
        logger.exception("Absolute 0 invoice %s could not be sent", invoice["number"])
        return False


async def run_local_invoices(_app=None) -> dict:
    """Monthly: every Absolute 0 account gets its invoice and receipt.

    Runs after the usage snapshots, on the same day, so the month it
    bills is the month just closed.  One account failing never blocks
    the rest.
    """
    platform_db = _platform_router().platform
    try:
        accounts = await platform_db.list_accounts(active_only=True)
    except Exception:
        logger.exception("run_local_invoices: list_accounts failed")
        return {"issued": 0, "failed": 0}
    issued = failed = 0
    for acc in accounts:
        try:
            if await issue_local_invoice(acc.id):
                issued += 1
        except Exception:
            failed += 1
            logger.exception("run_local_invoices: account %s — continuing", acc.id)
    logger.info("Absolute 0 invoices: %d issued, %d failed", issued, failed)
    return {"issued": issued, "failed": failed}


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
    "run_local_invoices",
    "issue_local_invoice",
    "snapshot_account_billing",
    "_previous_month_window",
    "month_window",
    "run_comp_expiry_sweep",
    "run_billing_quantity_sync",
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


# ── Daily billable-quantity sync ──────────────────────────────────


async def run_discount_reconcile(_app=None) -> dict:
    """Daily: make each live discount row say what Stripe holds.

    Stripe ends a repeating coupon on its own clock — three months from
    the moment it was applied, not three of our calendar months — so a
    row left saying ``active`` after that tells the operator something
    untrue, and the customer's page shows a promotion that is no longer
    coming off the bill.  A row the provider no longer knows is closed.

    One account's failure never blocks the rest, and nothing here is
    granted or removed: this only reconciles what is already true.
    """
    from capabilities.platform.billing import get_provider

    platform_db = _platform_router().platform
    provider = get_provider()
    if not hasattr(provider, "discount_state"):
        return {"checked": 0, "ended": 0, "failed": 0}
    try:
        rows = await platform_db.discounts_to_reconcile()
    except Exception:
        logger.exception("run_discount_reconcile: could not list discounts")
        return {"checked": 0, "ended": 0, "failed": 0}
    ended = failed = 0
    for row in rows:
        account_id = int(row["account_id"])
        try:
            state = await provider.discount_state(account_id, platform_db, row)
        except Exception:
            failed += 1
            logger.exception("run_discount_reconcile: account %s — continuing", account_id)
            continue
        if state is None:
            # pending grants have nothing at the provider yet and are
            # not "ended"; only one the provider HELD and lost is
            if str(row.get("stripe_discount_id") or ""):
                await platform_db.mark_account_discount(int(row["id"]), status="ended")
                ended += 1
                logger.info("discount %s for account %s ended at the provider", row["id"], account_id)
            continue
        if state.get("ends_at") and state["ends_at"] != row.get("ends_at"):
            await platform_db.mark_account_discount(int(row["id"]), ends_at=state["ends_at"])
    logger.info("discount reconcile: %d checked, %d ended, %d failed", len(rows), ended, failed)
    return {"checked": len(rows), "ended": ended, "failed": failed}


async def run_billing_quantity_sync(_app=None) -> dict:
    """Daily: reconcile the provider's extras quantity with the registry.

    The billable count is computed from OUR vehicle registry
    (``BillingMixin.count_billable_vehicles``), never from a telematics
    signal, so it must reach the provider on a clock of our own.  The
    two other triggers are event-driven and both have a hole this job
    covers:

      * the after-ingest sync (samsara/sync.py) only fires for accounts
        whose Samsara integration is alive — a paused or disconnected
        account is exactly the one that never gets it;
      * the operator's console button is a human remembering.

    Every account is visited; ``sync_billing_quantity`` itself skips the
    ones that are not billed by the provider (stub deployments, comped
    accounts without a checkout, subscriptions without an extras line)
    after one local read, so the loop costs nothing for them and one
    provider read per billed account.  Outcomes are tallied by the
    provider's own ``skipped`` reason so the log line says how many
    subscriptions were actually PATCHed.

    One account erroring out never blocks the rest.  Idempotent: the
    sync PATCHes only when the quantity differs, so a re-run is a no-op.
    """
    from capabilities.platform.billing import get_provider

    platform_db = _platform_router().platform
    provider = get_provider()
    try:
        accounts = await platform_db.list_accounts(active_only=True)
    except Exception:
        logger.exception("run_billing_quantity_sync: list_accounts failed")
        return {"patched": 0, "noop": 0, "skipped": 0, "failed": 0, "total": 0}
    patched = noop = skipped = failed = 0
    # A jump the guard held is money not billed until a person releases
    # it — it used to live in a WARNING line and a metric, and nobody
    # reads either at 03:30.  Named here, told below.
    held: list[dict] = []
    for acc in accounts:
        try:
            result = await provider.sync_billing_quantity(acc.id, platform_db)
        except Exception:
            failed += 1
            logger.exception(
                "run_billing_quantity_sync: sync failed acct=%d — continuing", acc.id,
            )
            continue
        reason = result.get("skipped")
        if reason is None:
            patched += 1
        elif reason == "noop":
            noop += 1
        elif reason == "stripe_error":
            # The provider already logged the exception with the item
            # id; here it only counts as a failure of this run.
            failed += 1
        else:
            skipped += 1
            if reason == "jump_guard":
                held.append({
                    "account_id": acc.id, "name": getattr(acc, "name", "") or f"#{acc.id}",
                    "before": int(result.get("before") or 0), "after": int(result.get("after") or 0),
                })
    logger.info(
        "billing quantity sync: %d patched, %d unchanged, %d not provider-billed, "
        "%d failed, %d held (of %d accounts)",
        patched, noop, skipped, failed, len(held), len(accounts),
    )
    if held:
        await _tell_operators_about_held_jumps(held)
    return {
        "patched": patched, "noop": noop, "skipped": skipped,
        "failed": failed, "total": len(accounts), "held": held,
    }


async def _tell_operators_about_held_jumps(held: list[dict]) -> int:
    """One Telegram message a day, naming every account whose extras
    count the guard would not raise on its own, with the two numbers
    and where the button is.  Best-effort: the job's own return value
    carries the same list, so a bot that is down loses nothing."""
    from capabilities.platform.billing.notifications import tell_operators
    lines = [f"• {h['name']} (#{h['account_id']}): {h['before']} → {h['after']} extra trucks"
             for h in held]
    text = (
        "<b>⏸ Billing: extras held by the jump guard</b>\n\n"
        + "\n".join(lines)
        + "\n\nEach rose by more than the guard allows unattended, so Stripe still bills the "
        "old count. Open the account in the console and press <b>Sync quantity</b> to release it."
    )
    try:
        return await tell_operators(text)
    except Exception:
        # the held list is in the job's result and the log; a bot that
        # cannot send must not turn the sync itself into a failure
        logger.exception("run_billing_quantity_sync: could not tell the operators about %d held jump(s)", len(held))
        return 0
