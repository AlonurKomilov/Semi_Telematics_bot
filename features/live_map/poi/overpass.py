"""The OpenStreetMap source: mirrors, retries, and what a 200 is worth.

Overpass answers HTTP 200 to a query it gave up on, so "no features"
and "no answer" arrive down the same wire.  Telling those two apart is
this module's real job; the rest is the client that makes the request.
"""
from __future__ import annotations

import asyncio
import re

import aiohttp
from fastapi import HTTPException


# Lazily-created shared aiohttp session — safe in single-threaded asyncio.
_http_session: aiohttp.ClientSession | None = None

_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

#: How long ONE attempt at ONE endpoint may take, and how many passes
#: we make over the endpoint list.
#:
#: Measured against the panel's own bbox (a 3x2 degree box over Chicago,
#: which is what the 1-degree grid expands a city view into): the
#: heaviest layer's query — four clauses, one of them a brand regex —
#: runs in 7.8s, 11.9s and 16.8s on three consecutive tries, returning
#: 83-85 features.  The query is not slow.  What is slow, sometimes, is
#: the shared mirror: a fourth try never returned inside ninety seconds
#: at all, and a fifth answered in twelve.
#:
#: So the answer is not a cheaper query, it is a second try.  35s is
#: over twice the worst honest run, and two passes leave the whole
#: thing under the 90s the browser will wait — a server that keeps
#: trying after its caller has left is burning a mirror nobody is
#: listening to.
_OVERPASS_ATTEMPT_S = 35
_OVERPASS_ATTEMPTS = 2
_OVERPASS_RETRY_PAUSE_S = 1.5

_MAX_POI_RESULTS = 6000  # Caps response at ~5200 fuel stops / ~4300 weigh stations CONUS-wide.

# Custom-layer Overpass DSL hardening
_MAX_CUSTOM_OVERPASS_LEN = 500
_OVERPASS_OPENERS_RE = re.compile(r"^\s*(node|way|nwr)\s*\[", re.IGNORECASE)


async def _get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


#: Words Overpass puts in `remark` when it abandoned a query it had
#: already answered 200 to.  Matched loosely on purpose: the exact
#: wording is not a contract, and a remark we do not recognise is
#: better retried than believed.
_OVERPASS_GAVE_UP = ("error", "timed out", "timeout", "out of memory")


def _overpass_gave_up(data: dict) -> bool:
    """Whether a 200 response is actually a refusal."""
    remark = str((data or {}).get("remark") or "").lower()
    return bool(remark) and any(w in remark for w in _OVERPASS_GAVE_UP)


async def _fetch_overpass(query_parts: list[str], bbox: str) -> list[dict]:
    """Fetch nodes/ways from Overpass inside bbox and return GeoJSON features.

    THE BBOX IS THE ONLY GEOGRAPHIC BOUND, and that is deliberate.

    These filters used to carry ``(area.us)`` as well — an Overpass AREA
    lookup for the US boundary — so that a bbox spanning the northern
    border would not return Canadian results.  That filter silently
    emptied every POI layer in the product.

    Areas are not part of an Overpass database; they are built by a
    separate periodic job, and public mirrors often do not run it.  Ours
    fell onto a mirror that does not: ``overpass-api.de`` stopped
    accepting connections from this host entirely (every address, v4 and
    v6, refused on 443 — the same week OpenStreetMap blocked our tile
    access), leaving only ``overpass.kumi.systems``, where the area
    resolves to nothing.  Measured over one Chicago-metro bbox:

        node["amenity"="fuel"](bbox)            ->  29-34 results
        node["amenity"="fuel"](area.us)(bbox)   ->  0, or a 504

    Zero, at HTTP 200.  Indistinguishable from "there are no fuel
    stations in Chicago", which is what the panel dutifully reported.

    The bound it was providing is already there: ``_clip_bbox_to_usa``
    intersects the request with US bounding boxes BEFORE we get here, so
    the query never reaches beyond them.  What is lost is the last
    sliver — those regions are rectangles, so a Detroit-sized box still
    includes some of Ontario, and a few Canadian stations may appear
    near a border.  A handful of foreign POIs is a far smaller wrong
    than every layer being empty everywhere.
    """
    parts_str = "\n  ".join(f"{p}({bbox});" for p in query_parts)
    overpass_query = (
        f"[out:json][timeout:25][maxsize:4000000];\n"
        f"(\n  {parts_str}\n);\n"
        f"out center;"
    )

    session = await _get_http_session()
    last_exc: Exception = RuntimeError("No Overpass endpoint reachable")

    data: dict = {}
    for attempt in range(_OVERPASS_ATTEMPTS):
        if attempt:
            # A moment, not a backoff curve: what this is waiting out is
            # a queue on a shared mirror, and the queue is either moving
            # or it is not.
            await asyncio.sleep(_OVERPASS_RETRY_PAUSE_S)
        for endpoint in _OVERPASS_ENDPOINTS:
            try:
                async with session.post(
                    endpoint,
                    data=overpass_query,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=aiohttp.ClientTimeout(total=_OVERPASS_ATTEMPT_S),
                ) as resp:
                    if resp.status != 200:
                        last_exc = RuntimeError(f"Overpass returned HTTP {resp.status}")
                        continue
                    data = await resp.json(content_type=None)
                # Overpass can give up INSIDE a 200: exceed the
                # `[timeout:25]` the query carries, or its memory, and
                # the documented answer is a successful HTTP response
                # with no elements and a `remark` saying why.  Read as a
                # feature list, that is "there is nothing here" — the
                # same disguise as the tile block (a PNG that said 403)
                # and the missing area index (an empty list).
                #
                # HONESTLY LABELLED: this branch is defensive.  A
                # 0-feature 200 was observed once here (31.3s, no
                # exception, where a retry then returned 85), but six
                # deliberate attempts to catch the remark itself all
                # came back with 85 features and no remark.  The
                # mechanism is Overpass's documented one; that this
                # particular zero came from it is inference, not
                # measurement.  It costs one string check either way.
                if _overpass_gave_up(data):
                    last_exc = RuntimeError(
                        f"Overpass: {str(data.get('remark'))[:160]}")
                    data = {}
                    continue
                break
            except Exception as exc:
                last_exc = exc
                continue
        else:
            continue          # this pass found nothing; try the list again
        break                 # an endpoint answered
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
