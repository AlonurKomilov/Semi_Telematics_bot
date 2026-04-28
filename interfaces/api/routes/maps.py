"""Map data API endpoints — positions, routes, geofences, POI overlays."""

import time
from typing import Optional
import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from interfaces.api.deps import require_permission, require_permission_any, get_user_company_codes, validate_company_access, filter_by_allowed_companies, filter_by_assigned_trucks
from core.services import get_client
from capabilities.location.service import classify_vehicle_status
from capabilities.geofencing.geometry import geofence_shape_type as _geofence_shape_type
from interfaces.bot.config import get_tenant_db

router = APIRouter(prefix="/map", tags=["map"])

_MILES_TO_METERS = 1609.344

class GeofenceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    geofence_type: str = Field(default="custom", max_length=50)
    shape_type: str = Field(default="circle", pattern="^(circle|polygon)$")
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_miles: Optional[float] = Field(default=None, gt=0, le=500)
    # zone_role: which team owns this zone. Owner can set explicitly;
    # all other roles have it forced server-side from their own role.
    zone_role: Optional[str] = Field(default=None, max_length=50)


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
        # Raw Samsara data: lat/lng are nested inside v["location"], NOT at top level.
        loc = v.get("location", {})
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        if lat is None or lng is None:
            continue
        speed = float(loc.get("speedMilesPerHour") or loc.get("speed") or 0)
        status = classify_vehicle_status(v)
        engine_state = "On" if status == "moving" else "Idle" if status == "idle" else "Off"
        address = (
            loc.get("reverseGeo", {}).get("formattedLocation")
            or loc.get("address")
            or ""
        )
        fuel = v.get("fuel", {})
        fuel_pct = fuel.get("value") if isinstance(fuel, dict) else None
        def_level = v.get("def_level", {})
        def_pct = def_level.get("value") if isinstance(def_level, dict) else None

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
    user: dict = Depends(require_permission("can_location_map")),
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
    return {"positions": positions}


@router.get("/geofences")
async def map_geofences(
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_geofence_all", "can_geofence_own")),
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
                        "source": "samsara",
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
                "source": "samsara",
            },
        })

    # Merge platform-owned zones
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
        pass  # Platform zones are additive — never break the Samsara response

    return {
        "type": "FeatureCollection",
        "features": features,
    }


@router.post("/geofences", status_code=201)
async def create_geofence(
    body: GeofenceCreateRequest,
    user: dict = Depends(require_permission("can_geofence_all")),
):
    """Create a new platform-owned geofence zone."""
    if body.shape_type == "circle":
        if body.latitude is None or body.longitude is None or body.radius_miles is None:
            raise HTTPException(status_code=422, detail="Circle zones require latitude, longitude, and radius_miles")

    # Role-based zone attribution:
    # Only owner can set the zone_role explicitly.  All other roles are forced
    # to their own role so zones are isolated per team (role-based audit).
    user_role = user.get("role", "owner")
    valid_zone_roles = {"owner", "admin", "fleet", "safety", "dispatcher", "driver", "all"}
    if user_role == "owner":
        zone_role = body.zone_role or "all"
    else:
        zone_role = user_role  # auto-attributed — non-owner cannot override
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
            created_by=int(user["sub"]),
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


@router.delete("/geofences/{zone_id}", status_code=200)
async def delete_geofence(
    zone_id: int,
    user: dict = Depends(require_permission("can_geofence_all")),
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


# ── POI Overlay Layers ────────────────────────────────────────────────────────
#
# To ADD a new POI layer:
#   1. Add an entry to POI_OVERPASS_QUERIES below.
#   2. Add the matching entry to poiLayers.ts in the dashboard config.
#   Nothing else changes — the endpoint and the hook handle the rest.
#
# To REMOVE a layer: delete the entry here and in poiLayers.ts.
#
# Overpass API returns all nodes/ways matching the filter inside the given
# bounding box.  Results are cached server-side (in _poi_cache) for 5 minutes
# per layer+bbox so repeated map pans are fast and don't hammer Overpass.
#
# Each entry is a LIST of Overpass filter expressions — all are unioned into
# one request.  Single-element lists are the common case; multi-element lists
# let a layer combine several OSM tags (e.g. truck_stop).

POI_OVERPASS_QUERIES: dict[str, list[str]] = {
    # ── Fuel Stations (diesel-capable) ────────────────────────────────────────
    # All stations that sell diesel to commercial trucks — from a rural Chevron
    # to a full-service Pilot Plaza.  Includes amenity=truck_stop (full plazas)
    # since OSM tagging is inconsistent: many big chains are mapped as
    # amenity=fuel with brand=Pilot rather than amenity=truck_stop.
    #
    # The frontend applies brand sub-filters client-side so drivers can narrow
    # to specific chains.  DEF availability passes through as the fuel:adblue
    # tag; the dedicated def_station layer below filters by it.
    "fuel_station": [
        'node["amenity"="fuel"]["fuel:diesel"="yes"]',
        'node["amenity"="fuel"]["hgv"="yes"]',
        'nwr["amenity"="truck_stop"]',
    ],
    # ── DEF / AdBlue Stations ─────────────────────────────────────────────────
    # Diesel Exhaust Fluid (AdBlue) — without it, SCR-equipped trucks derate.
    # Filtered subset of fuel stations where fuel:adblue=yes is explicitly tagged.
    # Many truck plazas have DEF but lack the OSM tag; users can still use the
    # Fuel Stations layer's brand filter for known DEF-providing chains.
    "def_station": [
        'node["amenity"="fuel"]["fuel:adblue"="yes"]',
        'nwr["amenity"="truck_stop"]["fuel:adblue"="yes"]',
    ],
    # ── Truck parking ─────────────────────────────────────────────────────────
    # HOS Hours-of-Service compliance — drivers need designated parking before
    # their clock runs out.  Excludes truck_stop (already in fuel_station layer).
    # Uses node only — way/relation queries silently timeout on large bboxes.
    "truck_parking": [
        'node["amenity"="parking"]["truck"="yes"]["amenity"!="truck_stop"]',
        'node["amenity"="parking"]["hgv"="yes"]["amenity"!="truck_stop"]',
        'node["amenity"="parking"]["access:hgv"~"yes|designated"]["amenity"!="truck_stop"]',
    ],
    # ── Showers ───────────────────────────────────────────────────────────────
    # Driver shower facilities.  OSM has very few standalone amenity=shower
    # nodes — most showers are tagged as shower=yes on truck stops or fuel
    # stations.  We pick up both patterns.
    "shower": [
        'node["amenity"="shower"]',
        'nwr["amenity"="truck_stop"]["shower"="yes"]',
        'node["amenity"="fuel"]["shower"="yes"]',
    ],
    # ── Rest areas ────────────────────────────────────────────────────────────
    # Designated government-maintained rest stops on US interstates and highways.
    # highway=rest_area is the primary OSM tag; amenity=rest_area is a common alt.
    # Excludes amenity=truck_stop to prevent truck plazas from bleeding in.
    "rest_area": [
        'node["highway"="rest_area"]["amenity"!="truck_stop"]',
        'nwr["amenity"="rest_area"]["amenity"!="truck_stop"]',
    ],
    # ── Weigh stations ────────────────────────────────────────────────────────
    # DOT/FMCSA compliance checkpoints.  In US OSM, weigh stations are almost
    # never tagged highway=weigh_station — they are consistently mapped as
    # highway=motorway_junction nodes whose name contains "Weigh Station".
    # "Port of Entry" is included — functionally identical for DOT purposes.
    "weigh_station": [
        'node["highway"="weigh_station"]',
        'node["name"~"^Weigh Station|Weigh Station$|Port of Entry",i]',
    ],
    # ─── Add new types above this line ────────────────────────────────────────
}

# Simple in-process cache: (poi_type, bbox_rounded) → (timestamp, features)
# Reset on every import so query changes in POI_OVERPASS_QUERIES take effect
# immediately when the server restarts rather than serving stale empty results.
_poi_cache: dict[tuple[str, str], tuple[float, list]] = {}
_POI_CACHE_TTL = 300  # seconds

# ── HTTP session — reused across requests to avoid per-call connection overhead
# Lazily created on first use; safe in asyncio single-threaded model.
_http_session: aiohttp.ClientSession | None = None

# Overpass mirror list — tried in order; first successful response wins.
_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


async def _get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


def _round_bbox(bbox: str, precision: int = 2) -> str:
    """Round bbox coords so nearby requests share cache entries."""
    parts = bbox.split(",")
    if len(parts) != 4:
        return bbox
    return ",".join(f"{float(p):.{precision}f}" for p in parts)


_MAX_POI_RESULTS = 1000  # cap per request to keep map usable at any zoom level

# Maximum bbox area in square degrees.
# The contiguous US is roughly 35°×70° = 2450 sq degrees so we set the cap
# comfortably above that. Overpass's own [timeout:25][maxsize:4000000] directives
# handle genuinely huge/slow queries — we just block requests that are plainly
# unreasonable (e.g. entire hemisphere).
_MAX_BBOX_AREA = 5000.0  # roughly North America


async def _fetch_overpass(query_parts: list[str], bbox: str) -> list[dict]:
    """Fetch nodes/ways from Overpass API inside bbox and return GeoJSON features.

    Uses a shared aiohttp session and falls back through _OVERPASS_ENDPOINTS on
    error.  Uses 'out center;' so way/relation centroids are returned alongside
    node lat/lon — all element types produce a usable coordinate.

    Results are capped at _MAX_POI_RESULTS so large-area queries remain fast.
    """
    # Build union query: each filter expression gets the bbox appended,
    # then all are unioned in a single Overpass request.
    parts_str = "\n  ".join(f"{p}({bbox});" for p in query_parts)
    overpass_query = (
        f"[out:json][timeout:25][maxsize:4000000];\n"
        f"(\n  {parts_str}\n);\n"
        f"out center;"
    )

    session = await _get_http_session()
    last_exc: Exception = RuntimeError("No Overpass endpoint reachable")

    data: dict = {}
    for endpoint in _OVERPASS_ENDPOINTS:
        try:
            async with session.post(
                endpoint,
                data=overpass_query,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    last_exc = RuntimeError(f"Overpass returned HTTP {resp.status}")
                    continue
                data = await resp.json(content_type=None)
            break  # success — stop trying mirrors
        except Exception as exc:
            last_exc = exc
            continue
    else:
        raise last_exc

    features = []
    for element in data.get("elements", []):
        # Nodes expose lat/lon directly; ways and relations expose center.lat/center.lon
        center = element.get("center") or {}
        lat = element.get("lat") if element.get("lat") is not None else center.get("lat")
        lon = element.get("lon") if element.get("lon") is not None else center.get("lon")
        if lat is None or lon is None:
            continue
        tags = element.get("tags", {})
        # Pick the most human-readable name available
        display_name = (
            tags.get("name")
            or tags.get("brand")
            or tags.get("operator")
            or ""
        )
        # Collect service-relevant tags for the popup
        _SERVICE_TAGS = (
            "amenity", "highway", "brand", "operator",
            "phone", "website", "opening_hours",
            "fuel:diesel", "fuel:adblue", "fuel:HGV_diesel",
            "hgv", "truck", "shower", "toilets",
            "capacity", "fee",
        )
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "name": display_name,
                "osm_id": element.get("id"),
                **{k: v for k, v in tags.items() if k in _SERVICE_TAGS},
            },
        })
        if len(features) >= _MAX_POI_RESULTS:
            break

    return features


@router.get("/pois")
async def map_pois(
    poi_type: str = Query(..., alias="type", min_length=1, max_length=50),
    bbox: str = Query(..., description="south,west,north,east"),
    user: dict = Depends(require_permission("can_location_map")),
):
    """
    POI overlay data for map layers.

    Returns a GeoJSON FeatureCollection of points for the requested layer type
    within the given bounding box.  Results are cached for 5 minutes.

    Supported types are defined in POI_OVERPASS_QUERIES.  Unknown types return
    an empty FeatureCollection rather than an error so the frontend degrades
    gracefully.
    """
    if poi_type not in POI_OVERPASS_QUERIES:
        # Unknown type — return empty rather than 400 so UI degrades gracefully
        return {"type": "FeatureCollection", "features": []}

    bbox_parts = bbox.split(",")
    if len(bbox_parts) != 4:
        raise HTTPException(status_code=422, detail="bbox must be south,west,north,east")

    # Validate coordinate ranges and reject unreasonably large bboxes
    try:
        s, w, n, e = (float(x) for x in bbox_parts)
    except ValueError:
        raise HTTPException(status_code=422, detail="bbox values must be numeric")
    if not (-90 <= s < n <= 90) or not (-180 <= w <= 180) or not (-180 <= e <= 180):
        raise HTTPException(status_code=422, detail="bbox coordinates out of valid range")
    if (n - s) * abs(e - w) > _MAX_BBOX_AREA:
        raise HTTPException(
            status_code=422,
            detail="bbox area too large — zoom in to load POI layers",
        )

    bbox_key = _round_bbox(bbox)
    cache_key = (poi_type, bbox_key)
    now = time.monotonic()

    # Cache hit?
    if cache_key in _poi_cache:
        ts, features = _poi_cache[cache_key]
        if now - ts < _POI_CACHE_TTL:
            return {"type": "FeatureCollection", "features": features}

    query_parts = POI_OVERPASS_QUERIES[poi_type]
    try:
        features = await _fetch_overpass(query_parts, bbox)
    except Exception:
        features = []

    _poi_cache[cache_key] = (now, features)

    # Prune old cache entries (keep memory bounded)
    if len(_poi_cache) > 500:
        oldest = sorted(_poi_cache.items(), key=lambda x: x[1][0])
        for k, _ in oldest[:100]:
            del _poi_cache[k]

    return {"type": "FeatureCollection", "features": features}

