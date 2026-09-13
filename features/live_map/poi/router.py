"""The HTTP surface of the POI sub-feature."""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): ONLY router.py may import interfaces.api.deps —
# layers/viewport/overpass/custom never may.
from __future__ import annotations

import csv as _csv
import io
import re

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query

from interfaces.api.deps import require_permission
from interfaces.bot.state import get_tenant_db

from . import overpass
from .custom import (
    _CSV_MAX_BYTES,
    _CSV_MAX_ROWS,
    _CsvUpload,
    _CustomLayerCreate,
    _CustomLayerPatch,
    _FromBrandRequest,
    _PinDropRequest,
    _PreviewPinRequest,
    _layer_to_dto,
    _serve_custom_layer,
)
from .layers import POI_OVERPASS_QUERIES
from .viewport import (
    _MAX_BBOX_AREA,
    _bbox_to_str,
    _clip_bbox_to_usa,
    _poi_cache,
    _round_bbox,
)

router = APIRouter(prefix="/map", tags=["map"])


@router.get("/pois")
async def map_pois(
    poi_type: str = Query(..., alias="type", min_length=1, max_length=50),
    bbox: str = Query(..., description="south,west,north,east"),
    user: dict = Depends(require_permission("can_view_poi")),
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
                    "chain": r.get("chain") or "",
                    "_directory": True,
                },
            }
            for r in rows
        ]
        _poi_cache[cache_key] = features
        return {"type": "FeatureCollection", "features": features}

    if poi_type == "my_vendors":
        # THIS account's shops (auto-linked directory entries).  Per-
        # tenant data → the cache key MUST carry the account id (the
        # shared-key rule above applies only to platform-global layers).
        bbox_key = _round_bbox(bbox)
        cache_key = (f"my_vendors_{user['account_id']}", bbox_key)
        cached = _poi_cache.get(cache_key)
        if cached is not None:
            return {"type": "FeatureCollection", "features": cached}
        tenant = await get_tenant_db(user["account_id"])
        rows = await tenant.my_vendor_entries_in_bbox(
            user["account_id"], s, w, n, e,
        )
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
                    "chain": r.get("chain") or "",
                    "my_vendor_name": r.get("my_vendor_name") or "",
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
        features = await overpass._fetch_overpass(query_parts, bbox)
    except Exception as exc:
        # A source that did not answer is NOT an area with nothing in
        # it.  This used to be `features = []`, cached for five minutes
        # and served as HTTP 200 — so every client showed "none here"
        # for a failure, and kept showing it after the failure passed.
        # That is the same shape as the OpenStreetMap tile block, which
        # went unnoticed for a day because it too arrived as a
        # successful-looking answer.
        #
        # 502: the refusal is upstream's, and a caller that sees its own
        # server blamed goes looking in the wrong place.  Nothing is
        # cached — the next request gets a fresh attempt rather than
        # five more minutes of a wrong answer.
        raise HTTPException(
            status_code=502,
            detail="The map-data source is not answering — try again shortly.",
        ) from exc

    _poi_cache[cache_key] = features
    return {"type": "FeatureCollection", "features": features}


# ── Custom-layer routes ───────────────────────────────────────────────────────

@router.get("/custom-layers")
async def list_custom_layers(
    user: dict = Depends(require_permission("can_view_poi")),
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
        validated = overpass._validate_overpass_query(body.overpass_query)
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

    # NOT named `overpass`: that is the module this file reaches the
    # Overpass client through, and a local of the same name hides it.
    overpass_query = body.overpass_query
    if overpass_query is not None:
        if layer["source_type"] != "overpass":
            raise HTTPException(status_code=422, detail="Cannot set overpass_query on non-overpass layer")
        overpass_query = overpass._validate_overpass_query(overpass_query)

    ok = await tenant.update_custom_poi_layer(
        user["account_id"], layer_id,
        label=body.label,
        color=body.color,
        icon=body.icon,
        overpass_query=overpass_query,
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
    session = await overpass._get_http_session()
    data: dict = {}
    last_exc: Exception = RuntimeError("Overpass unreachable")
    for endpoint in overpass._OVERPASS_ENDPOINTS:
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

    overpass._validate_overpass_query(query)

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
    data = await overpass._overpass_post(discovery, timeout=30)
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
    count = await overpass._count_brand_in_usa(brand, amenity or None)
    sample = await overpass._sample_brand_in_usa(brand, amenity or None, limit=5)
    return {
        "brand": brand,
        "amenity": amenity,
        "count": count,
        "sample": sample,
    }


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
    data = await overpass._overpass_post(overpass_q, timeout=30)
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
        precise = await overpass._count_brand_in_usa(brand, amenity or None)
        results.append({"brand": brand, "amenity": amenity, "count": precise})
    results.sort(key=lambda r: -r["count"])
    return {"results": results}


@router.post("/custom-layers/from-brand", status_code=201)
async def create_layer_from_brand(
    body: _FromBrandRequest,
    user: dict = Depends(require_permission("can_manage_poi_layers")),
):
    """Persist a pre-discovered brand as a custom POI layer."""
    brand = body.brand.strip()
    if not brand:
        raise HTTPException(status_code=422, detail="brand is required")
    query = overpass._build_brand_query(brand, (body.amenity or "").strip() or None)
    overpass._validate_overpass_query(query)

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
