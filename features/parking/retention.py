"""Parking retention — the Parking feature owns its stop history.

``parking.events`` -> physical table ``parking_events``.

WINDOW: 1095 days (3 years), matching ``safety_events`` rather than the
shorter operational windows.  An unsafe-parking record is the same class
of evidence as a safety event — where a truck was left, for how long, and
the AI's reasoning about whether that was safe — which is exactly what an
FMCSA review, an insurance claim, or a cargo-theft dispute reaches for.
``features/events/retention.py`` already justifies 1095 for that reason;
copying the number keeps one answer for one question instead of two.

Measured before choosing (2026-07-31, this account): 4,536 rows, oldest
2026-03-30.  A 90-day window — the obvious "operational" default — would
have deleted 1,318 rows on the next nightly run.  1095 deletes nothing
for years, which is the correct outcome for evidence.

The table had NO retention at all before this: it appeared in no target,
so it grew unbounded while email, ai, notifications, scorecards, loads and
capacity were all governed.
"""

from capabilities.data_lifecycle.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

register_target(RetentionTarget(
    "parking.events", "Parking stop history", "tenant",
    lambda db, acct, days: db.prune_parking_events(acct, keep_days=days),
))
register_need(RetentionNeed(
    "parking", "parking.events", 1095,
    "unsafe-parking evidence for FMCSA / insurance / theft disputes",
))
