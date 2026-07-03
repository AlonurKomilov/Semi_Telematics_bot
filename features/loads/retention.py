"""Loads' data-lifecycle declaration — contributes to the Retention hub.

Loads are business records: the KPI trends and dispatcher analytics read up
to two years back, so the default keep window is long.  The prune drops rows
whose pickup_date fell out of the window (rows with no pickup date are kept).
"""

from capabilities.data_lifecycle.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

register_target(RetentionTarget(
    "loads.records", "Loads (load/shipment records)", "tenant",
    lambda db, acct, days: db.prune_loads(acct, days_keep=days),
))

register_need(RetentionNeed(
    "loads", "loads.records", 730, "load history for KPI trends + reports",
))
