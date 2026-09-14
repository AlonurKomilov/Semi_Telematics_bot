"""The OpenStreetMap source: mirrors, retries, and what a 200 is worth.

Overpass answers HTTP 200 to a query it gave up on, so "no features"
and "no answer" arrive down the same wire.  Telling those two apart is
this module's real job; the rest is the client that makes the request.
"""
from __future__ import annotations

import asyncio
import logging
import re

import aiohttp
from fastapi import HTTPException

from .layers import point_to_feature

logger = logging.getLogger(__name__)


# Lazily-created shared aiohttp session — safe in single-threaded asyncio.
_http_session: aiohttp.ClientSession | None = None

#: Measured from this host on 2026-09-13, and the first line is why this
#: comment exists: ``overpass-api.de`` REFUSES 443 on every address it
#: publishes — 162.55.144.139, 65.109.112.52 and both IPv6 — while the
#: same host answers 200 on port 80.  So the canonical URL has never once
#: worked from this server; every request has been spending an attempt on
#: an instant refusal before reaching a mirror that answers.
#:
#: ``lambert.openstreetmap.de`` is that same instance: it is what
#: ``overpass-api.de/api/status`` names as its "Announced endpoint", and
#: it serves HTTPS here (TLS in 0.097s).  Not a new operator and not a new
#: recipient of anyone's viewport — the same service, by a hostname this
#: host can reach.  If the canonical name's TLS is ever fixed, it can come
#: back and this note should go.
_OVERPASS_ENDPOINTS = [
    "https://lambert.openstreetmap.de/api/interpreter",
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

#: The whole ladder's wall-clock budget — and it is a BUDGET, not another
#: per-attempt number, because the per-attempt number could not promise
#: what this has to promise.
#:
#: nginx gives /api/ sixty seconds (nginx/4truck.conf, `proxy_read_timeout
#: 60s`).  Past that nginx answers with its OWN 504: an HTML gateway page,
#: which the dashboard reads — correctly — as the platform being down, and
#: reloads the whole page for, taking a half-filled form somewhere else in
#: the app with it.  So this app has to give up first, with its own JSON,
#: while it still can.
#:
#: Two passes over two mirrors at 35s each is 141.5 seconds.  The guard
#: that was meant to hold this under the browser's patience multiplied the
#: passes but not the MIRRORS — it read 71.5 and passed.  A budget is
#: arithmetic nobody has to redo when a third mirror is added.
_OVERPASS_TOTAL_S = 50

_MAX_POI_RESULTS = 6000  # Caps response at ~5200 fuel stops / ~4300 weigh stations CONUS-wide.

# Custom-layer Overpass DSL hardening
_MAX_CUSTOM_OVERPASS_LEN = 500
_OVERPASS_OPENERS_RE = re.compile(r"^\s*(node|way|nwr)\s*\[", re.IGNORECASE)


async def _get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


async def close_http_session() -> None:
    """Let go of the shared session.

    The API never calls this — the session lives as long as the worker,
    which is the point of sharing it.  A SCRIPT is different: it finishes,
    and aiohttp prints two ERROR lines on the way out about a session and
    a connector nobody closed.  The owner saw them at the end of the first
    real import, under a line that said the import had worked, and the
    only honest reading of an ERROR there is that something went wrong.
    Nothing had.
    """
    global _http_session
    if _http_session is not None and not _http_session.closed:
        await _http_session.close()
    _http_session = None


#: The OSM tags a POI carries onto the map — what a popup shows and what
#: a brand chip matches on.  MODULE level because two callers read an
#: element now: the request path below, and the import job that fills our
#: own table.  It was a local inside the loop, which meant the importer's
#: only way to agree with the map was to copy the tuple — and a copied
#: forty lines is exactly how /custom-layers/from-pin ended up missing a
#: fix its twin had received.
SERVICE_TAGS = (
    "amenity", "highway", "brand", "operator",
    "phone", "website", "opening_hours",
    "fuel:diesel", "fuel:adblue", "fuel:HGV_diesel",
    "hgv", "truck", "shower", "toilets",
    "capacity", "fee",
)


def element_to_point(element: dict) -> dict | None:
    """One Overpass element as a plain point, or None if it has no place.

    A `way` or `relation` answers with a `center` rather than its own
    lat/lon — asked for by `out center`, and an element with neither is
    not something we can draw.
    """
    center = element.get("center") or {}
    lat = element.get("lat") if element.get("lat") is not None else center.get("lat")
    lon = element.get("lon") if element.get("lon") is not None else center.get("lon")
    if lat is None or lon is None:
        return None
    tags = element.get("tags", {}) or {}
    return {
        "osm_type": element.get("type") or "node",
        "osm_id": element.get("id"),
        "lat": float(lat),
        "lng": float(lon),
        # The name a marker shows: OSM fills exactly one of these on most
        # branded places, and an unnamed one is legitimate.
        "name": tags.get("name") or tags.get("brand") or tags.get("operator") or "",
        "props": {k: v for k, v in tags.items() if k in SERVICE_TAGS},
    }


#: Words Overpass puts in `remark` when it abandoned a query it had
#: already answered 200 to.  Matched loosely on purpose: the exact
#: wording is not a contract, and a remark we do not recognise is
#: better retried than believed.
_OVERPASS_GAVE_UP = ("error", "timed out", "timeout", "out of memory")


def _overpass_gave_up(data: dict) -> bool:
    """Whether a 200 response is actually a refusal."""
    remark = str((data or {}).get("remark") or "").lower()
    return bool(remark) and any(w in remark for w in _OVERPASS_GAVE_UP)


#: How old the OpenStreetMap extract behind every layer is, as the last
#: mirror to answer reported it.  A property of the MIRROR rather than of
#: one query, which is why it lives here instead of being threaded back
#: through three call sites as a second return value.
#:
#: It is worth carrying because the mirrors are far behind.  Measured
#: 2026-09-13, the two this host can reach were stamped 2026-06-01 and
#: 2026-07-28 — a truck stop that opened in July is simply not in the
#: data, and with no date on screen that reads as the product being
#: wrong rather than the free source being old.
_source_as_of: str | None = None


def source_as_of() -> str | None:
    """When the OSM extract behind the layers was last updated, or None
    if no mirror has answered this worker yet.  OMITTED, never guessed:
    a wrong date here would be worse than no date."""
    return _source_as_of


def stamp_of(data: dict) -> str | None:
    """THIS REPLY's extract date, or None if it did not carry one.

    The per-reply fact, as opposed to `source_as_of()`'s per-worker one.
    Anything that needs to know which answer a date belongs to must read
    it here: the module global is whatever replied LAST, which is the
    right answer for "how old is what this worker is serving" and the
    wrong one for "how old is this box I just fetched".
    """
    ts = ((data or {}).get("osm3s") or {}).get("timestamp_osm_base")
    return ts if isinstance(ts, str) and ts else None


def _remember_source_age(data: dict) -> None:
    """Every Overpass reply carries its extract's date in `osm3s`."""
    global _source_as_of
    ts = stamp_of(data)
    if ts:
        _source_as_of = ts


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
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _OVERPASS_TOTAL_S

    data: dict = {}
    for attempt in range(_OVERPASS_ATTEMPTS):
        if attempt:
            # A moment, not a backoff curve: what this is waiting out is
            # a queue on a shared mirror, and the queue is either moving
            # or it is not.
            await asyncio.sleep(_OVERPASS_RETRY_PAUSE_S)
        for endpoint in _OVERPASS_ENDPOINTS:
            # OUTSIDE the try on purpose: raised inside it, the handler
            # below would swallow the deadline and the ladder would keep
            # spending time it no longer has.
            left = deadline - loop.time()
            if left <= 0:
                raise TimeoutError(
                    f"Overpass ladder spent its {_OVERPASS_TOTAL_S}s budget"
                ) from last_exc
            try:
                async with session.post(
                    endpoint,
                    data=overpass_query,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=aiohttp.ClientTimeout(total=min(_OVERPASS_ATTEMPT_S, left)),
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
                _remember_source_age(data)
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

        point = element_to_point(element)
        if point is None:
            continue
        features.append(point_to_feature(point))
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

async def _overpass_post(
    query: str, timeout: int = 30, budget: float | None = None,
) -> dict:
    """Single Overpass POST with mirror failover. Raises 502 on total failure.

    One pass, no retry: every caller here is interactive (a pin the owner
    just dropped, a name they are still typing) and already makes two or
    three of these in a row.

    ``budget`` is the wall clock the whole failover may spend, and it
    defaults to the one nginx allows a REQUEST.  A job answering to
    nobody is the one caller that may raise it — patience is the whole
    reason an import can ask for a state at a time where the map could
    only ever ask for a screenful.
    """
    session = await _get_http_session()
    last_exc: Exception = RuntimeError("Overpass unreachable")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (budget if budget is not None else _OVERPASS_TOTAL_S)
    for endpoint in _OVERPASS_ENDPOINTS:
        # `timeout` is this caller's patience; the budget is nginx's.
        # Whichever runs out first ends the attempt.
        left = deadline - loop.time()
        if left <= 0:
            break
        try:
            async with session.post(
                endpoint,
                data=query,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=aiohttp.ClientTimeout(total=min(timeout, left)),
            ) as resp:
                if resp.status != 200:
                    last_exc = RuntimeError(f"Overpass returned HTTP {resp.status}")
                    continue
                data = await resp.json(content_type=None)
            # The same check _fetch_overpass makes, and it was missing
            # here — which matters more on this path, not less.  Read as
            # data, a `remark` reply is a result set with nothing in it,
            # and nothing in it becomes "this chain has no locations in
            # the United States" on the screen that asks the owner to
            # save a layer covering all of them.
            if _overpass_gave_up(data):
                last_exc = RuntimeError(f"Overpass: {str(data.get('remark'))[:160]}")
                continue
            _remember_source_age(data)
            return data
        except Exception as exc:
            last_exc = exc
            continue
    # The reason belongs in the log, not in the reply: the caller cannot
    # act on a dispatcher's words, and the refusal is upstream's either
    # way.  Same sentence the built-in layers use, because it is the same
    # thing that happened.
    logger.warning("Overpass POST failed on every endpoint: %s", last_exc)
    raise HTTPException(
        status_code=502,
        detail="The map-data source is not answering — try again shortly.",
    ) from last_exc


async def _discover_brand_at(lat: float, lng: float) -> tuple[str, str]:
    """The brand of the closest branded OSM feature within ~50 m of a click.

    Returns ``(brand, amenity)``; amenity is "" when the feature has none.
    Raises 404 when there is genuinely nothing branded there, and 502 —
    through _overpass_post — when the source did not answer.

    ONE COPY, and that is the point.  This lived twice: once in
    /custom-layers/from-pin and once in /custom-layers/preview-pin, the
    same forty lines with the same three error messages.  When the
    soft-refusal check was added, only the copy that went through
    _overpass_post got it; from-pin kept its own hand-rolled endpoint
    loop, so a mirror answering 200 with a `remark` gave it zero
    elements and it told the owner "No branded POI within 50 m of the
    click" — blaming where they clicked for what the source had done.
    """
    query = (
        f"[out:json][timeout:25];"
        f"(nwr(around:50,{lat},{lng})[brand];);"
        f"out center tags 50;"
    )
    data = await _overpass_post(query, timeout=30)
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
        return (float(elat) - lat) ** 2 + (float(elon) - lng) ** 2

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
    if not brand:
        raise HTTPException(status_code=404, detail="Closest POI has empty brand tag")
    return brand, (tags.get("amenity") or "").strip()


def _build_brand_query(brand: str, amenity: str | None) -> str:
    """Build a USA-wide Overpass clause for a brand (no bbox/area/out)."""
    brand_re = re.escape(brand)
    if amenity:
        return f'node["amenity"="{amenity}"]["brand"~"^{brand_re}$",i]'
    return f'node["brand"~"^{brand_re}$",i]'


async def _count_brand_in_usa(brand: str, amenity: str | None) -> int | None:
    """How many OSM nodes match the brand inside the US — or None if the
    source did not answer.

    NONE AND ZERO ARE DIFFERENT ANSWERS and every caller has to keep them
    apart.  This used to `return 0` on any failure, so a mirror too busy
    to run the query told the owner that the truck stop they had just
    clicked on does not exist anywhere in the country — on the one screen
    that then offers to save a layer covering "all 0" of them.  Measured
    2026-09-13: the only mirror this host can reach answered that exact
    query with HTTP 504 after 178 seconds.
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
        logger.warning("brand count unavailable for %r (%s)", brand, amenity)
        return None
    for el in data.get("elements", []) or []:
        if el.get("type") == "count":
            return int((el.get("tags") or {}).get("total") or el.get("count") or 0)
    # A 200 with no count element is not a count of zero either.
    return None


async def _sample_brand_in_usa(
    brand: str, amenity: str | None, limit: int = 5,
) -> list[dict] | None:
    """Up to ``limit`` representative locations of the brand in the US —
    or None if the source did not answer.  An empty LIST means the query
    ran and found none; None means it never ran."""
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
        logger.warning("brand sample unavailable for %r (%s)", brand, amenity)
        return None
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
