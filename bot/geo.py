"""Geometry utilities for geofence calculations."""

from math import radians, sin, cos, sqrt, atan2


def point_in_circle(lat: float, lng: float,
                    center_lat: float, center_lng: float,
                    radius_m: float) -> bool:
    """Check if a point is inside a circular geofence."""
    R = 6371000  # Earth radius in meters
    dlat = radians(lat - center_lat)
    dlng = radians(lng - center_lng)
    a = sin(dlat / 2) ** 2 + cos(radians(center_lat)) * cos(radians(lat)) * sin(dlng / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    distance = R * c
    return distance <= radius_m


def point_in_polygon(lat: float, lng: float, vertices: list[dict]) -> bool:
    """Ray-casting point-in-polygon test."""
    n = len(vertices)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi = vertices[i].get("latitude", 0)
        xi = vertices[i].get("longitude", 0)
        yj = vertices[j].get("latitude", 0)
        xj = vertices[j].get("longitude", 0)
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def is_inside_geofence(lat: float, lng: float, geofence: dict) -> bool:
    """Check if a vehicle is inside a geofence (circle or polygon)."""
    # Circle geofence
    circle = geofence.get("circularGeofence")
    if circle:
        c_lat = circle.get("latitude", 0)
        c_lng = circle.get("longitude", 0)
        radius = circle.get("radiusMeters", 0)
        return point_in_circle(lat, lng, c_lat, c_lng, radius)

    # Polygon geofence
    polygon = geofence.get("polygonGeofence", {})
    vertices = polygon.get("vertices", [])
    if vertices:
        return point_in_polygon(lat, lng, vertices)

    return False
