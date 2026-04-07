"""Fleet data API endpoints."""

from fastapi import APIRouter, Depends, Query

from api.deps import require_permission
from bot.state import get_client

router = APIRouter(prefix="/fleet", tags=["fleet"])


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
    user: dict = Depends(require_permission("can_faults")),
):
    """Vehicle list with location and engine state."""
    client = await get_client(user["account_id"])
    vehicles = await client.get_fleet_overview(company=company)
    # Return simplified list
    result = []
    for v in vehicles:
        result.append({
            "id": v.get("id"),
            "name": v.get("name", ""),
            "company": v.get("_org", ""),
            "latitude": v.get("latitude"),
            "longitude": v.get("longitude"),
            "speed_mph": v.get("speed_mph", 0),
            "address": v.get("formattedAddress", ""),
            "engine_state": v.get("engineState", "Off"),
            "fuel_percent": v.get("fuelPercent"),
            "fault_count": len(v.get("faults", [])),
        })
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
