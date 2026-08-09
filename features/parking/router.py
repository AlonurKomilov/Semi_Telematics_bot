"""Parking safety API endpoints.

URL history: the utilisation heatmap was /fleet/utilisation/heatmap until 2026-06-11; now /parking/utilisation/heatmap.
"""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may;
# service/alert/ai_tool/signal modules never do.


from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import Response

from adapters.storage.object_storage import get_object_storage_for_account

from interfaces.api import deps as _deps
from interfaces.api.deps import (
    require_permission_any, get_tenant_db, get_user_vehicle_nums,
    get_user_company_codes, filter_by_allowed_companies,
)

from features.parking import service as parking_service

router = APIRouter(prefix="/parking", tags=["parking"])

_parking_read = require_permission_any("can_parking_all", "can_parking_vehicle")


async def _scope(user: dict):
    """``(company_codes, truck_names, scope)`` for this caller — see
    ``features.parking.service`` for what each value means.  ``scope`` is
    the identity ladder; ``truck_names`` is kept for the callers that
    still pass it down to the query layer."""
    return await parking_service.scope_for(user, _deps)

# No ``_PROJECT_ROOT`` here any more.  This router used to resolve stored
# paths itself and chained FOUR dirname calls where three were needed,
# landing on the project's PARENT — which is why every parking map
# answered 404 from the day it shipped, behind a permanent "Loading
# map…" that hid it.  The same off-by-one widened the traversal guard to
# the whole parent directory.  The map route now reads through the object
# store, which owns the only project root and enforces containment
# centrally, so the constant is gone rather than corrected.


@router.get("/active")
async def active_parking(
    attention_only: bool = Query(True, description="Only unsafe/unknown events"),
    vehicle: str | None = Query(None, description="Filter by vehicle name (substring)"),
    user: dict = Depends(require_permission_any("can_parking_all", "can_parking_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Get all active (unresolved) parking events.

    Always queries the UNFILTERED set and narrows in Python, so the
    response can carry ``total_active`` beside ``count``.  The UI defaults
    to ``attention_only`` and previously showed only the filtered number,
    which contradicted the nav counters ("6 events" under a "Parked
    unsafely 18" chip) with nothing on screen explaining the gap.  Same
    single query either way — no extra cost for the second number.
    """
    company_codes, truck_names, scope = await _scope(user)
    result = await parking_service.get_events(
        user["account_id"],
        include_resolved=False,
        attention_only=attention_only,
        company_codes=company_codes,
        truck_names=truck_names,
        scope=scope,
        vehicle=vehicle,
    )
    # ``total_active`` is this endpoint's historical name for "everything
    # parked you may see, before the attention narrowing".
    return {
        "events": result["events"],
        "count": result["count"],
        "total_active": result["total"],
    }


@router.get("/vehicle/{vehicle_id}/history")
async def parking_vehicle_history(
    vehicle_id: str,
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(_parking_read),
    tenant_db=Depends(get_tenant_db),
):
    """One vehicle's parking events — the detail drawer's history panel.

    Scoped through the same predicate as every other read, so passing
    another company's (or another driver's) vehicle_id returns nothing
    rather than that vehicle's record.

    ``tenant_db`` is declared but unused: its side effect is opening the
    RLS ``with_account`` context, which every other route on this router
    gets.  ``parking_events`` carries no RLS policy today, so the
    explicit ``WHERE account_id = ?`` in the adapter is what actually
    isolates this read — but a route silently missing the GUC would
    become the one hole the day RLS is extended to this table.
    """
    company_codes, truck_names, scope = await _scope(user)
    events = await parking_service.get_vehicle_history(
        user["account_id"], vehicle_id,
        company_codes=company_codes, truck_names=truck_names, scope=scope, limit=limit,
    )
    return {"events": events, "count": len(events)}


@router.get("/history")
async def parking_history(
    days: int = Query(7, ge=1, le=90),
    vehicle: str | None = Query(None, description="Filter by vehicle name (substring)"),
    location_class: str | None = Query(None, description="Filter: safe, unsafe, unknown"),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_permission_any("can_parking_all", "can_parking_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Get resolved parking event history."""
    events = await tenant_db.get_parking_history(
        user["account_id"], days=days, limit=limit,
    )
    company_codes, truck_names, scope = await _scope(user)
    events = parking_service.scope_events(
        events, company_codes=company_codes, truck_names=truck_names, scope=scope,
    )
    if vehicle:
        q = vehicle.lower()
        events = [e for e in events if q in (e.get("vehicle_name") or "").lower()]
    if location_class:
        events = [e for e in events if e.get("location_class") == location_class]
    return {"events": events, "count": len(events)}


@router.get("/{event_id}")
async def parking_detail(
    event_id: int,
    user: dict = Depends(require_permission_any("can_parking_all", "can_parking_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Get a single parking event by ID."""
    event = await tenant_db.get_parking_event_by_id(event_id, account_id=user["account_id"])
    if not event:
        raise HTTPException(status_code=404, detail="Parking event not found")
    company_codes, truck_names, scope = await _scope(user)
    if not parking_service.is_visible(
        event, company_codes=company_codes, truck_names=truck_names, scope=scope,
    ):
        raise HTTPException(status_code=404, detail="Parking event not found")
    return event


@router.post("/{event_id}/resolve")
async def resolve_parking(
    event_id: int,
    user: dict = Depends(require_permission_any("can_parking_all", "can_parking_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Manually resolve a parking event from the web UI."""
    event = await tenant_db.get_parking_event_by_id(event_id, account_id=user["account_id"])
    if not event:
        raise HTTPException(status_code=404, detail="Parking event not found")
    company_codes, truck_names, scope = await _scope(user)
    if not parking_service.is_visible(
        event, company_codes=company_codes, truck_names=truck_names, scope=scope,
    ):
        raise HTTPException(status_code=404, detail="Parking event not found")
    if event.get("resolved"):
        raise HTTPException(status_code=400, detail="Event already resolved")
    # By id, not by vehicle: the button the operator pressed belongs to
    # ONE event, and the vehicle-scoped call closed every unresolved row
    # for that vehicle.
    await tenant_db.resolve_parking_event_by_id(user["account_id"], event_id)
    return {"status": "resolved", "event_id": event_id}


@router.get("/stats/summary")
async def parking_stats(
    user: dict = Depends(require_permission_any("can_parking_all", "can_parking_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Summary stats for parking events (used by Overview page)."""
    all_active = await tenant_db.get_active_parking_events(
        user["account_id"], attention_only=False,
    )
    # Previously truck-scoped only: a company-restricted caller's summary
    # counted every company's trucks.
    company_codes, truck_names, scope = await _scope(user)
    all_active = parking_service.scope_events(
        all_active, company_codes=company_codes, truck_names=truck_names, scope=scope,
    )
    unsafe = sum(1 for e in all_active if e.get("location_class") == "unsafe")
    unknown = sum(1 for e in all_active if e.get("location_class") == "unknown")
    # STRUCTURALLY ALWAYS 0 — not a bug, and not something to "fix" into
    # counting.  features/parking/check.py resolves and returns before the
    # insert for geofence stops, safe-keyword stops and AI-confirmed-safe
    # stops, so no safe row can reach this table.  Kept so the response
    # shape stays stable, and so the zero is explained where it is read.
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
    user: dict = Depends(require_permission_any("can_parking_all", "can_parking_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Serve the AI-analyzed satellite/road map image for a parking event."""
    event = await tenant_db.get_parking_event_by_id(event_id, account_id=user["account_id"])
    if not event:
        raise HTTPException(status_code=404, detail="Parking event not found")
    # Previously truck-scoped only, so a company-restricted caller was
    # blocked from an event's DETAIL while the photo of the same stop was
    # still served.  Same predicate as the detail route now.
    company_codes, truck_names, scope = await _scope(user)
    if not parking_service.is_visible(
        event, company_codes=company_codes, truck_names=truck_names, scope=scope,
    ):
        raise HTTPException(status_code=404, detail="Parking event not found")
    img_path = event.get("map_image_path", "")
    if not img_path:
        raise HTTPException(status_code=404, detail="No map image available")
    # A legacy row whose file a later stop overwrote must not serve that
    # file: the address and AI reasoning beside it belong to THIS event,
    # so a map of somewhere else reads as this event's location.  410 (not
    # 404) because the snapshot genuinely existed and is gone — the client
    # says so rather than implying none was ever taken.
    if await tenant_db.map_image_overwritten_by_newer(user["account_id"], event_id):
        raise HTTPException(
            status_code=410,
            detail="Snapshot was overwritten by a later stop for this vehicle",
        )
    # Read THROUGH the object store rather than off the filesystem: on a
    # ``gdrive`` account there is no local copy, and on ``hybrid`` the
    # sync worker deletes the local copy once the file reaches Drive, so
    # a disk-only read 404s the moment either backend is in play.
    # ``get_by_id`` takes a stored path OR a Drive id, so one call is
    # correct on all three.  Path-traversal containment moved INTO the
    # store (_disk_path_candidates), where it also covers the get_by_id
    # callers — inspections, drivers, knowledge — that never had it.
    store = await get_object_storage_for_account(user["account_id"], tenant_db)
    data = store.get_by_id(img_path)
    if data is None:
        raise HTTPException(status_code=404, detail="Map image file not found")
    return Response(content=data, media_type="image/png")


# ═══ Utilisation heatmap (a Live-Map overlay served from THIS
# feature's data — parking_events) ════════════════════════════════
from interfaces.api.deps import require_permission  # noqa: F811

@router.get("/utilisation/heatmap")
async def fleet_utilisation_heatmap(
    days: int = Query(30, ge=1, le=90),
    user: dict = Depends(require_permission("can_vehicle_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Aggregate fleet activity points for the Owner / Admin Live Map
    ``UtilisationHeatmap`` overlay.

    Built from ``parking_events`` — every stop a truck makes is a
    sample of "where this fleet operates."  Each point's weight is
    the ``duration_hours`` of that stop (capped at 24h to keep one
    long weekend stop from drowning out a busy weekday).  The result
    is a [[lat, lon, weight], ...] array sized for ``leaflet.heat`` —
    same wire shape the Safety event heatmap uses, so the frontend
    overlay reuses the heatLayer wiring.

    Permission-gated by ``can_vehicle_all`` — only roles whose job
    spans the full fleet (Owner / Admin / Fleet / Safety / Dispatch
    today) see utilisation density.  Driver / HR / Accounting either
    don't reach the Live Map or have their own persona overlay.

    That permission alone is NOT sufficient scope.  Company restriction
    is a per-USER assignment (Team Management) that is orthogonal to
    role: ``get_user_company_codes`` auto-unrestricts only ``owner``, so
    a company-restricted Fleet/Dispatch/Safety user holds
    ``can_vehicle_all`` and still must not see another company's
    density.  Points are aggregated, but "where do that company's trucks
    park" is exactly what the restriction exists to withhold.  Truck
    scope is deliberately NOT applied — this permission means the caller
    legitimately sees every vehicle in the companies they can see.
    """
    # 24h cap on a single stop's weight.  Without this, a 72-hour
    # weekend yard-stop shows up 3× brighter than a 24h Friday
    # parking, which makes the heatmap visually misleading
    # ("everyone's at the yard!") when really it's just one truck.
    MAX_HOURS_PER_POINT = 24.0

    rows = await tenant_db.get_parking_points_for_heatmap(
        user["account_id"], days=days,
    )
    rows = parking_service.scope_events(
        rows,
        company_codes=await get_user_company_codes(user),
        truck_names=None,
    )
    points: list[list[float]] = []
    for r in rows:
        lat = float(r.get("latitude") or 0)
        lon = float(r.get("longitude") or 0)
        weight = min(float(r.get("duration_hours") or 0), MAX_HOURS_PER_POINT)
        if weight <= 0:
            continue
        points.append([lat, lon, weight])
    return {"points": points, "count": len(points), "days": days}


