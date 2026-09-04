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

from fastapi import APIRouter, Depends, Query

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
        from interfaces.api.deps import get_user_vehicle_nums, require_permission
        trucks = await get_user_vehicle_nums(user)
        if not trucks:
            return {"positions": {}}
        needles = [t.lower() for t in trucks]
        id_to_name = {str(v.get("id")): (v.get("name") or "").lower() for v in location_raw}
        positions = {
            vid: pos for vid, pos in positions.items()
            if any(n in id_to_name.get(vid, "") for n in needles)
        }

    return {"positions": positions}
