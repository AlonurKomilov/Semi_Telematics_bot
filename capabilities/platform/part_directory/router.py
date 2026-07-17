"""Public-parts-catalog operator API — /system/parts-directory/*.

Platform-operator only (``require_system_owner``): canonical entries
CRUD, alias mapping, bulk import, and the cross-account CANDIDATES
queue (promote / dismiss).  There is deliberately NO user contribution
pipeline — curation is top-down (see adapters/storage/part_directory.py
for the guardrails: generic-key blocklist, dismissal tombstones,
alias≠entry-key, unlink suppression).  Account-facing reads live on
the parts feature router (active identities only) — never here.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from interfaces.api.deps import get_tenant_db, require_system_owner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system/parts-directory", tags=["system"])


class EntryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    category: str = Field("", max_length=100)
    part_number: str = Field("", max_length=100)
    description: str = Field("", max_length=1000)


class EntryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    category: Optional[str] = Field(None, max_length=100)
    part_number: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=1000)
    status: Optional[str] = Field(None, pattern=r"^(active|archived)$")


class AliasCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class PromoteBody(BaseModel):
    raw_key: str = Field(..., min_length=1, max_length=200)
    canonical_name: str = Field(..., min_length=1, max_length=200)
    category: str = Field("", max_length=100)
    part_number: str = Field("", max_length=100)
    description: str = Field("", max_length=1000)


class DismissBody(BaseModel):
    raw_key: str = Field(..., min_length=1, max_length=200)


class ImportBody(BaseModel):
    rows: list[EntryCreate] = Field(..., max_length=2000)


@router.get("")
async def list_entries(
    status: Optional[str] = Query(None, pattern=r"^(active|archived)$"),
    q: str = Query("", max_length=200),
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    return {"entries": await tenant_db.list_part_directory(
        status=status or "", q=q,
    )}


@router.post("")
async def create_entry(
    body: EntryCreate,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """Born active; creation fires the adopt fan-out.  Blocklisted
    generic names and key collisions come back as 422 with the
    storage layer's exact message."""
    try:
        entry = await tenant_db.create_part_directory_entry(
            body.name, category=body.category,
            part_number=body.part_number, description=body.description,
            source="manual",
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not entry:
        raise HTTPException(status_code=422, detail="Name is empty")
    return entry


@router.put("/{entry_id}")
async def update_entry(
    entry_id: int,
    body: EntryUpdate,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    try:
        ok = await tenant_db.update_part_directory_entry(entry_id, **updates)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}


@router.post("/{entry_id}/aliases")
async def add_alias(
    entry_id: int,
    body: AliasCreate,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """Map a raw name variant to this entry; re-fires adoption so
    accounts using the variant link up."""
    try:
        ok = await tenant_db.add_part_directory_alias(entry_id, body.name)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Entry not found or name empty")
    return {"ok": True}


@router.delete("/aliases/{alias_id}")
async def delete_alias(
    alias_id: int,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    if not await tenant_db.delete_part_directory_alias(alias_id):
        raise HTTPException(status_code=404, detail="Alias not found")
    return {"ok": True}


@router.get("/candidates")
async def candidates(
    min_accounts: int = Query(2, ge=1, le=50),
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """Cross-account curation signal: name_keys used by ≥ N accounts
    that match nothing yet.  Operator-only data by design — the
    account-facing browse never carries usage numbers."""
    return {"candidates": await tenant_db.part_directory_candidates(
        min_accounts=min_accounts,
    )}


@router.post("/candidates/promote")
async def promote_candidate(
    body: PromoteBody,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """One click: canonical entry (source 'promoted') + raw key as
    alias + adopt fan-out across every account."""
    try:
        entry = await tenant_db.promote_part_candidate(
            body.raw_key, body.canonical_name,
            category=body.category, part_number=body.part_number,
            description=body.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not entry:
        raise HTTPException(status_code=422, detail="Canonical name is empty")
    return entry


@router.post("/candidates/dismiss")
async def dismiss_candidate(
    body: DismissBody,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """Tombstone a candidate key so the queue stays workable."""
    if not await tenant_db.dismiss_part_candidate(body.raw_key):
        raise HTTPException(status_code=422, detail="Key is empty")
    return {"ok": True}


@router.post("/import")
async def import_entries(
    body: ImportBody,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """Bulk create; per-row outcome so a blocklisted or duplicate row
    never aborts the batch."""
    created, skipped = 0, []
    for row in body.rows:
        try:
            entry = await tenant_db.create_part_directory_entry(
                row.name, category=row.category,
                part_number=row.part_number, description=row.description,
                source="import",
            )
            if entry:
                created += 1
            else:
                skipped.append({"name": row.name, "reason": "empty name"})
        except ValueError as e:
            skipped.append({"name": row.name, "reason": str(e)})
    return {"created": created, "skipped": skipped}
