"""Route-replay API endpoints — GPS history per vehicle, lives under /fleet/routes.

URL history: was /fleet/routes (+legacy /dispatch/*) until 2026-06-11; now /routes.
"""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): ONLY router.py may import interfaces.api.deps;
# service/alert/ai_tool/signal modules never do.


from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, HTTPException

from interfaces.api.deps import require_permission_any, get_user_vehicle_nums
from features.routes.service import total_route_miles, get_vehicle_gps_history
from features.vehicles.service import get_vehicles_overview as _svc_vehicles_overview

router = APIRouter(prefix="/routes", tags=["routes"])


@router.get("/{vehicle_name}")
async def route_replay(
    vehicle_name: str,
    date: str | None = Query(
        None,
        description="Date in YYYY-MM-DD format (defaults to today UTC)",
    ),
    user: dict = Depends(require_permission_any("can_route_all", "can_route_vehicle")),
):
    """Get GPS breadcrumb trail for a vehicle on a given day.

    Returns an array of ``{lat, lng, time, speed_mph}`` points
    suitable for drawing a polyline on Leaflet.
    """
    # If user only has _own, verify they're requesting their own truck
    if user.get("_matched_perm") == "can_route_vehicle":
        trucks = await get_user_vehicle_nums(user)
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

    # Fetch GPS history via shared service (same as bot/routes.py)
    raw_points = await get_vehicle_gps_history(user["account_id"], vehicle_name, start, end)

    # Normalise raw Samsara GPS dicts to a consistent API shape
    points: list[dict] = [
        {
            "lat": p.get("latitude"),
            "lng": p.get("longitude"),
            "speed_mph": round(p.get("speedMilesPerHour", p.get("speed", 0)) or 0, 1),
            "time": p.get("time", ""),
        }
        for p in raw_points
        if p.get("latitude") is not None and p.get("longitude") is not None
    ]

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
        coords = [(p["lat"], p["lng"]) for p in points]
        total_miles = total_route_miles(coords)

    max_speed = max((p["speed_mph"] for p in points), default=0)

    return {
        "vehicle": vehicle_name,
        "date": start.strftime("%Y-%m-%d"),
        "points": points,
        "point_count": len(points),
        "total_miles": round(total_miles, 1),
        "max_speed_mph": round(max_speed, 1),
    }


@router.get("")
async def routes_vehicles(
    user: dict = Depends(require_permission_any("can_route_all", "can_route_vehicle")),
):
    """Vehicle picker list for route replay (used by frontend dropdown)."""
    overview = await _svc_vehicles_overview(user["account_id"])
    vehicles = [
        {"id": v.get("id"), "name": v.get("name", ""), "company": v.get("_org", "")}
        for v in overview
    ]

    # If user only has _own, filter to their assigned truck
    if user.get("_matched_perm") == "can_route_vehicle":
        trucks = await get_user_vehicle_nums(user)
        if trucks:
            needles = [t.lower() for t in trucks]
            vehicles = [v for v in vehicles if any(n in v["name"].lower() for n in needles)]
        else:
            vehicles = []

    vehicles.sort(key=lambda v: v["name"])
    return {"vehicles": vehicles}
