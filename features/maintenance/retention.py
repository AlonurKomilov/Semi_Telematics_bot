"""Maintenance's data-retention need — contributes to the Retention hub.

The service-due projection reads per-day miles from
``vehicle_metrics_daily`` to estimate each vehicle's velocity over a long
window, so the daily tier must persist.
"""

from capabilities.data_lifecycle.retention.registry import RetentionNeed, register_need

register_need(RetentionNeed(
    "maintenance", "vehicle.metrics_daily", 730, "service-due velocity projection",
))
