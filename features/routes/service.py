"""Routes service — GPS history, distance calculation, and route data."""

from __future__ import annotations

from math import radians, sin, cos, sqrt, atan2

import logging

from infra.services import get_client, get_tenant_db
from features.vehicles.service import prepare_companies

logger = logging.getLogger(__name__)

# Earth radius in miles
_EARTH_RADIUS_MI = 3958.8


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance between two points in miles."""
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return _EARTH_RADIUS_MI * c


def total_route_miles(coords: list[tuple[float, float]]) -> float:
    """Sum haversine distances along a list of (lat, lon) waypoints.

    Used by both bot/routes.py (PNG rendering) and api/routes/routes.py
    to avoid duplicating the same distance formula.
    """
    total = 0.0
    for i in range(1, len(coords)):
        total += haversine_miles(
            coords[i - 1][0], coords[i - 1][1],
            coords[i][0], coords[i][1],
        )
    return total


async def get_vehicle_gps_history(
    account_id: int | str,
    vehicle_name: str,
    start,
    end,
) -> list[dict]:
    """Fetch GPS breadcrumb trail for a named vehicle across all companies.

    Returns list of point dicts with latitude, longitude, speed, time.

    Fast path: look up the vehicle's company in the warehouse
    ``vehicle_state`` table and make a single targeted history call.
    Slow path (fallback): walk every company client when the warehouse
    miss-fires (e.g. a freshly-onboarded vehicle that hasn't been
    ingested yet).
    """
    client = await get_client(account_id)
    needle = vehicle_name.lower()

    # ── Fast path: warehouse lookup → single targeted Samsara call.
    try:
        tenant = await get_tenant_db(int(account_id))
    except (TypeError, ValueError, Exception):
        tenant = None
    if tenant is not None:
        try:
            states = await tenant.get_vehicle_state(int(account_id))
            target = next(
                (
                    s for s in states
                    if needle in str(s.get("vehicle_name") or "").lower()
                ),
                None,
            )
            if target and target.get("company_code"):
                code = str(target["company_code"])
                single_client = client.clients.get(code)
                if single_client is not None:
                    gps_data = await single_client._get_paginated_history(
                        "gps", start, end,
                    )
                    points: list[dict] = []
                    for vid, vdata in gps_data.items():
                        if needle in vdata.get("name", "").lower():
                            for pt in vdata.get("gps", []):
                                val = pt.get("value", pt)
                                points.append(val if isinstance(val, dict) else pt)
                            return points
        except Exception as e:
            logger.debug(
                "GPS history warehouse fast-path failed for %s: %s — "
                "falling back to all-companies search",
                vehicle_name, e,
            )

    # ── Fallback: serial walk across all company clients.
    points = []
    for code, single_client in client.clients.items():
        gps_data = await single_client._get_paginated_history("gps", start, end)
        for vid, vdata in gps_data.items():
            if needle in vdata.get("name", "").lower():
                for pt in vdata.get("gps", []):
                    val = pt.get("value", pt)
                    if isinstance(val, dict):
                        points.append(val)
                    else:
                        points.append(pt)
                return points
    return points


async def get_gps_history(
    account_id: int,
    vehicle_id: str,
    start_time: str,
    end_time: str,
) -> list[dict]:
    """Fetch GPS trail for a vehicle in a time window.

    Returns list of GPS point dicts with lat, lng, speed, time.
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client._get_paginated_history("gps", start_time, end_time, vehicle_id=vehicle_id)
