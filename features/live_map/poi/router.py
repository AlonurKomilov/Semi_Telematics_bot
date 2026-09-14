"""The HTTP surface of the POI sub-feature."""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): ONLY router.py may import interfaces.api.deps —
# layers/viewport/overpass/custom never may.
from __future__ import annotations

import csv as _csv
import io
import re

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
from .layers import (
    POI_LAYER_NOTES,
    POI_OVERPASS_QUERIES,
    SERVED_LAYERS,
    fetch_layer_for,
    point_to_feature,
    split_of,
)
from .viewport import (
    _MAX_BBOX_AREA,
    _bbox_to_str,
    _clip_bbox_to_usa,
    _poi_cache,
    _round_bbox,
)

router = APIRouter(prefix="/map", tags=["map"])

#: How many branded nodes the type-ahead asks for.  It is a SAMPLE SIZE,
#: not a page: hit it and every per-brand tally below is a floor, which
#: is why the reply says which of the two it is rather than leaving the
#: client to guess from the number.
_BRAND_SEARCH_CAP = 1000


@router.get("/poi-versions")
async def poi_versions(
    user: dict = Depends(require_permission("can_view_poi")),
):
    """When each built-in layer was last imported — the whole reply is a
    few hundred bytes, and it is what lets a client skip a download.

    A client holding a layer asks this first: same version, and it draws
    from its own copy without another byte crossing the wire.  Different
    version, and it fetches the layer WHOLE from /map/poi-set.

    NULL means the layer has never imported cleanly.  The client must
    then use /map/pois, which is what it has always done — never draw an
    empty layer off the back of a missing version.
    """
    tenant = await get_tenant_db(user["account_id"])
    runs = await tenant.poi_layer_imports()
    return {"versions": {
        layer: ((runs.get(layer) or {}).get("imported_at") or None)
        # SERVED_LAYERS and not POI_OVERPASS_QUERIES: one fetch can
        # produce two layers (see layers.py), and a client that never
        # hears a version for the second one can never hold it.
        for layer in SERVED_LAYERS
    }}


@router.get("/poi-set")
async def poi_set(
    poi_type: str = Query(..., alias="type", min_length=1, max_length=50),
    user: dict = Depends(require_permission("can_view_poi")),
):
    """ONE built-in layer, whole, with the version it is.

    A query parameter and not a path segment because the extension's
    scoped token matches EXTENSION_ROUTES exactly — a path parameter
    would mean listing every layer there, and forgetting the next one.

    Whole rather than by viewport because these layers do not move and
    are small: the largest is about 5,400 points, 169 KB gzipped.  One
    download, then every pan is local — and no third party is asked for
    anything, ever.
    """
    if poi_type not in SERVED_LAYERS:
        raise HTTPException(
            status_code=404,
            detail=f"{poi_type!r} is not a built-in POI layer")
    tenant = await get_tenant_db(user["account_id"])
    version = await tenant.poi_layer_imported_at(poi_type)
    if not version:
        # NOT an empty set: this layer has never imported, so there is
        # nothing to hand over and a client that cached [] would believe
        # the country has no truck stops in it.  409, and the client
        # falls back to the viewport endpoint it has always used.
        raise HTTPException(
            status_code=409,
            detail=f"{poi_type} has not been imported yet — use /map/pois")
    rows = await tenant.poi_points_all(poi_type)
    return {
        "layer": poi_type,
        # TWO DATES, DELIBERATELY.  `version` is when we imported — the
        # value a client compares to decide whether its copy is current,
        # and never something to show a person.  `source_as_of` is what
        # the OSM extract behind it was stamped, which is the only one
        # that answers "is this map up to date".
        "version": version,
        "source_as_of": await tenant.poi_layer_source_as_of(poi_type),
        "type": "FeatureCollection",
        "features": [point_to_feature(r) for r in rows],
    }


@router.get("/pois")
async def map_pois(
    poi_type: str = Query(..., alias="type", min_length=1, max_length=50),
    bbox: str = Query(..., description="south,west,north,east"),
    user: dict = Depends(require_permission("can_view_poi")),
):
    """POI overlay data for map layers.

    Built-in types are the ones in SERVED_LAYERS — which is no longer
    the same list as POI_OVERPASS_QUERIES, because one fetch can produce
    two layers (see layers.py).  Custom per-tenant
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
        tenant = await get_tenant_db(user["account_id"])

        # WHAT THE MAP CANNOT SHOW, SAID OUT LOUD.
        #
        # A vendor reaches this layer only through an active, geocoded
        # directory entry, and that chain starts from an address the
        # vendor row does not have to carry.  Live account, 2026-09-14:
        # 443 vendors, 2 with an address, 4 on the map.  The query is
        # right; drawing 4 and saying nothing is not — it reads as "you
        # have four vendors".
        #
        # NOT AN ERROR, and it must not be rendered as one: nothing is
        # broken and nothing is retryable.  It rides the same `note`
        # channel as "zoom in to load", which exists for exactly this —
        # a true state of a working layer.
        #
        # Computed before the cache is consulted: the cache is keyed by
        # viewport and this fact is account-wide, so a cache hit must
        # not be a quieter answer than a miss.
        total, mappable = await tenant.count_mappable_vendors(user["account_id"])
        note = (f"{mappable} of {total} vendors have a location on file"
                if total > mappable else None)

        cached = _poi_cache.get(cache_key)
        if cached is not None:
            return {"type": "FeatureCollection", "features": cached,
                    "note": note}
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
        return {"type": "FeatureCollection", "features": features, "note": note}

    if poi_type.startswith("custom_"):
        try:
            layer_id = int(poi_type.split("_", 1)[1])
        except (ValueError, IndexError):
            return {"type": "FeatureCollection", "features": []}
        return await _serve_custom_layer(
            user["account_id"], layer_id, bbox, (s, w, n, e),
        )

    if poi_type not in SERVED_LAYERS:
        return {"type": "FeatureCollection", "features": []}

    # ── Our own table, once this layer has been imported ─────────────
    #
    # A truck stop does not move, so there was never a reason to ask a
    # live query service for one on every pan.  Measured 2026-09-13: all
    # three reachable Overpass mirrors refused a one-node query inside
    # one hour, and a layer's whole answer hung on one of them replying
    # in seconds.  It reads from here now — an indexed bbox query — and
    # a mirror outage stops reaching the map at all.
    #
    # NOT CACHED, deliberately: the five-minute cache below exists
    # because the call under it takes tens of seconds.  This one is
    # milliseconds, and a cache would only add a second place for a
    # wrong answer to survive.
    #
    # A layer that has NEVER imported falls through to the mirror, which
    # is what it has always done.  That is the whole migration: nothing
    # breaks before the first import, and the path below dies once every
    # layer has had one.
    tenant = await get_tenant_db(user["account_id"])
    imported_at = await tenant.poi_layer_imported_at(poi_type)
    if imported_at:
        rows = await tenant.poi_points_in_bbox(poi_type, s, w, n, e)
        return {
            "type": "FeatureCollection",
            "features": [point_to_feature(r) for r in rows],
            # THE MIRROR'S DATE, not ours.  Both panels render this field
            # as "OpenStreetMap · N old", which is a claim about OSM's
            # data — so handing it our import time made it say the data
            # was hours old while its OSM base was three months behind.
            # That line exists to explain a truck stop that opened in
            # July being absent; our own timestamp answers a question
            # nobody asked, in the voice of the one they did.
            #
            # None until an import has recorded one, and None draws no
            # line at all.  Saying nothing is the honest unknown.
            "source_as_of": await tenant.poi_layer_source_as_of(poi_type),
            # What this layer's points MEAN, when that needs saying —
            # see POI_LAYER_NOTES.  A note, never an error: nothing is
            # broken and there is nothing to retry.
            "note": POI_LAYER_NOTES.get(poi_type),
        }

    bbox_key = _round_bbox(bbox)
    cache_key = (poi_type, bbox_key)

    cached = _poi_cache.get(cache_key)
    if cached is not None:
        return {"type": "FeatureCollection", "features": cached}

    # HALF A SPLIT ASKS ITS SOURCE'S QUESTION.  `truck_scale` has no
    # Overpass query of its own — it is one half of what `weigh_station`
    # fetches — so before the first import it asks the source's query and
    # keeps only its own half.  Without this the fallback would KeyError,
    # and answering with an empty list instead would tell a driver there
    # are no scales here, which is the one thing this file exists to stop.
    fetched = fetch_layer_for(poi_type) or poi_type
    query_parts = POI_OVERPASS_QUERIES[fetched]
    classify = split_of(fetched)
    try:
        features = await overpass._fetch_overpass(query_parts, bbox)
        if classify is not None:
            features = [
                f for f in features
                if classify({"name": (f.get("properties") or {}).get("name") or "",
                             "props": f.get("properties") or {}}) == poi_type
            ]
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
    # OSM-sourced, so it carries OSM's date.  The vendor-directory and
    # my-vendors branches above deliberately do not: those come from our
    # own database and a date from the wrong source is worse than none.
    return {"type": "FeatureCollection", "features": features,
            "source_as_of": overpass.source_as_of(),
            # The same standing caveat the stored branch carries: a
            # layer means what it means whichever path answered.
            "note": POI_LAYER_NOTES.get(poi_type)}


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
    # Shared with preview-pin.  It used to be a second copy of the same
    # forty lines with its own endpoint loop, and the copy is why the
    # soft-refusal check reached only one of them.
    brand, amenity = await overpass._discover_brand_at(body.lat, body.lng)
    query = overpass._build_brand_query(brand, amenity or None)
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

    Returns ``{brand, amenity, count, sample[]}`` — no DB writes.  `count`
    and `sample` are null when the source refused that query; null is not
    zero and the client must not print it as one.
    """
    brand, amenity = await overpass._discover_brand_at(body.lat, body.lng)
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
    """Type-ahead search for OSM brands matching ``q`` inside the USA.

    ONE Overpass query, not eleven.  This used to take the sampled list
    below and then re-count each of the top ten chains nationwide — ten
    more area-wide queries, sequential, 30 seconds budgeted apiece, per
    settled keystroke.  On a healthy mirror that was merely wasteful; on
    the one mirror this host can still reach it is the whole feature
    failing, and it points 11 nationwide scans at a volunteer server for
    a list the owner is only skimming to pick a name from.

    The exact total is not what this list is for — it is for telling
    Pilot from Pilot Travel Center.  The precise count arrives one step
    later, from preview-pin, on the screen that actually spends it.
    """
    safe = re.escape(q.strip())
    if not safe:
        return {"results": []}
    overpass_q = (
        '[out:json][timeout:25];'
        'area["ISO3166-1"="US"][admin_level=2]->.us;'
        f'node["brand"~"^{safe}",i](area.us);'
        f'out {_BRAND_SEARCH_CAP} tags;'
    )
    data = await overpass._overpass_post(overpass_q, timeout=30)
    elements = data.get("elements", []) or []
    # The cap is on the whole reply, so it decides the whole reply: under
    # it, every bucket below IS that chain's count; at it, every bucket is
    # a floor and the client must not print any of them as a total.
    exact = len(elements) < _BRAND_SEARCH_CAP
    buckets: dict[tuple[str, str], int] = {}
    for el in elements:
        tags = el.get("tags") or {}
        b = (tags.get("brand") or "").strip()
        a = (tags.get("amenity") or "").strip()
        if not b:
            continue
        buckets[(b, a)] = buckets.get((b, a), 0) + 1
    top = sorted(buckets.items(), key=lambda kv: -kv[1])[:10]
    return {"results": [
        {"brand": brand, "amenity": amenity, "count": n, "exact": exact}
        for (brand, amenity), n in top
    ]}


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
