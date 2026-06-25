"""Scorecards retention — the Scorecards hub owns its score-event history.

``scorecards.score_history`` -> physical table ``score_events`` (per-account
driver score events).  Declared at the legacy 90-day window so the cutover is
behavior-preserving.
"""

from capabilities.data_lifecycle.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

register_target(RetentionTarget(
    "scorecards.score_history", "Driver score history", "tenant",
    lambda db, acct, days: db.prune_score_events(acct, keep_days=days),
))
register_need(RetentionNeed(
    "scorecards", "scorecards.score_history", 90, "driver score history",
))
