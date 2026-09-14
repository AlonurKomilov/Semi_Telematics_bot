"""Vendor-directory operator API — /system/vendor-directory/*.

Platform-operator only (``require_system_owner``): list with the
pending-suggestions queue first, create/edit entries, and approve /
reject account suggestions.  Account-facing reads live on the vendors
feature router (active entries, identity fields only) — never here.
"""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp
from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from interfaces.api.deps import get_tenant_db, require_system_owner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system/vendor-directory", tags=["system"])

# ── Geocoding (Nominatim proxy) ───────────────────────────────────────
#
# Operator-triggered only (require_system_owner) so call volume is a
# handful per curation session — far inside Nominatim's 1 req/s usage
# policy.  The cache absorbs repeat lookups of the same address, and
# results are SUGGESTIONS: nothing is stored until the operator
# confirms the pin (PUT /{id}/geo).

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_UA = "4truck-system-dashboard/1.0 (+https://4truck.us)"
_geocode_cache: TTLCache = TTLCache(maxsize=200, ttl=3600)


async def _nominatim_search(q: str) -> list[dict]:
    """Address → up to 5 coordinate suggestions.  Split out so tests
    can stub the network hop."""
    session = aiohttp.ClientSession()
    try:
        async with session.get(
            _NOMINATIM_URL,
            params={
                "q": q,
                "format": "jsonv2",
                "limit": 5,
                "countrycodes": "us",
            },
            headers={"User-Agent": _NOMINATIM_UA},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            raw = await resp.json()
    finally:
        await session.close()
    return [
        {
            "lat": float(r["lat"]),
            "lng": float(r["lon"]),
            "label": str(r.get("display_name") or ""),
        }
        for r in raw
        if r.get("lat") is not None and r.get("lon") is not None
    ]


class DirectoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    address: str = ""
    phone: str = Field("", max_length=50)
    email: str = Field("", max_length=200)
    website: str = Field("", max_length=300)
    services: str = Field("", max_length=500)
    notes: str = ""
    # Family label for multi-location brands ("TA / Petro") — one entry
    # per LOCATION (numbered name), chain groups them.  '' = independent.
    chain: str = Field("", max_length=80)


class DirectoryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    address: Optional[str] = None
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=200)
    website: Optional[str] = Field(None, max_length=300)
    services: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = None
    chain: Optional[str] = Field(None, max_length=80)


@router.get("")
async def list_directory(
    status: Optional[str] = Query(None, pattern=r"^(pending|active|rejected)$"),
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """All entries (pending first) or one status bucket."""
    return {"entries": await tenant_db.list_vendor_directory(status=status)}


@router.post("")
async def create_entry(
    body: DirectoryCreate,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """Operator-curated entry — born active.  Idempotent on the global
    normalized name (a duplicate returns the existing identity)."""
    entry = await tenant_db.create_directory_entry(
        body.name, address=body.address, phone=body.phone,
        email=body.email, website=body.website,
        services=body.services, notes=body.notes,
        status="active", source="operator", chain=body.chain,
    )
    if not entry:
        raise HTTPException(status_code=422, detail="Name is empty")
    # Operator-added shops adopt matching account vendors immediately.
    if entry.get("status") == "active":
        await tenant_db.adopt_matching_vendors(entry["id"])
    return entry


@router.put("/{entry_id}")
async def update_entry(
    entry_id: int,
    body: DirectoryUpdate,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    try:
        ok = await tenant_db.update_directory_entry(entry_id, **updates)
    except Exception:
        raise HTTPException(
            status_code=409,
            detail="Another directory entry already has this name.",
        )
    if not ok:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}


class ImportRow(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    chain: str = Field("", max_length=80)
    address: str = Field("", max_length=300)
    phone: str = Field("", max_length=50)
    website: str = Field("", max_length=300)
    services: str = Field("", max_length=500)
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)


class ImportBody(BaseModel):
    entries: list[ImportRow] = Field(..., min_length=1, max_length=2000)


@router.post("/import")
async def import_entries(
    body: ImportBody,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """Bulk curation: chain location lists (official directories,
    Overture/OSM extracts) land as ACTIVE, geocoded, chain-labelled
    entries; matching account vendors adopt immediately.  Existing
    names are skipped, never overwritten."""
    rows = [r.model_dump() for r in body.entries]
    result = await tenant_db.import_directory_entries(rows)
    return result


class GeoUpdate(BaseModel):
    """Both set, or both null to clear."""
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)


@router.get("/geocode")
async def geocode_address(
    q: str = Query(..., min_length=3, max_length=300),
    user: dict = Depends(require_system_owner),
):
    """Coordinate SUGGESTIONS for an address — operator picks/adjusts
    and confirms via PUT /{id}/geo; nothing is stored here."""
    key = q.strip().lower()
    cached = _geocode_cache.get(key)
    if cached is not None:
        return {"results": cached}
    try:
        results = await _nominatim_search(q.strip())
    except Exception as exc:
        logger.warning("Nominatim geocode failed for %r: %s", q, exc)
        raise HTTPException(
            status_code=502,
            detail="Geocoding service unavailable — try again "
                   "or enter coordinates manually.",
        )
    _geocode_cache[key] = results
    return {"results": results}


@router.put("/{entry_id}/geo")
async def set_entry_geo(
    entry_id: int,
    body: GeoUpdate,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """Operator-confirmed coordinates (or clear with both null).  The
    single writer of directory geo — generic PUT never touches it."""
    if (body.lat is None) != (body.lng is None):
        raise HTTPException(
            status_code=422, detail="lat and lng must be set together",
        )
    ok = await tenant_db.set_directory_geo(entry_id, body.lat, body.lng)
    if not ok:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}


@router.post("/{entry_id}/approve")
async def approve_entry(
    entry_id: int,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """Suggestion → live directory identity.  Approval fans out: every
    account's unlinked vendor with the same normalized name auto-links
    and inherits the curated contact fields (empty fields only)."""
    ok = await tenant_db.update_directory_entry(entry_id, status="active")
    if not ok:
        raise HTTPException(status_code=404, detail="Entry not found")
    adopted = await tenant_db.adopt_matching_vendors(entry_id)
    return {"ok": True, "vendors_linked": adopted}


@router.post("/{entry_id}/reject")
async def reject_entry(
    entry_id: int,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """Kept as a tombstone (not deleted) so the same suggestion doesn't
    bounce back through the queue forever."""
    ok = await tenant_db.update_directory_entry(entry_id, status="rejected")
    if not ok:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}


@router.get("/reviews")
async def list_reviews(
    status: str = Query("pending", pattern=r"^(pending|approved|rejected)$"),
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """Moderation queue — reviews with the shop name and the bare
    suggesting-account id (audit only)."""
    return {"reviews": await tenant_db.list_reviews_moderation(status=status)}


@router.post("/reviews/{review_id}/approve")
async def approve_review(
    review_id: int,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    ok = await tenant_db.set_review_status(review_id, "approved")
    if not ok:
        raise HTTPException(status_code=404, detail="Review not found")
    return {"ok": True}


@router.post("/reviews/{review_id}/reject")
async def reject_review(
    review_id: int,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    ok = await tenant_db.set_review_status(review_id, "rejected")
    if not ok:
        raise HTTPException(status_code=404, detail="Review not found")
    return {"ok": True}
