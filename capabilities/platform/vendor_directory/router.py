"""Vendor-directory operator API — /system/vendor-directory/*.

Platform-operator only (``require_system_owner``): list with the
pending-suggestions queue first, create/edit entries, and approve /
reject account suggestions.  Account-facing reads live on the vendors
feature router (active entries, identity fields only) — never here.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from interfaces.api.deps import get_tenant_db, require_system_owner

router = APIRouter(prefix="/system/vendor-directory", tags=["system"])


class DirectoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    address: str = ""
    phone: str = Field("", max_length=50)
    email: str = Field("", max_length=200)
    website: str = Field("", max_length=300)
    services: str = Field("", max_length=500)
    notes: str = ""


class DirectoryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    address: Optional[str] = None
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=200)
    website: Optional[str] = Field(None, max_length=300)
    services: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = None


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
        status="active", source="operator",
    )
    if not entry:
        raise HTTPException(status_code=422, detail="Name is empty")
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


@router.post("/{entry_id}/approve")
async def approve_entry(
    entry_id: int,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """Suggestion → live directory identity."""
    ok = await tenant_db.update_directory_entry(entry_id, status="active")
    if not ok:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}


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
