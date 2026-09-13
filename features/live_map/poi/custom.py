"""Per-tenant POI layers — the ones an account drew for itself.

Everything here except the DTOs is about serving ONE custom layer; the
routes that create and edit them live in router.py, because they are
HTTP.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from interfaces.bot.state import get_tenant_db

from . import overpass
from .overpass import _MAX_CUSTOM_OVERPASS_LEN
from .viewport import _poi_cache, _round_bbox

logger = logging.getLogger(__name__)


# ── Custom (per-tenant) POI layers ────────────────────────────────────────────
#
# Three source types:
#   1. Pin-drop (brand sample) — server reads closest OSM POI's brand tag and
#      auto-builds a CONUS Overpass query for it. Stored same as overpass-source.
#   2. Overpass query (advanced) — admin pastes a raw OSM filter expression.
#      Validated against a whitelist; server adds bbox + header.
#   3. CSV upload — static (name, lat, lng, brand?) rows stored in
#      custom_poi_points and served from DB (no Overpass).

# CSV upload limits
_CSV_MAX_BYTES = 5 * 1024 * 1024
_CSV_MAX_ROWS = 50_000


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
                features = await overpass._fetch_overpass([query], bbox)
            except Exception as exc:
                # The built-in layers stopped doing this the week the
                # owner opened Chicago and read "None in this view": a
                # source that did not answer was becoming an empty list,
                # and the empty list was being CACHED for five minutes,
                # so the wrong answer outlived the outage that caused
                # it.  That fix landed one branch over and left this one
                # — a custom layer is not a lesser layer.
                logger.warning("custom Overpass layer %s failed: %s", layer_id, exc)
                raise HTTPException(
                    status_code=502,
                    detail="The map-data source is not answering — try again shortly.",
                ) from exc

    _poi_cache[cache_key] = features
    out = {"type": "FeatureCollection", "features": features}
    if src == "overpass":
        # A CSV layer is the account's own file and has nothing to do
        # with OSM's extract date — saying one would be a wrong answer.
        out["source_as_of"] = overpass.source_as_of()
    return out


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


class _PreviewPinRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class _FromBrandRequest(BaseModel):
    brand: str = Field(..., min_length=1, max_length=80)
    amenity: Optional[str] = Field(default=None, max_length=40)
    label: Optional[str] = Field(default=None, min_length=1, max_length=80)
    color: Optional[str] = Field(default=None, pattern="^#[0-9a-fA-F]{6}$")
    icon: Optional[str] = Field(default=None, max_length=8)
    default_on: bool = False


class _CsvUpload(BaseModel):
    """CSV body sent as JSON to avoid the python-multipart dependency."""
    csv: str = Field(..., max_length=_CSV_MAX_BYTES + 1)
