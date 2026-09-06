"""Map vehicle position endpoints — current positions and live updates.

Other map data lives in dedicated routers:
    /map/pois, /map/custom-layers/*   → routes/pois.py
    /fleet/geofences/*                → routes/geofences.py
"""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may;
# service/alert/ai_tool/signal modules never do.


from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from interfaces.api.deps import (
    member_unit_scope,
    require_permission, require_permission_any,
    get_user_company_codes,
    validate_company_access,
    filter_by_allowed_companies,
    filter_by_assigned_trucks,
)
from infra.services import get_client
from features.location.service import classify_vehicle_status, get_vehicles_for_map

router = APIRouter(prefix="/map", tags=["map"])


@router.get("/vehicles")
async def map_vehicles(
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_view_location")),
):
    """Current positions for all vehicles — optimized for map rendering.

    Assigned-width members (``can_view_location``, unit width 'assigned') get the same payload but the
    response is restricted to their assigned truck(s) by
    ``filter_by_assigned_trucks`` below, so the miniapp can render a
    map for them too.
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    # get_vehicles_for_map merges real CAN-bus engineStates onto each vehicle
    # so classify_vehicle_status can distinguish On/Idle/Off authoritatively
    # rather than guessing from speed.
    vehicles = await get_vehicles_for_map(user["account_id"], company=company)
    vehicles = filter_by_allowed_companies(vehicles, allowed)
    vehicles = await filter_by_assigned_trucks(vehicles, user)
    features = []
    for v in vehicles:
        loc = v.get("location", {})
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        if lat is None or lng is None:
            continue
        # Skip phantom (0, 0) GPS — Samsara returns this for trucks that have
        # lost GPS lock; rendering them puts a marker off the coast of Africa.
        if abs(lat) < 0.01 and abs(lng) < 0.01:
            continue
        # Prefer explicit mph field; only fall back to raw `speed` (which may be
        # m/s on some payload variants) when speedMilesPerHour is genuinely absent.
        speed_mph = loc.get("speedMilesPerHour")
        speed = float(speed_mph if speed_mph is not None else (loc.get("speed") or 0))
        status = classify_vehicle_status(v)
        # Prefer real engineState merged in by get_vehicles_for_map; only fall
        # back to deriving it from status when the Samsara plan or a transient
        # error left the field empty.
        engine_state = v.get("engineState") or (
            "On" if status == "moving" else "Idle" if status == "idle" else "Off"
        )
        address = (
            loc.get("reverseGeo", {}).get("formattedLocation")
            or loc.get("address")
            or ""
        )
        fuel = v.get("fuel", {})
        fuel_pct = fuel.get("value") if isinstance(fuel, dict) else None
        def_level = v.get("def_level", {})
        def_pct = def_level.get("value") if isinstance(def_level, dict) else None
        dtcs = v.get("activeFaultCodes") or v.get("active_fault_codes") or []
        fault_count = len(dtcs) if isinstance(dtcs, list) else 0

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "id": v.get("id"),
                "name": v.get("name", ""),
                "company": v.get("_org", ""),
                # The registry row's own id — what a provider-link lookup
                # is keyed by.  NOT the provider's vehicle id, which is
                # what ``id`` above carries.
                "registry_id": v.get("_registry_id"),
                # Who supplies this record.  Already on the row from the
                # registry merge, so carrying it costs nothing and saves
                # every map surface a second request: the dashboard's
                # Live Map and the browser extension both draw the
                # provenance beside the unit number, the same way the
                # vehicle page does.  ``source`` is the creator,
                # ``sources`` is creator-then-enrichers.
                "source": v.get("source") or "",
                "sources": list(v.get("sources") or []),
                "speed_mph": speed,
                "address": address,
                "engine_state": engine_state,
                "status": status,
                "fuel_percent": fuel_pct,
                "def_percent": def_pct,
                "fault_count": fault_count,
                "heading": loc.get("heading"),
                "updated_at": loc.get("time", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
    }


@router.get("/engine")
async def map_engine(
    user: dict = Depends(require_permission("can_view_location")),
):
    """Which map this account's live map is drawn on, and what it needs.

    Asked once when a map mounts, by every surface that draws one — the
    dashboard Live Map today, the browser panel next.  Behind
    ``can_view_location`` because it is part of drawing the map, and
    because the Google key it may carry is billable: public by design
    and referrer-restricted, but not something to hand an anonymous
    caller.

    The answer can differ from what the account asked for; it says both,
    so a settings page can explain itself.  See
    features/location/map_engine.py for why it fails to the free engine.
    """
    from features.location.map_engine import for_account
    from infra.platform import get_tenant_db

    account_id = int(user["account_id"])
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        # No tenant DB is no reason to draw no map.
        from features.location.map_engine import OSM, ENGINES, google_available
        return {"engine": OSM, "requested": OSM, "engines": list(ENGINES),
                "google_available": google_available()}
    return await for_account(account_id, tenant)


@router.get("/tiles/session")
async def map_tiles_session(
    type: str = Query("roadmap"),
    user: dict = Depends(require_permission("can_view_location")),
):
    """A Google Map Tiles session for one map type, for a browser that
    is about to add the layer.

    Refused for an account whose engine is not Google: a session is a
    billable thing to hand out, and an account on the free engine has
    no use for one.  When Google itself refuses — the key, the API not
    enabled, the network — the answer is 503 with the reason, and the
    map falls back to the free engine rather than staying blank.
    """
    from features.location import map_engine
    from infra.platform import get_tenant_db

    if type not in map_engine.TILE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"unknown tile type {type!r}; one of {list(map_engine.TILE_TYPES)}")
    account_id = int(user["account_id"])
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(status_code=503, detail="tenant DB unavailable")
    engine = await map_engine.for_account(account_id, tenant)
    if engine["engine"] != map_engine.GOOGLE:
        raise HTTPException(
            status_code=403,
            detail="This account draws its map on OpenStreetMap.")
    try:
        entry = await map_engine.tile_session(type, engine["key"])
    except map_engine.TileSessionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return map_engine.tile_wire(type, entry, engine["key"])


@router.get("/vehicles/live")
async def map_vehicles_live(
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_view_location")),
):
    """Lightweight position-only update for smooth live tracking.

    Uses _run_per_company to call get_locations() on each underlying
    SamsaraClient in parallel, suitable for 5-second polling.
    Returns id -> {lat, lng, speed_mph, heading, updated_at}.
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])

    async def _get_locs(c):
        return await c.get_locations()

    per_co = await client._run_per_company(_get_locs, company=company)
    location_raw: list = []
    for locs in per_co.values():
        if isinstance(locs, list):
            location_raw.extend(locs)

    positions: dict = {}
    for v in location_raw:
        vid = v.get("id")
        loc = v.get("location", {})
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        if lat is None or lng is None or vid is None:
            continue
        speed = float(loc.get("speedMilesPerHour") or loc.get("speed") or 0)
        positions[str(vid)] = {
            "lat": lat,
            "lng": lng,
            "speed_mph": speed,
            "heading": loc.get("heading"),
            "updated_at": loc.get("time", ""),
        }

    # Width, asked of the width layer.  This used to read
    # ``_matched_perm`` — the flag require_permission_any happened
    # to match — which encoded "wide grant absent" as a side effect
    # of dependency ordering.  member_unit_scope asks it directly
    # and additionally honours a member-level override.
    if await member_unit_scope(user, "location") == "assigned":
        from interfaces.api.deps import get_user_vehicle_nums
        from infra.platform import get_router as _get_router
        from capabilities.permissions.vehicle_scope import build_vehicle_scope
        trucks = await get_user_vehicle_nums(user)
        if not trucks:
            return {"positions": {}}
        # Membership by the identity ladder, never by substring.  This
        # endpoint compared an assignment string against the display
        # name with ``in``, so an assignment of "1" admitted 110, 128
        # and 101, and "230" admitted 2303 — the exact over-match the
        # ladder was written to end, still live here because the fix
        # landed on the LIST endpoint next door and this one kept its
        # own hand-rolled filter.  These are the rows a narrowed member
        # is ALLOWED to see, so an over-match is a disclosure.
        #
        # The raw provider payload carries no registry id, so rung 1
        # never fires here: the provider's own vehicle id decides, and a
        # truck the registry has not linked yet falls to its exact name.
        #
        # This endpoint is why the scope holds one identity PER VEHICLE
        # rather than three pooled sets.  Pooled, one linked truck's
        # provider id chose the rung for every row, so a driver holding
        # one linked and one unlinked truck lost the unlinked one — here
        # only, because every other surface has a registry id to answer
        # on rung 1.  Pinned in
        # features/location/tests/test_live_positions_scope.py.
        account_id = int(user["account_id"])
        tenant = await _get_router().get_tenant(account_id)
        scope = await build_vehicle_scope(tenant, account_id, trucks)
        if scope.empty:
            return {"positions": {}}
        allowed_ids = {
            str(v.get("id")) for v in location_raw
            if v.get("id") is not None and scope.allows_row(v)
        }
        positions = {vid: pos for vid, pos in positions.items() if vid in allowed_ids}

    return {"positions": positions}
