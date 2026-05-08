"""Fleet operational view endpoints.

URL structure (category/feature, matches sidebar grouping):
    GET /api/fleet/overview         raw fleet snapshot (admin/fleet/safety roles)
    GET /api/fleet/overview/stats   aggregated overview-page stats (role-aware)
    GET /api/fleet/weather          ambient temperature readings from sensors

Per-vehicle data (list, detail, health, faults, timeline) lives in the
canonical vehicle resource at /api/vehicles/* — see routes/vehicles.py.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query

from interfaces.api.deps import (
    require_permission,
    get_current_user,
    get_user_company_codes,
    get_user_vehicle_nums,
    get_tenant_db,
    validate_company_access,
    filter_by_allowed_companies,
    filter_by_assigned_trucks,
)
from capabilities.iam.permissions import can
from capabilities.vehicles.service import get_fleet_overview as _svc_fleet_overview
from capabilities.telemetry.service import get_fleet_weather as _svc_fleet_weather
from capabilities.telemetry import warehouse_reader as _wh_reader
from capabilities.location.service import classify_vehicle_status, get_fleet_for_map

router = APIRouter(prefix="/fleet", tags=["fleet"])


@router.get("/overview")
async def fleet_overview(
    company: str | None = Query(None, description="Filter by company code"),
    user: dict = Depends(require_permission("can_faults")),
):
    """Fleet snapshot — vehicles with status, location, faults."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    async def _live():
        return await _svc_fleet_overview(user["account_id"], company=company)
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


@router.get("/overview/stats")
async def overview_stats(
    company: str | None = Query(None),
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Aggregated fleet stats for the overview page (role-aware)."""
    account_id = user["account_id"]
    role = user.get("role", "driver")

    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)

    # Fan out the four independent reads concurrently. Without this, the
    # Samsara fetch (~200-500ms) blocks the three DB reads (~30-50ms each),
    # forcing the user to wait ~350ms instead of ~250ms for the Overview page.
    # Skip the role-gated calls so a driver doesn't open three connections
    # only to discard the results.
    fetch_alerts = (
        role == "driver"
        or can(role, "can_alerts_all")
        or can(role, "can_alerts_own")
    )
    fetch_parking = can(role, "can_alerts_all") or can(role, "can_alerts_own")
    fetch_maintenance = can(role, "can_maintenance_all")

    overview, pending_alerts, all_parked, maint_tasks = await asyncio.gather(
        get_fleet_for_map(account_id, company=company),
        tenant_db.get_pending_alerts(account_id) if fetch_alerts else asyncio.sleep(0, result=[]),
        tenant_db.get_active_parking_events(account_id, attention_only=False) if fetch_parking else asyncio.sleep(0, result=[]),
        tenant_db.get_maintenance_tasks(account_id, status="pending") if fetch_maintenance else asyncio.sleep(0, result=[]),
    )
    overview = filter_by_allowed_companies(overview, allowed)

    if role == "driver":
        trucks = await get_user_vehicle_nums(user)
        truck_num = trucks[0] if trucks else None
        my_truck = None
        if trucks:
            needles = [t.lower() for t in trucks]
            my_truck = next(
                (v for v in overview if any(n in v.get("name", "").lower() for n in needles)),
                None,
            )

        my_alerts = []
        if trucks and pending_alerts:
            needles = [t.lower() for t in trucks]
            my_alerts = [a for a in pending_alerts if any(n in (a.get("vehicle_name") or "").lower() for n in needles)]

        return {
            "role": "driver",
            "truck_num": truck_num,
            "my_truck": {
                "name": my_truck.get("name", truck_num or "—") if my_truck else (truck_num or "—"),
                "status": (
                    {"moving": "Moving", "idle": "Idle", "stopped": "Stopped"}.get(
                        classify_vehicle_status(my_truck), "Unknown"
                    )
                ) if my_truck else "Unknown",
                "speed_mph": round(
                    my_truck.get("location", {}).get("speedMilesPerHour")
                    or my_truck.get("location", {}).get("speed")
                    or 0, 1
                ) if my_truck else 0,
                "fuel_pct": (
                    my_truck.get("fuel", {}).get("value")
                    if isinstance(my_truck.get("fuel"), dict)
                    else my_truck.get("fuel")
                ) if my_truck else None,
                "location": (
                    my_truck.get("location", {}).get("reverseGeo", {}).get("formattedLocation")
                    or my_truck.get("location", {}).get("address")
                    or ""
                ) if my_truck else "",
                "faults": len(
                    my_truck.get("fault_codes", {}).get("j1939", {}).get("diagnosticTroubleCodes", [])
                ) if my_truck else 0,
                "company": my_truck.get("_org", "") if my_truck else "",
            },
            "my_alerts": len(my_alerts),
            "fleet": {"total": 1 if my_truck else 0, "moving": 0, "idle": 0, "stopped": 0},
        }

    total = len(overview)
    moving = idle = stopped = 0
    for v in overview:
        s = classify_vehicle_status(v)
        if s == "moving":
            moving += 1
        elif s == "idle":
            idle += 1
        else:
            stopped += 1

    result: dict = {
        "role": role,
        "fleet": {"total": total, "moving": moving, "idle": idle, "stopped": stopped},
    }

    if can(role, "can_faults"):
        result["faults"] = sum(
            1 for v in overview
            if v.get("fault_codes", {}).get("j1939", {}).get("diagnosticTroubleCodes")
        )

    if can(role, "can_fuel"):
        result["low_fuel"] = sum(
            1 for v in overview
            if isinstance(v.get("fuel"), dict)
            and v["fuel"].get("value") is not None
            and v["fuel"]["value"] < 20
        )

    if fetch_parking:
        result["pending_alerts"] = len(pending_alerts) if pending_alerts else 0
        result["unsafe_parking"] = sum(1 for e in all_parked if e.get("location_class") == "unsafe")
        result["unknown_parking"] = sum(1 for e in all_parked if e.get("location_class") == "unknown")

    if fetch_maintenance:
        result["maintenance_due"] = len(maint_tasks) if maint_tasks else 0

    return result


legacy_router = APIRouter(tags=["fleet"], include_in_schema=False)


@legacy_router.get("/overview/fleet")
async def _legacy_fleet_overview(
    company: str | None = Query(None, description="Filter by company code"),
    user: dict = Depends(require_permission("can_faults")),
):
    return await fleet_overview(company=company, user=user)


@legacy_router.get("/weather/fleet")
async def _legacy_fleet_weather(
    user: dict = Depends(require_permission("can_faults")),
):
    return await fleet_weather(user=user)


@legacy_router.get("/dashboard/stats")
async def _legacy_dashboard_stats(
    company: str | None = Query(None),
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    return await overview_stats(company=company, user=user, tenant_db=tenant_db)
