"""Vehicle data-lifecycle — declared in ONE place.

The Vehicle feature owns its telemetry tiers, so this module declares their
WHOLE lifecycle side by side:

  * BUILD  (roll-up cascade) — how each tier is downsampled from the one below,
    on its own cadence.  Registered with the Roll-up hub.
  * KEEP   (retention)       — how long each tier is kept before pruning, and
    the consumers that need it.  Registered with the Retention hub.

Both hubs live under ``capabilities/data_lifecycle``; the aggregation/prune
implementations stay in the warehouse storage layer.  Colocating the two
declarations means a tier's full lifecycle (born → rolled up → kept → deleted)
reads top-to-bottom in one file instead of being split across two.
"""

from __future__ import annotations

# ── BUILD: the roll-up cascade ───────────────────────────────────
from capabilities.data_lifecycle.rollups.registry import (
    RollupCascade,
    RollupStage,
    register_cascade,
)
from features.vehicles.warehouse.aggregator import (
    aggregate_metrics_daily,
    aggregate_metrics_weekly,
    aggregate_telemetry_hourly,
    snapshot_vehicle_state,
)


async def _reroll_vehicle_tiers(account_id: int, *, days: int) -> dict:
    """The cascade's whole-stream rebuild hook, LATE-BOUND on purpose:
    resolving ``backfill_aggregations`` at call time (not registration
    time) keeps monkeypatches and hot-fixes effective — a bound
    reference frozen into the registry ignores them forever."""
    from features.vehicles.warehouse.aggregator import backfill_aggregations
    return await backfill_aggregations(account_id, days=days)

# Cadences match exactly what the scheduler ran before this was hub-driven:
#   snapshot — on the minute (wall-aligned cron; see timegrid.py)
#   hourly   — :05 every hour
#   daily    — 00:05 UTC.  The ``tz`` is REQUIRED: a bare local 00:05 fires
#     before UTC midnight and the aggregator's "yesterday UTC" then resolves
#     two days back, leaving the daily tier perpetually ~1 day stale.
register_cascade(
    RollupCascade(
        "vehicle",
        (
            RollupStage(
                # Every minute, matching the live ingest: the provider is
                # already asked at 60s cadence, so sampling any slower
                # threw away readings we had already paid for — 4 of
                # every 5, for the life of the table.  Duty math is
                # gap-based (cadence-independent), so the 5-minute
                # history and 1-minute samples coexist correctly.
                # Wall-aligned cron, not an interval: an interval fires
                # N seconds from process BOOT, so every restart minted a
                # new second-offset in the minute grid.
                "warehouse_state_snapshot",
                {"cron": "* * * * *", "tz": "UTC"},
                snapshot_vehicle_state,
                "Capture the minute-grain vehicle-state history",
                stream="vehicle.timeline", grain="minute",
            ),
            RollupStage(
                # UTC-pinned like its siblings.  Unpinned, this ran in the
                # server's local zone, where the autumn DST fall-back
                # repeats 02:00 and the spring jump skips it — one hourly
                # bucket lost or doubled every year, permanently, since the
                # hourly tier has no self-heal that reaches back for a hole
                # once its minute snapshots age out.
                "warehouse_telemetry_hourly",
                {"cron": "5 * * * *", "tz": "UTC"},
                aggregate_telemetry_hourly,
                "Roll minute samples into the hour tier",
                stream="vehicle.timeline", grain="hour",
            ),
            RollupStage(
                "warehouse_metrics_daily",
                {"cron": "5 0 * * *", "tz": "UTC"},
                aggregate_metrics_daily,
                "Roll the hour tier into the day tier",
                stream="vehicle.timeline", grain="day",
            ),
            RollupStage(
                # Mondays 00:10 UTC — after the daily roll-up (00:05) has
                # closed out Sunday, so the just-completed week is whole.
                # Spelled "mon", not "1": APScheduler's crontab parser
                # numbers days from Monday=0, so the numeric form fired
                # every TUESDAY while every comment and the operator
                # console said Monday.
                "warehouse_metrics_weekly",
                {"cron": "10 0 * * mon", "tz": "UTC"},
                aggregate_metrics_weekly,
                "Roll the day tier into the week tier",
                stream="vehicle.timeline", grain="week",
            ),
        ),
        # The whole-cascade rebuild the history backfill calls through
        # the registry (capabilities may not import features).
        reroll=_reroll_vehicle_tiers,
    )
)


# ── KEEP: retention targets (HOW to prune) + the feature's needs (HOW LONG) ──
from capabilities.data_lifecycle.retention.registry import (  # noqa: E402
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

# Target names are the feature-component identity (``vehicle.<component>``),
# decoupled from the physical table the executor prunes:
#   vehicle.timeline_minute -> vehicle_state_minute        (minute-grain state;
#                              renamed from timeline_5min — migration 186 folds
#                              the ledger history onto the new key)
#   vehicle.timeline_hourly -> vehicle_state_hour          (hour roll-up)
#   vehicle.metrics_daily   -> vehicle_state_day           (day roll-up + EOD odometer)
#   vehicle.timeline_weekly -> vehicle_state_week          (week roll-up, long horizon)
#   vehicle.faults          -> vehicle_fault_log        (CLEARED DTC history only)
register_target(RetentionTarget(
    "vehicle.timeline_minute", "Vehicle timeline (minute state history)", "tenant",
    lambda db, acct, days: db.prune_vehicle_state_minutes(acct, days_keep=days),
))
register_target(RetentionTarget(
    "vehicle.timeline_hourly", "Vehicle timeline (hourly roll-up)", "tenant",
    lambda db, acct, days: db.prune_vehicle_state_hour(acct, days_keep=days),
))
register_target(RetentionTarget(
    "vehicle.metrics_daily", "Vehicle daily metrics (+ end-of-day odometer)", "tenant",
    lambda db, acct, days: db.prune_vehicle_state_day(acct, days_keep=days),
))
register_target(RetentionTarget(
    "vehicle.timeline_weekly", "Vehicle timeline (weekly roll-up)", "tenant",
    lambda db, acct, days: db.prune_vehicle_state_week(acct, days_keep=days),
))
# Cleared DTCs only — the executor never touches active faults (cleared_at
# IS NULL), so live faults are kept regardless of the window.
register_target(RetentionTarget(
    "vehicle.faults", "Vehicle fault history (cleared DTCs)", "tenant",
    lambda db, acct, days: db.prune_vehicle_fault_log(acct, days_keep=days),
))

# The Vehicle feature's own needs.  (vehicle.metrics_daily's window comes from
# its consumers — Maintenance + Work Orders — via their own retention modules.)
register_need(RetentionNeed(
    "vehicles", "vehicle.timeline_minute", 7, "live map + recent vehicle timeline",
))
register_need(RetentionNeed(
    "vehicles", "vehicle.timeline_hourly", 90, "vehicle timeline trend lines",
))
register_need(RetentionNeed(
    "vehicles", "vehicle.timeline_weekly", 1825, "multi-year year-over-year trend lines",
))
register_need(RetentionNeed(
    "vehicles", "vehicle.faults", 365, "resolved-fault diagnostic history",
))


async def job_departure_sweep(_app=None) -> None:
    """Nightly: retire vehicles the provider stopped reporting.

    Runs per active account.  The sweep itself refuses to act when an
    account's ENTIRE fleet is stale (that is an ingest outage, not a
    mass departure) — see adapters/storage/vehicle_departure.py for the
    rules.  History is never touched; only the pretense that a silent
    badge is a current vehicle ends.
    """
    import logging

    from infra.platform import get_platform_db, get_tenant_db

    log = logging.getLogger(__name__)
    accounts = await get_platform_db().list_accounts(active_only=True)
    retired = 0
    for acc in accounts:
        try:
            tenant = await get_tenant_db(acc.id)
            if tenant is None:
                continue
            result = await tenant.sweep_departed_vehicles(acc.id)
            retired += len(result["departed"])
        except Exception:
            log.exception("departure sweep failed acct=%d — continuing", acc.id)
    if retired:
        log.info("departure sweep: %d badge(s) retired across the platform",
                 retired)


# ── ACQUIRE: ingest datasets ─────────────────────────────────────
# The vehicles feature declares what it pulls from the provider; the
# ingest hub runs it, records every run in the day-grain ledger, and
# the watchdog judges freshness from the declaration — no dataset of
# this feature can die silently again.  vehicles.state is the first
# declared dataset; its siblings (health, faults, weather) follow as
# the provider sync splits into fetchers.

from capabilities.data_lifecycle.ingest import IngestDataset, register_dataset


def _run_vehicle_state(account_id: int):
    from capabilities.integrations.samsara.sync import ingest_vehicle_state
    return ingest_vehicle_state(account_id)


def _run_vehicle_health(account_id: int):
    from capabilities.integrations.samsara.sync import ingest_vehicle_health
    return ingest_vehicle_health(account_id)


def _run_vehicle_faults(account_id: int):
    from capabilities.integrations.samsara.sync import ingest_vehicle_faults
    return ingest_vehicle_faults(account_id)


def _run_fleet_weather(account_id: int):
    from capabilities.integrations.samsara.sync import ingest_fleet_weather
    return ingest_fleet_weather(account_id)


def _run_fleet_efficiency(account_id: int):
    from capabilities.integrations.samsara.sync import ingest_fleet_efficiency
    return ingest_fleet_efficiency(account_id, days=7)


register_dataset(IngestDataset(
    key="vehicles.state",
    owner="vehicles",
    job_id="warehouse_vehicle_state",
    capability="vehicle_state",
    cadence={"interval_min": 1},
    run=_run_vehicle_state,
    tables=("vehicle_state_live", "vehicle_state_minute"),
    freshness_sla_min=15,
    label="Pull live vehicle state from the provider",
))

register_dataset(IngestDataset(
    key="vehicles.health",
    owner="vehicles",
    job_id="warehouse_vehicle_health",
    capability="vehicle_health",
    cadence={"interval_min": 5},
    run=_run_vehicle_health,
    tables=("vehicle_health_live",),
    freshness_sla_min=60,
    label="Ingest current vehicle health",
))

register_dataset(IngestDataset(
    key="vehicles.faults",
    owner="vehicles",
    job_id="warehouse_vehicle_faults",
    capability="vehicle_faults",
    cadence={"interval_min": 2},
    run=_run_vehicle_faults,
    # Both tables this run writes.  fault_snapshot stays FIRST — the
    # watchdog's freshness probe reads tables[0].
    tables=("vehicle_fault_live", "vehicle_fault_log"),
    freshness_sla_min=60,
    # A fleet with nothing broken reports nothing — an honest zero, not
    # an outage.  This is the dataset the watchdog must never cry over.
    expect_rows=False,
    label="Ingest current vehicle faults (DTCs)",
))

register_dataset(IngestDataset(
    key="vehicles.weather",
    owner="vehicles",
    job_id="warehouse_fleet_weather",
    capability="fleet_weather",
    cadence={"interval_min": 10},
    run=_run_fleet_weather,
    tables=("weather_live",),
    freshness_sla_min=120,
    label="Ingest cabin-weather snapshots",
))

register_dataset(IngestDataset(
    key="vehicles.efficiency",
    owner="vehicles",
    job_id="warehouse_fleet_efficiency",
    capability="fleet_efficiency",
    cadence={"interval_min": 30},
    run=_run_fleet_efficiency,
    tables=("efficiency_live",),
    # A windowed aggregate carries no per-record world-time, so its
    # rows are ageless by design; the SLA exists for the declaration's
    # completeness and the watchdog skips the age question for it.
    freshness_sla_min=180,
    label="Ingest fleet-efficiency aggregates",
))
