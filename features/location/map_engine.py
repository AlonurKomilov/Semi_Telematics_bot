"""Which map an account's live map is drawn on.

Two engines, and the platform means to keep both:

  ``osm``     — Leaflet over OpenStreetMap and Esri tiles.  Free, no
                key, no per-view cost, and what every account gets
                today.
  ``google``  — Google's own renderer.  Carriers in the US already run
                their day on Google Maps, so the familiar map is worth
                paying for; Google bills per map load, against OUR
                cloud project, so an account only gets it when somebody
                decided it should.

WHO DECIDES is deliberately not answered here.  This module resolves
what an account HAS; whether they may have it is billing's question,
and billing writes the same setting this module reads.  Keeping the
decision out of the resolver is what lets the switch exist before the
plan does.

THE KEY IS OURS, one for the platform, and it lives in the environment
— not in ``account_settings``.  A per-account key would mean each
customer holding a Google Cloud billing account, which is the opposite
of what selling this is for.  The Maps JavaScript API key is public by
design (it is read by the browser that draws the map) and is protected
by an HTTP-referrer restriction on Google's side, not by secrecy.

FAIL CLOSED TO THE FREE ENGINE.  An account set to ``google`` with no
key configured gets ``osm`` and a map that works, never a blank frame:
a missing key is our misconfiguration, and a customer must not read it
as a broken product.  Same for an engine name we do not know.
"""

from __future__ import annotations

import os

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
    """The platform's Maps JavaScript API key, or "" when unset."""
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
