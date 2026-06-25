"""Driver retention — the Driver feature owns its per-driver daily metrics.

``driver.efficiency_daily`` -> physical table ``driver_efficiency_daily``
(one row per driver per day).  The Driver-side analogue of
``vehicle.metrics_daily``; kept for the same 730-day year-over-year window
so driver scorecard history lines up with the vehicle daily tier.
"""

from capabilities.data_lifecycle.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

register_target(RetentionTarget(
    "driver.efficiency_daily", "Driver daily efficiency", "tenant",
    lambda db, acct, days: db.prune_driver_efficiency_daily(acct, days_keep=days),
))
register_need(RetentionNeed(
    "drivers", "driver.efficiency_daily", 730, "per-driver year-over-year scorecard history",
))
