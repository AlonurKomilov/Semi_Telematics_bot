"""Vehicles' data-retention needs — contributes to the Retention hub.

The Vehicle feature reads the 5-minute snapshot tier for the live map +
recent timeline, and the hourly roll-up for the timeline trend lines.
"""

from capabilities.retention.registry import RetentionNeed, register_need

register_need(RetentionNeed(
    "vehicles", "vehicle_state_snapshot", 7, "live map + recent vehicle timeline",
))
register_need(RetentionNeed(
    "vehicles", "vehicle_telemetry_hourly", 90, "vehicle timeline trend lines",
))
