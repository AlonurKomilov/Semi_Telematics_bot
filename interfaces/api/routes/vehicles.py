"""Vehicle resource endpoints.

Resource-first URL convention: /vehicles is the canonical home for all
per-vehicle data regardless of which role is calling.  Permission guards
+ ``filter_by_assigned_trucks`` enforce what each role can actually see.

URL structure:
    GET /api/vehicles/                    list (all roles that can see vehicles)
    GET /api/vehicles/{name}              detail
    GET /api/vehicles/{name}/health       subsystem — battery, oil, DEF, …
    GET /api/vehicles/{name}/faults       active DTCs
    GET /api/vehicles/{name}/timeline     hourly telemetry roll-up (warehouse)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from interfaces.api.deps import (
    require_permission,
    require_permission_any,
    get_user_company_codes,
    validate_company_access,
    filter_by_allowed_companies,
    filter_by_assigned_trucks,
    paginate,
)
from capabilities.vehicles.service import (
    get_fleet_overview as _svc_fleet_overview,
    get_vehicle_detail as _svc_vehicle_detail,
)
from capabilities.telemetry.service import get_vehicle_health as _svc_vehicle_health
from capabilities.telemetry import warehouse_reader as _wh_reader
from capabilities.location.service import classify_vehicle_status
from infra.platform import get_tenant_db as _get_tenant_db
import infra.cache as _redis

# Short TTL for the full Samsara snapshot backing the vehicle list.
# Collapses burst polls from concurrent driver sessions without making
# GPS positions feel stale.
_FLEET_CACHE_TTL = 30

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


# ── Raw-field extractors (Samsara nested → flat) ─────────────────

def _extract_fuel(v: dict) -> float | None:
    """Extract fuel percent from raw Samsara vehicle dict."""
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
    """Derive a human-readable engine state from classified status."""
    if status == "moving":
        return "On"
    if status == "idle":
        return "Idle"
    return "Off"


def _extract_odometer(v: dict) -> tuple[float | None, str | None]:
    """Pull odometer (miles, ISO timestamp) from a merged vehicle dict.

    The value comes from the warehouse ``vehicle_state.odometer_mi``
    column populated by ``ingest_vehicle_state``; the fallback live
    path (cold cache) shapes the same key.  Vehicles without a CAN
    bus gateway return None for both fields.
    """
    odometer = v.get("odometer")
    if isinstance(odometer, dict):
        miles = odometer.get("miles")
        timestamp = odometer.get("time")
        if isinstance(miles, (int, float)):
            return float(miles), timestamp if isinstance(timestamp, str) else None
    return None, None


def _simplify(v: dict) -> dict:
    """Flatten a fleet overview vehicle into the consistent API shape."""
    loc = v.get("location", {})
    speed = _extract_speed(v)
    status = classify_vehicle_status(v)
    engine_state = _derive_engine_state(status)
    address = (
        loc.get("reverseGeo", {}).get("formattedLocation")
        or loc.get("address")
        or ""
    )
    odometer_miles, odometer_time = _extract_odometer(v)
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
        "odometer_miles": odometer_miles,
        "odometer_time": odometer_time,
        "status": status,
        "time": (
            loc.get("time")
            or (v.get("fuel") or {}).get("time")
            or (v.get("def_level") or {}).get("time")
            or v.get("time")
        ),
    }


def _normalize_detail(v: dict) -> dict:
    """Produce a normalized vehicle dict for the detail endpoint."""
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
    odometer_miles, odometer_time = _extract_odometer(v)
    return {
        **v,
        "fuel_percent": fuel_pct,
        "fuelPercent": fuel_pct,
        "def_percent": def_pct,
        "defPercent": def_pct,
        "speed_mph": speed,
        "engine_state": engine_state,
        "engineState": engine_state,
        "status": status,
        "fault_count": len(dtcs),
        "odometer_miles": odometer_miles,
        "odometer_time": odometer_time,
        "formattedAddress": address,
        "address": address,
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "licensePlate": v.get("licensePlate") or v.get("license_plate") or "N/A",
    }


# ── Routes ───────────────────────────────────────────────────────

@router.get("/")
async def vehicles_list(
    company: str | None = Query(None),
    search: str | None = Query(None, description="Search by vehicle name"),
    status: str | None = Query(None, description="Filter: moving, idle, stopped"),
    sort: str | None = Query(None, description="Sort field: name, fuel_percent, fault_count, status"),
    order: str = Query("asc", description="Sort order: asc or desc"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_own")),
):
    """Vehicle list with location and engine state — supports filtering, sorting, pagination."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)

    async def _live_cached() -> list:
        """Hit Samsara, serve Redis cache when warm.

        Caches the full unfiltered snapshot per account+company slice.
        Python-side search/status/sort filters below still apply on the
        cached list so a cache hit never returns stale partial data.
        """
        cache_key = f"fleet:raw:{user['account_id']}:{company or '_all'}"
        cached = await _redis.get(cache_key)
        if cached is not None:
            return cached
        data = await _svc_fleet_overview(user["account_id"], company=company)
        await _redis.cache_set(cache_key, data, ttl=_FLEET_CACHE_TTL)
        return data

    vehicles = await _wh_reader.get_current_vehicles(
        user["account_id"], company=company, samsara_fallback=_live_cached,
    )
    vehicles = filter_by_allowed_companies(vehicles, allowed)
    vehicles = await filter_by_assigned_trucks(vehicles, user)

    # Enrich with warehouse-sourced odometer regardless of which path
    # produced ``vehicles``.  When WAREHOUSE_READS_ENABLED is off the
    # rows came from live Samsara via _live_cached and don't include
    # odometer (Samsara overview never does); when on the rows already
    # have odometer but a fresh re-read is cheap and keeps the contract
    # uniform.  Read directly from vehicle_state mixin to bypass the
    # cutover flag.
    if vehicles:
        tenant_db = await _get_tenant_db(user["account_id"])
        warehouse_rows = await tenant_db.get_vehicle_state(
            user["account_id"], company=company,
        )
        odometer_by_id: dict[str, dict] = {}
        odometer_by_name: dict[str, dict] = {}
        for row in warehouse_rows:
            miles = row.get("odometer_mi")
            if miles is None:
                continue
            odometer = {"miles": miles, "time": row.get("odometer_time")}
            rid = str(row.get("vehicle_id") or "")
            rname = (row.get("vehicle_name") or "").lower()
            if rid:
                odometer_by_id[rid] = odometer
            if rname:
                odometer_by_name[rname] = odometer
        for v in vehicles:
            if v.get("odometer"):
                continue
            vid = str(v.get("id") or "")
            vname = (v.get("name") or "").lower()
            odometer = odometer_by_id.get(vid) or odometer_by_name.get(vname)
            if odometer:
                v["odometer"] = odometer

    result = [_simplify(v) for v in vehicles]

    if search:
        q = search.lower()
        result = [v for v in result if q in v["name"].lower()]

    if status and status in ("moving", "idle", "stopped"):
        result = [v for v in result if v["status"] == status]

    if sort and sort in ("name", "fuel_percent", "fault_count", "status", "company"):
        reverse = order.lower() == "desc"
        result.sort(key=lambda v: (v.get(sort) is None, v.get(sort, "")), reverse=reverse)

    paged = paginate(result, page, page_size)
    return {
        "vehicles": paged["items"],
        "count": paged["total"],
        "page": paged["page"],
        "page_size": paged["page_size"],
        "total_pages": paged["total_pages"],
    }


@router.get("/{vehicle_name}")
async def vehicle_detail(
    vehicle_name: str,
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_own")),
):
    """Single vehicle detail by name.

    Live Samsara is the source for static metadata (VIN, make, model,
    license plate) which the warehouse intentionally doesn't track.
    Dynamic telemetry (odometer_miles + odometer_time) is merged in
    from ``vehicle_state`` so the value is consistent with everywhere
    else in the app — DB stays the single source of truth for state.
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    matches = await _svc_vehicle_detail(user["account_id"], vehicle_name, company=company)
    matches = filter_by_allowed_companies(matches, allowed)
    matches = await filter_by_assigned_trucks(matches, user)
    if not matches:
        return {"error": "Vehicle not found", "vehicles": []}

    # Enrich with warehouse-sourced odometer.  Read the raw vehicle_state
    # table directly via the mixin — bypasses the WAREHOUSE_READS_ENABLED
    # cutover flag because odometer is *only* in the warehouse, never in
    # the live overview, so we always need it regardless of cutover phase.
    tenant = await _get_tenant_db(user["account_id"])
    warehouse_rows = await tenant.get_vehicle_state(
        user["account_id"], company=company, vehicle_nums=[vehicle_name],
    )
    odometer_by_id: dict[str, dict] = {}
    odometer_by_name: dict[str, dict] = {}
    for row in warehouse_rows:
        miles = row.get("odometer_mi")
        if miles is None:
            continue
        odometer = {"miles": miles, "time": row.get("odometer_time")}
        rid = str(row.get("vehicle_id") or "")
        rname = (row.get("vehicle_name") or "").lower()
        if rid:
            odometer_by_id[rid] = odometer
        if rname:
            odometer_by_name[rname] = odometer
    for match in matches:
        if match.get("odometer"):
            continue
        match_id = str(match.get("id") or "")
        match_name = (match.get("name") or "").lower()
        odometer = odometer_by_id.get(match_id) or odometer_by_name.get(match_name)
        if odometer:
            match["odometer"] = odometer

    normalized = [_normalize_detail(m) for m in matches]
    return {"vehicles": normalized, "count": len(normalized)}


@router.get("/{vehicle_name}/health")
async def vehicle_health(
    vehicle_name: str,
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_health", "can_vehicle_own")),
):
    """Vehicle health stats: battery, oil, coolant, DEF, seatbelt, engine load."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    all_health = await _svc_vehicle_health(user["account_id"], company=company)
    all_health = filter_by_allowed_companies(all_health, allowed)
    all_health = await filter_by_assigned_trucks(all_health, user)
    name_lower = vehicle_name.lower()
    match = [v for v in all_health if v.get("name", "").lower() == name_lower]
    if not match:
        return {"error": "Vehicle not found or no health data", "health": None}
    v = match[0]
    return {
        "name": v.get("name"),
        "company": v.get("_org", ""),
        "health": v.get("_health", {}),
        "alerts": v.get("_health_alerts", []),
    }


@router.get("/{vehicle_name}/faults")
async def vehicle_faults(
    vehicle_name: str,
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_all", "can_vehicle_own")),
):
    """Active fault codes for a specific vehicle."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    overview = await _svc_fleet_overview(user["account_id"], company=company)
    overview = filter_by_allowed_companies(overview, allowed)
    overview = await filter_by_assigned_trucks(overview, user)
    name_lower = vehicle_name.lower()
    match = [v for v in overview if v.get("name", "").lower() == name_lower]
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


@router.get("/{vehicle_name}/timeline")
async def vehicle_timeline(
    vehicle_name: str,
    days: int = Query(7, ge=1, le=30),
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_all", "can_vehicle_own")),
):
    """Hourly telemetry roll-up for a single vehicle (warehouse).

    Returns oldest-first ``points`` from ``vehicle_telemetry_hourly``.
    Returns empty list when the warehouse flag is off or the table is cold.
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    matches = await _svc_vehicle_detail(user["account_id"], vehicle_name, company=company)
    matches = filter_by_allowed_companies(matches, allowed)
    matches = await filter_by_assigned_trucks(matches, user)
    if not matches:
        return {"error": "Vehicle not found", "points": []}
    vehicle_id = str(matches[0].get("id") or "")
    if not vehicle_id:
        return {"name": matches[0].get("name"), "points": []}
    points = await _wh_reader.get_vehicle_telemetry_hourly(
        user["account_id"], vehicle_id=vehicle_id, hours=days * 24,
    )
    points = list(reversed(points))  # reader returns DESC; chart wants oldest-first
    return {
        "name": matches[0].get("name"),
        "vehicle_id": vehicle_id,
        "days": days,
        "points": points,
    }
