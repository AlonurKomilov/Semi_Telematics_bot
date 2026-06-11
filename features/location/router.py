"""Map vehicle position endpoints — current positions and live updates.

Other map data lives in dedicated routers:
    /map/pois, /map/custom-layers/*   → routes/pois.py
    /fleet/geofences/*                → routes/geofences.py
"""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): ONLY router.py may import interfaces.api.deps;
# service/alert/tool/signal modules never do.


from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from interfaces.api.deps import (
    require_permission_any,
    get_user_company_codes,
    validate_company_access,
    filter_by_allowed_companies,
    filter_by_assigned_trucks,
)
from infra.services import get_client
from features.location.service import classify_vehicle_status, get_fleet_for_map

router = APIRouter(prefix="/map", tags=["map"])


@router.get("/vehicles")
async def map_vehicles(
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_location_map", "can_location_vehicle")),
):
    """Current positions for all vehicles — optimized for map rendering.

    Drivers (``can_location_vehicle`` only) get the same payload but the
    response is restricted to their assigned truck(s) by
    ``filter_by_assigned_trucks`` below, so the miniapp can render a
    map for them too.
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    # get_fleet_for_map merges real CAN-bus engineStates onto each vehicle
    # so classify_vehicle_status can distinguish On/Idle/Off authoritatively
    # rather than guessing from speed.
    vehicles = await get_fleet_for_map(user["account_id"], company=company)
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
        # Prefer real engineState merged in by get_fleet_for_map; only fall
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
    user: dict = Depends(require_permission_any("can_location_map", "can_location_vehicle")),
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

    if user.get("_matched_perm") == "can_location_vehicle":
        from interfaces.api.deps import get_user_vehicle_nums
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
