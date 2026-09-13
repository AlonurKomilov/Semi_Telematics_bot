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

# USA-only POI restriction — clip viewport to US regions before querying.
# Per product requirement, POIs in Mexico/Canada/Caribbean are not displayed
# even when the visible viewport extends beyond the US border.
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
