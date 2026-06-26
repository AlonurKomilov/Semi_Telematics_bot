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
    GET /api/vehicles/overview            fleet snapshot (was /fleet/overview)
    GET /api/vehicles/weather             cabin/ambient sensors (was /fleet/weather)
    GET /api/vehicles/utilization-summary per-vehicle utilization
"""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): ONLY router.py may import interfaces.api.deps;
# service/alert/ai_tool/signal modules never do.


from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from interfaces.api.deps import (
    require_permission_any,
    get_user_company_codes,
    validate_company_access,
    filter_by_allowed_companies,
    filter_by_assigned_trucks,
    require_permission,
    paginate,
)
from features.vehicles.service import (
    get_vehicles_overview as _svc_vehicles_overview,
    get_vehicle_detail as _svc_vehicle_detail,
)
from capabilities.warehouse.telemetry.service import (
    get_vehicle_health as _svc_vehicle_health,
    get_fleet_weather as _svc_fleet_weather,
)
from capabilities.warehouse.telemetry import warehouse_reader as _wh_reader
from features.location.service import classify_vehicle_status
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


def _extract_engine_hours(v: dict) -> tuple[float | None, str | None]:
    """Pull cumulative engine hours (hours, ISO timestamp) from a merged
    vehicle dict.  Mirrors ``_extract_odometer`` — same warehouse-or-
    live source distinction.  Vehicles without an engine-hours OBD
    signal return None for both fields.
    """
    eng = v.get("engine_hours_reading")
    if isinstance(eng, dict):
        hours = eng.get("hours")
        timestamp = eng.get("time")
        if isinstance(hours, (int, float)):
            return float(hours), timestamp if isinstance(timestamp, str) else None
    return None, None


def _simplify(v: dict) -> dict:
    """Flatten a fleet overview vehicle into the consistent API shape."""
    loc = v.get("location", {})
    speed = _extract_speed(v)
    # Registry vehicles with no telematics match (trailers, manual
    # trucks) carry a marker so we don't mis-classify a GPS-less row
    # as "stopped" — they get an explicit "no_telemetry" status.
    no_telemetry = bool(v.get("_no_telemetry"))
    status = "no_telemetry" if no_telemetry else classify_vehicle_status(v)
    engine_state = "" if no_telemetry else _derive_engine_state(status)
    address = (
        loc.get("reverseGeo", {}).get("formattedLocation")
        or loc.get("address")
        or ""
    )
    odometer_miles, odometer_time = _extract_odometer(v)
    engine_hours, engine_hours_time = _extract_engine_hours(v)
    return {
        "id": v.get("id"),
        "name": v.get("name", ""),
        "company": v.get("_org", ""),
        # Registry fields — drive the Type column + source hint on the
        # Vehicles page.  Default to truck/samsara for any live row the
        # registry overlay didn't tag (steady state: none).
        "vehicle_type": v.get("vehicle_type", "truck"),
        "source": v.get("source", "samsara"),
        # Registry row id for the manage UI's edit/delete; null for a
        # live-only vehicle the registry hasn't caught yet (then the
        # edit affordance is disabled until the next ingest registers it).
        "registry_id": v.get("_registry_id"),
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
        "engine_hours": engine_hours,
        "engine_hours_time": engine_hours_time,
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
    engine_hours, engine_hours_time = _extract_engine_hours(v)
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
        "engine_hours": engine_hours,
        "engine_hours_time": engine_hours_time,
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
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_vehicle")),
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
        data = await _svc_vehicles_overview(user["account_id"], company=company)
        await _redis.cache_set(cache_key, data, ttl=_FLEET_CACHE_TTL)
        return data

    vehicles = await _wh_reader.get_current_vehicles(
        user["account_id"], company=company, samsara_fallback=_live_cached,
    )
    vehicles = filter_by_allowed_companies(vehicles, allowed)
    vehicles = await filter_by_assigned_trucks(vehicles, user)

    # Enrich with warehouse-sourced odometer + engine hours regardless
    # of which path produced ``vehicles``.  When WAREHOUSE_READS_ENABLED
    # is off the rows came from live Samsara via _live_cached and don't
    # include either (Samsara overview never does); when on the rows
    # already have them but a fresh re-read is cheap and keeps the
    # contract uniform.  Read directly from vehicle_state mixin to
    # bypass the cutover flag.
    if vehicles:
        tenant_db = await _get_tenant_db(user["account_id"])
        warehouse_rows = await tenant_db.get_vehicle_state(
            user["account_id"], company=company,
        )
        odometer_by_id: dict[str, dict] = {}
        odometer_by_name: dict[str, dict] = {}
        engine_hours_by_id: dict[str, dict] = {}
        engine_hours_by_name: dict[str, dict] = {}
        for row in warehouse_rows:
            rid = str(row.get("vehicle_id") or "")
            rname = (row.get("vehicle_name") or "").lower()
            miles = row.get("odometer_mi")
            if miles is not None:
                odometer = {"miles": miles, "time": row.get("odometer_time")}
                if rid:
                    odometer_by_id[rid] = odometer
                if rname:
                    odometer_by_name[rname] = odometer
            hours = row.get("engine_hours")
            if hours is not None:
                eng = {"hours": hours, "time": row.get("engine_hours_time")}
                if rid:
                    engine_hours_by_id[rid] = eng
                if rname:
                    engine_hours_by_name[rname] = eng
        for v in vehicles:
            vid = str(v.get("id") or "")
            vname = (v.get("name") or "").lower()
            if not v.get("odometer"):
                o_hit = odometer_by_id.get(vid) or odometer_by_name.get(vname)
                if o_hit:
                    v["odometer"] = o_hit
            if not v.get("engine_hours_reading"):
                e_hit = engine_hours_by_id.get(vid) or engine_hours_by_name.get(vname)
                if e_hit:
                    v["engine_hours_reading"] = e_hit

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


@router.get("/overview")
async def fleet_overview(
    company: str | None = Query(None, description="Filter by company code"),
    user: dict = Depends(require_permission("can_faults")),
):
    """Fleet snapshot — vehicles with status, location, faults.

    URL history: was GET /fleet/overview before 2026-06-11."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    async def _live():
        return await _svc_vehicles_overview(user["account_id"], company=company)
    vehicles = await _wh_reader.get_current_vehicles(
        user["account_id"], company=company, samsara_fallback=_live,
    )
    vehicles = filter_by_allowed_companies(vehicles, allowed)
    vehicles = await filter_by_assigned_trucks(vehicles, user)
    return {"vehicles": vehicles, "count": len(vehicles)}


@router.get("/weather")
async def fleet_weather(
    user: dict = Depends(require_permission("can_faults")),
):
    """Ambient temperature readings from vehicle sensors.

    URL history: was GET /fleet/weather before 2026-06-11."""
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


@router.get("/utilization-summary")
async def fleet_utilization_summary(
    days: int = Query(30, ge=7, le=365),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_all", "can_vehicle_vehicle")),
):
    """Per-vehicle utilization across the entire visible fleet.

    Backs the Vehicles page utilization card.  Filters down to the
    caller's visible vehicles so drivers see only their own row and
    operators see everything in their company allow-list.
    """
    allowed = await get_user_company_codes(user)
    rows = await _wh_reader.get_account_utilization_summary(
        user["account_id"], days=days,
    )
    if not rows:
        return {"days": days, "vehicles": []}

    # Cross-reference against the visible-vehicles list so callers
    # don't see utilization for trucks outside their allow-list / not
    # in their assigned-trucks set.  Hot path is the dashboard so the
    # extra fetch is acceptable; the filter is in-memory.
    visible = await _wh_reader.get_current_vehicles(user["account_id"])
    visible = filter_by_allowed_companies(visible, allowed)
    visible = await filter_by_assigned_trucks(visible, user)
    name_by_vid: dict[str, dict] = {
        str(v.get("id") or ""): v for v in visible if v.get("id")
    }

    enriched: list[dict] = []
    for r in rows:
        vid = str(r.get("vehicle_id") or "")
        meta = name_by_vid.get(vid)
        if not meta:
            continue
        enriched.append({
            **r,
            "vehicle_name":   meta.get("name") or "",
            "company_code":   meta.get("_org") or meta.get("company_code") or "",
        })
    enriched.sort(key=lambda r: float(r.get("utilization_pct") or 0), reverse=True)
    return {"days": days, "vehicles": enriched}


@router.get("/{vehicle_name}")
async def vehicle_detail(
    vehicle_name: str,
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_vehicle")),
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

    # Enrich with warehouse-sourced odometer + engine hours.  Read the
    # raw vehicle_state table directly via the mixin — bypasses the
    # WAREHOUSE_READS_ENABLED cutover flag because both fields are
    # *only* in the warehouse, never in the live overview.
    tenant = await _get_tenant_db(user["account_id"])
    warehouse_rows = await tenant.get_vehicle_state(
        user["account_id"], company=company, vehicle_nums=[vehicle_name],
    )
    odometer_by_id: dict[str, dict] = {}
    odometer_by_name: dict[str, dict] = {}
    engine_hours_by_id: dict[str, dict] = {}
    engine_hours_by_name: dict[str, dict] = {}
    for row in warehouse_rows:
        rid = str(row.get("vehicle_id") or "")
        rname = (row.get("vehicle_name") or "").lower()
        miles = row.get("odometer_mi")
        if miles is not None:
            odometer = {"miles": miles, "time": row.get("odometer_time")}
            if rid:
                odometer_by_id[rid] = odometer
            if rname:
                odometer_by_name[rname] = odometer
        hours = row.get("engine_hours")
        if hours is not None:
            eng = {"hours": hours, "time": row.get("engine_hours_time")}
            if rid:
                engine_hours_by_id[rid] = eng
            if rname:
                engine_hours_by_name[rname] = eng
    for match in matches:
        match_id = str(match.get("id") or "")
        match_name = (match.get("name") or "").lower()
        if not match.get("odometer"):
            o_hit = odometer_by_id.get(match_id) or odometer_by_name.get(match_name)
            if o_hit:
                match["odometer"] = o_hit
        if not match.get("engine_hours_reading"):
            e_hit = engine_hours_by_id.get(match_id) or engine_hours_by_name.get(match_name)
            if e_hit:
                match["engine_hours_reading"] = e_hit

    normalized = [_normalize_detail(m) for m in matches]
    return {"vehicles": normalized, "count": len(normalized)}


@router.get("/{vehicle_name}/health")
async def vehicle_health(
    vehicle_name: str,
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_health", "can_vehicle_vehicle")),
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
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_all", "can_vehicle_vehicle")),
):
    """Active fault codes for a specific vehicle."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    overview = await _svc_vehicles_overview(user["account_id"], company=company)
    overview = filter_by_allowed_companies(overview, allowed)
    overview = await filter_by_assigned_trucks(overview, user)
    name_lower = vehicle_name.lower()
    match = [v for v in overview if v.get("name", "").lower() == name_lower]
    if not match:
        return {"error": "Vehicle not found", "faults": []}
    v = match[0]

    # Prefer the fault snapshot — it carries the live Samsara shape
    # (spnDescription / fmiDescription / sourceAddressName) so the UI
    # can render real fault names.  ``_extract_dtcs`` on the warehouse
    # vehicle row only sees ``[{}, {}, …]`` placeholders because
    # vehicle_state stores fault_count, not per-DTC detail — that's
    # why the dashboard was rendering 4 generic "DTC" rows.
    try:
        snap = await _wh_reader.get_vehicle_fault_snapshot(
            user["account_id"], v.get("name", ""),
        )
    except Exception:
        snap = None
    if snap:
        snap_dtcs = (
            (snap.get("fault_codes") or {})
            .get("j1939", {})
            .get("diagnosticTroubleCodes")
        ) or snap.get("dtcs") or []
        if snap_dtcs:
            return {
                "name": v.get("name"),
                "company": v.get("_org", ""),
                "faults": snap_dtcs,
                "fault_count": len(snap_dtcs),
            }

    # Fallback — placeholder DTCs from vehicle_state (count only).
    dtcs = _extract_dtcs(v)
    return {
        "name": v.get("name"),
        "company": v.get("_org", ""),
        "faults": dtcs,
        "fault_count": len(dtcs),
    }


@router.get("/{vehicle_name}/reading-as-of")
async def vehicle_reading_as_of(
    vehicle_name: str,
    date: str = Query(..., description="Service date (YYYY-MM-DD)"),
    user: dict = Depends(require_permission_any(
        "can_work_orders_all", "can_faults", "can_vehicle_vehicle",
    )),
):
    """Odometer + engine-hours for a vehicle AS OF a date — backs the
    work-order form's back-dated auto-fill.

    When a snapshot exists, returns it.  When none does, returns nulls plus
    the vehicle's snapshot *coverage window* (``telematics_linked`` +
    ``coverage_start``/``coverage_end``) so the caller can explain WHY —
    no telematics link vs. history not reaching that far back."""
    tenant_db = await _get_tenant_db(user["account_id"])
    r = await tenant_db.get_reading_as_of(user["account_id"], vehicle_name, date)
    if r:
        return {**r, "telematics_linked": True}
    cov = await tenant_db.get_snapshot_coverage(user["account_id"], vehicle_name)
    return {
        "odometer_miles": None, "engine_hours": None, "as_of": None,
        "telematics_linked": cov["telematics_linked"],
        "coverage_start": cov["coverage_start"],
        "coverage_end": cov["coverage_end"],
    }


@router.get("/{vehicle_name}/timeline")
async def vehicle_timeline(
    vehicle_name: str,
    days: int = Query(7, ge=1, le=30),
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_all", "can_vehicle_vehicle")),
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


@router.get("/{vehicle_name}/usage")
async def vehicle_usage(
    vehicle_name: str,
    days: int = Query(30, ge=7, le=365),
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_all", "can_vehicle_vehicle")),
):
    """Per-vehicle usage summary + daily series over the window.

    Combines the daily roll-up (miles, drive/idle hours, harsh events)
    with the work-orders cost aggregate to produce a single payload
    the dashboard renders as the "Usage trends" card on the vehicle
    detail page.  Fleet ops uses utilization; accounting uses
    cost-per-mile; safety uses harsh-event totals — same query.
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    matches = await _svc_vehicle_detail(user["account_id"], vehicle_name, company=company)
    matches = filter_by_allowed_companies(matches, allowed)
    matches = await filter_by_assigned_trucks(matches, user)
    if not matches:
        return {"error": "Vehicle not found", "summary": None, "series": []}
    vehicle_id = str(matches[0].get("id") or "")
    if not vehicle_id:
        return {"name": matches[0].get("name"), "summary": None, "series": []}
    summary = await _wh_reader.get_vehicle_usage_summary(
        user["account_id"], vehicle_id, days=days,
    )
    series = await _wh_reader.get_vehicle_metrics_daily(
        user["account_id"], vehicle_id=vehicle_id, days=days,
    )
    return {
        "name":       matches[0].get("name"),
        "vehicle_id": vehicle_id,
        "days":       days,
        "summary":    summary,
        "series":     series,
    }


# ── Registry management (add / edit / remove vehicles) ───────────
#
# The vehicle registry in our DB is the single source of truth.  These
# write endpoints let an operator add a truck or trailer by hand —
# including equipment Samsara doesn't report — and are gated by the
# delegatable ``can_manage_vehicles`` permission (Owner/Admin/Fleet by
# default; grantable to any role via the Permissions matrix).  Reads
# come through the merged ``GET /vehicles/`` list, which already carries
# ``vehicle_type`` / ``source`` / ``registry_id`` per row.

_manage_vehicles = require_permission("can_manage_vehicles")


class VehicleCreate(BaseModel):
    unit_number: str = Field(..., min_length=1, max_length=64)
    vehicle_type: str = Field("truck")
    company_code: str = Field("", max_length=64)
    vin: str = Field("", max_length=64)
    plate_number: str = Field("", max_length=32)
    make: str = Field("", max_length=64)
    model: str = Field("", max_length=64)
    year: int | None = Field(None, ge=1900, le=2100)
    notes: str = Field("", max_length=500)


class VehicleUpdate(BaseModel):
    unit_number: str | None = Field(None, min_length=1, max_length=64)
    vehicle_type: str | None = None
    company_code: str | None = Field(None, max_length=64)
    vin: str | None = Field(None, max_length=64)
    plate_number: str | None = Field(None, max_length=32)
    make: str | None = Field(None, max_length=64)
    model: str | None = Field(None, max_length=64)
    year: int | None = Field(None, ge=1900, le=2100)
    status: str | None = None
    notes: str | None = Field(None, max_length=500)


def _vehicle_to_dict(v) -> dict:
    return {
        "id": v.id, "company_code": v.company_code,
        "unit_number": v.unit_number, "vehicle_type": v.vehicle_type,
        "vin": v.vin, "plate_number": v.plate_number, "make": v.make,
        "model": v.model, "year": v.year, "status": v.status,
        "source": v.source, "notes": v.notes,
    }


@router.post("/")
async def create_vehicle(
    body: VehicleCreate,
    user: dict = Depends(_manage_vehicles),
):
    """Add a vehicle to the registry by hand (truck, trailer, or other).
    Works with no telematics — the row stands on its own and is enriched
    later if an integration matches it."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    try:
        vid = await tenant.add_vehicle(
            account_id,
            unit_number=body.unit_number,
            vehicle_type=body.vehicle_type,
            company_code=body.company_code,
            vin=body.vin, plate_number=body.plate_number,
            make=body.make, model=body.model, year=body.year,
            notes=body.notes, source="manual",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        # UNIQUE(account_id, company_code, unit_number) collision, etc.
        raise HTTPException(409, f"could not add vehicle: {e}")
    v = await tenant.get_vehicle(account_id, vid)
    return _vehicle_to_dict(v)


@router.put("/registry/{vehicle_id}")
async def update_registry_vehicle(
    vehicle_id: int,
    body: VehicleUpdate,
    user: dict = Depends(_manage_vehicles),
):
    """Edit a registry vehicle's spec / type / status."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        ok = await tenant.update_vehicle(account_id, vehicle_id, **fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "vehicle not found")
    return _vehicle_to_dict(await tenant.get_vehicle(account_id, vehicle_id))


@router.delete("/registry/{vehicle_id}")
async def delete_registry_vehicle(
    vehicle_id: int,
    user: dict = Depends(_manage_vehicles),
):
    """Soft-delete a registry vehicle.  History (maintenance, fuel,
    inspections referencing the unit by name) is untouched."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    ok = await tenant.deactivate_vehicle(account_id, vehicle_id)
    if not ok:
        raise HTTPException(404, "vehicle not found")
    return {"deactivated": True, "id": vehicle_id}
