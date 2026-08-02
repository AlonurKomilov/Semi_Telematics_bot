"""Activity Trail's data-lifecycle declaration — Retention hub entry.

The trail is ACCOUNTABILITY data: it answers "who deleted what" months
later and doubles as the recovery record for deletions (every delete
event carries the row's full body).  The keep window is therefore the
longest in the product — three years by default — and pruning it early
would re-open the exact blindness the 2026-07-30 incident exposed.
The two shipped rich trails (load_events, vehicle_inventory_events)
keep their own owners' declarations.
"""

from capabilities.data_lifecycle.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

register_target(RetentionTarget(
    "activity_trail.events", "Activity trail (who-did-what events)",
    "tenant",
    lambda db, acct, days: db.prune_activity_events(acct, days_keep=days),
))

register_need(RetentionNeed(
    "activity_trail", "activity_trail.events", 1095,
    "accountability + deletion-recovery record (owner-facing audit)",
))
