"""POI overlay endpoints — built-in OSM/Overpass layers + tenant custom layers.

Two cohesive feature areas live in this file because the /pois endpoint
dispatches custom layers (`type=custom_{id}`) through the same code path as
built-in layers (fuel, def, parking, etc.) and they share the cache + Overpass
client.

URL structure (mounted under /map):
    GET    /map/pois                              built-in or custom POI fetch
    GET    /map/custom-layers                     list custom layers
    POST   /map/custom-layers                     create custom (overpass/csv)
    PATCH  /map/custom-layers/{id}                edit custom layer
    DELETE /map/custom-layers/{id}                soft-delete custom layer
    POST   /map/custom-layers/from-pin            pin-drop UX shortcut
    POST   /map/custom-layers/preview-pin         non-persistent preview
    GET    /map/custom-layers/brand-search        type-ahead brand picker
    POST   /map/custom-layers/from-brand          persist a previewed brand
    POST   /map/custom-layers/{id}/csv            replace CSV-source points
"""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): ONLY router.py may import interfaces.api.deps;
# service/alert/ai_tool/signal modules never do.


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

from interfaces.api.deps import require_permission, require_permission_any
from interfaces.bot.state import get_tenant_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/map", tags=["map"])


# ── POI Overlay Layers ────────────────────────────────────────────────────────
#
# To ADD a new POI layer:
#   1. Add an entry to POI_OVERPASS_QUERIES below — or, for a DB-backed
#      layer, a source branch in map_pois() (see vendor_directory).
#   2. Add the matching entry to poiLayers.ts in the dashboard config.
#   Nothing else changes — the endpoint and the hook handle the rest.
#
# Each entry is a LIST of Overpass filter expressions — all are unioned into
# one request.  Single-element lists are the common case; multi-element lists
# let a layer combine several OSM tags (e.g. truck_stop).

POI_OVERPASS_QUERIES: dict[str, list[str]] = {
    # ── Fuel Stations (diesel-capable) ────────────────────────────────────────
    # Tag-based filtering misses ~70% of diesel-capable stations in the US, so
    # we supplement with a brand allowlist for major chains. Brand allowlist
    # uses `node` only (not `nwr`) — the area variant times out on CONUS bbox.
    "fuel_station": [
        'node["amenity"="fuel"]["fuel:diesel"="yes"]',
        'node["amenity"="fuel"]["hgv"="yes"]',
        'nwr["amenity"="truck_stop"]',
        'node["amenity"="fuel"]["brand"~"^(Pilot|Flying J|Pilot Flying J|Love.s|TA|Petro|TravelCenters|Sapp Bros|Road Ranger|Kwik Trip|Kwik Star|Bosselman|Ambest)",i]',
    ],
    # ── DEF / AdBlue Stations ─────────────────────────────────────────────────
    # fuel:adblue=yes has ~15-25% coverage; brand allowlist catches the rest.
    "def_station": [
        'node["amenity"="fuel"]["fuel:adblue"="yes"]',
        'nwr["amenity"="truck_stop"]["fuel:adblue"="yes"]',
        'nwr["amenity"="truck_stop"]["brand"~"^(Pilot|Flying J|Pilot Flying J|Love.s|TA|Petro|TravelCenters|Sapp Bros|Road Ranger)",i]',
        'node["amenity"="fuel"]["brand"~"^(Pilot|Flying J|Pilot Flying J|Love.s|TA|Petro|TravelCenters|Sapp Bros|Road Ranger)",i]',
    ],
    # ── Truck parking ─────────────────────────────────────────────────────────
    # Node-only — way/relation queries silently timeout on large bboxes.
    "truck_parking": [
        'node["amenity"="parking"]["truck"="yes"]',
        'node["amenity"="parking"]["hgv"="yes"]',
        'node["amenity"="parking"]["access:hgv"~"yes|designated"]',
    ],
    # ── Showers ───────────────────────────────────────────────────────────────
    "shower": [
        'node["amenity"="shower"]',
        'nwr["amenity"="truck_stop"]["shower"="yes"]',
        'node["amenity"="fuel"]["shower"="yes"]',
    ],
    # ── Rest areas ────────────────────────────────────────────────────────────
    "rest_area": [
        'node["highway"="rest_area"]["amenity"!="truck_stop"]',
        'nwr["amenity"="rest_area"]["amenity"!="truck_stop"]',
    ],
    # ── Weigh stations ────────────────────────────────────────────────────────
    # amenity=weighbridge is the primary tag (~3800 nationwide).
    "weigh_station": [
        'nwr["amenity"="weighbridge"]',
        'nwr["highway"="weigh_station"]',
        'nwr["amenity"="vehicle_inspection"]',
        'node["highway"="motorway_junction"]["name"~"Weigh Station|Weigh Sta|Port of Entry|Inspection Station",i]',
    ],
}

# Bounded TTL cache: (poi_type, bbox_rounded) → features.
# POI data is intentionally tenant-agnostic (public OSM data). If tenant-
# specific layers are added later, the cache key MUST include account_id.
_POI_CACHE_TTL = 300
_poi_cache: TTLCache = TTLCache(maxsize=500, ttl=_POI_CACHE_TTL)

# Lazily-created shared aiohttp session — safe in single-threaded asyncio.
_http_session: aiohttp.ClientSession | None = None

_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

_MAX_POI_RESULTS = 6000  # Caps response at ~5200 fuel stops / ~4300 weigh stations CONUS-wide.
_MAX_BBOX_AREA = 5000.0  # roughly North America (CONUS is ~2450 sq deg).

# USA-only POI restriction — clip viewport to US regions before querying.
# Per product requirement, POIs in Mexico/Canada/Caribbean are not displayed
# even when the visible viewport extends beyond the US border.
_USA_REGIONS: tuple[tuple[float, float, float, float], ...] = (
    (24.396308, -125.000000, 49.500000,  -66.500000),  # CONUS
    (51.000000, -179.500000, 71.500000, -129.000000),  # Alaska
    (18.500000, -161.000000, 22.500000, -154.500000),  # Hawaii
)

# Custom-layer Overpass DSL hardening
_MAX_CUSTOM_OVERPASS_LEN = 500
_OVERPASS_OPENERS_RE = re.compile(r"^\s*(node|way|nwr)\s*\[", re.IGNORECASE)

# CSV upload limits
_CSV_MAX_BYTES = 5 * 1024 * 1024
_CSV_MAX_ROWS = 50_000


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


def _clip_bbox_to_usa(s: float, w: float, n: float, e: float) -> tuple[float, float, float, float] | None:
    """Intersect the requested bbox with the union of US regions.

    Returns the clipped tuple of the FIRST region the bbox intersects, or
    None if the request lies entirely outside US bounds.
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

    Each filter is restricted to the US administrative boundary (ISO3166-1=US)
    AND the caller's bbox. Without the area filter Overpass returns features
    from Canada/Mexico/Caribbean whenever the bbox spans the border.
    """
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
            break
        except Exception as exc:
            last_exc = exc
            continue
    else:
        raise last_exc

    features = []
    seen_ids: set = set()
    for element in data.get("elements", []):
        # Dedupe by (type, id) — union queries deliberately overlap (e.g. a
        # Pilot tagged fuel:diesel=yes also matches the brand allowlist).
        oid_key = (element.get("type"), element.get("id"))
        if oid_key in seen_ids:
            continue
        seen_ids.add(oid_key)

        center = element.get("center") or {}
        lat = element.get("lat") if element.get("lat") is not None else center.get("lat")
        lon = element.get("lon") if element.get("lon") is not None else center.get("lon")
        if lat is None or lon is None:
            continue
        tags = element.get("tags", {})
        display_name = (
            tags.get("name")
            or tags.get("brand")
            or tags.get("operator")
            or ""
        )
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
    user: dict = Depends(require_permission_any("can_location_map", "can_location_vehicle")),
):
    """POI overlay data for map layers.

    Built-in types are defined in POI_OVERPASS_QUERIES; custom per-tenant
    layers use ``type=custom_{id}`` and dispatch to either Overpass or the
    DB-points reader. Unknown types return an empty FeatureCollection.
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

    clipped = _clip_bbox_to_usa(s, w, n, e)
    if clipped is None:
        return {"type": "FeatureCollection", "features": []}
    cs, cw, cn, ce = clipped
    bbox = _bbox_to_str(cs, cw, cn, ce)
    s, w, n, e = cs, cw, cn, ce

    if poi_type == "vendor_directory":
        # Platform-curated repair-shop directory (identity fields only —
        # never any account's transactions).  Platform-GLOBAL data, so
        # the tenant-agnostic cache key below is correct as-is.
        bbox_key = _round_bbox(bbox)
        cache_key = (poi_type, bbox_key)
        cached = _poi_cache.get(cache_key)
        if cached is not None:
            return {"type": "FeatureCollection", "features": cached}
        tenant = await get_tenant_db(user["account_id"])
        rows = await tenant.directory_entries_in_bbox(s, w, n, e)
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [r["lng"], r["lat"]]},
                "properties": {
                    "entry_id": r["id"],
                    "name": r["name"],
                    "address": r.get("address") or "",
                    "phone": r.get("phone") or "",
                    "website": r.get("website") or "",
                    "services": r.get("services") or "",
                    "_directory": True,
                },
            }
            for r in rows
        ]
        _poi_cache[cache_key] = features
        return {"type": "FeatureCollection", "features": features}

    if poi_type.startswith("custom_"):
        try:
            layer_id = int(poi_type.split("_", 1)[1])
        except (ValueError, IndexError):
            return {"type": "FeatureCollection", "features": []}
        return await _serve_custom_layer(
            user["account_id"], layer_id, bbox, (s, w, n, e),
        )

    if poi_type not in POI_OVERPASS_QUERIES:
        return {"type": "FeatureCollection", "features": []}

    bbox_key = _round_bbox(bbox)
    cache_key = (poi_type, bbox_key)

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
# Three source types:
#   1. Pin-drop (brand sample) — server reads closest OSM POI's brand tag and
#      auto-builds a CONUS Overpass query for it. Stored same as overpass-source.
#   2. Overpass query (advanced) — admin pastes a raw OSM filter expression.
#      Validated against a whitelist; server adds bbox + header.
#   3. CSV upload — static (name, lat, lng, brand?) rows stored in
#      custom_poi_points and served from DB (no Overpass).


def _validate_overpass_query(raw: str) -> str:
    """Validate an admin-supplied Overpass element.

    Returns the trimmed query if safe; raises HTTPException(422) otherwise.
    The result must NOT include a bbox or trailing semicolon (we add those).
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
    """Fetch + cache features for a single custom layer."""
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
    user: dict = Depends(require_permission_any("can_location_map", "can_location_vehicle")),
):
    """List active custom POI layers for the caller's account."""
    tenant = await get_tenant_db(user["account_id"])
    layers = await tenant.get_custom_poi_layers(user["account_id"], is_active=True)
    return {"layers": [_layer_to_dto(lyr) for lyr in layers]}


@router.post("/custom-layers", status_code=201)
async def create_custom_layer(
    body: _CustomLayerCreate,
    user: dict = Depends(require_permission("can_manage_poi_layers")),
):
    """Create a custom POI layer (overpass or csv source)."""
    if body.source_type == "overpass":
        if not body.overpass_query:
            raise HTTPException(status_code=422, detail="overpass_query required for source_type=overpass")
        validated = _validate_overpass_query(body.overpass_query)
    else:
        validated = ""

    tenant = await get_tenant_db(user["account_id"])
    import time as _t
    layer_key = f"user_{user['account_id']}_{int(_t.time() * 1000)}"
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
        raise HTTPException(status_code=409, detail=f"Could not create layer: {exc}")
    layer = await tenant.get_custom_poi_layer_by_id(user["account_id"], layer_id)
    return _layer_to_dto(layer)


@router.patch("/custom-layers/{layer_id}")
async def patch_custom_layer(
    layer_id: int,
    body: _CustomLayerPatch,
    user: dict = Depends(require_permission("can_manage_poi_layers")),
):
    """Rename / recolor / re-icon / toggle / refine query.  source_type is
    immutable — delete + recreate to switch sources."""
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
    """Pin-drop UX shortcut. Auto-builds a USA-wide Overpass query for the
    closest branded POI within ~50 m of the click."""
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

    brand_re = re.escape(brand)
    amenity = tags.get("amenity")
    if amenity:
        query = f'node["amenity"="{amenity}"]["brand"~"^{brand_re}$",i]'
    else:
        query = f'node["brand"~"^{brand_re}$",i]'

    _validate_overpass_query(query)

    tenant = await get_tenant_db(user["account_id"])
    import time as _t
    layer_key = f"pin_{user['account_id']}_{int(_t.time() * 1000)}"
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


# ── Pin-drop preview + brand search ───────────────────────────────────────────

async def _overpass_post(query: str, timeout: int = 30) -> dict:
    """Single Overpass POST with mirror failover. Raises 502 on total failure."""
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
    """Build a USA-wide Overpass clause for a brand (no bbox/area/out)."""
    brand_re = re.escape(brand)
    if amenity:
        return f'node["amenity"="{amenity}"]["brand"~"^{brand_re}$",i]'
    return f'node["brand"~"^{brand_re}$",i]'


async def _count_brand_in_usa(brand: str, amenity: str | None) -> int:
    """Return the number of OSM nodes matching the brand inside the US."""
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
    """Up to ``limit`` representative locations of the brand in the US."""
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
    Returns ``{brand, amenity, count, sample[]}`` — no DB writes."""
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
    """Type-ahead search for OSM brands matching ``q`` inside the USA."""
    safe = re.escape(q.strip())
    if not safe:
        return {"results": []}
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
    top = sorted(buckets.items(), key=lambda kv: -kv[1])[:10]
    results: list[dict] = []
    for (brand, amenity), _sample_count in top:
        precise = await _count_brand_in_usa(brand, amenity or None)
        results.append({"brand": brand, "amenity": amenity, "count": precise})
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
    """Persist a pre-discovered brand as a custom POI layer."""
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
    """CSV body sent as JSON to avoid the python-multipart dependency."""
    csv: str = Field(..., max_length=_CSV_MAX_BYTES + 1)


@router.post("/custom-layers/{layer_id}/csv", status_code=200)
async def upload_custom_layer_csv(
    layer_id: int,
    body: _CsvUpload,
    user: dict = Depends(require_permission("can_manage_poi_layers")),
):
    """Replace all stored points for a CSV-source layer.
    Required header: ``name,lat,lng`` (with optional ``brand``)."""
    tenant = await get_tenant_db(user["account_id"])
    layer = await tenant.get_custom_poi_layer_by_id(user["account_id"], layer_id)
    if not layer or not layer.get("is_active"):
        raise HTTPException(status_code=404, detail="Layer not found")
    if layer["source_type"] != "csv":
        raise HTTPException(status_code=422, detail="Layer is not a CSV layer")

    text = body.csv
    if len(text.encode("utf-8")) > _CSV_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"CSV exceeds {_CSV_MAX_BYTES // (1024*1024)} MB limit")

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
