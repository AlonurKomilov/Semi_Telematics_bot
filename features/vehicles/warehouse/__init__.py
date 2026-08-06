"""The vehicle feature's warehouse logic — aggregation, readers, facade.

Re-homed from features/vehicles/warehouse/ at the end of the
warehouse arc (Phase 5): features own their domain logic; the generic
engines stay in capabilities/data_lifecycle/.  Same function names,
same job ids — only the address changed.
"""

from features.vehicles.warehouse.service import (  # noqa: F401
    get_vehicle_health,
    get_fleet_weather,
    get_fleet_efficiency,
    get_vehicles_with_faults,
    get_low_fuel_vehicles,
    get_driver_efficiency,
)
