"""Which map an account's live map is drawn on.

Two engines, and the platform means to keep both:

  ``osm``     — Leaflet over OpenStreetMap and Esri tiles.  Free, no
                key, no per-view cost, and what every account gets
                today.
  ``google``  — the same Leaflet, with Google's basemap under it,
                through the Map Tiles API — the product Google sells
                for exactly this ("third-party renderers", in its own
                policy).  Every overlay the map draws is untouched; only
                the tiles change.  Carriers in the US already run their
                day on Google Maps, so the familiar map is worth paying
                for; Google bills PER TILE against OUR cloud project,
                so an account only gets it when somebody decided it
                should.

Not the Maps JavaScript API.  That product forbids its content inside a
non-Google map (Terms 3.2.4(e)) and would have meant re-drawing every
overlay on Google's renderer; the Map Tiles API permits Leaflet and
leaves them alone.

WHO DECIDES is deliberately not answered here.  This module resolves
what an account HAS; whether they may have it is billing's question,
and billing writes the same setting this module reads.  Keeping the
decision out of the resolver is what lets the switch exist before the
plan does.

THE KEY IS OURS, one for the platform, and it lives in the environment
— not in ``account_settings``.  A per-account key would mean each
customer holding a Google Cloud billing account, which is the opposite
of what selling this is for.  The key is public by design (every tile
request the browser makes carries it) and is protected by an
HTTP-referrer restriction on Google's side, not by secrecy.

FAIL CLOSED TO THE FREE ENGINE.  An account set to ``google`` with no
key configured gets ``osm`` and a map that works, never a blank frame:
a missing key is our misconfiguration, and a customer must not read it
as a broken product.  Same for an engine name we do not know.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

#: The account-level choice.  Feature-owned key in ``account_settings``,
#: the Config store — account scope, because the map everyone looks at
#: is one truth for the whole account, not a per-role arrangement
#: (capabilities/config/docs/ARCHITECTURE.md, the blast-radius rule).
MAP_ENGINE_KEY = "map.engine"

OSM = "osm"
GOOGLE = "google"

#: Every engine the platform can draw.  The order is the order a picker
#: should offer them: the one that costs nothing first.
ENGINES = (OSM, GOOGLE)

#: Platform-wide, from the environment.  Empty means the platform has
#: not been given a Google project yet, and then nobody gets Google
#: however their account is set.
ENV_GOOGLE_KEY = "GOOGLE_MAPS_API_KEY"


def google_key() -> str:
    """The platform's Google Maps key (Map Tiles API), or "" when unset."""
    return (os.environ.get(ENV_GOOGLE_KEY) or "").strip()


def google_available() -> bool:
    """Whether the platform CAN draw Google at all.

    Separate from whether an account is set to it, so a picker can say
    "not configured on this server" rather than offering a choice that
    silently does nothing.
    """
    return bool(google_key())


def normalise(value: str | None) -> str:
    """An engine name we recognise, or the free one.

    A stored value we do not know is treated as absent rather than
    trusted — the same rule ``scope_with_role_default`` applies to a
    stored scope, and for the same reason: a typo must not decide.
    """
    name = (value or "").strip().lower()
    return name if name in ENGINES else OSM


def resolve(setting: str | None) -> str:
    """The engine an account actually gets, from what it asked for."""
    wanted = normalise(setting)
    if wanted == GOOGLE and not google_available():
        return OSM
    return wanted


async def for_account(account_id: int, tenant_db) -> dict:
    """What the client needs to draw this account's map.

    ``key`` is present only for the engine that needs one, and only
    when that engine is the resolved one — a client on the free engine
    is never handed a billable credential it might load anyway.

    ``requested`` says what the account asked for even when the answer
    differs, so a settings page can show "Google, but this server has
    no key" instead of silently reading back as OpenStreetMap.
    """
    requested = OSM
    try:
        requested = normalise(
            await tenant_db.get_account_setting(account_id, MAP_ENGINE_KEY, OSM))
    except Exception:
        # A settings read that fails is not a reason to break the map.
        requested = OSM
    engine = resolve(requested)
    out: dict = {
        "engine": engine,
        "requested": requested,
        "engines": list(ENGINES),
        "google_available": google_available(),
    }
    if engine == GOOGLE:
        out["key"] = google_key()
    return out


async def set_engine(account_id: int, tenant_db, value: str) -> dict:
    """Record what an account asked for, and answer with what it gets.

    Billing will call this when there is a plan to call it from; until
    then the owner does, from the map's own control.  The stored value
    is normalised first, so a typo cannot be written and then trusted.
    """
    wanted = normalise(value)
    await tenant_db.set_account_setting(account_id, MAP_ENGINE_KEY, wanted)
    return await for_account(account_id, tenant_db)


# ── Google Map Tiles sessions ──────────────────────────────────────
#
# The Map Tiles API draws Google's basemap INSIDE Leaflet — the product
# Google sells for exactly that ("third-party renderers", in their own
# policy).  Every tile request must carry a session token that names
# the map type; the session itself is free of quota, lasts two weeks,
# and "can be used across multiple clients", so the platform opens ONE
# per map type and hands it to every browser rather than letting each
# open its own.
#
# The token is created HERE and not in the browser for two reasons.
# Creating it needs the key with a referrer the key allows, which a
# server can send and a mis-restricted browser cannot; and the day an
# account is billed for Google, "how many sessions did this account
# draw" is a number we want to have been counting from the start.

#: Google's names for what our Map Type picker calls standard /
#: satellite / terrain.  Terrain is imagery that Google REQUIRES a road
#: layer on top of, which is why it carries an extra field.
TILE_TYPES: dict[str, dict] = {
    "roadmap":   {"mapType": "roadmap"},
    "satellite": {"mapType": "satellite"},
    "terrain":   {"mapType": "terrain", "layerTypes": ["layerRoadmap"]},
}

SESSION_URL = "https://tile.googleapis.com/v1/createSession"
TILE_URL = "https://tile.googleapis.com/v1/2dtiles/{z}/{x}/{y}"
VIEWPORT_URL = "https://tile.googleapis.com/tile/v1/viewport"

#: The referrer the key allows.  A server-side call carries no page,
#: so it says which site it acts for — the same one the tiles are
#: drawn on.
SESSION_REFERER = "https://4truck.us/"

#: A session is renewed this long before Google's expiry, so a client
#: that was handed a token an hour ago never watches it die mid-view.
RENEW_BEFORE_S = 24 * 3600


class TileSessionError(RuntimeError):
    """Google would not open a session: the key, its restrictions, the
    API not being enabled on the project, or the network.  The caller
    falls back to the free engine and says which."""


_sessions: dict[str, dict] = {}
_locks: dict[str, asyncio.Lock] = {}


def _lock(map_type: str) -> asyncio.Lock:
    lock = _locks.get(map_type)
    if lock is None:
        lock = _locks[map_type] = asyncio.Lock()
    return lock


async def _create_session(map_type: str, key: str) -> dict:
    """One POST to Google.  Kept separate so tests replace it."""
    import httpx
    body = {**TILE_TYPES[map_type], "language": "en-US", "region": "US"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(SESSION_URL, params={"key": key}, json=body,
                         headers={"Referer": SESSION_REFERER})
    if r.status_code != 200:
        raise TileSessionError(
            f"Google refused a {map_type} tile session: HTTP {r.status_code} "
            f"{r.text[:200]}")
    j = r.json()
    return {
        "session": j["session"],
        "expiry": int(j.get("expiry") or 0),
        "tile_size": int(j.get("tileWidth") or 256),
        "image_format": j.get("imageFormat") or "png",
    }


def _fresh(entry: dict | None, now: float) -> bool:
    return bool(entry) and (entry["expiry"] - now) > RENEW_BEFORE_S


async def tile_session(map_type: str, key: str, *, now: float | None = None) -> dict:
    """The platform's session for one map type — cached, renewed a day
    before it would expire, one creation at a time per type."""
    if map_type not in TILE_TYPES:
        raise ValueError(f"unknown tile type {map_type!r}")
    if not key:
        raise TileSessionError("no Google Maps key is configured")
    t = time.time() if now is None else now
    cached = _sessions.get(map_type)
    if _fresh(cached, t):
        return cached
    async with _lock(map_type):
        cached = _sessions.get(map_type)
        if _fresh(cached, t):
            return cached
        entry = await _create_session(map_type, key)
        _sessions[map_type] = entry
        logger.info("map tiles: new %s session, expires in %.1f days",
                    map_type, (entry["expiry"] - t) / 86400)
        return entry


def reset_sessions_for_tests() -> None:
    _sessions.clear()
    _locks.clear()


def tile_wire(map_type: str, entry: dict, key: str) -> dict:
    """What a browser needs to add the layer: the template Leaflet
    expands, plus what its attribution control must show.

    The key rides in the template because Google requires it on every
    tile request; it is the same public, referrer-restricted key the
    map already needs, not a second secret.
    """
    query = f"session={entry['session']}&key={key}"
    return {
        "type": map_type,
        "tile_url": f"{TILE_URL}?{query}",
        "viewport_url": f"{VIEWPORT_URL}?{query}",
        "tile_size": entry["tile_size"],
        "image_format": entry["image_format"],
        "expiry": entry["expiry"],
        "max_zoom": 22,
    }
