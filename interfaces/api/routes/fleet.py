"""Fleet data API endpoints."""

from fastapi import APIRouter, Depends, Query

from interfaces.api.deps import require_permission, get_user_company_codes, validate_company_access, filter_by_allowed_companies, filter_by_assigned_trucks, paginate
from capabilities.vehicle_catalog.service import (
    get_fleet_overview as _svc_fleet_overview,
    get_vehicle_detail as _svc_vehicle_detail,
)
from capabilities.telemetry.service import (
    get_vehicle_health as _svc_vehicle_health,
    get_fleet_weather as _svc_fleet_weather,
)
from capabilities.location.service import classify_vehicle_status

router = APIRouter(prefix="/fleet", tags=["fleet"])


def _extract_fuel(v: dict) -> float | None:
    """Extract fuel percent from raw Samsara vehicle dict.

    The Samsara adapter stores fuel as v["fuel"] = {"value": 45.3, "time": "..."}
    (not as v["fuelPercent"] which is the key Samsara uses internally in the
    raw stats payload before the adapter processes it).
    """
    fuel = v.get("fuel", {})
    if isinstance(fuel, dict):
        return fuel.get("value")
    if isinstance(fuel, (int, float)):
        return float(fuel)
    return None


def _extract_def(v: dict) -> float | None:
    """Extract DEF level percent from raw Samsara vehicle dict."""
    def_lvl = v.get("def_level", {})
    if isinstance(def_lvl, dict):
        return def_lvl.get("value")
    if isinstance(def_lvl, (int, float)):
        return float(def_lvl)
    return None


def _extract_fault_count(v: dict) -> int:
    """Count active DTCs from raw Samsara fault_codes dict."""
    fc = v.get("fault_codes", {})
    if isinstance(fc, dict):
        return len(fc.get("j1939", {}).get("diagnosticTroubleCodes", []))
    if isinstance(fc, list):
        return len(fc)
    return 0


def _extract_dtcs(v: dict) -> list:
    """Return raw DTC list from fault_codes."""
    fc = v.get("fault_codes", {})
    if isinstance(fc, dict):
        return fc.get("j1939", {}).get("diagnosticTroubleCodes", [])
    return []


def _extract_speed(v: dict) -> float:
    """Extract speed (mph) from nested location dict."""
    loc = v.get("location", {})
    speed = loc.get("speedMilesPerHour") or loc.get("speed") or 0
    return float(speed or 0)


def _derive_engine_state(status: str) -> str:
    """Derive a human-readable engine state from classified status.

    Engine state data is not included in the fleet overview API call,
    so we derive it from the speed-based status classification.
    """
    if status == "moving":
        return "On"
    if status == "idle":
        return "Idle"
    return "Off"


def _simplify(v: dict) -> dict:
    """Flatten a fleet overview vehicle into a consistent API shape.

    Samsara's get_fleet_overview() returns nested structures:
      - fuel:       {"value": 45.3, "time": "..."}
      - def_level:  {"value": 78.0, "time": "..."}
      - fault_codes: {"j1939": {"diagnosticTroubleCodes": [...]}}
      - location:   {"latitude": ..., "speedMilesPerHour": ..., "reverseGeo": {...}}

    This function flattens all of them into the fields the frontend expects.
    """
    loc = v.get("location", {})
    speed = _extract_speed(v)
    status = classify_vehicle_status(v)
    engine_state = _derive_engine_state(status)
    address = (
        loc.get("reverseGeo", {}).get("formattedLocation")
        or loc.get("address")
        or ""
    )
    return {
        "id": v.get("id"),
        "name": v.get("name", ""),
        "company": v.get("_org", ""),
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "speed_mph": speed,
        "address": address,
        "engine_state": engine_state,
        "fuel_percent": _extract_fuel(v),
        "def_percent": _extract_def(v),
        "fault_count": _extract_fault_count(v),
        "status": status,
    }


def _normalize_detail(v: dict) -> dict:
    """Produce a normalized vehicle dict for the detail endpoint.

    Keeps the nested ``location`` and ``fault_codes`` objects intact
    (the frontend uses them) while also adding flat aliases for fuel,
    DEF, speed, engine state, and address so the UI can read them
    directly without traversing the nested structure.
    """
    loc = v.get("location", {})
    speed = _extract_speed(v)
    fuel_pct = _extract_fuel(v)
    def_pct = _extract_def(v)
    dtcs = _extract_dtcs(v)
    status = classify_vehicle_status(v)
    engine_state = _derive_engine_state(status)
    address = (
        loc.get("reverseGeo", {}).get("formattedLocation")
        or loc.get("address")
        or ""
    )
    return {
        **v,
        # Flat fields the frontend reads directly:
        "fuel_percent": fuel_pct,
        "fuelPercent": fuel_pct,
        "def_percent": def_pct,
        "defPercent": def_pct,
        "speed_mph": speed,
        "engine_state": engine_state,
        "engineState": engine_state,
        "status": status,
        "fault_count": len(dtcs),
        "formattedAddress": address,
        "address": address,
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        # Normalize license plate key (Samsara uses "licensePlate" in /vehicles,
        # but the adapter stores it as "license_plate").
        "licensePlate": v.get("licensePlate") or v.get("license_plate") or "N/A",
    }


@router.get("/overview")
async def fleet_overview(
    company: str | None = Query(None, description="Filter by company code"),
    user: dict = Depends(require_permission("can_faults")),
):
    """Fleet snapshot — vehicles with status, location, faults."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    vehicles = await _svc_fleet_overview(user["account_id"], company=company)
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
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    user: dict = Depends(require_permission("can_faults")),
):
    """Vehicle list with location and engine state, supports filtering and sorting."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    vehicles = await _svc_fleet_overview(user["account_id"], company=company)
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

    paged = paginate(result, page, page_size)
    return {"vehicles": paged["items"], "count": paged["total"],
            "page": paged["page"], "page_size": paged["page_size"],
            "total_pages": paged["total_pages"]}


@router.get("/vehicle/{truck_name}")
async def fleet_vehicle_detail(
    truck_name: str,
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_faults")),
):
    """Single vehicle detail by name."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    matches = await _svc_vehicle_detail(user["account_id"], truck_name, company=company)
    matches = filter_by_allowed_companies(matches, allowed)
    matches = await filter_by_assigned_trucks(matches, user)
    if not matches:
        return {"error": "Vehicle not found", "vehicles": []}
    normalized = [_normalize_detail(m) for m in matches]
    return {"vehicles": normalized, "count": len(normalized)}


@router.get("/vehicle/{truck_name}/health")
async def fleet_vehicle_health(
    truck_name: str,
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_health")),
):
    """Vehicle health stats: battery, oil, coolant, DEF, seatbelt, engine load."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    all_health = await _svc_vehicle_health(user["account_id"], company=company)
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
    overview = await _svc_fleet_overview(user["account_id"], company=company)
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
    dtcs = _extract_dtcs(v)
    return {
        "name": v.get("name"),
        "company": v.get("_org", ""),
        "faults": dtcs,
        "fault_count": len(dtcs),
    }


# ── Weather (ambient temp from vehicle sensors) ─────────────────

@router.get("/weather")
async def fleet_weather(
    user: dict = Depends(require_permission("can_faults")),
):
    """Ambient temperature readings from vehicle sensors."""
    vehicles = await _svc_fleet_weather(user["account_id"])
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
