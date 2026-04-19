"""Dispatch API endpoints — route replay with GPS history."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, HTTPException

from api.deps import require_permission, require_permission_any, get_user_truck_nums
from bot.config import get_client

router = APIRouter(prefix="/dispatch", tags=["dispatch"])


@router.get("/route/{vehicle_name}")
async def route_replay(
    vehicle_name: str,
    date: str | None = Query(
        None,
        description="Date in YYYY-MM-DD format (defaults to today UTC)",
    ),
    user: dict = Depends(require_permission_any("can_route_all", "can_route_own")),
):
    """Get GPS breadcrumb trail for a vehicle on a given day.

    Returns an array of ``{lat, lng, time, speed_mph}`` points
    suitable for drawing a polyline on Leaflet.
    """
    # If user only has _own, verify they're requesting their own truck
    if user.get("_matched_perm") == "can_route_own":
        trucks = await get_user_truck_nums(user)
        if not trucks or not any(vehicle_name.lower() == t.lower() for t in trucks):
            raise HTTPException(status_code=403, detail="You can only view routes for your assigned vehicle")
    # Parse date range
    if date:
        try:
            day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    else:
        day = datetime.now(timezone.utc)

    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = day.replace(hour=23, minute=59, second=59, microsecond=0)

    client = await get_client(user["account_id"])

    # Fetch GPS history from all company clients
    points: list[dict] = []
    matched_name = ""
    needle = vehicle_name.lower()

    for code, single_client in client.clients.items():
        gps_data = await single_client._get_paginated_history("gps", start, end)
        for vid, vdata in gps_data.items():
            if needle in vdata.get("name", "").lower():
                matched_name = vdata.get("name", vehicle_name)
                for pt in vdata.get("gps", []):
                    val = pt.get("value", pt)
                    if isinstance(val, dict):
                        lat = val.get("latitude")
                        lng = val.get("longitude")
                        spd = val.get("speedMilesPerHour", val.get("speed", 0))
                        t = val.get("time") or pt.get("time", "")
                    else:
                        lat = pt.get("latitude")
                        lng = pt.get("longitude")
                        spd = pt.get("speedMilesPerHour", pt.get("speed", 0))
                        t = pt.get("time", "")
                    if lat is not None and lng is not None:
                        points.append({
                            "lat": lat,
                            "lng": lng,
                            "speed_mph": round(spd, 1) if spd else 0,
                            "time": t,
                        })
                break
        if points:
            break

    # Sort by time
    points.sort(key=lambda p: p.get("time", ""))

    # Downsample if too many points (keep first, last, and every Nth)
    max_points = 1000
    if len(points) > max_points:
        step = len(points) // max_points
        sampled = points[::step]
        if points[-1] not in sampled:
            sampled.append(points[-1])
        points = sampled

    # Compute summary
    total_miles = 0.0
    if len(points) >= 2:
        from math import radians, sin, cos, sqrt, atan2
        for i in range(1, len(points)):
            lat1, lon1 = radians(points[i - 1]["lat"]), radians(points[i - 1]["lng"])
            lat2, lon2 = radians(points[i]["lat"]), radians(points[i]["lng"])
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
            c = 2 * atan2(sqrt(a), sqrt(1 - a))
            total_miles += 3958.8 * c  # Earth radius in miles

    max_speed = max((p["speed_mph"] for p in points), default=0)

    return {
        "vehicle": matched_name or vehicle_name,
        "date": start.strftime("%Y-%m-%d"),
        "points": points,
        "point_count": len(points),
        "total_miles": round(total_miles, 1),
        "max_speed_mph": round(max_speed, 1),
    }


@router.get("/vehicles")
async def dispatch_vehicles(
    user: dict = Depends(require_permission_any("can_route_all", "can_route_own")),
):
    """Get vehicle list for route replay picker."""
    client = await get_client(user["account_id"])
    overview = await client.get_fleet_overview()
    vehicles = [
        {"id": v.get("id"), "name": v.get("name", ""), "company": v.get("_org", "")}
        for v in overview
    ]

    # If user only has _own, filter to their assigned truck
    if user.get("_matched_perm") == "can_route_own":
        trucks = await get_user_truck_nums(user)
        if trucks:
            needles = [t.lower() for t in trucks]
            vehicles = [v for v in vehicles if any(n in v["name"].lower() for n in needles)]
        else:
            vehicles = []

    vehicles.sort(key=lambda v: v["name"])
    return {"vehicles": vehicles}
