"""Where a POI request may look, and what is remembered of the answer.

The US clip bounds every layer whatever its source, and the cache holds
OSM features, vendor-directory rows and an account's own CSV points
alike — so neither the Overpass client nor the custom-layer code owns
this module; both borrow it.
"""
from __future__ import annotations

from cachetools import TTLCache


# Bounded TTL cache: (poi_type, bbox_rounded) → features.
# POI data is intentionally tenant-agnostic (public OSM data). If tenant-
# specific layers are added later, the cache key MUST include account_id.
_POI_CACHE_TTL = 300
_poi_cache: TTLCache = TTLCache(maxsize=500, ttl=_POI_CACHE_TTL)

_MAX_BBOX_AREA = 5000.0  # roughly North America (CONUS is ~2450 sq deg).

# THE CLIP IS TO THESE RECTANGLES, NOT TO A BORDER — and the difference
# is load-bearing, so do not "tighten" it without reading this.
#
# This comment used to say "per product requirement, POIs in Mexico,
# Canada and the Caribbean are not displayed".  That has never been what
# the code does: the CONUS rectangle runs 24.4N to 49.5N, which takes in
# northern Mexico at one end and southern Ontario and Quebec whole at the
# other, and the Alaska box reaches into British Columbia and the Yukon.
# Points there have always been served.
#
# As of 2026-09-17 that is the DECISION rather than the side-effect.  The
# owner's instruction, given after a pass had deleted 866 Petro-Canada
# stations to tidy the layer: a real diesel stop is filtered by the user,
# never removed by us — someone hauling north needs exactly the points
# that pass was throwing away.  Fuel Stations admits Petro-Canada by
# measurement now (features/live_map/poi/layers.py), points carry their
# region (`addr:*` in overpass.SERVICE_TAGS), and the map's brand chips
# let a driver who only runs the lower 48 switch them off.
#
# So these rectangles bound COST — how much of the planet we ask a
# volunteer mirror for — and no longer claim to bound a country.
# Narrowing them to the real border would silently delete every
# cross-border stop the layer now deliberately holds.  If that is ever
# wanted it is an owner's call, made in the open, not a tidy-up.
_USA_REGIONS: tuple[tuple[float, float, float, float], ...] = (
    (24.396308, -125.000000, 49.500000,  -66.500000),  # CONUS
    (51.000000, -179.500000, 71.500000, -129.000000),  # Alaska
    (18.500000, -161.000000, 22.500000, -154.500000),  # Hawaii
)


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
