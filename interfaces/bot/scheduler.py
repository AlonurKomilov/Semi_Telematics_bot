"""Centralized scheduled job registration.

Alert sources (the per-feature contributions to the Alerts hub) are
registry-driven: each source self-registers its job id + cadence via
``@register_alert_source`` in its own module, and this file just loops
``alert_sources()``.  Platform jobs (warehouse ingestion, billing,
driver pay, PTI, reports, cleanup) stay registered inline below.  Job
*implementations* remain in their domain modules either way.
"""

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application


logger = logging.getLogger(__name__)


# (category, description) per job id, for the operator-console Scheduler
# page — the single source of truth for grouping + prose.  Schedule +
# next-run come live from the snapshot; only this metadata lives here.
# Jobs with no entry fall back to ("Other", "").
_JOB_META = {
    # ── Telematics (warehouse ingest + rollup) ──
    "warehouse_vehicle_state":        ("Telematics", "Pull live vehicle state from Samsara"),
    "warehouse_state_snapshot":       ("Telematics", "Capture the 5-min vehicle-state history"),
    "warehouse_telemetry_hourly":     ("Telematics", "Roll 5-min snapshots into the hourly tier"),
    "warehouse_metrics_daily":        ("Telematics", "Roll the hourly tier into the daily tier"),
    "warehouse_metrics_weekly":       ("Telematics", "Roll the daily tier into the weekly tier"),
    "warehouse_vehicle_health":       ("Telematics", "Ingest current vehicle health"),
    "warehouse_vehicle_faults":       ("Telematics", "Ingest current vehicle faults (DTCs)"),
    "warehouse_safety_events":        ("Telematics", "Ingest safety / harsh-driving events"),
    "warehouse_driver_efficiency":    ("Telematics", "Ingest per-driver daily efficiency"),
    "warehouse_fleet_weather":        ("Telematics", "Ingest cabin-weather snapshots"),
    "warehouse_fleet_efficiency":     ("Telematics", "Ingest fleet-efficiency aggregates"),
    "warehouse_geofence_definitions": ("Telematics", "Sync geofence definitions"),
    "fault_cache_warmup":             ("Telematics", "Warm the vehicle-fault cache on startup"),
    # ── Datatruck (TMS) auto-sync ──
    "datatruck_sync_drivers":         ("TMS", "Sync Datatruck drivers"),
    "datatruck_sync_trucks":          ("TMS", "Sync Datatruck trucks"),
    "datatruck_sync_trailers":        ("TMS", "Sync Datatruck trailers"),
    "datatruck_sync_orders":          ("TMS", "Sync Datatruck loads / orders"),
    "datatruck_sync_work_orders":     ("TMS", "Sync Datatruck work orders"),
    # ── Alerts (per-feature checks + delivery/escalation) ──
    "events_check":                   ("Alerts", "Check for new safety events to alert on"),
    "geofence_check":                 ("Alerts", "Check geofence entries/exits to alert on"),
    "health_check":                   ("Alerts", "Check vehicle health for alerts"),
    "fault_check":                    ("Alerts", "Check vehicle faults (DTCs) for alerts"),
    "fuel_check":                     ("Alerts", "Check fuel anomalies to alert on"),
    "parking_check":                  ("Alerts", "Check unsafe / long parking to alert on"),
    "camera_check":                   ("Alerts", "Check dashcam events to alert on"),
    "maintenance_check":              ("Alerts", "Check maintenance due to alert on"),
    "maintenance_warning_check":      ("Alerts", "Check upcoming maintenance warnings"),
    "maintenance_mileage_check":      ("Alerts", "Check mileage-based maintenance thresholds"),
    "maintenance_engine_hours_check": ("Alerts", "Check engine-hours maintenance thresholds"),
    "critical_reescalate":            ("Alerts", "Re-escalate unacknowledged critical alerts"),
    "dnd_delivery":                   ("Alerts", "Deliver do-not-disturb-queued alerts"),
    "scorecard_drop_alerts":          ("Alerts", "Alert on significant driver score drops"),
    "driver_doc_expiry_check":        ("Alerts", "Alert on expiring driver documents"),
    # ── Scorecards & coaching ──
    "scorecard_snapshot":             ("Scorecards & coaching", "Write nightly driver/vehicle scorecard snapshots"),
    "coaching_nightly":               ("Scorecards & coaching", "Run the nightly auto-coaching evaluation"),
    # ── Inspections (PTI) ──
    "pti_spawn_weekly":               ("Inspections (PTI)", "Spawn the weekly PTI inspections"),
    "pti_remind_due_soon":            ("Inspections (PTI)", "Remind drivers of PTI inspections due soon"),
    "pti_escalate_overdue":           ("Inspections (PTI)", "Escalate overdue PTI inspections"),
    "pti_fleet_digest":               ("Inspections (PTI)", "PTI fleet digest to managers"),
    # ── Work orders ──
    "nightly_stale_close":            ("Work orders", "Auto-close stale work orders"),
    # ── Driver Pay (tenant feature — the customer paying their drivers) ──
    "driver_pay_monthly":                ("Driver Pay", "Draft the monthly driver-pay run for each enabled account"),
    # ── Platform billing (system side — us charging the customer) ──
    # Deliberately a SEPARATE category from tenant Driver Pay: same word
    # ("money"), opposite direction and audience.
    "billing_snapshot_monthly":       ("Platform billing", "Record monthly billing-usage snapshots"),
    "billing_comp_expiry_sweep":      ("Platform billing", "Expire lapsed comp accounts + send reminders"),
    # ── Reporting ──
    "scheduled_reports_send":         ("Reporting", "Send due scheduled reports"),
    # ── Integrations ──
    "integration_health_checks":      ("Integrations", "Check each account's integration health"),
    "driver_samsara_sync":            ("Integrations", "Sync drivers from Samsara"),
    # ── Recruiting ──
    "application_draft_reminders":    ("Recruiting", "Nudge abandoned apply drafts (per-link opt-in policy)"),
    # ── Storage & data lifecycle ──
    "storage_sync":                   ("Storage & data", "Upload queued media to the customer's cloud (Drive)"),
    "data_retention":                 ("Storage & data", "Prune every retention target to its window"),
    # ── Accounts & system ──
    "account_lifecycle_housekeeping": ("Accounts & system", "Hard-purge expired accounts + send deletion warnings"),
    "scheduler_jobs_snapshot":        ("Accounts & system", "Snapshot scheduled jobs for the operator console"),
    "market_rollups":                 ("Accounts & system", "Rebuild anonymized vendor market-price rollups (dark until MARKET_INTEL_ENABLED)"),
    "capacity_sample":                ("Accounts & system", "Sample host/DB/Redis/request-rate into the capacity history (60s)"),
    "capacity_flush_yesterday":       ("Accounts & system", "Close out yesterday's per-account request metering"),
    "capacity_alerts":                ("Accounts & system", "Telegram the operators when capacity crosses 85% (stateful, breach/recovery)"),
}


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
    # Market intel (Phase D): nightly rollup rebuild at 04:40 UTC —
    # after the 04:10 account purge, before morning traffic.  No-op
    # while MARKET_INTEL_ENABLED is unset (dark launch).
    from capabilities.platform.market_intel.jobs import job_market_rollups
    scheduler.add_job(
        job_market_rollups, "cron",
        hour=4, minute=40, args=[app], id="market_rollups",
        max_instances=1, coalesce=True,
    )

    # ── Capacity monitoring (operator console) ──────────────────────
    # 60s sampler: host/PG/Redis/queue/request-rate → system_metrics_minute;
    # tops of hours fold the previous hour + flush per-account metering.
    # The 00:10 job closes out yesterday's metering after its Redis hash
    # stops changing (after the 00:00 boundary, before the 04:10 purge).
    from capabilities.platform.capacity.sampler import (
        job_capacity_flush_yesterday,
        job_capacity_sample,
    )
    scheduler.add_job(
        job_capacity_sample, "interval",
        minutes=1, args=[app], id="capacity_sample",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_capacity_flush_yesterday, "cron",
        hour=0, minute=10, args=[app], id="capacity_flush_yesterday",
        max_instances=1, coalesce=True,
    )
    # Threshold alerts: stateful breach/recovery (one message per
    # transition, hysteresis 80/85) — safe at a 5-min cadence.
    from capabilities.platform.capacity.alerts import job_capacity_alerts
    scheduler.add_job(
        job_capacity_alerts, "interval",
        minutes=5, args=[app], id="capacity_alerts",
        max_instances=1, coalesce=True,
    )

    # ── Notification digests ────────────────────────────────────────
    # Recipients who chose a batched cadence get ONE grouped summary per
    # channel instead of a message per event.  Telegram stays immediate,
    # so today these drain nothing — the queue fills once Email ships.
    from capabilities.notifications.jobs import (
        job_flush_daily_digests,
        job_flush_hourly_digests,
    )
    scheduler.add_job(
        job_flush_hourly_digests, "cron",
        minute=5, args=[app], id="notification_digest_hourly",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_flush_daily_digests, "cron",
        hour=13, minute=0, args=[app], id="notification_digest_daily",
        max_instances=1, coalesce=True,
    )

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

    # ── monthly driver-pay job ───────────────────────────
    from features.driver_pay.jobs import run_monthly_driver_pay_job
    scheduler.add_job(
        run_monthly_driver_pay_job, "cron",
        day=1, hour=2, minute=0, args=[app], id="driver_pay_monthly",
        max_instances=1, coalesce=True,
    )

    # ── monthly billing snapshot ─────────────────────────
    # Records one billing_usage_snapshots row per active account so the
    # dashboard can render historical periods (and finance can audit
    # what we actually billed for).  Fires 30 min after driver pay so the
    # database isn't doing two big batches simultaneously.
    from capabilities.platform.billing.jobs import run_monthly_billing_snapshots
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
    from capabilities.platform.billing.jobs import run_comp_expiry_sweep
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
    from capabilities.integrations.samsara.sync import (
        job_ingest_vehicle_state,
        job_ingest_safety_events,
        job_ingest_driver_efficiency_daily,
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
    # ── telemetry roll-up cascades (Roll-up hub) ─────────────
    # The downsampling tiers — vehicle state → 5-min snapshot → hourly →
    # daily, and any future high-frequency stream — are registered as
    # cascades in capabilities/data_lifecycle/rollups (each feature owns its aggregation in
    # its own rollups.py).  We register one scheduler job per stage from that
    # registry, so adding a new cascade needs NO scheduler edit.  Job ids +
    # cadences are unchanged from when these were wired by hand:
    #   warehouse_state_snapshot   — every 5 min (interval, tz-agnostic)
    #   warehouse_telemetry_hourly — :05 every hour
    #   warehouse_metrics_daily    — 00:05 UTC.  The UTC pin is REQUIRED: the
    #     scheduler runs in the server's local zone, so a bare local 00:05
    #     fires before UTC midnight and the aggregator's "yesterday UTC" then
    #     resolves two days back, leaving the daily tier perpetually ~1 day
    #     stale.  (These tiers were silently dropped once in a refactor, which
    #     froze all back-dated odometer/usage history — the live-state job
    #     masked it — so the registry is the single source of what runs.)
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    from capabilities.data_lifecycle.rollups import discover as _discover_rollups
    from capabilities.data_lifecycle.rollups.engine import run_stage
    from capabilities.data_lifecycle.rollups.registry import all_stages

    _discover_rollups()
    for stage in all_stages():
        cadence = stage.cadence
        if "cron" in cadence:
            trigger = CronTrigger.from_crontab(cadence["cron"], timezone=cadence.get("tz"))
        else:
            trigger = IntervalTrigger(minutes=cadence["interval_min"])
        scheduler.add_job(
            run_stage, trigger, args=[stage], id=stage.job_id,
            max_instances=1, coalesce=True,
        )

    # ── data retention (cross-cutting hub) ───────────────────
    # One nightly pass that prunes every registered retention target to the
    # window its OWNING feature declares (capabilities/data_lifecycle/retention):
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
    from capabilities.data_lifecycle.retention.jobs import job_run_retention
    scheduler.add_job(
        job_run_retention, "cron",
        hour=2, minute=0, timezone="UTC", args=[app], id="data_retention",
        max_instances=1, coalesce=True,
    )
    # Abandoned apply-draft nudges — hourly so each reminder lands roughly
    # the same time of day the applicant was last active (per-link opt-in;
    # the job itself no-ops when no link has the policy on).
    from features.applications.drafts_reminder import job_send_draft_reminders
    scheduler.add_job(
        job_send_draft_reminders, "interval",
        hours=1, args=[app], id="application_draft_reminders",
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

    # ── Datatruck (TMS) auto-sync ─────────────────────────────────
    # Periodic counterpart to the manual "Sync now" button.  One job per
    # resource, each fanning out only to accounts whose toggle for that
    # resource is ON (the fan-out skips the rest, so a tick is a cheap
    # no-op otherwise).  Cadence is read from the catalog so it lives in
    # ONE place; default-disabled resources (trailers/orders/work_orders)
    # register a job but won't pull until an account enables the toggle —
    # the same always-registered / per-account-gated model the Samsara
    # ingest jobs above use.  Lazy import so non-TMS installs still boot.
    from adapters.telematics.catalog import PROVIDER_CATALOG as _dt_catalog
    from capabilities.integrations.datatruck.sync import (
        RESOURCES as _dt_resources,
        SYNC_JOBS as _dt_jobs,
    )
    _dt_defaults = _dt_catalog["datatruck"].feature_defaults
    for _res, _dt_job in _dt_jobs.items():
        _dt_cap = _dt_resources[_res].capability
        _dt_interval = int((_dt_defaults.get(_dt_cap) or {}).get("interval_min", 30))
        scheduler.add_job(
            _dt_job, "interval",
            minutes=_dt_interval, args=[app], id=f"datatruck_sync_{_res}",
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
                # Reclaim the account's SERVER-LOCAL files before
                # purge_account_data drops the rows that hold its storage
                # backend choice — otherwise the right backend can't be
                # resolved and local blobs would orphan forever.  Files in
                # the customer's own Google Drive are deliberately left
                # intact (their cloud, their data).  Best-effort: a
                # file-cleanup failure must not block the DB purge.
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

    # ── Operator-console scheduler snapshot ───────────────────────
    # The API process can't read this (bot-process) scheduler's in-memory
    # jobs, so persist a snapshot to the DB for the Scheduler page.  Runs
    # right after startup (next_run_time=now) and every 15 min after, so
    # the stored next-run times stay reasonably fresh as jobs fire.
    async def _snapshot_scheduler_jobs(_app=None):
        from infra.platform import get_platform_db
        try:
            rows = []
            for job in scheduler.get_jobs():
                category, description = _JOB_META.get(job.id, ("Other", ""))
                rows.append({
                    "job_id": job.id,
                    "trigger": str(job.trigger),
                    "next_run_at": (
                        job.next_run_time.isoformat() if job.next_run_time else None
                    ),
                    "category": category,
                    "description": description,
                })
            await get_platform_db().record_scheduler_jobs(rows)
        except Exception:
            logger.exception("scheduler: job snapshot failed")

    scheduler.add_job(
        _snapshot_scheduler_jobs, "interval",
        minutes=15, args=[app], id="scheduler_jobs_snapshot",
        next_run_time=datetime.now(timezone.utc),
        max_instances=1, coalesce=True,
    )
