"""Inventory's data-retention contribution — the events trail.

Items themselves are never pruned (they ARE the live inventory); the
event trail is accountability history and keeps a long window — damage
and theft disputes surface months later, and the trail is the proof.
Tenant scope: pruned per account by the nightly retention run.
"""

from capabilities.data_lifecycle.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

register_target(RetentionTarget(
    "vehicles.inventory_events", "Onboard-inventory event trail", "tenant",
    lambda db, acct, days: db.prune_inventory_events(acct, days_keep=days),
))

register_need(RetentionNeed(
    "vehicles", "vehicles.inventory_events", 730,
    "accountability window for damage/theft disputes (2 years, matches "
    "the metrics-daily tier)",
))
