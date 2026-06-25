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
from capabilities.telemetry.aggregator import (
    aggregate_metrics_daily,
    aggregate_telemetry_hourly,
    snapshot_vehicle_state,
)

# Cadences match exactly what the scheduler ran before this was hub-driven:
#   snapshot — every 5 min (interval, tz-agnostic)
#   hourly   — :05 every hour
#   daily    — 00:05 UTC.  The ``tz`` is REQUIRED: a bare local 00:05 fires
#     before UTC midnight and the aggregator's "yesterday UTC" then resolves
#     two days back, leaving the daily tier perpetually ~1 day stale.
register_cascade(
    RollupCascade(
        "vehicle",
        (
            RollupStage(
                "warehouse_state_snapshot",
                {"interval_min": 5},
                snapshot_vehicle_state,
                "Capture the 5-min vehicle-state history",
            ),
            RollupStage(
                "warehouse_telemetry_hourly",
                {"cron": "5 * * * *"},
                aggregate_telemetry_hourly,
                "Roll 5-min snapshots into the hourly tier",
            ),
            RollupStage(
                "warehouse_metrics_daily",
                {"cron": "5 0 * * *", "tz": "UTC"},
                aggregate_metrics_daily,
                "Roll the hourly tier into the daily tier",
            ),
        ),
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
#   vehicle.timeline_5min   -> vehicle_state_snapshot   (5-min state history)
#   vehicle.timeline_hourly -> vehicle_telemetry_hourly (hourly roll-up)
#   vehicle.metrics_daily   -> vehicle_metrics_daily    (daily roll-up + EOD odometer)
#   vehicle.faults          -> vehicle_fault_detail     (CLEARED DTC history only)
register_target(RetentionTarget(
    "vehicle.timeline_5min", "Vehicle timeline (5-min state history)", "tenant",
    lambda db, acct, days: db.prune_vehicle_state_snapshots(acct, days_keep=days),
))
register_target(RetentionTarget(
    "vehicle.timeline_hourly", "Vehicle timeline (hourly roll-up)", "tenant",
    lambda db, acct, days: db.prune_vehicle_telemetry_hourly(acct, days_keep=days),
))
register_target(RetentionTarget(
    "vehicle.metrics_daily", "Vehicle daily metrics (+ end-of-day odometer)", "tenant",
    lambda db, acct, days: db.prune_vehicle_metrics_daily(acct, days_keep=days),
))
# Cleared DTCs only — the executor never touches active faults (cleared_at
# IS NULL), so live faults are kept regardless of the window.
register_target(RetentionTarget(
    "vehicle.faults", "Vehicle fault history (cleared DTCs)", "tenant",
    lambda db, acct, days: db.prune_vehicle_fault_detail(acct, days_keep=days),
))

# The Vehicle feature's own needs.  (vehicle.metrics_daily's window comes from
# its consumers — Maintenance + Work Orders — via their own retention modules.)
register_need(RetentionNeed(
    "vehicles", "vehicle.timeline_5min", 7, "live map + recent vehicle timeline",
))
register_need(RetentionNeed(
    "vehicles", "vehicle.timeline_hourly", 90, "vehicle timeline trend lines",
))
register_need(RetentionNeed(
    "vehicles", "vehicle.faults", 365, "resolved-fault diagnostic history",
))
