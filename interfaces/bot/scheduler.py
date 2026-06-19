"""Centralized scheduled job registration.

Alert sources (the per-feature contributions to the Alerts hub) are
registry-driven: each source self-registers its job id + cadence via
``@register_alert_source`` in its own module, and this file just loops
``alert_sources()``.  Platform jobs (warehouse ingestion, billing,
payroll, PTI, reports, cleanup) stay registered inline below.  Job
*implementations* remain in their domain modules either way.
"""

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application

from interfaces.bot.config import ALERT_INTERVAL

logger = logging.getLogger(__name__)


def register_all(scheduler: AsyncIOScheduler, app: Application):
    """Register every periodic background job on *scheduler*."""

    # ── Startup: warm up the in-memory fault cache immediately ───
    # Ensures _known_faults is pre-populated from Samsara before the
    # first fault-check interval fires, preventing false-positive
    # "new fault" alerts on restart.
    async def _warm_fault_cache(_app=None):
        from features.vehicles.faults.alert import initialize_known_faults
        try:
            await initialize_known_faults()
        except Exception:
            logger.exception("Fault cache warm-up failed")

    scheduler.add_job(
        _warm_fault_cache, "date",
        run_date=datetime.now(timezone.utc),
        id="fault_cache_warmup",
        max_instances=1,
    )

    # ── Alert sources (registry-driven) ─────────────────────────────
    # Each source module self-registers its check via
    # ``@register_alert_source`` (capabilities/alerting/registry.py) —
    # job id, trigger, and cadence live WITH the source.  Importing the
    # module is what populates the registry; the loop below registers
    # them all with the exact same APScheduler options as before.
    # Cadence rationale (e.g. the mileage/engine-hours 6h pairing, the
    # local-hour gates on doc-expiry/samsara-sync) lives at each
    # decorator site + function docstring.
    from capabilities.alerting.registry import alert_sources
    import features.vehicles.faults.alert        # noqa: F401  fault_check
    import features.vehicles.health.alert        # noqa: F401  health_check
    import features.vehicles.fuel.alert          # noqa: F401  fuel_check
    import features.cameras.alert       # noqa: F401  camera_check
    import features.events.alert                 # noqa: F401  events_check
    import capabilities.alerting.dnd             # noqa: F401  dnd_delivery
    import capabilities.alerting.escalation      # noqa: F401  critical_reescalate
    import features.parking.check                # noqa: F401  parking_check
    import interfaces.bot.geofences              # noqa: F401  geofence_check
    import interfaces.bot.maintenance            # noqa: F401  maintenance_* (x4)
    import features.drivers.documents.alert      # noqa: F401  driver_doc_expiry_check
    import interfaces.bot.driver_samsara_sync    # noqa: F401  driver_samsara_sync
    import capabilities.scorecards.jobs             # noqa: F401  scorecard_drop_alerts

    for src in alert_sources():
        scheduler.add_job(
            src.fn, src.trigger,
            args=[app], id=src.key,
            max_instances=1, coalesce=True,
            **src.schedule,
        )

    from interfaces.bot.scheduled_reports import send_scheduled_reports
    from capabilities.scorecards.jobs import take_daily_scorecard_snapshots
    from features.inspections.jobs import (
        job_pti_spawn_weekly,
        job_pti_remind_due_soon,
        job_pti_escalate_overdue,
        job_pti_fleet_digest,
    )

    scheduler.add_job(
        send_scheduled_reports, "interval",
        hours=1, args=[app], id="scheduled_reports_send",
        max_instances=1, coalesce=True,
    )
    # ── PTI (Pre-Trip Inspection) jobs ──────────────────────────────
    #
    # All four jobs use the hourly-cron pattern + per-account local-hour
    # gating: APScheduler fires every hour at a staggered minute and
    # each job iterates active accounts, skipping any whose local clock
    # isn't at the target hour.  Same convention the doc-expiry and
    # samsara-sync jobs use.  Minute offsets are spread out (10/15/20/25)
    # so all four don't race to acquire the single-writer DB lock at the
    # top of the hour.
    scheduler.add_job(
        job_pti_spawn_weekly, "cron",
        minute=10, args=[app], id="pti_spawn_weekly",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_pti_remind_due_soon, "cron",
        minute=15, args=[app], id="pti_remind_due_soon",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_pti_escalate_overdue, "cron",
        minute=20, args=[app], id="pti_escalate_overdue",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_pti_fleet_digest, "cron",
        minute=25, args=[app], id="pti_fleet_digest",
        max_instances=1, coalesce=True,
    )
    # Hybrid-storage sync: drain ``storage_sync_queue`` to each
    # account's cloud backend (Drive).  60s interval is the right
    # cadence — files land in cloud within a minute of upload in the
    # happy path, and a per-row lease ensures a crashed worker's rows
    # auto-recover.  Env-tunable batch + per-account concurrency:
    # ``SYNC_WORKER_BATCH_SIZE`` / ``SYNC_WORKER_ACCOUNT_CONCURRENCY``.
    from capabilities.storage.sync_worker import sync_pending_storage
    scheduler.add_job(
        sync_pending_storage, "interval",
        seconds=60, args=[app], id="storage_sync",
        max_instances=1, coalesce=True,
    )
    # Nightly stale-alert cleanup — closes ``alert_acknowledgments`` rows
    # that have been sitting at ``status='active'`` for > 14 days (per
    # NIGHTLY_DEFAULT_DAYS) so the Alerts dashboard stays scannable.
    # ``events`` + ``maintenance`` types are skipped — see
    # ``capabilities/alerting/cleanup.py`` for the safety rails.
    # Scheduled at 03:15 UTC = quiet hours across all US zones.
    from capabilities.alerting.cleanup import nightly_stale_close
    scheduler.add_job(
        nightly_stale_close, "cron",
        hour=3, minute=15, args=[app], id="nightly_stale_close",
        max_instances=1, coalesce=True,
    )

    # (The Resend email-webhook cleanup now runs through the Retention
    # hub's platform pass — email.delivery_events, 14d.  See data_retention
    # below.)
    # Nightly scorecard snapshot. Tick hourly; the per-account body
    # gates on local 02:00 so each account snapshots "yesterday" at a
    # consistent local cadence.
    scheduler.add_job(
        take_daily_scorecard_snapshots, "cron",
        minute=30, args=[app], id="scorecard_snapshot",
        max_instances=1, coalesce=True,
    )

    # ── monthly Pay-for-Performance payroll job ─────────
    from features.payroll.jobs import run_monthly_payroll_job
    scheduler.add_job(
        run_monthly_payroll_job, "cron",
        day=1, hour=2, minute=0, args=[app], id="payroll_monthly",
        max_instances=1, coalesce=True,
    )

    # ── monthly billing snapshot ─────────────────────────
    # Records one billing_usage_snapshots row per active account so the
    # dashboard can render historical periods (and finance can audit
    # what we actually billed for).  Fires 30 min after payroll so the
    # database isn't doing two big batches simultaneously.
    from capabilities.billing.jobs import run_monthly_billing_snapshots
    scheduler.add_job(
        run_monthly_billing_snapshots, "cron",
        day=1, hour=2, minute=30, args=[app], id="billing_snapshot_monthly",
        max_instances=1, coalesce=True,
    )

    # ── daily comp-expiry sweep ──────────────────────────
    # Flips ``is_comped`` off for accounts whose comp window has
    # closed (and pings their admins), then sends 7-day / 3-day /
    # 1-day reminders to comps approaching expiry.  Both phases are
    # idempotent so a re-run on the same UTC day is a no-op.
    from capabilities.billing.jobs import run_comp_expiry_sweep
    scheduler.add_job(
        run_comp_expiry_sweep, "cron",
        hour=3, minute=0, args=[app], id="billing_comp_expiry_sweep",
        max_instances=1, coalesce=True,
    )

    # ── nightly Auto Coaching evaluation ─────────────────
    # Tick hourly; per-account gate fires at local 03:30 so coaching
    # runs after the snapshot+drop alerts but before the morning
    # driver-doc digest.
    from features.coaching.jobs import run_nightly_coaching_job
    scheduler.add_job(
        run_nightly_coaching_job, "cron",
        minute=30, args=[app], id="coaching_nightly",
        max_instances=1, coalesce=True,
    )

    # ── telemetry warehouse ingestion ─────────────────────
    # Imported lazily so non-warehouse deployments (older installs that
    # haven't run migrations yet) can still boot the scheduler.
    from capabilities.telemetry.ingestor import (
        job_ingest_vehicle_state,
        job_ingest_safety_events,
        job_ingest_driver_efficiency_daily,
        job_aggregate_telemetry_hourly,
        job_snapshot_vehicle_state,
        job_aggregate_metrics_daily,
        job_ingest_vehicle_health,
        job_ingest_vehicle_faults,
        job_ingest_fleet_weather,
        job_ingest_fleet_efficiency,
        job_ingest_geofence_definitions,
    )
    scheduler.add_job(
        job_ingest_vehicle_state, "interval",
        seconds=60, args=[app], id="warehouse_vehicle_state",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_ingest_safety_events, "interval",
        minutes=5, args=[app], id="warehouse_safety_events",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_ingest_driver_efficiency_daily, "interval",
        hours=1, args=[app], id="warehouse_driver_efficiency",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_aggregate_telemetry_hourly, "cron",
        minute=5, args=[app], id="warehouse_telemetry_hourly",
        max_instances=1, coalesce=True,
    )
    # The 5-min vehicle-state HISTORY (vehicle_state_snapshot) + the
    # nightly daily rollup (vehicle_metrics_daily).  These were dropped
    # from the scheduler in the feature-centric refactor (3864e08), which
    # silently froze all back-dated odometer/usage history at the last
    # run — the current-state job kept the live map fresh, masking it.
    # Cadences match the provider catalog (STATE_SNAPSHOT_HISTORY: 5 min,
    # METRICS_DAILY: 00:05 UTC).
    scheduler.add_job(
        job_snapshot_vehicle_state, "interval",
        minutes=5, args=[app], id="warehouse_state_snapshot",
        max_instances=1, coalesce=True,
    )
    # ``timezone="UTC"`` is REQUIRED here: the scheduler runs in the
    # server's local zone (e.g. CEST/UTC+2), so a bare ``hour=0`` fires at
    # 00:05 LOCAL = 22:05 UTC — BEFORE UTC midnight — and the aggregator's
    # "yesterday UTC" then resolves two days back, leaving the daily
    # roll-up perpetually ~1 day stale.  Pinning the trigger to UTC makes
    # 00:05 land just after UTC midnight so it aggregates the just-closed
    # UTC day.  (The 5-min snapshot above is an interval job — tz-agnostic.)
    scheduler.add_job(
        job_aggregate_metrics_daily, "cron",
        hour=0, minute=5, timezone="UTC", args=[app], id="warehouse_metrics_daily",
        max_instances=1, coalesce=True,
    )
    # ── data retention (cross-cutting hub) ───────────────────
    # One nightly pass that prunes every registered retention target to the
    # window its OWNING feature declares (capabilities/retention):
    #   tenant   — vehicle.timeline_5min (7d), vehicle.timeline_hourly (90d),
    #              vehicle.metrics_daily (730d), scorecards.score_history (90d)
    #   platform — email.delivery_events (14d)
    # Replaces three scattered paths (the per-capability HISTORY_PRUNE job,
    # the inline score_events prune, the email-webhook cleanup) so the
    # windows live in one readable contract.  Runs for EVERY active account —
    # a prune on a tier with no rows is a harmless no-op, and HISTORY_PRUNE
    # was on-by-default for every telematics account anyway, so this only
    # adds no-ops for non-telematics tenants (the dropped capability gate is
    # the single behavior change to verify on deploy).  Safe now that the
    # 730-day EOD daily tier is populated, so back-dated work orders keep
    # their reach after the 7-day raw snapshots are pruned.  02:00 UTC, after
    # the 00:05 daily roll-up; UTC-pinned for the same reason as the roll-up.
    from capabilities.retention.jobs import job_run_retention
    scheduler.add_job(
        job_run_retention, "cron",
        hour=2, minute=0, timezone="UTC", args=[app], id="data_retention",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_ingest_vehicle_health, "interval",
        minutes=5, args=[app], id="warehouse_vehicle_health",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_ingest_vehicle_faults, "interval",
        minutes=2, args=[app], id="warehouse_vehicle_faults",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_ingest_fleet_weather, "interval",
        minutes=10, args=[app], id="warehouse_fleet_weather",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_ingest_fleet_efficiency, "interval",
        minutes=30, args=[app], id="warehouse_fleet_efficiency",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_ingest_geofence_definitions, "interval",
        hours=1, args=[app], id="warehouse_geofence_definitions",
        max_instances=1, coalesce=True,
    )

    # ── Integration-health probes ──────────────────────────────────
    # Every 15 min: probe each active account × connected integration's
    # ``test_connection`` and record the result on ``account_integrations``,
    # so an upstream token revocation surfaces as an Error badge on the
    # dashboard within 15 min without an owner clicking "Test connection".
    # Bounded by the per-account semaphore (5) + a 12s per-probe timeout;
    # no DB write when the status is unchanged.
    from capabilities.integrations.telematics_health import (
        job_run_integration_health_checks,
    )
    scheduler.add_job(
        job_run_integration_health_checks, "interval",
        minutes=15, args=[app], id="integration_health_checks",
        max_instances=1, coalesce=True,
    )

    # ── Account-lifecycle housekeeping ─────────────────────────────
    # Daily at 04:10 UTC: hard-purge accounts whose 90-day deletion
    # grace has elapsed, warn accounts ~7 days out, and drop expired
    # deletion-confirmation codes.  All three live in one job so a
    # single failure surface covers the whole lifecycle tail.
    async def _account_lifecycle_housekeeping(_app):
        from datetime import datetime, timezone
        from infra.platform import get_platform_db
        try:
            db = get_platform_db()

            # 1. Hard purge — point of no return.  Each account logs an
            #    audit row BEFORE the data vanishes (the row itself has
            #    account_id NULLed context preserved in details).
            now_iso = (
                datetime.now(timezone.utc).isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
            for acct_id in await db.list_accounts_pending_purge(before_iso=now_iso):
                lc = await db.get_account_lifecycle(acct_id)
                name = lc["name"] if lc else "?"
                # Delete the account's stored files (disk + Google Drive)
                # BEFORE purge_account_data drops the rows that hold its
                # storage backend choice + Drive credentials — otherwise
                # the right backend can't be resolved and the blobs (incl.
                # driver-licence PII) would orphan forever.  Best-effort:
                # a file-cleanup failure must not block the DB purge.
                files_purged = 0
                try:
                    from adapters.storage.object_store import purge_account_files
                    from infra.platform import get_tenant_db
                    tdb = await get_tenant_db(acct_id)
                    if tdb is not None:
                        files_purged = await purge_account_files(acct_id, tdb)
                except Exception:
                    logger.exception("purge: file cleanup failed acct=%s", acct_id)
                deleted = await db.purge_account_data(acct_id)
                total = sum(deleted.values())
                try:
                    await db.add_platform_audit(
                        "account_purged",
                        account_id=None,  # the row is gone; keep id in details
                        actor="scheduler",
                        details=(
                            f"account_id={acct_id} name={name!r} "
                            f"rows={total} files={files_purged}"
                        ),
                    )
                except Exception:
                    logger.exception("purge: audit write failed acct=%s", acct_id)
                logger.info(
                    "Account %s (%r) purged: %d rows across %d tables, %d file(s)",
                    acct_id, name, total, len(deleted), files_purged,
                )

            # 2. Purge warnings — accounts erasing within 7 days.  The
            #    job runs daily and the window is (now, now+7d], so an
            #    account gets roughly one warning per day for its final
            #    week; acceptable cadence for a destructive deadline.
            from capabilities.email.lifecycle_emails import (
                send_purge_warning_email,
            )
            for row in await db.list_accounts_purging_within(days=7):
                cur = await db._db.execute(
                    "SELECT email FROM users "
                    "WHERE account_id = ? AND role = 'owner' AND email IS NOT NULL",
                    (row["id"],),
                )
                for r in await cur.fetchall():
                    try:
                        send_purge_warning_email(
                            to=dict(r)["email"],
                            account_name=row["name"],
                            purge_at=row["purge_at"],
                        )
                    except Exception:
                        logger.exception("purge-warning email failed acct=%s", row["id"])

            # 3. Expired deletion codes — dead weight, not a security
            #    risk; swept for hygiene.
            n = await db.cleanup_expired_deletion_codes()
            if n:
                logger.info("Cleaned up %d expired deletion code(s)", n)
        except Exception:
            logger.exception("account_lifecycle_housekeeping failed")

    scheduler.add_job(
        _account_lifecycle_housekeeping, "cron",
        hour=4, minute=10, args=[app], id="account_lifecycle_housekeeping",
        max_instances=1, coalesce=True,
    )
