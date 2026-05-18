"""Parking safety API endpoints."""

import os

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import FileResponse

from interfaces.api.deps import require_permission_any, get_tenant_db, get_user_vehicle_nums

router = APIRouter(prefix="/parking", tags=["parking"])

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@router.get("/active")
async def active_parking(
    attention_only: bool = Query(True, description="Only unsafe/unknown events"),
    vehicle: str | None = Query(None, description="Filter by vehicle name (substring)"),
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own", "can_vehicle_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Get all active (unresolved) parking events."""
    events = await tenant_db.get_active_parking_events(
        user["account_id"], attention_only=attention_only,
    )
    if user.get("_matched_perm") == "can_alerts_own":
        trucks = await get_user_vehicle_nums(user)
        if trucks:
            needles = {t.lower() for t in trucks}
            events = [e for e in events if any(n in (e.get("vehicle_name") or "").lower() for n in needles)]
    if vehicle:
        q = vehicle.lower()
        events = [e for e in events if q in (e.get("vehicle_name") or "").lower()]
    return {"events": events, "count": len(events)}


@router.get("/history")
async def parking_history(
    days: int = Query(7, ge=1, le=90),
    vehicle: str | None = Query(None, description="Filter by vehicle name (substring)"),
    location_class: str | None = Query(None, description="Filter: safe, unsafe, unknown"),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own", "can_vehicle_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Get resolved parking event history."""
    events = await tenant_db.get_parking_history(
        user["account_id"], days=days, limit=limit,
    )
    if user.get("_matched_perm") == "can_alerts_own":
        trucks = await get_user_vehicle_nums(user)
        if trucks:
            needles = {t.lower() for t in trucks}
            events = [e for e in events if any(n in (e.get("vehicle_name") or "").lower() for n in needles)]
    if vehicle:
        q = vehicle.lower()
        events = [e for e in events if q in (e.get("vehicle_name") or "").lower()]
    if location_class:
        events = [e for e in events if e.get("location_class") == location_class]
    return {"events": events, "count": len(events)}


@router.get("/{event_id}")
async def parking_detail(
    event_id: int,
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own", "can_vehicle_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Get a single parking event by ID."""
    event = await tenant_db.get_parking_event_by_id(event_id, account_id=user["account_id"])
    if not event:
        raise HTTPException(status_code=404, detail="Parking event not found")
    # Drivers (``can_alerts_own``) may only inspect events for trucks
    # they are assigned to.  Without this check a driver could enumerate
    # the rest of the fleet's parking detail by guessing event ids.
    if user.get("_matched_perm") == "can_alerts_own":
        trucks = await get_user_vehicle_nums(user)
        ev_name = (event.get("vehicle_name") or "").lower()
        if not trucks or not any(t.lower() in ev_name for t in trucks):
            raise HTTPException(status_code=404, detail="Parking event not found")
    return event


@router.post("/{event_id}/resolve")
async def resolve_parking(
    event_id: int,
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own", "can_vehicle_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Manually resolve a parking event from the web UI."""
    event = await tenant_db.get_parking_event_by_id(event_id, account_id=user["account_id"])
    if not event:
        raise HTTPException(status_code=404, detail="Parking event not found")
    if user.get("_matched_perm") == "can_alerts_own":
        trucks = await get_user_vehicle_nums(user)
        ev_name = (event.get("vehicle_name") or "").lower()
        if not trucks or not any(t.lower() in ev_name for t in trucks):
            raise HTTPException(status_code=404, detail="Parking event not found")
    if event.get("resolved"):
        raise HTTPException(status_code=400, detail="Event already resolved")
    await tenant_db.resolve_parking_event(user["account_id"], event["vehicle_id"])
    return {"status": "resolved", "event_id": event_id}


@router.get("/stats/summary")
async def parking_stats(
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own", "can_vehicle_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Summary stats for parking events (used by Overview page)."""
    all_active = await tenant_db.get_active_parking_events(
        user["account_id"], attention_only=False,
    )
    if user.get("_matched_perm") == "can_alerts_own":
        trucks = await get_user_vehicle_nums(user)
        if trucks:
            needles = {t.lower() for t in trucks}
            all_active = [e for e in all_active if any(n in (e.get("vehicle_name") or "").lower() for n in needles)]
    unsafe = sum(1 for e in all_active if e.get("location_class") == "unsafe")
    unknown = sum(1 for e in all_active if e.get("location_class") == "unknown")
    safe = sum(1 for e in all_active if e.get("location_class") in ("safe", "geofence"))
    return {
        "total_parked": len(all_active),
        "unsafe": unsafe,
        "unknown": unknown,
        "safe": safe,
    }


@router.get("/{event_id}/map-image")
async def parking_map_image(
    event_id: int,
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own", "can_vehicle_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Serve the AI-analyzed satellite/road map image for a parking event."""
    event = await tenant_db.get_parking_event_by_id(event_id, account_id=user["account_id"])
    if not event:
        raise HTTPException(status_code=404, detail="Parking event not found")
    if user.get("_matched_perm") == "can_alerts_own":
        trucks = await get_user_vehicle_nums(user)
        ev_name = (event.get("vehicle_name") or "").lower()
        if not trucks or not any(t.lower() in ev_name for t in trucks):
            raise HTTPException(status_code=404, detail="Parking event not found")
    img_path = event.get("map_image_path", "")
    if not img_path:
        raise HTTPException(status_code=404, detail="No map image available")
    full_path = os.path.realpath(os.path.join(_PROJECT_ROOT, img_path))
    if not full_path.startswith(os.path.realpath(_PROJECT_ROOT)):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="Map image file not found")
    return FileResponse(full_path, media_type="image/png")
