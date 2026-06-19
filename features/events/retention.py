"""Safety Events retention — the Safety Events feature owns its event log.

``safety_events`` -> physical table ``safety_event_log`` (harsh-driving /
safety events).  This is its OWN feature, not a Driver component, so the
target is the plain ``safety_events`` (no ``driver.`` prefix).

Compliance-sensitive (FMCSA / litigation / insurance), so the window is
deliberately long — 3 years — and is a product/legal decision, not a
technical default.
"""

from capabilities.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

register_target(RetentionTarget(
    "safety_events", "Safety / harsh-event log", "tenant",
    lambda db, acct, days: db.prune_safety_event_log(acct, days_keep=days),
))
register_need(RetentionNeed(
    "events", "safety_events", 1095, "FMCSA / litigation / insurance lookback (3 years)",
))
