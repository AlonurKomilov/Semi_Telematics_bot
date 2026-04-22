"""Geofencing capability — geometry and zone checking."""
from capabilities.geofencing.geometry import (  # noqa: F401
    point_in_circle,
    point_in_polygon,
    is_inside_geofence,
)
from capabilities.geofencing.service import (  # noqa: F401
    get_geofences,
    get_fleet_for_geofence_check,
)
