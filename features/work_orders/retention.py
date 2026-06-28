"""Work Orders' data-retention need — contributes to the Retention hub.

Back-dated work orders resolve their odometer/engine-hours from the
end-of-day reading in the daily tier of ``vehicle_telemetry`` (730-day
retention), so a WO dated up to ~2 years ago still shows the mileage then.
"""

from capabilities.data_lifecycle.retention.registry import RetentionNeed, register_need

register_need(RetentionNeed(
    "work_orders", "vehicle.metrics_daily", 730,
    "back-dated work-order odometer (end-of-day tier)",
))
