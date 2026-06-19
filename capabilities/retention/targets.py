"""Platform declaration of HOW to prune each retention target.

Thin adapters over the EXISTING, tested storage prune methods — so the
hub is behavior-preserving (it reuses the same DELETE logic, not a
reimplementation).  The retention WINDOWS live with the features that
need the data (``features/<x>/retention.py``); a couple of cross-cutting
subsystems that live under ``capabilities/`` (scorecards, email) declare
their needs here at the current windows until they grow their own
retention modules.
"""

from __future__ import annotations

from .registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

# ── tenant-scoped targets (per-account warehouse / scorecard tables) ──
register_target(RetentionTarget(
    "vehicle_state_snapshot", "Vehicle state history (5-min)", "tenant",
    lambda db, acct, days: db.prune_vehicle_state_snapshots(acct, days_keep=days),
))
register_target(RetentionTarget(
    "vehicle_telemetry_hourly", "Hourly roll-up", "tenant",
    lambda db, acct, days: db.prune_vehicle_telemetry_hourly(acct, days_keep=days),
))
register_target(RetentionTarget(
    "vehicle_metrics_daily", "Daily roll-up", "tenant",
    lambda db, acct, days: db.prune_vehicle_metrics_daily(acct, days_keep=days),
))
register_target(RetentionTarget(
    "score_events", "Driver score events", "tenant",
    lambda db, acct, days: db.prune_score_events(acct, keep_days=days),
))

# ── platform-scoped targets (global tables) ──
register_target(RetentionTarget(
    "email_webhook_events", "Email delivery webhook log", "platform",
    lambda db, _acct, days: db.prune_email_webhook_events(days=days),
))

# Cross-cutting needs whose owner is a capability (not a features/<x>/
# module).  Declared at the current windows so the cutover is
# behavior-preserving; relocate into capabilities/{scorecards,email}/
# retention.py when those grow one.
register_need(RetentionNeed("scorecards", "score_events", 90, "driver score history"))
register_need(RetentionNeed("email", "email_webhook_events", 14, "bounce/complaint debugging"))
