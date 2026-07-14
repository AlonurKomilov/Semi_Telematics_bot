"""Vendors API — per-account repair-vendor registry (Phase A).

Access model: ``can_work_orders_all`` for EVERYTHING, reads included.
Vendors is manager-side master data — a vendor profile aggregates the
whole account's work orders and spend for that shop, so vehicle-scoped
users (drivers see only their own truck's WOs on the WO endpoints)
must not read it: the profile would leak other trucks' records and
account-wide totals.  The permission pair is deliberately shared with
Work Orders (no separate can_vendors_* flags): same audience, and the
Permissions-matrix "Work Orders" row governs both surfaces coherently.

Merge is the human fix for typo-duplicates (auto-create from sync
guarantees they eventually exist).  It repoints work orders, records
the loser's name_key as an alias of the winner (so a later Datatruck
re-sync resolves to the winner instead of recreating the loser), and
deletes the loser — all strictly scoped to the caller's account.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from interfaces.api.deps import get_current_user, get_tenant_db, require_permission
from capabilities.permissions.roles import can_for_account, Role

router = APIRouter(prefix="/vendors", tags=["vendors"])


def _market_intel_enabled() -> bool:
    """Dark-launch flag for Phase D market intelligence.

    Deliberate TWIN of ``market_intel_enabled`` in
    capabilities/platform/vendor_directory/jobs.py — the features
    layer may not import from the platform sub-family (audience layer
    boundary, enforced by test_layer_boundaries), so both sides read
    the same env var independently.  Keep name + truthy set in sync."""
    import os
    return os.getenv("MARKET_INTEL_ENABLED", "0").strip() in ("1", "true", "TRUE", "yes")


async def _vendor_access(user: dict) -> bool:
    """Account-aware manager gate — ``can_work_orders_all`` only.
    Vehicle-scope work-order access does NOT grant vendor reads (a
    profile aggregates ALL trucks' records for the shop)."""
    role, acct = Role(user["role"]), user["account_id"]
    return await can_for_account(acct, role, "can_work_orders_all")


class VendorCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    address: str = ""
    phone: str = Field("", max_length=50)
    email: str = Field("", max_length=200)
    notes: str = ""


class VendorUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    address: str | None = None
    phone: str | None = Field(None, max_length=50)
    email: str | None = Field(None, max_length=200)
    notes: str | None = None


@router.get("")
async def list_vendors(
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Registry list with usage rollups (WO count, total spend, last
    visit) — powers the Vendors page and the form picker."""
    if not await _vendor_access(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"vendors": await tenant_db.list_vendors(user["account_id"])}


@router.get("/directory/search")
async def search_directory(
    q: str = "",
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """ACTIVE global-directory entries (identity fields only) for the
    link picker.  No account transaction data crosses here, ever."""
    if not await _vendor_access(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"entries": await tenant_db.search_directory_active(q)}


@router.get("/directory/browse")
async def browse_directory(
    q: str = "",
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """The public directory as a browsable list: identity fields, the
    anonymous rating aggregate, and whether one of the CALLER's vendors
    links to each entry.  Nothing account-attributable beyond the
    caller's own link status."""
    if not await _vendor_access(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"entries": await tenant_db.browse_directory(user["account_id"], q)}


# NOTE: single-segment literal routes — declared BEFORE /{vendor_id}
# so the int param cannot capture them.
class MarketSharingBody(BaseModel):
    enabled: bool


@router.get("/market-sharing")
async def get_market_sharing(
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Give-to-get consent state for this account + whether the
    feature is live at all (flag)."""
    if not await _vendor_access(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {
        "available": _market_intel_enabled(),
        "enabled": await tenant_db.get_market_sharing(user["account_id"]),
    }


@router.put("/market-sharing")
async def set_market_sharing(
    body: MarketSharingBody,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """Flip the account's give-to-get consent.  can_manage_account —
    sharing (anonymized) business data is an account-owner decision,
    not a fleet-manager one."""
    if not _market_intel_enabled():
        raise HTTPException(status_code=404, detail="Market intelligence is not enabled")
    await tenant_db.set_market_sharing(user["account_id"], body.enabled)
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "market_sharing_toggle",
        target_type="account", target_id=str(user["account_id"]),
        details="enabled" if body.enabled else "disabled",
    )
    return {"ok": True, "enabled": body.enabled}


@router.get("/{vendor_id}")
async def get_vendor(
    vendor_id: int,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Vendor profile: the record + its work-order history (the
    'every part bought, at what price, with what labor' view — parts
    detail hangs off each WO in the standard detail endpoint)."""
    if not await _vendor_access(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    vendor = await tenant_db.get_vendor(vendor_id, user["account_id"])
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    work_orders = await tenant_db.vendor_work_orders(vendor_id, user["account_id"])
    # Linked global-directory identity (identity fields only).
    directory = None
    if vendor.get("global_vendor_id"):
        entry = await tenant_db.get_directory_entry(vendor["global_vendor_id"])
        if entry and entry.get("status") == "active":
            directory = {k: entry[k] for k in
                         ("id", "name", "address", "phone", "email",
                          "website", "services")}
            # Community signal (approved-only, fully anonymized) + the
            # caller's own review so the UI can show its pending state.
            directory.update(await tenant_db.review_aggregate_for_entry(entry["id"]))
            directory["reviews"] = await tenant_db.approved_reviews_for_entry(entry["id"])
            directory["my_review"] = await tenant_db.get_my_vendor_review(
                user["account_id"], entry["id"],
            )
    return {"vendor": vendor, "work_orders": work_orders, "directory": directory}


@router.post("")
async def create_vendor(
    body: VendorCreate,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Create (idempotent on the normalized name — re-submitting an
    existing name returns that vendor, mirroring custom task types)."""
    if not await _vendor_access(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    vendor = await tenant_db.create_vendor(
        user["account_id"], body.name,
        address=body.address, phone=body.phone,
        email=body.email, notes=body.notes,
    )
    if not vendor:
        raise HTTPException(status_code=422, detail="Vendor name is empty")
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "vendor_create",
        target_type="vendor", target_id=str(vendor["id"]),
        details=vendor["name"],
    )
    return vendor


@router.put("/{vendor_id}")
async def update_vendor(
    vendor_id: int,
    body: VendorUpdate,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Edit contact/notes/name.  NEVER rewrites work-order snapshots —
    historical invoices keep saying what they said."""
    if not await _vendor_access(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    try:
        ok = await tenant_db.update_vendor(vendor_id, user["account_id"], **updates)
    except Exception:
        # UNIQUE(account_id, name_key) collision on rename → the right
        # fix is a merge, tell the operator exactly that.
        raise HTTPException(
            status_code=409,
            detail="Another vendor already has this name — merge them instead.",
        )
    if not ok:
        raise HTTPException(status_code=404, detail="Vendor not found")
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "vendor_update",
        target_type="vendor", target_id=str(vendor_id),
        details=", ".join(sorted(updates)),
    )
    return {"ok": True}


@router.post("/{loser_id}/merge-into/{winner_id}")
async def merge_vendors(
    loser_id: int,
    winner_id: int,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Fold a typo-duplicate into the real vendor.  Both ids are
    validated against the caller's account before anything moves."""
    if not await _vendor_access(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if loser_id == winner_id:
        raise HTTPException(status_code=422, detail="Cannot merge a vendor into itself")
    ok = await tenant_db.merge_vendors(user["account_id"], loser_id, winner_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Vendor not found")
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "vendor_merge",
        target_type="vendor", target_id=str(winner_id),
        details=f"merged #{loser_id} into #{winner_id}",
    )
    return {"ok": True}


@router.post("/{vendor_id}/suggest-to-directory")
async def suggest_to_directory(
    vendor_id: int,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Contribute this vendor's IDENTITY (name/contact only — never any
    transaction data) to the global directory as a pending suggestion
    for operator review.  If an entry with the same normalized name
    already exists, that identity comes back instead of a duplicate —
    and when it's already active we auto-link this vendor to it."""
    if not await _vendor_access(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    vendor = await tenant_db.get_vendor(vendor_id, user["account_id"])
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    # Quality gate: a name-only suggestion gives the reviewing operator
    # nothing to verify against the real world.  The dashboard collects
    # the address in the suggestion dialog before calling this.
    if not (vendor.get("address") or "").strip():
        raise HTTPException(
            status_code=422,
            detail="Add the shop's address before suggesting it to the directory.",
        )
    entry = await tenant_db.create_directory_entry(
        vendor["name"], address=vendor["address"], phone=vendor["phone"],
        email=vendor["email"],
        status="pending", source="suggestion",
        suggested_by_account=user["account_id"],
    )
    if not entry:
        raise HTTPException(status_code=422, detail="Vendor name is empty")
    linked = False
    if entry["status"] == "active":
        linked = await tenant_db.link_vendor_to_directory(
            user["account_id"], vendor_id, entry["id"],
        )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "vendor_directory_suggest",
        target_type="vendor", target_id=str(vendor_id),
        details=entry["name"],
    )
    # Identity-only response: status tells the user "pending review"
    # vs "already in the directory (and now linked)".
    return {"status": entry["status"], "linked": linked}


@router.post("/{vendor_id}/link-directory/{entry_id}")
async def link_directory(
    vendor_id: int,
    entry_id: int,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    if not await _vendor_access(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if not await tenant_db.get_vendor(vendor_id, user["account_id"]):
        raise HTTPException(status_code=404, detail="Vendor not found")
    ok = await tenant_db.link_vendor_to_directory(
        user["account_id"], vendor_id, entry_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Directory entry not found or not active")
    return {"ok": True}


@router.delete("/{vendor_id}/link-directory")
async def unlink_directory(
    vendor_id: int,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    if not await _vendor_access(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    ok = await tenant_db.link_vendor_to_directory(
        user["account_id"], vendor_id, None,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return {"ok": True}


class ReviewBody(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str = Field("", max_length=1000)


@router.post("/directory/{entry_id}/review")
async def review_directory_entry(
    entry_id: int,
    body: ReviewBody,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Rate a directory shop (anonymous stars + optional comment).

    Verified-usage gate: only accounts whose work orders actually link
    to this shop may review it.  One review per account per shop —
    resubmitting edits it and sends it back through moderation.  The
    review is displayed with NO attribution; account_id exists solely
    for uniqueness + operator audit."""
    if not await _vendor_access(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if not await tenant_db.review_eligible(user["account_id"], entry_id):
        raise HTTPException(
            status_code=403,
            detail="Reviews are limited to shops your work orders actually used.",
        )
    review = await tenant_db.upsert_vendor_review(
        user["account_id"], entry_id, body.rating, body.comment,
    )
    if not review:
        raise HTTPException(status_code=404, detail="Directory entry not found or not active")
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "vendor_review_submit",
        target_type="vendor_directory", target_id=str(entry_id),
        details=f"{body.rating}★",
    )
    return {"status": review["status"]}


# ── Market intelligence (Phase D — dark until MARKET_INTEL_ENABLED) ──




@router.get("/directory/{entry_id}/market")
async def market_for_entry(
    entry_id: int,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Anonymized typical price ranges for a directory shop.

    Triple gate: platform flag ON, caller has vendor access, AND the
    caller's account shares its own data (give-to-get).  The payload
    is the published rollup shape only — counts + p25/p75, nothing
    joinable back to any account."""
    if not await _vendor_access(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if not _market_intel_enabled():
        return {"available": False, "reason": "disabled", "rows": []}
    if not await tenant_db.get_market_sharing(user["account_id"]):
        return {"available": False, "reason": "not_sharing", "rows": []}
    return {
        "available": True,
        "reason": "",
        "rows": await tenant_db.market_rollups_for_entry(entry_id),
    }
