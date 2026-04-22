"""telemetry capability — health, weather, efficiency data facade."""

from capabilities.telemetry.service import (  # noqa: F401
    get_vehicle_health,
    get_fleet_weather,
    get_fleet_efficiency,
    get_vehicles_with_faults,
    get_driver_efficiency,
)
