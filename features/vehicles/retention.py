"""Vehicle telemetry retention — Vehicle owns these targets + its needs.

The Vehicle feature owns its telemetry tiers, so it declares both *how* to
prune them (``register_target`` — thin adapters over the existing storage
prune methods, so behavior is preserved) and *how long it* needs them
(``register_need``).  Other features that read the same tiers (Maintenance,
Work Orders) add their own needs in their modules; ``resolve()`` keeps each
tier for the hungriest consumer.

Target names are the feature-component identity (``vehicle.<component>``),
decoupled from the physical table the executor prunes:

  vehicle.timeline_5min   -> vehicle_state_snapshot   (5-min state history)
  vehicle.timeline_hourly -> vehicle_telemetry_hourly (hourly roll-up)
  vehicle.metrics_daily   -> vehicle_metrics_daily    (daily roll-up + EOD odometer)
  vehicle.faults          -> vehicle_fault_detail     (CLEARED DTC history only)
"""

from capabilities.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

# ── targets the Vehicle feature owns (HOW to prune) ──
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

# ── Vehicle's own needs (HOW LONG) ──
register_need(RetentionNeed(
    "vehicles", "vehicle.timeline_5min", 7, "live map + recent vehicle timeline",
))
register_need(RetentionNeed(
    "vehicles", "vehicle.timeline_hourly", 90, "vehicle timeline trend lines",
))
register_need(RetentionNeed(
    "vehicles", "vehicle.faults", 365, "resolved-fault diagnostic history",
))
