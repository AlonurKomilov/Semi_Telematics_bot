"""Dashboard overview stats endpoint — role-aware."""

from fastapi import APIRouter, Depends, Query

from api.deps import get_current_user, get_tenant_db, get_user_truck_nums, get_user_company_codes, validate_company_access, filter_by_allowed_companies
from bot.config import get_client
from permissions import can

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
    client = await get_client(account_id)

    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    overview = await client.get_fleet_overview(company=company)
    overview = filter_by_allowed_companies(overview, allowed)

    # ── Driver: own truck only ──────────────────────────────────
    if role == "driver":
        trucks = await get_user_truck_nums(user)
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
                    "Moving" if my_truck and my_truck.get("speed_mph", 0) and my_truck["speed_mph"] > 0
                    else "Idle" if my_truck and my_truck.get("engineState") == "On"
                    else "Stopped"
                ) if my_truck else "Unknown",
                "speed_mph": round(my_truck.get("speed_mph", 0) or 0, 1) if my_truck else 0,
                "fuel_pct": my_truck.get("fuelPercent") if my_truck else None,
                "location": my_truck.get("location", "") if my_truck else "",
                "faults": len(my_truck.get("faults", [])) if my_truck else 0,
                "company": my_truck.get("_org", "") if my_truck else "",
            } if True else None,
            "my_alerts": len(my_alerts),
            "fleet": {"total": 1 if my_truck else 0, "moving": 0, "idle": 0, "stopped": 0},
        }

    # ── All other roles: fleet-wide stats ───────────────────────
    total = len(overview)
    moving = sum(1 for v in overview if v.get("speed_mph", 0) and v["speed_mph"] > 0)
    idle = sum(
        1 for v in overview
        if v.get("engineState") == "On" and not v.get("speed_mph")
    )
    stopped = total - moving - idle

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
    if can(role, "can_faults"):
        result["faults"] = sum(1 for v in overview if v.get("faults"))

    # Low fuel — visible to roles with can_fuel
    if can(role, "can_fuel"):
        result["low_fuel"] = sum(
            1 for v in overview
            if v.get("fuelPercent") is not None and v["fuelPercent"] < 20
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
