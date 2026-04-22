"""Map data API endpoints — positions, routes, geofences."""

from fastapi import APIRouter, Depends, Query

from interfaces.api.deps import require_permission, get_user_company_codes, validate_company_access, filter_by_allowed_companies, filter_by_assigned_trucks
from core.services import get_client
from capabilities.location.service import classify_vehicle_status
from capabilities.geofencing.geometry import geofence_shape_type as _geofence_shape_type

router = APIRouter(prefix="/map", tags=["map"])


@router.get("/vehicles")
async def map_vehicles(
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_location_map")),
):
    """Current positions for all vehicles — optimized for map rendering."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])
    vehicles = await client.get_fleet_overview(company=company)
    vehicles = filter_by_allowed_companies(vehicles, allowed)
    vehicles = await filter_by_assigned_trucks(vehicles, user)
    features = []
    for v in vehicles:
        lat = v.get("latitude")
        lng = v.get("longitude")
        if lat is None or lng is None:
            continue
        speed = v.get("speed_mph", 0) or 0
        engine = v.get("engineState", "Off")
        status = classify_vehicle_status(v)

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "id": v.get("id"),
                "name": v.get("name", ""),
                "company": v.get("_org", ""),
                "speed_mph": speed,
                "address": v.get("formattedAddress", ""),
                "engine_state": engine,
                "status": status,
                "fuel_percent": v.get("fuelPercent"),
                "heading": v.get("heading"),
                "updated_at": v.get("time", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
    }


@router.get("/geofences")
async def map_geofences(
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_geofence_all")),
):
    """Geofence polygons for map overlay."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])
    geofences = await client.get_geofences(company=company)
    geofences = filter_by_allowed_companies(geofences, allowed)
    features = []
    for gf in geofences:
        shape = _geofence_shape_type(gf)
        if shape == "circle":
            circle = gf.get("geofence", {}).get("circle", {}) or gf.get("circularGeofence", {})
            if circle:
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [
                            circle.get("longitude", 0),
                            circle.get("latitude", 0),
                        ],
                    },
                    "properties": {
                        "name": gf.get("name", ""),
                        "type": "circle",
                        "radius_meters": circle.get("radiusMeters", 0),
                        "company": gf.get("_org", ""),
                    },
                })
            continue

        vertices = gf.get("geofence", {}).get("polygon", {}).get("vertices", [])
        if not vertices:
            continue

        coords = [[v.get("longitude", 0), v.get("latitude", 0)] for v in vertices]
        # Close the polygon
        if coords and coords[0] != coords[-1]:
            coords.append(coords[0])

        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [coords]},
            "properties": {
                "name": gf.get("name", ""),
                "type": "polygon",
                "company": gf.get("_org", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
    }
