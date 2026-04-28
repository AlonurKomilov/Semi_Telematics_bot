"""Geometry utilities for geofence calculations."""

from math import radians, sin, cos, sqrt, atan2


# ── Haversine distance ────────────────────────────────────────────────────────

def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Straight-line distance in metres between two lat/lng points."""
    R = 6_371_000.0
    dlat = radians(lat1 - lat2)
    dlng = radians(lng1 - lng2)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _point_to_segment_m(
    lat: float, lng: float,
    lat1: float, lng1: float,
    lat2: float, lng2: float,
) -> float:
    """Approximate distance in metres from (lat,lng) to a great-circle segment.

    Uses a flat-earth projection centred on the midpoint — accurate enough
    for geofence-sized regions (< 100 km).
    """
    avg_lat = radians((lat + lat1 + lat2) / 3.0)
    m_per_deg_lat = 111_320.0
    m_per_deg_lng = 111_320.0 * cos(avg_lat)

    # Convert to metres relative to (lat1, lng1)
    px = (lng - lng1) * m_per_deg_lng
    py = (lat - lat1) * m_per_deg_lat
    sx = (lng2 - lng1) * m_per_deg_lng
    sy = (lat2 - lat1) * m_per_deg_lat

    seg_sq = sx * sx + sy * sy
    if seg_sq == 0:
        return sqrt(px * px + py * py)

    t = max(0.0, min(1.0, (px * sx + py * sy) / seg_sq))
    dx = px - t * sx
    dy = py - t * sy
    return sqrt(dx * dx + dy * dy)


# ── Point-in-geometry ─────────────────────────────────────────────────────────

def point_in_circle(lat: float, lng: float,
                    center_lat: float, center_lng: float,
                    radius_m: float) -> bool:
    """Check if a point is inside a circular geofence."""
    return _haversine_m(lat, lng, center_lat, center_lng) <= radius_m


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


def geofence_shape_type(gf: dict) -> str:
    """Return 'circle' or 'polygon' for a Samsara geofence dict."""
    if gf.get("circularGeofence") or gf.get("geofence", {}).get("circle"):
        return "circle"
    return "polygon"


def is_inside_geofence(lat: float, lng: float, geofence: dict) -> bool:
    """Check if a vehicle is inside a geofence (circle or polygon)."""
    circle = geofence.get("circularGeofence")
    if circle:
        return point_in_circle(
            lat, lng,
            circle.get("latitude", 0),
            circle.get("longitude", 0),
            circle.get("radiusMeters", 0),
        )
    polygon = geofence.get("polygonGeofence", {})
    vertices = polygon.get("vertices", [])
    if vertices:
        return point_in_polygon(lat, lng, vertices)
    return False


def distance_to_geofence(lat: float, lng: float, geofence: dict) -> float:
    """Distance in metres from (lat,lng) to the nearest boundary of a geofence.

    Returns 0.0 if the point is already inside.

    - Circle: distance to centre minus radius, clamped to 0.
    - Polygon: minimum perpendicular distance to any edge, 0 if inside.
    """
    circle = geofence.get("circularGeofence")
    if circle:
        c_lat = circle.get("latitude", 0)
        c_lng = circle.get("longitude", 0)
        radius = circle.get("radiusMeters", 0)
        return max(0.0, _haversine_m(lat, lng, c_lat, c_lng) - radius)

    polygon = geofence.get("polygonGeofence", {})
    vertices = polygon.get("vertices", [])
    if not vertices:
        return float("inf")
    if point_in_polygon(lat, lng, vertices):
        return 0.0

    n = len(vertices)
    min_dist = float("inf")
    for i in range(n):
        j = (i + 1) % n
        d = _point_to_segment_m(
            lat, lng,
            vertices[i].get("latitude", 0), vertices[i].get("longitude", 0),
            vertices[j].get("latitude", 0), vertices[j].get("longitude", 0),
        )
        if d < min_dist:
            min_dist = d
    return min_dist
