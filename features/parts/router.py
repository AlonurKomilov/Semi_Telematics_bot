"""Parts API — per-account parts master data + per-part analytics.

Graduated from a Work Orders component to its own feature (the
vendor-parts master-data plan's §1b threshold: it grew a standalone
management screen).  Work Orders CONSUMES parts (line resolve at save
time via the shared ``Database``, autocomplete via ``GET /parts``); it
never owns them.

Access model: ``can_parts`` — the feature-owned gate (seeded for
owner/admin/fleet + the accounting senior tier).  The one shared read
is the catalog LIST, which also feeds the work-order editor's parts
autocomplete: it accepts ``can_work_orders_all`` too, so an account
that narrows ``can_parts`` doesn't silently break invoice entry.
Everything else — analytics, edit, merge — is strictly ``can_parts``
(a part profile aggregates the whole account's spend, so vehicle-scoped
users must not read it).

Wire compat: the pre-graduation URLs (``/work-orders/parts-catalog…``)
stay mounted below as DEPRECATED aliases binding the very same handler
functions — the alias==primary contract is test-enforced.  New callers
use ``/parts``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from interfaces.api.deps import (
    get_tenant_db,
    require_permission,
    require_permission_any,
)

router = APIRouter(prefix="/parts", tags=["parts"])


@router.get("")
async def list_parts(
    user: dict = Depends(
        require_permission_any("can_parts", "can_work_orders_all")
    ),
    tenant_db=Depends(get_tenant_db),
):
    """Catalog with usage rollups — the Parts page grid AND the
    work-order editor's autocomplete (hence the widened read gate)."""
    return {"parts": await tenant_db.list_parts_catalog(user["account_id"])}


class PartCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    part_number: str = Field("", max_length=100)
    notes: str = Field("", max_length=2000)


@router.post("")
async def create_part(
    body: PartCreate,
    user: dict = Depends(require_permission("can_parts")),
    tenant_db=Depends(get_tenant_db),
):
    """Add a part before its first invoice.  Resolve semantics (same
    contract as Add-vendor): if the name already resolves — directly
    or via a merge alias — the existing row returns with
    ``created: false`` and the typed part_number/notes are NOT applied;
    the UI says so and navigates there instead of faking a create."""
    part, created = await tenant_db.create_catalog_part(
        user["account_id"], body.name,
        part_number=body.part_number.strip(),
        notes=body.notes.strip(),
    )
    if not part:
        raise HTTPException(status_code=422, detail="Part name is empty")
    if created:
        await tenant_db.add_audit_log(
            user["account_id"], int(user["sub"]),
            "part_catalog_create",
            target_type="part", target_id=str(part["id"]),
            details=part["name"],
        )
    return {"part": part, "created": created}


@router.get("/{part_id}")
async def get_part(
    part_id: int,
    user: dict = Depends(require_permission("can_parts")),
    tenant_db=Depends(get_tenant_db),
):
    """The drill-down: part record + recurrence per vehicle (with the
    mean-gap early-warning number) + price per vendor + purchase
    history (price-trend source).  Void invoices and drafts never
    count — same rule as every cost report."""
    data = await tenant_db.part_analytics(part_id, user["account_id"])
    if not data:
        raise HTTPException(status_code=404, detail="Catalog part not found")
    return data


class PartUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    part_number: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = Field(None, max_length=2000)


@router.put("/{part_id}")
async def update_part(
    part_id: int,
    body: PartUpdate,
    user: dict = Depends(require_permission("can_parts")),
    tenant_db=Depends(get_tenant_db),
):
    """Edit name/part-number/notes.  Line-row ``part_name`` snapshots
    stay invoice-truth — renames never rewrite history."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    try:
        ok = await tenant_db.update_catalog_part(
            part_id, user["account_id"], **updates,
        )
    except Exception:
        # UNIQUE(account_id, name_key) collision on rename → the right
        # fix is a merge, tell the operator exactly that.
        raise HTTPException(
            status_code=409,
            detail="Another part already has this name — merge them instead.",
        )
    if not ok:
        raise HTTPException(status_code=404, detail="Catalog part not found")
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "part_catalog_update",
        target_type="part", target_id=str(part_id),
        details=", ".join(sorted(updates)),
    )
    return {"ok": True}


@router.post("/{loser_id}/merge-into/{winner_id}")
async def merge_parts(
    loser_id: int,
    winner_id: int,
    user: dict = Depends(require_permission("can_parts")),
    tenant_db=Depends(get_tenant_db),
):
    """Fold a duplicate catalog part into the canonical one (same
    contract as vendor merge: repoint lines, alias the loser's key so
    re-syncs resolve to the survivor, delete the loser)."""
    if loser_id == winner_id:
        raise HTTPException(status_code=422, detail="Cannot merge a part into itself")
    ok = await tenant_db.merge_catalog_parts(user["account_id"], loser_id, winner_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Catalog part not found")
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "part_catalog_merge",
        target_type="part", target_id=str(winner_id),
        details=f"merged #{loser_id} into #{winner_id}",
    )
    return {"ok": True}


# ── DEPRECATED aliases (pre-graduation URLs) ──────────────────────
# Same handler objects, so behavior can never drift from the primary
# routes (alias==primary is pinned by test).  Remove after the next
# frontend-cache cycle.
legacy_router = APIRouter(prefix="/work-orders", tags=["parts-legacy"])
legacy_router.get("/parts-catalog", deprecated=True)(list_parts)
legacy_router.post(
    "/parts-catalog/{loser_id}/merge-into/{winner_id}", deprecated=True,
)(merge_parts)
