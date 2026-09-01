"""Geofence zone API endpoints.

Geofences are part of fleet operations, so they live under /fleet/geofences
to match the sidebar grouping. Legacy /map/geofences aliases are kept hidden
from the schema for backwards-compat with old bookmarks.

URL history: was /fleet/geofences (+legacy /map/geofences) until 2026-06-11; now /geofences.
"""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may;
# service/alert/ai_tool/signal modules never do.


from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from interfaces.api.deps import (
    require_permission,
    require_permission_any,
    get_user_company_codes,
    validate_company_access,
    filter_by_allowed_companies,
    resolve_user_id,
)
from features.geofencing.geometry import geofence_shape_type as _geofence_shape_type
from interfaces.bot.state import get_tenant_db

router = APIRouter(prefix="/geofences", tags=["geofences"])

_MILES_TO_METERS = 1609.344


class GeofenceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    geofence_type: str = Field(default="custom", max_length=50)
    shape_type: str = Field(default="circle", pattern="^(circle|polygon)$")
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_miles: Optional[float] = Field(default=None, gt=0, le=500)
    zone_role: Optional[str] = Field(default=None, max_length=50)


@router.get("")
async def list_geofences(
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_view_geofence")),
):
    """Geofence polygons for map overlay."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    from features.geofencing.service import get_geofences as _svc_geofences
    geofences = await _svc_geofences(user["account_id"], company=company)
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
                        "source": "samsara",
                    },
                })
            continue

        vertices = gf.get("geofence", {}).get("polygon", {}).get("vertices", [])
        if not vertices:
            continue

        coords = [[v.get("longitude", 0), v.get("latitude", 0)] for v in vertices]
        if coords and coords[0] != coords[-1]:
            coords.append(coords[0])

        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [coords]},
            "properties": {
                "name": gf.get("name", ""),
                "type": "polygon",
                "company": gf.get("_org", ""),
                "source": "samsara",
            },
        })

    try:
        tenant = await get_tenant_db(user["account_id"])
        platform_zones = await tenant.get_platform_geofences(user["account_id"], is_active=True)
        for z in platform_zones:
            if z["shape_type"] == "circle":
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [z.get("longitude") or 0, z.get("latitude") or 0],
                    },
                    "properties": {
                        "id": z.get("id"),
                        "name": z["name"],
                        "type": "circle",
                        "radius_meters": z.get("radius_meters") or 0,
                        "geofence_type": z.get("geofence_type", "custom"),
                        "zone_role": z.get("zone_role", "all"),
                        "source": "platform",
                    },
                })
            else:
                verts = z.get("vertices") or []
                coords = [
                    [v.get("lng", v.get("longitude", 0)), v.get("lat", v.get("latitude", 0))]
                    for v in verts
                ]
                if coords and coords[0] != coords[-1]:
                    coords.append(coords[0])
                if len(coords) >= 4:
                    features.append({
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": [coords]},
                        "properties": {
                            "id": z.get("id"),
                            "name": z["name"],
                            "type": "polygon",
                            "geofence_type": z.get("geofence_type", "custom"),
                            "zone_role": z.get("zone_role", "all"),
                            "source": "platform",
                        },
                    })
    except Exception:
        pass

    return {
        "type": "FeatureCollection",
        "features": features,
    }


@router.post("", status_code=201)
async def create_geofence(
    body: GeofenceCreateRequest,
    user: dict = Depends(require_permission("can_manage_geofence")),
):
    """Create a new platform-owned geofence zone."""
    if body.shape_type == "circle":
        if body.latitude is None or body.longitude is None or body.radius_miles is None:
            raise HTTPException(status_code=422, detail="Circle zones require latitude, longitude, and radius_miles")

    user_role = user.get("role", "owner")
    valid_zone_roles = {"owner", "admin", "fleet", "safety", "dispatcher", "driver", "all"}
    if user_role == "owner":
        zone_role = body.zone_role or "all"
    else:
        zone_role = user_role
    if zone_role not in valid_zone_roles:
        raise HTTPException(status_code=422, detail=f"Invalid zone_role: {zone_role!r}")

    tenant = await get_tenant_db(user["account_id"])
    radius_meters = round(body.radius_miles * _MILES_TO_METERS) if body.radius_miles else None
    try:
        zone_id = await tenant.add_platform_geofence(
            account_id=user["account_id"],
            name=body.name,
            shape_type=body.shape_type,
            geofence_type=body.geofence_type,
            latitude=body.latitude,
            longitude=body.longitude,
            radius_meters=radius_meters,
            zone_role=zone_role,
            created_by=await resolve_user_id(user),
        )
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail=f"A zone named '{body.name}' already exists")
        raise HTTPException(status_code=500, detail="Failed to create zone")

    return {
        "id": zone_id,
        "name": body.name,
        "geofence_type": body.geofence_type,
        "shape_type": body.shape_type,
        "radius_meters": radius_meters,
        "radius_miles": body.radius_miles,
        "zone_role": zone_role,
    }


@router.delete("/{zone_id}", status_code=200)
async def delete_geofence(
    zone_id: int,
    user: dict = Depends(require_permission("can_manage_geofence")),
):
    """Soft-delete a platform-owned geofence zone."""
    tenant = await get_tenant_db(user["account_id"])
    zone = await tenant.get_platform_geofence_by_id(user["account_id"], zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    ok = await tenant.delete_platform_geofence(user["account_id"], zone_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete zone")
    return {"deleted": zone_id}
