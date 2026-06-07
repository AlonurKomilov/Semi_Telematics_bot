"""Centralized scheduled job registration.

All 12 periodic background jobs are registered here instead of inline
in app.py.  Job *implementations* remain in their domain modules.
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
        from capabilities.alerting.faults import initialize_known_faults
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

    from capabilities.alerting import (
        check_new_faults,
        check_health_alerts,
        check_low_fuel,
        check_camera_alerts,
        deliver_dnd_alerts,
        check_events,
        check_unsafe_parking,
        re_escalate_critical_alerts,
    )
    from interfaces.bot.scheduled_reports import send_scheduled_reports
    from interfaces.bot.maintenance import (
        check_overdue_maintenance,
        check_overdue_by_mileage,
        check_overdue_by_engine_hours,
        check_upcoming_maintenance_warnings,
    )
    from interfaces.bot.driver_expiry import check_document_expirations
    from interfaces.bot.driver_samsara_sync import check_driver_samsara_sync
    from interfaces.bot.geofences import check_geofence_events
    from capabilities.scoring.jobs import take_daily_scorecard_snapshots
    from capabilities.pti.jobs import (
        job_pti_spawn_weekly,
        job_pti_remind_due_soon,
        job_pti_escalate_overdue,
        job_pti_fleet_digest,
    )

    scheduler.add_job(
        check_new_faults, "interval",
        minutes=ALERT_INTERVAL, args=[app], id="fault_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_health_alerts, "interval",
        minutes=15, args=[app], id="health_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_low_fuel, "interval",
        minutes=ALERT_INTERVAL, args=[app], id="fuel_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        send_scheduled_reports, "interval",
        hours=1, args=[app], id="scheduled_reports_send",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_overdue_maintenance, "interval",
        hours=24, args=[app], id="maintenance_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_overdue_by_mileage, "interval",
        hours=6, args=[app], id="maintenance_mileage_check",
        max_instances=1, coalesce=True,
    )
    # Engine-hours overdue check.  Same cadence as the mileage twin
    # (6 h) — both share the ``vehicle_state`` warehouse table and share
    # the same once-per-crossing notification semantics via the
    # ``alerted_at`` throttle, so a paired cadence keeps them aligned.
    scheduler.add_job(
        check_overdue_by_engine_hours, "interval",
        hours=6, args=[app], id="maintenance_engine_hours_check",
        max_instances=1, coalesce=True,
    )
    # Pre-overdue warning ("due in 7d / 500 mi / 50 hrs").  Runs daily
    # because the warning window is measured in days — a faster cadence
    # wouldn't shift the answer.  Fires once per task per cycle (gated
    # by ``warning_sent_at``).
    scheduler.add_job(
        check_upcoming_maintenance_warnings, "interval",
        hours=24, args=[app], id="maintenance_warning_check",
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
    # Driver-document expiration alerts (CDL, medical card, MVR, …).
    # Ticks hourly; each per-account body checks ``is_local_hour(acct,
    # 9)`` and skips when it's not yet 09:00 in that account's
    # timezone.  So an Eastern fleet gets the digest at 09:00 ET
    # (14:00 UTC), a Pacific fleet at 09:00 PT (17:00 UTC).  Dedup is
    # per-(doc_id, bucket_days), so the once-per-day delivery is safe
    # even if a single account has admins across multiple timezones.
    scheduler.add_job(
        check_document_expirations, "cron",
        minute=2, args=[app], id="driver_doc_expiry_check",
        max_instances=1, coalesce=True,
    )
    # Cross-system identity drift watcher.  Same hourly-tick pattern;
    # fires at 10:00 LOCAL for each account so admins get the two
    # daily Driver-Module pings back-to-back regardless of timezone.
    scheduler.add_job(
        check_driver_samsara_sync, "cron",
        minute=4, args=[app], id="driver_samsara_sync",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_geofence_events, "interval",
        minutes=5, args=[app], id="geofence_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_events, "interval",
        minutes=5, args=[app], id="events_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_camera_alerts, "interval",
        hours=6, args=[app], id="camera_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        deliver_dnd_alerts, "cron",
        minute=0, args=[app], id="dnd_delivery",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_unsafe_parking, "interval",
        minutes=30, args=[app], id="parking_check",
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
    scheduler.add_job(
        re_escalate_critical_alerts, "interval",
        hours=1, args=[app], id="critical_reescalate",
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

    # Resend invite-email webhook idempotency-table cleanup.
    # 14-day retention is well past Resend's 10h retry ceiling.
    # Without this the table grows unbounded over months — at
    # 10k sends/month with healthy delivery + occasional bounces
    # that's ~50k rows/year per deploy.  Run at 03:20 UTC so it
    # doesn't collide with the alerting cleanup at 03:15.
    async def _prune_email_webhook_events(_app):
        from infra.platform import get_platform_db
        try:
            db = await get_platform_db()
            n = await db.prune_email_webhook_events(days=14)
            logger.info("prune_email_webhook_events: deleted %d row(s)", n)
        except Exception:
            logger.exception("prune_email_webhook_events failed")
    scheduler.add_job(
        _prune_email_webhook_events, "cron",
        hour=3, minute=20, args=[app], id="prune_email_webhook_events",
        max_instances=1, coalesce=True,
    )
    # Nightly scorecard snapshot. Tick hourly; the per-account body
    # gates on local 02:00 so each account snapshots "yesterday" at a
    # consistent local cadence.
    scheduler.add_job(
        take_daily_scorecard_snapshots, "cron",
        minute=30, args=[app], id="scorecard_snapshot",
        max_instances=1, coalesce=True,
    )
    from capabilities.scoring.jobs import check_scorecard_drop_alerts
    # Score-drop alerts run an hour after snapshots so the freshly
    # written row is the baseline.  Gated on local 03:00.
    scheduler.add_job(
        check_scorecard_drop_alerts, "cron",
        minute=0, args=[app], id="scorecard_drop_alerts",
        max_instances=1, coalesce=True,
    )

    # ── monthly Pay-for-Performance payroll job ─────────
    from capabilities.payroll.jobs import run_monthly_payroll_job
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
    from capabilities.coaching.jobs import run_nightly_coaching_job
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
