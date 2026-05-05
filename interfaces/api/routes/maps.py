"""Map data API endpoints — positions, routes, geofences, POI overlays."""

from __future__ import annotations

import csv as _csv
import io
import logging
import re
from typing import Optional
import aiohttp
from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from interfaces.api.deps import require_permission, require_permission_any, get_user_company_codes, validate_company_access, filter_by_allowed_companies, filter_by_assigned_trucks
from infra.services import get_client
from capabilities.location.service import classify_vehicle_status, get_fleet_for_map
from capabilities.geofencing.geometry import geofence_shape_type as _geofence_shape_type
from interfaces.bot.config import get_tenant_db

logger = logging.getLogger(__name__)

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
    user: dict = Depends(require_permission_any("can_location_map", "can_location_own")),
):
    """Current positions for all vehicles — optimized for map rendering.

    Drivers (``can_location_own`` only) get the same payload but the
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
        # Raw Samsara data: lat/lng are nested inside v["location"], NOT at top level.
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
    user: dict = Depends(require_permission_any("can_location_map", "can_location_own")),
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

    # Driver role (can_location_own only) \u2014 restrict to their assigned
    # truck name(s).  filter_by_assigned_trucks expects a list of dicts
    # with a name key, so adapt locally rather than reshape.
    if user.get("_matched_perm") == "can_location_own":
        from interfaces.api.deps import get_user_vehicle_nums
        trucks = await get_user_vehicle_nums(user)
        if not trucks:
            return {"positions": {}}
        needles = [t.lower() for t in trucks]
        # Map id\u2192name from location_raw so we can keep only positions
        # whose vehicle name matches one of the user's trucks.
        id_to_name = {str(v.get("id")): (v.get("name") or "").lower() for v in location_raw}
        positions = {
            vid: pos for vid, pos in positions.items()
            if any(n in id_to_name.get(vid, "") for n in needles)
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
    from capabilities.geofencing.service import get_geofences as _svc_geofences
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
    # to a full-service Pilot Plaza.
    #
    # COVERAGE STRATEGY:
    #   The OSM tags fuel:diesel=yes and hgv=yes have ~30%/~20% coverage in
    #   the US, so tag-based queries alone miss most major-chain stations.
    #   We supplement with a brand allowlist that catches every node tagged
    #   with the major chains (every location of which sells truck diesel).
    #   Without this, the Pilot/Flying J chip filter would show ~25 results
    #   for the entire CONUS instead of the real ~750.
    #
    # The frontend applies brand sub-filters client-side so drivers can narrow
    # to specific chains.  DEF availability passes through as the fuel:adblue
    # tag; the dedicated def_station layer below filters by it.
    "fuel_station": [
        # Tag-based: most accurate when present
        'node["amenity"="fuel"]["fuel:diesel"="yes"]',
        'node["amenity"="fuel"]["hgv"="yes"]',
        'nwr["amenity"="truck_stop"]',
        # Brand allowlist: guarantees major-chain coverage even when the
        # diesel/HGV tags are absent.  USES `node` ONLY (not `nwr`) — OSM
        # fuel stations are mapped as nodes ~99% of the time, and the area
        # variant (nwr) makes Overpass scan every relation/way in the bbox
        # which times out at CONUS scale.  Verified with live Overpass:
        # node-only returns 1500+ in 11s; nwr times out >60s.
        'node["amenity"="fuel"]["brand"~"^(Pilot|Flying J|Pilot Flying J|Love.s|TA|Petro|TravelCenters|Sapp Bros|Road Ranger|Kwik Trip|Kwik Star|Bosselman|Ambest)",i]',
    ],
    # ── DEF / AdBlue Stations ─────────────────────────────────────────────────
    # Diesel Exhaust Fluid (AdBlue) — without it, SCR-equipped trucks derate.
    #
    # COVERAGE STRATEGY:
    #   The fuel:adblue=yes OSM tag has ~15-25% coverage in the US, so a
    #   tag-only query misses thousands of known-DEF locations.  We add a
    #   brand allowlist for chains whose every location sells DEF per their
    #   corporate spec (Pilot/Flying J, Love's, TA, Petro, Sapp Bros, etc.).
    #   The CSV exporter on the frontend always reports DEF=Yes for rows
    #   from this layer regardless of upstream tag.
    "def_station": [
        # Explicitly tagged AdBlue (highest signal)
        'node["amenity"="fuel"]["fuel:adblue"="yes"]',
        'nwr["amenity"="truck_stop"]["fuel:adblue"="yes"]',
        # Known-DEF brand allowlist: every location of these chains is a
        # confirmed DEF provider.  Uses `node` (not `nwr`) on the fuel
        # variant for the same CONUS-timeout reason as fuel_station.
        'nwr["amenity"="truck_stop"]["brand"~"^(Pilot|Flying J|Pilot Flying J|Love.s|TA|Petro|TravelCenters|Sapp Bros|Road Ranger)",i]',
        'node["amenity"="fuel"]["brand"~"^(Pilot|Flying J|Pilot Flying J|Love.s|TA|Petro|TravelCenters|Sapp Bros|Road Ranger)",i]',
    ],
    # ── Truck parking ─────────────────────────────────────────────────────────
    # HOS Hours-of-Service compliance — drivers need designated parking before
    # their clock runs out.  Uses node only — way/relation queries silently
    # timeout on large bboxes.  (We do NOT exclude amenity=truck_stop here
    # because a node already filtered by amenity=parking cannot also be
    # amenity=truck_stop — the previous exclusion clauses were no-ops.)
    "truck_parking": [
        'node["amenity"="parking"]["truck"="yes"]',
        'node["amenity"="parking"]["hgv"="yes"]',
        'node["amenity"="parking"]["access:hgv"~"yes|designated"]',
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
    # DOT/FMCSA compliance checkpoints.  US OSM tagging audit (April 2026):
    #   - amenity=weighbridge   — PRIMARY tag, ~3800 nationwide
    #   - highway=weigh_station — secondary (mostly mapped as ways/relations)
    #   - amenity=vehicle_inspection — some Ports of Entry
    # The previous query used `node[highway=weigh_station]` which returns 0
    # nationwide because virtually no US weigh station is mapped that way.
    # Combined query reliably returns 4000+ across CONUS.
    "weigh_station": [
        'nwr["amenity"="weighbridge"]',
        'nwr["highway"="weigh_station"]',
        'nwr["amenity"="vehicle_inspection"]',
        # Catch motorway-junction nodes named like a weigh station (sparse,
        # but covers stations that haven't been re-tagged to amenity=weighbridge)
        'node["highway"="motorway_junction"]["name"~"Weigh Station|Weigh Sta|Port of Entry|Inspection Station",i]',
    ],
    # ─── Add new types above this line ────────────────────────────────────────
}

# Bounded in-process cache: (poi_type, bbox_rounded) → features.
# POI data is intentionally tenant-agnostic (public OSM data).  If/when
# tenant-specific layers are added (e.g. a customer-site overlay), the cache
# key MUST be widened to include account_id to prevent cross-tenant leak.
# TTLCache caps memory at ~500 entries × ~300 KB ≈ 150 MB worst case.
_POI_CACHE_TTL = 300  # seconds
_poi_cache: TTLCache = TTLCache(maxsize=500, ttl=_POI_CACHE_TTL)

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


_MAX_POI_RESULTS = 6000  # Cap per request.  Sized for CONUS-scale fuel
# (~5200 unique after dedup) and weigh stations (~4300).  Going higher
# ballooned response size > 2 MB without improving UX (clustering kicks in
# below zoom 6).  This cap matters for two paths:
#   (1) the CSV export at the visible bbox
#   (2) brand-chip filtering at zoom-out — if Pilot/FJ stations are dropped
#       by the cap, the chip would silently undercount them.

# Maximum bbox area in square degrees.
# The contiguous US is roughly 35°×70° = 2450 sq degrees so we set the cap
# comfortably above that. Overpass's own [timeout:25][maxsize:4000000] directives
# handle genuinely huge/slow queries — we just block requests that are plainly
# unreasonable (e.g. entire hemisphere).
_MAX_BBOX_AREA = 5000.0  # roughly North America


# ── USA-only POI restriction ──────────────────────────────────────────────────
#
# Per product requirement the dashboard should not display fuel stops, weigh
# stations, custom POIs, etc. that lie in Mexico, Canada, or the Caribbean
# even when the visible viewport extends beyond the US border.
#
# The clip is an axis-aligned bbox per US region.  We try CONUS first (covers
# ~99 % of fleet activity); if the requested viewport doesn't intersect CONUS
# we fall back to Alaska or Hawaii.  Anything outside all three returns an
# empty FeatureCollection without hitting Overpass.
#
# Bounds are slightly padded outward so highway POIs hugging the border
# (Detroit↔Windsor, Buffalo↔Niagara, San Diego↔Tijuana) still appear when the
# user is centred on the US side — Overpass natural filtering by tag values
# does the rest.
_USA_REGIONS: tuple[tuple[float, float, float, float], ...] = (
    # (south, west, north, east)
    (24.396308, -125.000000, 49.500000,  -66.500000),  # CONUS
    (51.000000, -179.500000, 71.500000, -129.000000),  # Alaska
    (18.500000, -161.000000, 22.500000, -154.500000),  # Hawaii
)


def _clip_bbox_to_usa(s: float, w: float, n: float, e: float) -> tuple[float, float, float, float] | None:
    """Intersect the requested bbox with the union of US regions.

    Returns the clipped (s,w,n,e) tuple of the FIRST region the bbox
    intersects, or ``None`` if the request lies entirely outside US bounds.
    Multi-region intersections (e.g. CONUS + Alaska in a single hemispheric
    view) are not supported — the caller would have been blocked by
    ``_MAX_BBOX_AREA`` long before this point.
    """
    for rs, rw, rn, re_ in _USA_REGIONS:
        cs, cw, cn, ce = max(s, rs), max(w, rw), min(n, rn), min(e, re_)
        if cs < cn and cw < ce:
            return cs, cw, cn, ce
    return None


def _bbox_to_str(s: float, w: float, n: float, e: float) -> str:
    return f"{s},{w},{n},{e}"


async def _fetch_overpass(query_parts: list[str], bbox: str) -> list[dict]:
    """Fetch nodes/ways from Overpass API inside bbox and return GeoJSON features.

    Uses a shared aiohttp session and falls back through _OVERPASS_ENDPOINTS on
    error.  Uses 'out center;' so way/relation centroids are returned alongside
    node lat/lon — all element types produce a usable coordinate.

    Results are capped at _MAX_POI_RESULTS so large-area queries remain fast.
    """
    # Build union query: each filter expression is restricted to the United
    # States administrative boundary (ISO3166-1=US, admin_level=2) AND the
    # caller's bbox.  Without the area filter Overpass returns features from
    # Canada / Mexico / Caribbean whenever the bbox extends across the border
    # — e.g. Buffalo↔Toronto, San Diego↔Tijuana — because the bbox is a plain
    # axis-aligned rectangle.  The area filter forces server-side clipping to
    # US territory (incl. Alaska, Hawaii, PR, etc.) and is far more accurate
    # than any client-side bbox heuristic.
    parts_str = "\n  ".join(f"{p}(area.us)({bbox});" for p in query_parts)
    overpass_query = (
        f"[out:json][timeout:25][maxsize:4000000];\n"
        f'area["ISO3166-1"="US"][admin_level=2]->.us;\n'
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
                timeout=aiohttp.ClientTimeout(total=90),
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
    seen_ids: set = set()
    for element in data.get("elements", []):
        # Dedupe by (type, id) — our union queries deliberately overlap
        # (a Pilot tagged fuel:diesel=yes also matches the brand allowlist)
        # so the same OSM element appears multiple times in the response.
        # Without dedup the duplicates eat the result cap and the CSV export
        # double-counts.
        oid_key = (element.get("type"), element.get("id"))
        if oid_key in seen_ids:
            continue
        seen_ids.add(oid_key)

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
    user: dict = Depends(require_permission_any("can_location_map", "can_location_own")),
):
    """
    POI overlay data for map layers.

    Returns a GeoJSON FeatureCollection of points for the requested layer type
    within the given bounding box.  Results are cached for 5 minutes.

    Built-in types are defined in POI_OVERPASS_QUERIES.  Custom per-tenant
    layers use ``type=custom_{id}`` and dispatch to either the Overpass
    pipeline (source_type=overpass) or the DB-points reader (source_type=csv).
    Unknown types return an empty FeatureCollection rather than an error so
    the frontend degrades gracefully.
    """
    bbox_parts = bbox.split(",")
    if len(bbox_parts) != 4:
        raise HTTPException(status_code=422, detail="bbox must be south,west,north,east")
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

    # ── USA-only restriction ───────────────────────────────────────────────
    # Clip the visible viewport to the US territory bounds before running
    # any query.  When the user pans into Mexico / Canada / Caribbean the
    # response is an empty FeatureCollection rather than fetching foreign
    # POIs that aren't relevant to a US trucking fleet.
    clipped = _clip_bbox_to_usa(s, w, n, e)
    if clipped is None:
        return {"type": "FeatureCollection", "features": []}
    cs, cw, cn, ce = clipped
    bbox = _bbox_to_str(cs, cw, cn, ce)
    s, w, n, e = cs, cw, cn, ce

    # ── Custom (per-tenant) layer dispatch ───────────────────────────
    if poi_type.startswith("custom_"):
        try:
            layer_id = int(poi_type.split("_", 1)[1])
        except (ValueError, IndexError):
            return {"type": "FeatureCollection", "features": []}
        return await _serve_custom_layer(
            user["account_id"], layer_id, bbox, (s, w, n, e),
        )

    # ── Built-in layer dispatch ───────────────────────────────────
    if poi_type not in POI_OVERPASS_QUERIES:
        return {"type": "FeatureCollection", "features": []}

    bbox_key = _round_bbox(bbox)
    cache_key = (poi_type, bbox_key)

    # Cache hit? (TTLCache handles expiry + size bound automatically.)
    cached = _poi_cache.get(cache_key)
    if cached is not None:
        return {"type": "FeatureCollection", "features": cached}

    query_parts = POI_OVERPASS_QUERIES[poi_type]
    try:
        features = await _fetch_overpass(query_parts, bbox)
    except Exception:
        features = []

    _poi_cache[cache_key] = features
    return {"type": "FeatureCollection", "features": features}



# ── Custom (per-tenant) POI layers ────────────────────────────────────────────
#
# Admins can register their own map overlay layers via 3 source types:
#
#   1. Pin-drop (brand sample) — UX shortcut.  Frontend sends a clicked lat/lng;
#      server reads the closest OSM POI's `brand` tag and auto-builds a CONUS
#      Overpass query for that brand.  Stored same as overpass-source.
#   2. Overpass query (advanced) — admin pastes a raw OSM filter expression.
#      Validated against a whitelist; wrapped server-side with the bbox + header.
#   3. CSV upload — static rows uploaded as (name, lat, lng, brand?).  Stored
#      in custom_poi_points and served from DB (no Overpass).
#
# The /pois endpoint above already dispatches `type=custom_{id}` to the helper
# below, so frontend code paths (5-tier cache, brand chips, render) all work
# unchanged.  Cache key uses ("custom_{id}", bbox) so a layer's data lives
# alongside built-in layers in the same _poi_cache.

# Hard cap on admin-supplied Overpass query length.  500 chars is generous
# (the longest built-in element is ~100) and prevents accidentally dragging
# in escaped DSL bombs.
_MAX_CUSTOM_OVERPASS_LEN = 500

# Whitelist of opening keywords for a custom Overpass element.  Anything else
# (relation, area, way[r], etc.) is rejected; we run multiple of these in
# parallel and they must all be a node/way/nwr filter we can prepend bbox to.
_OVERPASS_OPENERS_RE = re.compile(r"^\s*(node|way|nwr)\s*\[", re.IGNORECASE)

# CSV upload limits — protect server memory and DB row count.
_CSV_MAX_BYTES = 5 * 1024 * 1024     # 5 MB
_CSV_MAX_ROWS  = 50_000


def _validate_overpass_query(raw: str) -> str:
    """Validate an admin-supplied Overpass element.

    Returns the trimmed query if safe; raises HTTPException(422) otherwise.
    The result is intended to be inserted into _fetch_overpass's query_parts
    list — it must NOT include a bbox or trailing semicolon (we add those).
    """
    q = (raw or "").strip()
    if not q:
        raise HTTPException(status_code=422, detail="Overpass query is empty")
    if len(q) > _MAX_CUSTOM_OVERPASS_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Overpass query exceeds {_MAX_CUSTOM_OVERPASS_LEN} chars",
        )
    if not _OVERPASS_OPENERS_RE.match(q):
        raise HTTPException(
            status_code=422,
            detail="Overpass query must start with `node[...]`, `way[...]` or `nwr[...]`",
        )
    # Reject statements / multi-query — the only valid `;` is none (we add it).
    # Disallow `out`, `recurse`, comments, and any "out " prefix.
    lowered = q.lower()
    forbidden_tokens = (";", "out ", "(._;", "->.", "->_", "recurse", "/*", "//")
    for tok in forbidden_tokens:
        if tok in lowered:
            raise HTTPException(
                status_code=422,
                detail=f"Overpass query contains disallowed token: {tok!r}",
            )
    return q


def _layer_to_dto(layer: dict) -> dict:
    """Project a DB row to the frontend-facing layer DTO."""
    return {
        "id": int(layer["id"]),
        "layer_key": layer["layer_key"],
        "label": layer["label"],
        "color": layer["color"],
        "icon": layer["icon"],
        "source_type": layer["source_type"],
        "overpass_query": layer.get("overpass_query") or "",
        "brand_filters": layer.get("brand_filters") or [],
        "default_on": bool(layer.get("default_on")),
        "created_at": layer.get("created_at") or "",
        "updated_at": layer.get("updated_at") or "",
    }


async def _serve_custom_layer(
    account_id: int,
    layer_id: int,
    bbox: str,
    bbox_floats: tuple[float, float, float, float],
) -> dict:
    """Fetch + cache features for a single custom layer.  Always returns a
    GeoJSON FeatureCollection (empty on missing/inactive layer)."""
    tenant = await get_tenant_db(account_id)
    layer = await tenant.get_custom_poi_layer_by_id(account_id, layer_id)
    if not layer or not layer.get("is_active"):
        return {"type": "FeatureCollection", "features": []}

    cache_id = f"custom_{layer_id}"
    bbox_key = _round_bbox(bbox)
    cache_key = (cache_id, bbox_key)
    cached = _poi_cache.get(cache_key)
    if cached is not None:
        return {"type": "FeatureCollection", "features": cached}

    features: list[dict] = []
    src = layer["source_type"]

    if src == "csv":
        s, w, n, e = bbox_floats
        rows = await tenant.get_custom_poi_points(account_id, layer_id, bbox=(s, w, n, e))
        for r in rows:
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["lng"], r["lat"]]},
                "properties": {
                    "name": r.get("name") or layer["label"],
                    "brand": r.get("brand") or "",
                    "_custom": True,
                    **(r.get("properties") or {}),
                },
            })
    elif src == "overpass":
        query = (layer.get("overpass_query") or "").strip()
        if query:
            try:
                features = await _fetch_overpass([query], bbox)
            except Exception as exc:
                logger.warning("custom Overpass layer %s failed: %s", layer_id, exc)
                features = []

    _poi_cache[cache_key] = features
    return {"type": "FeatureCollection", "features": features}


# ── DTOs ──────────────────────────────────────────────────────────────────────

class _BrandFilterIn(BaseModel):
    value: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=64)
    icon: Optional[str] = Field(default=None, max_length=8)
    matchTerms: Optional[list[str]] = None


class _CustomLayerCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=80)
    color: str = Field(default="#3b82f6", pattern="^#[0-9a-fA-F]{6}$")
    icon: str = Field(default="📍", max_length=8)
    source_type: str = Field(..., pattern="^(overpass|csv)$")
    overpass_query: Optional[str] = Field(default=None, max_length=_MAX_CUSTOM_OVERPASS_LEN)
    brand_filters: Optional[list[_BrandFilterIn]] = None
    default_on: bool = False


class _CustomLayerPatch(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=80)
    color: Optional[str] = Field(default=None, pattern="^#[0-9a-fA-F]{6}$")
    icon: Optional[str] = Field(default=None, max_length=8)
    overpass_query: Optional[str] = Field(default=None, max_length=_MAX_CUSTOM_OVERPASS_LEN)
    brand_filters: Optional[list[_BrandFilterIn]] = None
    default_on: Optional[bool] = None


class _PinDropRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    label: Optional[str] = Field(default=None, min_length=1, max_length=80)
    color: Optional[str] = Field(default=None, pattern="^#[0-9a-fA-F]{6}$")
    icon: Optional[str] = Field(default=None, max_length=8)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/custom-layers")
async def list_custom_layers(
    user: dict = Depends(require_permission_any("can_location_map", "can_location_own")),
):
    """List active custom POI layers for the caller's account.  Any user
    with map access can read; only admins (can_manage_poi_layers) can write."""
    tenant = await get_tenant_db(user["account_id"])
    layers = await tenant.get_custom_poi_layers(user["account_id"], is_active=True)
    return {"layers": [_layer_to_dto(lyr) for lyr in layers]}


@router.post("/custom-layers", status_code=201)
async def create_custom_layer(
    body: _CustomLayerCreate,
    user: dict = Depends(require_permission("can_manage_poi_layers")),
):
    """Create a custom POI layer.  Pin-drop layers go through the dedicated
    /custom-layers/from-pin endpoint instead."""
    if body.source_type == "overpass":
        if not body.overpass_query:
            raise HTTPException(status_code=422, detail="overpass_query required for source_type=overpass")
        validated = _validate_overpass_query(body.overpass_query)
    else:
        validated = ""

    tenant = await get_tenant_db(user["account_id"])
    layer_key = f"user_{user['account_id']}_{int(__import__('time').time() * 1000)}"
    try:
        layer_id = await tenant.add_custom_poi_layer(
            user["account_id"],
            layer_key=layer_key,
            label=body.label,
            color=body.color,
            icon=body.icon,
            source_type=body.source_type,
            overpass_query=validated,
            brand_filters=[bf.model_dump() for bf in (body.brand_filters or [])],
            default_on=body.default_on,
            created_by=int(user.get("telegram_id") or 0),
        )
    except Exception as exc:
        # UNIQUE(account_id, label) collisions surface here
        raise HTTPException(status_code=409, detail=f"Could not create layer: {exc}")
    layer = await tenant.get_custom_poi_layer_by_id(user["account_id"], layer_id)
    return _layer_to_dto(layer)


@router.patch("/custom-layers/{layer_id}")
async def patch_custom_layer(
    layer_id: int,
    body: _CustomLayerPatch,
    user: dict = Depends(require_permission("can_manage_poi_layers")),
):
    """Rename / recolor / re-icon / toggle / refine query.  source_type
    is immutable — delete + recreate to switch sources."""
    tenant = await get_tenant_db(user["account_id"])
    layer = await tenant.get_custom_poi_layer_by_id(user["account_id"], layer_id)
    if not layer or not layer.get("is_active"):
        raise HTTPException(status_code=404, detail="Layer not found")

    overpass = body.overpass_query
    if overpass is not None:
        if layer["source_type"] != "overpass":
            raise HTTPException(status_code=422, detail="Cannot set overpass_query on non-overpass layer")
        overpass = _validate_overpass_query(overpass)

    ok = await tenant.update_custom_poi_layer(
        user["account_id"], layer_id,
        label=body.label,
        color=body.color,
        icon=body.icon,
        overpass_query=overpass,
        brand_filters=([bf.model_dump() for bf in body.brand_filters] if body.brand_filters is not None else None),
        default_on=body.default_on,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="No changes")

    # Bust the per-layer cache so the next /pois request re-runs the query
    cache_id = f"custom_{layer_id}"
    for k in list(_poi_cache.keys()):
        if k[0] == cache_id:
            _poi_cache.pop(k, None)

    refreshed = await tenant.get_custom_poi_layer_by_id(user["account_id"], layer_id)
    return _layer_to_dto(refreshed)


@router.delete("/custom-layers/{layer_id}")
async def delete_custom_layer(
    layer_id: int,
    user: dict = Depends(require_permission("can_manage_poi_layers")),
):
    """Soft-delete a custom layer."""
    tenant = await get_tenant_db(user["account_id"])
    layer = await tenant.get_custom_poi_layer_by_id(user["account_id"], layer_id)
    if not layer or not layer.get("is_active"):
        raise HTTPException(status_code=404, detail="Layer not found")
    ok = await tenant.delete_custom_poi_layer(user["account_id"], layer_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete layer")

    cache_id = f"custom_{layer_id}"
    for k in list(_poi_cache.keys()):
        if k[0] == cache_id:
            _poi_cache.pop(k, None)
    return {"deleted": layer_id}


@router.post("/custom-layers/from-pin", status_code=201)
async def create_layer_from_pin(
    body: _PinDropRequest,
    user: dict = Depends(require_permission("can_manage_poi_layers")),
):
    """Pin-drop UX shortcut.

    Given a clicked map coordinate, query Overpass for the closest POI
    within ~50 m, extract its `brand` tag, and auto-build a USA-wide
    Overpass query for that brand.  If the closest POI has no brand tag,
    surface an error suggesting Geofences for single-location markers.
    """
    # Tiny ad-hoc Overpass call — sub-2-second normally.  Uses around-radius
    # rather than bbox to handle cases where the user clicks slightly off
    # the actual POI marker.
    discovery = (
        f"[out:json][timeout:25];"
        f"(nwr(around:50,{body.lat},{body.lng})[brand];);"
        f"out center tags 50;"
    )
    session = await _get_http_session()
    data: dict = {}
    last_exc: Exception = RuntimeError("Overpass unreachable")
    for endpoint in _OVERPASS_ENDPOINTS:
        try:
            async with session.post(
                endpoint,
                data=discovery,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    last_exc = RuntimeError(f"Overpass returned HTTP {resp.status}")
                    continue
                data = await resp.json(content_type=None)
                break
        except Exception as exc:
            last_exc = exc
            continue
    else:
        raise HTTPException(status_code=502, detail=f"Overpass discovery failed: {last_exc}")

    elements = data.get("elements", []) or []
    if not elements:
        raise HTTPException(
            status_code=404,
            detail=(
                "No branded POI within 50 m of the click. "
                "For a single-location marker use a Geofence instead."
            ),
        )

    # Pick the element closest to the click whose brand tag is non-empty.
    def _dist(el: dict) -> float:
        c = el.get("center") or {}
        elat = el.get("lat") if el.get("lat") is not None else c.get("lat")
        elon = el.get("lon") if el.get("lon") is not None else c.get("lon")
        if elat is None or elon is None:
            return float("inf")
        return (float(elat) - body.lat) ** 2 + (float(elon) - body.lng) ** 2

    elements.sort(key=_dist)
    chosen = next(
        (el for el in elements if (el.get("tags") or {}).get("brand")),
        None,
    )
    if not chosen:
        raise HTTPException(
            status_code=404,
            detail=(
                "Closest POI has no `brand` OSM tag. "
                "For a single-location marker use a Geofence instead."
            ),
        )

    tags = chosen.get("tags") or {}
    brand = tags.get("brand", "").strip()
    if not brand:
        raise HTTPException(status_code=404, detail="Closest POI has empty brand tag")

    # Build a USA-wide Overpass query for that brand.  The brand string is
    # injected into a regex, so any characters that have regex meaning
    # need escaping.  Using `node` (not `nwr`) — fuel/POI nodes in OSM are
    # ~99% mapped as nodes; `nwr` makes Overpass scan ways/relations and
    # times out on CONUS-scale bboxes (verified during Phase A).
    brand_re = re.escape(brand)
    # The element type we sample from determines the right osm tag axis.
    # For a fuel station: amenity=fuel; truck stop: amenity=truck_stop;
    # restaurant: amenity=restaurant; etc.  Reuse the chosen node's amenity
    # tag if present, otherwise fall back to a brand-only query.
    amenity = tags.get("amenity")
    if amenity:
        query = f'node["amenity"="{amenity}"]["brand"~"^{brand_re}$",i]'
    else:
        query = f'node["brand"~"^{brand_re}$",i]'

    # Validate (this should always pass since we built it ourselves, but the
    # check is cheap and protects against future regressions in this builder)
    _validate_overpass_query(query)

    tenant = await get_tenant_db(user["account_id"])
    layer_key = f"pin_{user['account_id']}_{int(__import__('time').time() * 1000)}"
    label = body.label or f"{brand} (all locations)"
    try:
        layer_id = await tenant.add_custom_poi_layer(
            user["account_id"],
            layer_key=layer_key,
            label=label,
            color=body.color or "#7c3aed",
            icon=body.icon or "📍",
            source_type="overpass",
            overpass_query=query,
            brand_filters=None,
            default_on=False,
            created_by=int(user.get("telegram_id") or 0),
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Could not create layer: {exc}")

    layer = await tenant.get_custom_poi_layer_by_id(user["account_id"], layer_id)
    return {**_layer_to_dto(layer), "discovered_brand": brand, "discovered_amenity": amenity or ""}


# ── Pin-drop preview + brand search helpers ───────────────────────────────────
#
# These power the enhanced "New POI Layer" dialog: rather than blindly
# committing one click → one layer, the admin sees the discovered brand,
# the OSM amenity category, and the total US-wide location count BEFORE the
# layer is persisted.  Same machinery underpins the type-ahead brand search.


async def _overpass_post(query: str, timeout: int = 30) -> dict:
    """Single Overpass POST helper used by preview / count / brand-search.

    Centralises the failover-across-mirrors loop so the new helpers don't
    duplicate it.  Raises HTTPException(502) if every mirror fails.
    """
    session = await _get_http_session()
    last_exc: Exception = RuntimeError("Overpass unreachable")
    for endpoint in _OVERPASS_ENDPOINTS:
        try:
            async with session.post(
                endpoint,
                data=query,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    last_exc = RuntimeError(f"Overpass returned HTTP {resp.status}")
                    continue
                return await resp.json(content_type=None)
        except Exception as exc:
            last_exc = exc
            continue
    raise HTTPException(status_code=502, detail=f"Overpass unreachable: {last_exc}")


def _build_brand_query(brand: str, amenity: str | None) -> str:
    """Build a USA-wide Overpass *clause* for a brand (no bbox/area/out)."""
    brand_re = re.escape(brand)
    if amenity:
        return f'node["amenity"="{amenity}"]["brand"~"^{brand_re}$",i]'
    return f'node["brand"~"^{brand_re}$",i]'


async def _count_brand_in_usa(brand: str, amenity: str | None) -> int:
    """Return the number of OSM nodes matching the brand inside the US.

    Uses Overpass's `out count;` aggregation to avoid streaming thousands of
    elements over the wire — the response is a single JSON document with one
    element of type=count and a `tags.total` value.
    """
    clause = _build_brand_query(brand, amenity)
    q = (
        '[out:json][timeout:25];'
        'area["ISO3166-1"="US"][admin_level=2]->.us;'
        f'{clause}(area.us);'
        'out count;'
    )
    try:
        data = await _overpass_post(q, timeout=30)
    except HTTPException:
        return 0
    for el in data.get("elements", []) or []:
        if el.get("type") == "count":
            return int((el.get("tags") or {}).get("total") or el.get("count") or 0)
    return 0


async def _sample_brand_in_usa(
    brand: str, amenity: str | None, limit: int = 5,
) -> list[dict]:
    """Return up to ``limit`` representative locations of the brand in the US.

    Used by the preview UI so the admin can sanity-check the auto-built query
    actually points at the chain they expect.
    """
    clause = _build_brand_query(brand, amenity)
    q = (
        '[out:json][timeout:25];'
        'area["ISO3166-1"="US"][admin_level=2]->.us;'
        f'{clause}(area.us);'
        f'out center {limit};'
    )
    try:
        data = await _overpass_post(q, timeout=30)
    except HTTPException:
        return []
    out: list[dict] = []
    for el in (data.get("elements", []) or [])[:limit]:
        c = el.get("center") or {}
        lat = el.get("lat") if el.get("lat") is not None else c.get("lat")
        lon = el.get("lon") if el.get("lon") is not None else c.get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags") or {}
        out.append({
            "lat": float(lat),
            "lng": float(lon),
            "name": tags.get("name") or tags.get("brand") or "",
            "address": " ".join(filter(None, [
                tags.get("addr:housenumber"),
                tags.get("addr:street"),
                tags.get("addr:city"),
                tags.get("addr:state"),
            ])) or None,
        })
    return out


class _PreviewPinRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


@router.post("/custom-layers/preview-pin")
async def preview_pin(
    body: _PreviewPinRequest,
    user: dict = Depends(require_permission("can_manage_poi_layers")),
):
    """Detect the brand at the pinned point and return a USA-wide preview.

    Returns ``{brand, amenity, count, sample[]}`` so the admin can confirm
    they really want a layer covering every matching location before the
    layer is persisted.  No DB writes happen here.
    """
    discovery = (
        f"[out:json][timeout:25];"
        f"(nwr(around:50,{body.lat},{body.lng})[brand];);"
        f"out center tags 50;"
    )
    data = await _overpass_post(discovery, timeout=30)
    elements = data.get("elements", []) or []
    if not elements:
        raise HTTPException(
            status_code=404,
            detail=(
                "No branded POI within 50 m of the click. "
                "For a single-location marker use a Geofence instead."
            ),
        )

    def _dist(el: dict) -> float:
        c = el.get("center") or {}
        elat = el.get("lat") if el.get("lat") is not None else c.get("lat")
        elon = el.get("lon") if el.get("lon") is not None else c.get("lon")
        if elat is None or elon is None:
            return float("inf")
        return (float(elat) - body.lat) ** 2 + (float(elon) - body.lng) ** 2

    elements.sort(key=_dist)
    chosen = next(
        (el for el in elements if (el.get("tags") or {}).get("brand")),
        None,
    )
    if not chosen:
        raise HTTPException(
            status_code=404,
            detail=(
                "Closest POI has no `brand` OSM tag. "
                "For a single-location marker use a Geofence instead."
            ),
        )
    tags = chosen.get("tags") or {}
    brand = (tags.get("brand") or "").strip()
    amenity = tags.get("amenity") or ""
    count = await _count_brand_in_usa(brand, amenity or None)
    sample = await _sample_brand_in_usa(brand, amenity or None, limit=5)
    return {
        "brand": brand,
        "amenity": amenity,
        "count": count,
        "sample": sample,
    }


class _BrandSearchResult(BaseModel):
    brand: str
    amenity: str
    count: int


@router.get("/custom-layers/brand-search")
async def brand_search(
    q: str = Query(..., min_length=2, max_length=64),
    user: dict = Depends(require_permission("can_manage_poi_layers")),
):
    """Type-ahead search for OSM brands matching ``q`` inside the USA.

    Returns up to 10 brand+amenity buckets sorted by count desc so the
    admin can pick a chain (TA, Pilot, Loves) without writing Overpass.
    """
    safe = re.escape(q.strip())
    if not safe:
        return {"results": []}
    # Pull a sample of matching nodes, then aggregate brand+amenity counts in
    # Python.  Overpass doesn't support GROUP BY directly; doing it locally
    # over a capped result set (1000) is plenty for type-ahead.
    overpass_q = (
        '[out:json][timeout:25];'
        'area["ISO3166-1"="US"][admin_level=2]->.us;'
        f'node["brand"~"^{safe}",i](area.us);'
        'out 1000 tags;'
    )
    data = await _overpass_post(overpass_q, timeout=30)
    buckets: dict[tuple[str, str], int] = {}
    for el in data.get("elements", []) or []:
        tags = el.get("tags") or {}
        b = (tags.get("brand") or "").strip()
        a = (tags.get("amenity") or "").strip()
        if not b:
            continue
        buckets[(b, a)] = buckets.get((b, a), 0) + 1
    # Sort by sample-count desc; the sample is capped at 1000 so we then
    # issue a precise count for the top matches only.
    top = sorted(buckets.items(), key=lambda kv: -kv[1])[:10]
    results: list[dict] = []
    for (brand, amenity), _sample_count in top:
        precise = await _count_brand_in_usa(brand, amenity or None)
        results.append({"brand": brand, "amenity": amenity, "count": precise})
    # Re-sort by precise count
    results.sort(key=lambda r: -r["count"])
    return {"results": results}


class _FromBrandRequest(BaseModel):
    brand: str = Field(..., min_length=1, max_length=80)
    amenity: Optional[str] = Field(default=None, max_length=40)
    label: Optional[str] = Field(default=None, min_length=1, max_length=80)
    color: Optional[str] = Field(default=None, pattern="^#[0-9a-fA-F]{6}$")
    icon: Optional[str] = Field(default=None, max_length=8)
    default_on: bool = False


@router.post("/custom-layers/from-brand", status_code=201)
async def create_layer_from_brand(
    body: _FromBrandRequest,
    user: dict = Depends(require_permission("can_manage_poi_layers")),
):
    """Persist a pre-discovered brand as a custom POI layer.

    The frontend uses this after the user previews a pin or selects a brand
    from the type-ahead, so the actual layer creation is one round-trip with
    no extra Overpass discovery cost.
    """
    brand = body.brand.strip()
    if not brand:
        raise HTTPException(status_code=422, detail="brand is required")
    query = _build_brand_query(brand, (body.amenity or "").strip() or None)
    _validate_overpass_query(query)

    tenant = await get_tenant_db(user["account_id"])
    import time as _t
    layer_key = f"brand_{user['account_id']}_{int(_t.time() * 1000)}"
    label = (body.label or f"{brand} (all locations)").strip()
    try:
        layer_id = await tenant.add_custom_poi_layer(
            user["account_id"],
            layer_key=layer_key,
            label=label,
            color=body.color or "#7c3aed",
            icon=body.icon or "📍",
            source_type="overpass",
            overpass_query=query,
            brand_filters=None,
            default_on=body.default_on,
            created_by=int(user.get("telegram_id") or 0),
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Could not create layer: {exc}")

    layer = await tenant.get_custom_poi_layer_by_id(user["account_id"], layer_id)
    return {**_layer_to_dto(layer), "brand": brand, "amenity": body.amenity or ""}


class _CsvUpload(BaseModel):
    """CSV body sent as JSON to avoid the python-multipart dependency.

    The frontend reads the file with FileReader and posts the raw text.
    Limit enforced server-side; same caps as a real upload.
    """
    csv: str = Field(..., max_length=_CSV_MAX_BYTES + 1)


@router.post("/custom-layers/{layer_id}/csv", status_code=200)
async def upload_custom_layer_csv(
    layer_id: int,
    body: _CsvUpload,
    user: dict = Depends(require_permission("can_manage_poi_layers")),
):
    """Replace all stored points for a CSV-source layer with rows from the
    posted CSV text.  Required header: ``name,lat,lng`` (with optional
    ``brand``).
    """
    tenant = await get_tenant_db(user["account_id"])
    layer = await tenant.get_custom_poi_layer_by_id(user["account_id"], layer_id)
    if not layer or not layer.get("is_active"):
        raise HTTPException(status_code=404, detail="Layer not found")
    if layer["source_type"] != "csv":
        raise HTTPException(status_code=422, detail="Layer is not a CSV layer")

    text = body.csv
    if len(text.encode("utf-8")) > _CSV_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"CSV exceeds {_CSV_MAX_BYTES // (1024*1024)} MB limit")

    # Strip a leading BOM if present (Excel often saves UTF-8 with BOM)
    if text.startswith("\ufeff"):
        text = text[1:]

    reader = _csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=422, detail="CSV is empty")
    cols = {c.strip().lower() for c in reader.fieldnames}
    if not {"lat", "lng"}.issubset(cols):
        raise HTTPException(status_code=422, detail="CSV must include `lat` and `lng` columns")

    points: list[dict] = []
    skipped = 0
    for i, row in enumerate(reader):
        if i >= _CSV_MAX_ROWS:
            raise HTTPException(status_code=413, detail=f"CSV exceeds {_CSV_MAX_ROWS} rows")
        norm = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        try:
            lat = float(norm.get("lat", ""))
            lng = float(norm.get("lng", ""))
        except (ValueError, TypeError):
            skipped += 1
            continue
        points.append({
            "name": norm.get("name", ""),
            "brand": norm.get("brand", ""),
            "lat": lat,
            "lng": lng,
        })

    inserted = await tenant.replace_custom_poi_points(user["account_id"], layer_id, points)

    cache_id = f"custom_{layer_id}"
    for k in list(_poi_cache.keys()):
        if k[0] == cache_id:
            _poi_cache.pop(k, None)

    return {"inserted": inserted, "skipped": skipped}
