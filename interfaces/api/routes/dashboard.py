"""Dashboard overview stats endpoint — role-aware."""

from fastapi import APIRouter, Depends, Query

from interfaces.api.deps import get_current_user, get_tenant_db, get_user_vehicle_nums, get_user_company_codes, validate_company_access, filter_by_allowed_companies
from capabilities.iam.permissions import can
from capabilities.location.service import classify_vehicle_status, get_fleet_for_map

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats")
async def dashboard_stats(
    company: str | None = Query(None),
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Aggregated fleet stats for the overview page.

    Returns role-appropriate data:
    - owner/admin/fleet: full fleet overview + management stats
    - safety: safety-focused stats
    - dispatcher: location/route focused stats
    - driver: own truck only
    """
    account_id = user["account_id"]
    role = user.get("role", "driver")

    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    # Use get_fleet_for_map (not client.get_fleet_overview) so each vehicle
    # has the authoritative CAN-bus engineState merged in — without it the
    # Idle bucket is always 0 because fleet_overview alone exposes only
    # speed, and a parked truck with the engine running looks identical
    # to a fully off truck.
    overview = await get_fleet_for_map(account_id, company=company)
    overview = filter_by_allowed_companies(overview, allowed)

    # ── Driver: own truck only ──────────────────────────────────
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

        # Get driver's own alerts
        all_alerts = await tenant_db.get_pending_alerts(account_id)
        my_alerts = []
        if trucks and all_alerts:
            needles = [t.lower() for t in trucks]
            my_alerts = [a for a in all_alerts if any(n in (a.get("vehicle_name") or "").lower() for n in needles)]

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
            } if True else None,
            "my_alerts": len(my_alerts),
            "fleet": {"total": 1 if my_truck else 0, "moving": 0, "idle": 0, "stopped": 0},
        }

    # ── All other roles: fleet-wide stats ───────────────────────
    total = len(overview)

    # Use the same authoritative classifier as the live map: prefers the
    # CAN-bus engineState ('On'/'Idle'/'Off') merged in by get_fleet_for_map,
    # falls back to a speed heuristic.  Without this Idle was always 0 because
    # fleet_overview alone exposes only speed, so a parked truck with the
    # engine running was indistinguishable from a fully off truck.
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
        "fleet": {
            "total": total,
            "moving": moving,
            "idle": idle,
            "stopped": stopped,
        },
    }

    # Faults — visible to owner, admin, fleet, safety
    # Raw data: v["fault_codes"]["j1939"]["diagnosticTroubleCodes"] (list)
    if can(role, "can_faults"):
        result["faults"] = sum(
            1 for v in overview
            if v.get("fault_codes", {}).get("j1939", {}).get("diagnosticTroubleCodes")
        )

    # Low fuel — visible to roles with can_fuel
    # Raw data: v["fuel"] = {"value": 45.3, "time": "..."}
    if can(role, "can_fuel"):
        result["low_fuel"] = sum(
            1 for v in overview
            if isinstance(v.get("fuel"), dict)
            and v["fuel"].get("value") is not None
            and v["fuel"]["value"] < 20
        )

    # Pending alerts
    if can(role, "can_alerts_all") or can(role, "can_alerts_own"):
        pending_alerts = await tenant_db.get_pending_alerts(account_id)
        result["pending_alerts"] = len(pending_alerts) if pending_alerts else 0

    # Parking safety stats
    if can(role, "can_alerts_all") or can(role, "can_alerts_own"):
        all_parked = await tenant_db.get_active_parking_events(account_id, attention_only=False)
        unsafe_parked = sum(1 for e in all_parked if e.get("location_class") == "unsafe")
        unknown_parked = sum(1 for e in all_parked if e.get("location_class") == "unknown")
        result["unsafe_parking"] = unsafe_parked
        result["unknown_parking"] = unknown_parked

    # Maintenance due count — visible to roles with maintenance access
    if can(role, "can_maintenance_all"):
        tasks = await tenant_db.get_maintenance_tasks(account_id, status="pending")
        result["maintenance_due"] = len(tasks) if tasks else 0

    return result
