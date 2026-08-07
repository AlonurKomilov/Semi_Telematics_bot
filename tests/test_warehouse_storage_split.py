"""Parity guard — the six-way split + grain method renames lost nothing.

The 3,283-line warehouse.py monolith became a package of per-stream
mixins plus a root-level OpsRunsMixin.  Method names and bodies are
byte-identical by construction; this test freezes the inventory so a
future edit can't silently drop one during further re-homing.
"""

from adapters.storage import Database
from adapters.storage.ops_runs import OpsRunsMixin
from adapters.storage.warehouse import WarehouseMixin

EXPECTED = {
    # vehicles.py
    "upsert_vehicle_state", "get_vehicle_state",
    "upsert_vehicle_state_hour", "get_vehicle_state_hour",
    "get_vehicle_avg_daily_miles", "upsert_vehicle_state_minutes",
    "get_vehicle_state_at_or_before", "get_undriven_vehicles",
    "compute_vehicle_velocity_window", "compute_vehicle_velocity_daily",
    "prune_vehicle_state_minutes", "get_reading_as_of", "_days_between",
    "get_period_mileage", "get_vehicle_period_mileage",
    "get_snapshot_coverage", "get_feed_freshness",
    "get_telemetry_tier_freshness", "vehicle_state_minute_has_day",
    "vehicle_state_minute_day_summary", "query_vehicle_state_history",
    "upsert_vehicle_state_day", "prune_vehicle_state_day",
    "upsert_vehicle_state_week", "prune_vehicle_state_week",
    "prune_vehicle_fault_log", "get_vehicle_state_day",
    "get_vehicle_usage_summary", "get_account_utilization_summary",
    "prune_vehicle_state_hour", "upsert_vehicle_health_live",
    "get_vehicle_health_live", "upsert_vehicle_fault_live",
    "upsert_vehicle_fault_log", "get_vehicles_with_faults_warehouse",
    "get_vehicle_fault_live_by_name", "get_active_fault_dtc_ids",
    # safety.py
    "insert_safety_events", "count_safety_events_in_window",
    "get_safety_events_warehouse", "get_safety_event_counts_grouped",
    "prune_safety_event_log",
    # drivers.py
    "count_driver_efficiency_in_window", "upsert_driver_efficiency_day",
    "get_driver_efficiency_window", "prune_driver_efficiency_day",
    # geofences.py
    "upsert_geofence_definitions", "get_geofence_definitions",
    # aggregates.py
    "upsert_weather_live", "get_weather_live",
    "upsert_efficiency_live", "get_efficiency_live",
    # ledgers.py
    "record_ingest_orphans",
}
EXPECTED_OPS = {
    "_ensure_retention_runs_table", "record_retention_runs",
    "get_latest_retention_runs", "_ensure_scheduler_jobs_table",
    "record_scheduler_jobs", "get_scheduler_jobs",
}


def _methods(cls):
    return {m for m in dir(cls)
            if not m.startswith("__") and callable(getattr(cls, m))}


def test_split_lost_no_warehouse_method():
    missing = EXPECTED - _methods(WarehouseMixin)
    assert not missing, f"split dropped: {sorted(missing)}"


def test_ops_ledgers_moved_whole():
    missing = EXPECTED_OPS - _methods(OpsRunsMixin)
    assert not missing, f"ops split dropped: {sorted(missing)}"


def test_database_composes_everything():
    missing = (EXPECTED | EXPECTED_OPS) - _methods(Database)
    assert not missing, f"Database lost: {sorted(missing)}"
