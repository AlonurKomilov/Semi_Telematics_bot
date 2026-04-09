"""Fleet data API endpoints."""

from fastapi import APIRouter, Depends, Query

from api.deps import require_permission
from bot.state import get_client

router = APIRouter(prefix="/fleet", tags=["fleet"])


def _vehicle_status(v: dict) -> str:
    """Derive moving/idle/stopped from speed + engine state."""
    if v.get("speed_mph") and v["speed_mph"] > 0:
        return "moving"
    if v.get("engineState") == "On" or v.get("engine_state") == "On":
        return "idle"
    return "stopped"


def _simplify(v: dict) -> dict:
    """Flatten a fleet overview vehicle into a consistent API shape."""
    return {
        "id": v.get("id"),
        "name": v.get("name", ""),
        "company": v.get("_org", ""),
        "latitude": v.get("latitude"),
        "longitude": v.get("longitude"),
        "speed_mph": v.get("speed_mph", 0),
        "address": v.get("formattedAddress", ""),
        "engine_state": v.get("engineState", "Off"),
        "fuel_percent": v.get("fuelPercent"),
        "def_percent": v.get("defPercent"),
        "fault_count": len(v.get("faults", [])),
        "status": _vehicle_status(v),
    }


@router.get("/overview")
async def fleet_overview(
    company: str | None = Query(None, description="Filter by company code"),
    user: dict = Depends(require_permission("can_faults")),
):
    """Fleet snapshot — vehicles with status, location, faults."""
    client = await get_client(user["account_id"])
    vehicles = await client.get_fleet_overview(company=company)
    return {"vehicles": vehicles, "count": len(vehicles)}


@router.get("/vehicles")
async def fleet_vehicles(
    company: str | None = Query(None),
    search: str | None = Query(None, description="Search by vehicle name"),
    status: str | None = Query(None, description="Filter: moving, idle, stopped"),
    sort: str | None = Query(None, description="Sort field: name, fuel_percent, fault_count, status"),
    order: str = Query("asc", description="Sort order: asc or desc"),
    user: dict = Depends(require_permission("can_faults")),
):
    """Vehicle list with location and engine state, supports filtering and sorting."""
    client = await get_client(user["account_id"])
    vehicles = await client.get_fleet_overview(company=company)
    result = [_simplify(v) for v in vehicles]

    # Search filter
    if search:
        q = search.lower()
        result = [v for v in result if q in v["name"].lower()]

    # Status filter
    if status and status in ("moving", "idle", "stopped"):
        result = [v for v in result if v["status"] == status]

    # Sorting
    if sort and sort in ("name", "fuel_percent", "fault_count", "status", "company"):
        reverse = order.lower() == "desc"
        result.sort(key=lambda v: (v.get(sort) is None, v.get(sort, "")), reverse=reverse)

    return {"vehicles": result, "count": len(result)}


@router.get("/vehicle/{truck_name}")
async def fleet_vehicle_detail(
    truck_name: str,
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_faults")),
):
    """Single vehicle detail by name."""
    client = await get_client(user["account_id"])
    matches = await client.get_vehicle_detail(truck_name, company=company)
    if not matches:
        return {"error": "Vehicle not found", "vehicles": []}
    return {"vehicles": matches, "count": len(matches)}


@router.get("/vehicle/{truck_name}/health")
async def fleet_vehicle_health(
    truck_name: str,
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_health")),
):
    """Vehicle health stats: battery, oil, coolant, DEF, seatbelt, engine load."""
    client = await get_client(user["account_id"])
    all_health = await client.get_vehicle_health(company=company)
    name_lower = truck_name.lower()
    match = [
        v for v in all_health
        if v.get("name", "").lower() == name_lower
    ]
    if not match:
        return {"error": "Vehicle not found or no health data", "health": None}
    v = match[0]
    return {
        "name": v.get("name"),
        "company": v.get("_org", ""),
        "health": v.get("_health", {}),
        "alerts": v.get("_health_alerts", []),
    }


@router.get("/vehicle/{truck_name}/faults")
async def fleet_vehicle_faults(
    truck_name: str,
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_faults")),
):
    """Active fault codes for a specific vehicle."""
    client = await get_client(user["account_id"])
    overview = await client.get_fleet_overview(company=company)
    name_lower = truck_name.lower()
    match = [
        v for v in overview
        if v.get("name", "").lower() == name_lower
    ]
    if not match:
        return {"error": "Vehicle not found", "faults": []}
    v = match[0]
    return {
        "name": v.get("name"),
        "company": v.get("_org", ""),
        "faults": v.get("faults", []),
        "fault_count": len(v.get("faults", [])),
    }
