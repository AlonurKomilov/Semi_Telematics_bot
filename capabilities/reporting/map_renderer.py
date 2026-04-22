"""Map rendering — Single Source of Truth for static PNG map generation.

Uses the staticmap library to produce PNG images for fleet position
and route replay.  No Telegram or HTTP dependencies — pure image I/O.
"""

from __future__ import annotations

import io

from constants import OSM_TILE_URL
from capabilities.routes.service import total_route_miles
from capabilities.location.service import classify_vehicle_status, STATUS_COLORS


def render_fleet_map(vehicles: list[dict]) -> io.BytesIO | None:
    """Render a static fleet-position map with coloured vehicle markers.

    Colour scheme:
      green  — moving  (speed > 0)
      yellow — idle
      red    — stopped / no GPS data

    Returns a PNG BytesIO (name set to ``fleet_map.png``), or None when
    no vehicles have valid coordinates.
    """
    from staticmap import StaticMap, CircleMarker

    m = StaticMap(800, 600, url_template=OSM_TILE_URL)
    for v in vehicles:
        loc = v.get("location", {})
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        if lat is None or lng is None:
            continue

        status = classify_vehicle_status(v)
        color = STATUS_COLORS.get(status, "#ef4444")

        m.add_marker(CircleMarker((lng, lat), color, 10))
        has_markers = True

    if not has_markers:
        return None

    image = m.render()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "fleet_map.png"
    return buf


def render_route_map(gps_points: list[dict]) -> tuple[io.BytesIO | None, float]:
    """Render a route polyline on a static map.

    Accepts GPS points with keys ``lat``/``latitude`` and
    ``lng``/``longitude``.

    Returns ``(png_buffer, total_miles)``.  ``png_buffer`` is None when
    fewer than 2 valid coordinates are present.
    """
    from staticmap import StaticMap, CircleMarker, Line

    if len(gps_points) < 2:
        return None, 0

    # Downsample long traces to keep rendering fast
    if len(gps_points) > 500:
        step = len(gps_points) // 500
        sampled = gps_points[::step]
        if gps_points[-1] not in sampled:
            sampled.append(gps_points[-1])
        gps_points = sampled

    coords = []
    for pt in gps_points:
        lat = pt.get("latitude") or pt.get("lat")
        lng = pt.get("longitude") or pt.get("lng")
        if lat is not None and lng is not None:
            coords.append((float(lng), float(lat)))

    if len(coords) < 2:
        return None, 0

    lat_lon_coords = [(lat, lng) for lng, lat in coords]
    total_m = total_route_miles(lat_lon_coords)

    m = StaticMap(800, 600, url_template=OSM_TILE_URL)
    m.add_line(Line(coords, "#3b82f6", 3))
    m.add_marker(CircleMarker(coords[0], "#22c55e", 12))
    m.add_marker(CircleMarker(coords[-1], "#ef4444", 12))

    image = m.render()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "route_replay.png"
    return buf, round(total_m, 1)
