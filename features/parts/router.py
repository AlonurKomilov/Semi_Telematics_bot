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
    get_user_company_codes,
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


class PriceContextQuery(BaseModel):
    """The part names on the invoice being entered."""
    names: list[str] = Field(default_factory=list, max_length=100)
    months: int = Field(12, ge=1, le=60)


@router.post("/price-context")
async def price_context(
    body: PriceContextQuery,
    user: dict = Depends(
        require_permission_any("can_parts", "can_work_orders_all")
    ),
    tenant_db=Depends(get_tenant_db),
):
    """"Is this price normal?" for the lines on an invoice, answered
    from the account's OWN buying history.

    Declared BEFORE ``/{part_id}`` so the literal path wins over the
    int converter.  Keyed by NAME because the caller is a work order
    being typed or scanned — the line exists before any catalog row is
    resolved.  Read gate matches the catalog list (the work-order
    editor needs it), and parts with fewer than two prior purchases
    are simply absent from the response: one data point is a price,
    not a range.
    """
    # An account is not one company.  A user assigned to Company A must
    # not learn Company B's prices — and this response names the
    # cheapest VENDOR, so leaking it would disclose who the other
    # company buys from.  Same axis the work-order list filters on;
    # empty list = unrestricted (owners, unassigned users).
    codes = await get_user_company_codes(user)
    return {"context": await tenant_db.part_price_context(
        user["account_id"], body.names,
        company_codes=codes or None,
        months=body.months,
    )}


@router.get("/public/browse")
async def browse_public(
    q: str = "",
    user: dict = Depends(require_permission("can_parts")),
    tenant_db=Depends(get_tenant_db),
):
    """The Catalog tab: ACTIVE canonical part identities (operator-
    curated on the platform) + the caller's own link state.  Identity
    only — no usage data, nothing cross-account.  When market intel is
    live AND the account shares (give-to-get), each row also carries
    the NATIONAL typical range (published cells only — every one
    passed the 3-company rule)."""
    entries = await tenant_db.browse_part_directory(user["account_id"], q=q)
    if (await tenant_db.market_intel_enabled()
            and await tenant_db.get_market_sharing(user["account_id"])):
        national = await tenant_db.market_part_national_map()
        for e in entries:
            cell = national.get(e["id"])
            if cell:
                e["est_p25"] = cell["p25"]
                e["est_p75"] = cell["p75"]
    return {"entries": entries}


@router.get("/{part_id}")
async def get_part(
    part_id: int,
    user: dict = Depends(require_permission("can_parts")),
    tenant_db=Depends(get_tenant_db),
):
    """The drill-down: part record + recurrence per vehicle (with the
    mean-gap early-warning number) + price per vendor + purchase
    history (price-trend source).  Void invoices and drafts never
    count — same rule as every cost report.  When the part links to a
    public catalog entry, its identity rides along (category displays
    through this join — never copied onto the user's row)."""
    data = await tenant_db.part_analytics(part_id, user["account_id"])
    if not data:
        raise HTTPException(status_code=404, detail="Catalog part not found")
    public = None
    gid = data["part"].get("global_part_id")
    if gid:
        entry = await tenant_db.get_part_directory_entry(gid)
        if entry:
            public = {k: entry[k] for k in
                      ("id", "name", "category", "part_number",
                       "description", "status")}
    data["public"] = public

    # Geographic market estimates ("what should this part cost around
    # me?") — same triple gate as the vendor-side ranges: platform
    # switch ON, catalog-linked part, and the caller's account shares
    # its own data (give-to-get; count-only tease otherwise).
    if not await tenant_db.market_intel_enabled():
        data["market"] = {"available": False, "reason": "disabled"}
    elif not gid:
        data["market"] = {"available": False, "reason": "not_linked"}
    elif not await tenant_db.get_market_sharing(user["account_id"]):
        est = await tenant_db.market_part_estimates(gid)
        n = (1 if est["national"] else 0) + len(est["states"])
        data["market"] = {"available": False, "reason": "not_sharing",
                          "available_count": n}
    else:
        est = await tenant_db.market_part_estimates(gid)
        data["market"] = {"available": True, **est}
    return data


@router.post("/{part_id}/link-public/{entry_id}")
async def link_public(
    part_id: int,
    entry_id: int,
    user: dict = Depends(require_permission("can_parts")),
    tenant_db=Depends(get_tenant_db),
):
    """The dedup dialog's PUBLIC branch: this part IS that canonical
    catalog entry.  Non-destructive — the row and its invoice lines
    stay; an empty part_number fills from the entry.  Reversible."""
    if not await tenant_db.get_catalog_part(part_id, user["account_id"]):
        raise HTTPException(status_code=404, detail="Catalog part not found")
    ok = await tenant_db.link_part_to_public(
        user["account_id"], part_id, entry_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Public entry not found or not active")
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "part_public_link",
        target_type="part", target_id=str(part_id),
        details=f"linked to public entry #{entry_id}",
    )
    return {"ok": True}


@router.delete("/{part_id}/link-public")
async def unlink_public(
    part_id: int,
    user: dict = Depends(require_permission("can_parts")),
    tenant_db=Depends(get_tenant_db),
):
    """Unlink + SUPPRESS: the adopt fan-out will never silently
    re-link this row — the user's call sticks until they re-link."""
    if not await tenant_db.get_catalog_part(part_id, user["account_id"]):
        raise HTTPException(status_code=404, detail="Catalog part not found")
    await tenant_db.link_part_to_public(user["account_id"], part_id, None)
    return {"ok": True}


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
