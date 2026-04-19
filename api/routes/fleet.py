"""Fleet data API endpoints."""

from fastapi import APIRouter, Depends, Query

from api.deps import require_permission, get_user_company_codes, validate_company_access, filter_by_allowed_companies, filter_by_assigned_trucks
from bot.config import get_client

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
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])
    vehicles = await client.get_fleet_overview(company=company)
    vehicles = filter_by_allowed_companies(vehicles, allowed)
    vehicles = await filter_by_assigned_trucks(vehicles, user)
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
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])
    vehicles = await client.get_fleet_overview(company=company)
    vehicles = filter_by_allowed_companies(vehicles, allowed)
    vehicles = await filter_by_assigned_trucks(vehicles, user)
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
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])
    matches = await client.get_vehicle_detail(truck_name, company=company)
    matches = filter_by_allowed_companies(matches, allowed)
    matches = await filter_by_assigned_trucks(matches, user)
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
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])
    all_health = await client.get_vehicle_health(company=company)
    all_health = filter_by_allowed_companies(all_health, allowed)
    all_health = await filter_by_assigned_trucks(all_health, user)
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
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])
    overview = await client.get_fleet_overview(company=company)
    overview = filter_by_allowed_companies(overview, allowed)
    overview = await filter_by_assigned_trucks(overview, user)
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


# ── Weather (ambient temp from vehicle sensors) ─────────────────

@router.get("/weather")
async def fleet_weather(
    user: dict = Depends(require_permission("can_faults")),
):
    """Ambient temperature readings from vehicle sensors."""
    client = await get_client(user["account_id"])
    vehicles = await client.get_fleet_weather()
    vehicles = await filter_by_assigned_trucks(vehicles, user)

    items = []
    temps: list[float] = []
    for v in vehicles:
        w = v.get("_weather", {})
        temp_f = w.get("temp_f")
        entry = {
            "name": v.get("name", "?"),
            "company": v.get("_org", ""),
            "temp_f": round(temp_f, 1) if temp_f is not None else None,
            "temp_c": round(w["temp_c"], 1) if w.get("temp_c") is not None else None,
            "baro_inhg": round(w["baro_inhg"], 2) if w.get("baro_inhg") is not None else None,
            "temp_time": w.get("temp_time"),
            "location": v.get("location", {}).get("reverseGeo", {}).get("formattedLocation", ""),
        }
        items.append(entry)
        if temp_f is not None:
            temps.append(temp_f)

    summary = {}
    if temps:
        summary = {
            "avg_f": round(sum(temps) / len(temps), 1),
            "min_f": round(min(temps), 1),
            "max_f": round(max(temps), 1),
            "freezing_count": sum(1 for t in temps if t <= 32),
            "hot_count": sum(1 for t in temps if t >= 95),
            "reporting_count": len(temps),
        }

    return {"vehicles": items, "count": len(items), "summary": summary}
