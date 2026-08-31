"""Work Orders API — shop-invoice records, parts, attachments, cost reports.

Permissions are work-order-specific (defaults mirror Maintenance, but an
account can grant/revoke them separately) so a fleet manager sees the
full picture and a driver sees only their own truck's records:

* ``can_work_orders_all`` — create / edit / delete any work order;
  upload + delete attachments.
* ``can_work_orders_vehicle`` — read work orders for the driver's assigned
  truck; upload attachments to drafts for that truck (driver-from-the-
  shop workflow).

Object-store backend is account-agnostic at this layer — the route
calls ``get_object_storage()`` and hands it the structured path returned
by ``capabilities/work_orders/storage.py``.  When per-account BYO
Drive ships, only the factory function changes; this code is unchanged.
"""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may;
# service/alert/ai_tool/signal modules never do.


from __future__ import annotations

from typing import Optional

from fastapi import (
    APIRouter, Depends, File, HTTPException, Query, Request, UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from interfaces.api.rate_limit import limiter

# Lifecycle vocabulary (Fleetio-standard): open → in_progress →
# completed (no void — a mistaken WO is deleted).  Legacy
# draft/submitted/closed/void are accepted at the write boundary for
# one release (stale dashboard bundles) and normalized here, so the
# stored value is always current.
from adapters.storage.work_orders import normalize_wo_status

from interfaces.api.deps import (
    get_current_user, get_platform_db, get_tenant_db,
    get_user_vehicle_nums, require_permission, resolve_user_id,
    get_user_company_codes, filter_by_allowed_companies,
)
from capabilities.activity_trail import delete_changes, diff_rows, new_group_id, record_simple
from capabilities.permissions.roles import can_for_account, Role
from capabilities.object_storage.paths import resolve_company_folder
from features.work_orders.paths import safe_attachment_name, work_order_folder

router = APIRouter(prefix="/work-orders", tags=["work-orders"])

# Attachment upload constraints.  Matches files.py (10 MB) so the user
# can't sneak past one limit by using the other route.  Allowed types
# cover the realistic shop-invoice and photo formats — block exotic
# types so we don't host risky uploads.
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "image/heic", "image/heif",
}
_ALLOWED_KINDS = {"invoice", "photo", "warranty", "receipt", "other"}


# ── Pydantic models ──────────────────────────────────────────────────────────


class WorkOrderCreate(BaseModel):
    """Payload for creating a work order.  All cost fields default to
    zero so a driver can submit a photo-first draft and the manager
    fills in numbers later."""
    vehicle_name: str = Field(..., min_length=1)
    vehicle_type: str = Field("", pattern=r"^(truck|trailer|)$")
    company_code: str = ""
    vehicle_id: str = ""
    vendor_name: str = ""
    vendor_address: str = ""
    vendor_phone: str = ""
    # Contact for the vendor REGISTRY only (enrich-on-save) — work
    # orders keep no email snapshot (not invoice truth).
    vendor_email: str = ""
    # Registry link (features/vendors).  Snapshot fields above stay the
    # invoice truth; this id is the analytical spine.
    vendor_id: Optional[int] = None
    service_date: Optional[str] = None
    odometer_at_service: Optional[float] = Field(None, ge=0)
    engine_hours_at_service: Optional[float] = Field(None, ge=0)
    labor_cost: float = Field(0.0, ge=0)
    parts_cost: float = Field(0.0, ge=0)
    tax_amount: float = Field(0.0, ge=0)
    fee_amount: float = Field(0.0, ge=0)
    total_cost: float = Field(0.0, ge=0)
    invoice_number: str = ""
    payment_method: str = ""
    payment_status: str = Field("unpaid", pattern=r"^(unpaid|paid|partial|void)$")
    status: str = Field("open", pattern=r"^(open|in_progress|completed|draft|submitted|closed|void)$")
    # Reason-for-repair class (VMRS-style): planned upkeep vs unplanned
    # firefighting.  '' = unclassified.
    repair_priority: str = Field("", pattern=r"^(scheduled|non_scheduled|emergency|)$")

    @field_validator("status")
    @classmethod
    def _norm_status(cls, v: str) -> str:
        return normalize_wo_status(v) or "open"
    # 3C repair documentation (DOT / warranty standard).
    complaint: str = ""
    cause: str = ""
    correction: str = ""
    notes: str = ""
    assigned_to: str = ""


class WorkOrderUpdate(BaseModel):
    vehicle_name: Optional[str] = Field(None, min_length=1)
    vehicle_type: Optional[str] = Field(None, pattern=r"^(truck|trailer|)$")
    company_code: Optional[str] = None
    vehicle_id: Optional[str] = None
    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    vendor_phone: Optional[str] = None
    vendor_email: Optional[str] = None
    vendor_id: Optional[int] = None
    service_date: Optional[str] = None
    odometer_at_service: Optional[float] = Field(None, ge=0)
    engine_hours_at_service: Optional[float] = Field(None, ge=0)
    labor_cost: Optional[float] = Field(None, ge=0)
    parts_cost: Optional[float] = Field(None, ge=0)
    tax_amount: Optional[float] = Field(None, ge=0)
    fee_amount: Optional[float] = Field(None, ge=0)
    total_cost: Optional[float] = Field(None, ge=0)
    invoice_number: Optional[str] = None
    payment_method: Optional[str] = None
    payment_status: Optional[str] = Field(None, pattern=r"^(unpaid|paid|partial|void)$")
    status: Optional[str] = Field(None, pattern=r"^(open|in_progress|completed|draft|submitted|closed|void)$")
    repair_priority: Optional[str] = Field(None, pattern=r"^(scheduled|non_scheduled|emergency|)$")
    complaint: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _norm_status(cls, v: Optional[str]) -> Optional[str]:
        return normalize_wo_status(v) if v is not None else None
    cause: Optional[str] = None
    correction: Optional[str] = None
    notes: Optional[str] = None
    assigned_to: Optional[str] = None


class PartCreate(BaseModel):
    part_name: str = Field(..., min_length=1, max_length=200)
    part_number: str = ""
    quantity: float = Field(1.0, gt=0)
    unit_cost: float = Field(0.0, ge=0)
    total_cost: float = Field(0.0, ge=0)
    warranty_months: int = Field(0, ge=0, le=1200)
    # Task-type slug this part line belongs to ('brakes', 'oil',
    # 'custom_…'); shares the maintenance task-type vocabulary.  '' =
    # untagged.  Free-form (not a pattern) because custom slugs are
    # account-defined.
    service_task: str = Field("", max_length=100)
    # Catalog link — normally omitted; the server resolves it from
    # part_name on save.
    part_id: Optional[int] = None
    notes: str = ""


class LinkTasks(BaseModel):
    task_ids: list[int] = Field(..., min_length=1, max_length=200)


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _wo_access(user: dict) -> tuple[bool, bool]:
    """Account-aware work-order permissions for *user*: ``(can_all, has_any)``.

    Resolves through ``can_for_account`` so per-account overrides set in
    the Role Permissions matrix take effect — not just the role defaults.
    """
    role, acct = Role(user["role"]), user["account_id"]
    can_all = await can_for_account(acct, role, "can_work_orders_all")
    can_own = await can_for_account(acct, role, "can_work_orders_vehicle")
    return can_all, (can_all or can_own)


def _driver_owns_vehicle(
    can_all: bool, vehicle_name: str, trucks: list[str], scope=None,
) -> bool:
    """Driver-side ownership check.  ``can_all`` is the account-aware
    'all trucks' permission, computed once by the caller.

    A ``VehicleScope`` decides by the identity ladder; without one the
    fallback is exact lowercased equality — never the old substring,
    which let a driver assigned 230 open work orders for 2303.  This
    wall answers "may this driver see this record", so an over-match
    was a disclosure.
    """
    if can_all:
        return True
    if scope is not None:
        return (not scope.empty) and scope.allows(name=vehicle_name)
    if not trucks:
        return False
    allowed = {t.strip().lower() for t in trucks if t}
    return (vehicle_name or "").strip().lower() in allowed


async def _require_visible_work_order(
    work_order_id: int, user: dict, tenant_db,
) -> dict:
    """Fetch a work order and enforce per-truck visibility for drivers.

    Returns the work-order dict.  Raises 404 (not 403) for cross-account
    or cross-truck access so we don't leak existence to drivers who
    aren't supposed to see other trucks' records.
    """
    can_all, has_access = await _wo_access(user)
    if not has_access:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    wo = await tenant_db.get_work_order(work_order_id, account_id=user["account_id"])
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    _allowed = await get_user_company_codes(user)
    if _allowed and not filter_by_allowed_companies([wo], _allowed, key="company_code"):
        raise HTTPException(status_code=404, detail="Work order not found")
    if not can_all:
        from interfaces.api.deps import get_user_vehicle_scope
        scope = await get_user_vehicle_scope(user)
        trucks = await get_user_vehicle_nums(user)
        if not _driver_owns_vehicle(
            can_all, wo.get("vehicle_name", ""), trucks or [], scope=scope,
        ):
            raise HTTPException(status_code=404, detail="Work order not found")
    return wo


async def _build_user_name_map(account_id: int, platform_db) -> dict[int, str]:
    """telegram_id → display_name for the uploader-name enrichment."""
    try:
        users = await platform_db.list_account_users(account_id)
        return {int(u.telegram_id): (u.display_name or str(u.telegram_id)) for u in users}
    except Exception:
        return {}


# ── List + CRUD ──────────────────────────────────────────────────────────────


@router.get("")
async def list_work_orders(
    status: Optional[str] = Query(None),
    payment_status: Optional[str] = Query(None),
    vehicle: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """List work orders for the account with optional filters."""
    can_all, has_access = await _wo_access(user)
    if not has_access:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    rows = await tenant_db.list_work_orders(
        user["account_id"],
        status=status, payment_status=payment_status, vehicle_name=vehicle,
    )
    rows = filter_by_allowed_companies(rows, await get_user_company_codes(user), key="company_code")
    if not can_all:
        from interfaces.api.deps import get_user_vehicle_scope
        scope = await get_user_vehicle_scope(user)
        trucks = await get_user_vehicle_nums(user)
        rows = [r for r in rows
                if _driver_owns_vehicle(
                    can_all, r.get("vehicle_name", ""), trucks or [],
                    scope=scope,
                )]
    return {"work_orders": rows, "count": len(rows)}


@router.post("")
async def create_work_order(
    body: WorkOrderCreate,
    user: dict = Depends(require_permission("can_work_orders_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Create a new work order.  Manager-only — drivers use the bot
    /invoice flow (which creates a draft on their behalf)."""
    internal_uid = await resolve_user_id(user)
    payload = body.model_dump()
    # Registry-only contact: popped BEFORE **payload (work_orders has
    # no email column) and fed to enrich-on-save below.
    vendor_email = payload.pop("vendor_email", "") or ""
    # Vendor registry auto-link: a free-typed vendor name (no id from
    # the picker) resolves-or-creates its registry row so every saved
    # WO is linked.  Snapshot fields still store exactly what was
    # typed; only the id is derived.
    if payload.get("vendor_name") and not payload.get("vendor_id"):
        _v = await tenant_db.resolve_or_create_vendor(
            user["account_id"], payload["vendor_name"],
            address=payload.get("vendor_address") or "",
            phone=payload.get("vendor_phone") or "",
            email=vendor_email,
        )
        if _v:
            payload["vendor_id"] = _v["id"]
    wo_id = await tenant_db.add_work_order(
        account_id=user["account_id"], created_by=internal_uid,
        **payload,
    )
    await record_simple(
        tenant_db, user["account_id"], internal_uid,
        "create", "work_order", wo_id,
        changes=diff_rows({}, {
            "vehicle_name": payload.get("vehicle_name"),
            "vendor_name": payload.get("vendor_name"),
            "total_cost": payload.get("total_cost"),
            "service_date": payload.get("service_date"),
            "status": payload.get("status"),
        }),
    )
    return {"id": wo_id, "status": "created"}


# Parts master data GRADUATED to features/parts (own /parts prefix +
# can_parts gate).  The old /work-orders/parts-catalog URLs live on
# there as deprecated aliases; this router only CONSUMES parts via
# ``resolve_or_create_part`` on line saves.


@router.post("/extract-invoice")
@limiter.limit("30/hour")
async def extract_invoice_fields(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(require_permission("can_work_orders_all")),
):
    """Read a shop invoice (photo/PDF) → WO-shaped fields for the form.

    TRANSIENT by design: nothing is persisted here — no attachment, no
    WO.  The form pre-fills from the response, the human reviews, and
    only Save writes (in create mode the form then uploads the same
    file as the ``invoice`` attachment once the WO id exists).  Gated
    on ``can_work_orders_all`` — the permission matrix, not a role, is
    the access SSOT.  Rate-limited because every call is a paid
    vision-model request (the ocr-cdl precedent).
    """
    from features.work_orders.extraction import EXTRACT_MIMES, extract_invoice
    from infra.file_safety import sniff_mime

    raw = await file.read()
    if len(raw) > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {_MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB limit.",
        )
    if not raw:
        raise HTTPException(status_code=422, detail="Empty file.")

    # Magic bytes are the truth, not the declared Content-Type (a paid
    # model call deserves the same rigor as the public intake).  HEIC
    # isn't in the sniffer's vocabulary — verify its ISO-BMFF ``ftyp``
    # box instead, keeping iPhone photos first-class like the
    # attachment whitelist does.
    declared = (file.content_type or "").lower()
    mime = sniff_mime(raw)
    if mime is None and declared in ("image/heic", "image/heif") \
            and len(raw) > 12 and raw[4:8] == b"ftyp":
        mime = declared
    if mime not in EXTRACT_MIMES:
        raise HTTPException(
            status_code=415,
            detail="Please upload an invoice photo (JPG/PNG/WEBP/HEIC) or PDF.",
        )

    result = await extract_invoice(
        raw, mime,
        account_id=user["account_id"],
        user_id=int(user["sub"]), role=user.get("role"),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "Extraction failed."))
    return result


@router.get("/{work_order_id}")
async def get_work_order(
    work_order_id: int,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Fetch a single work order with its parts, attachments, and
    linked maintenance tasks.  Single round trip for the dashboard
    detail view."""
    wo = await _require_visible_work_order(work_order_id, user, tenant_db)
    parts = await tenant_db.list_work_order_parts(work_order_id)
    labor = await tenant_db.list_work_order_labor(
        work_order_id, user["account_id"],
    )
    attachments = await tenant_db.list_work_order_attachments(work_order_id)
    tasks = await tenant_db.list_tasks_for_work_order(
        work_order_id, account_id=user["account_id"],
    )
    # Enrich attachment uploader names so the UI can show
    # "Uploaded by John Doe" instead of a raw telegram_id.
    name_map = await _build_user_name_map(user["account_id"], platform_db)
    for a in attachments:
        uid = a.get("uploaded_by")
        if uid:
            n = name_map.get(int(uid))
            if n:
                a["uploaded_by_name"] = n
    return {
        "work_order": wo,
        "parts": parts,
        "labor": labor,
        "attachments": attachments,
        "linked_tasks": tasks,
    }


@router.put("/{work_order_id}")
async def update_work_order(
    work_order_id: int,
    body: WorkOrderUpdate,
    user: dict = Depends(require_permission("can_work_orders_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Update mutable fields on a work order."""
    old = await _require_visible_work_order(work_order_id, user, tenant_db)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    vendor_email = updates.pop("vendor_email", "") or ""
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    # Same auto-link on rename: a changed vendor_name without an
    # explicit vendor_id re-resolves so the link follows the edit.
    if updates.get("vendor_name") and "vendor_id" not in updates:
        _v = await tenant_db.resolve_or_create_vendor(
            user["account_id"], updates["vendor_name"],
            address=updates.get("vendor_address") or "",
            phone=updates.get("vendor_phone") or "",
            email=vendor_email,
        )
        if _v:
            updates["vendor_id"] = _v["id"]
    ok = await tenant_db.update_work_order(
        work_order_id, account_id=user["account_id"], **updates,
    )
    if ok:
        # Values, not field names — the old log's str(list(keys)) is
        # exactly what made the maintenance incident unrecoverable.
        changes = diff_rows(old or {}, updates, fields=updates.keys())
        if changes:
            await record_simple(
                tenant_db, user["account_id"], await resolve_user_id(user),
                "update", "work_order", work_order_id,
                changes=changes,
            )
    return {"ok": ok}


@router.delete("/{work_order_id}")
async def delete_work_order(
    work_order_id: int,
    user: dict = Depends(require_permission("can_work_orders_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Delete a work order, its parts, its attachments (DB rows only —
    files in the object store are cleared first), and unlink any
    maintenance tasks that pointed at it."""
    from adapters.storage.object_storage import get_object_storage_for_account
    wo = await _require_visible_work_order(work_order_id, user, tenant_db)

    # Drop physical files first so deleting the row doesn't orphan them.
    # Best-effort — a missing object is fine (idempotent delete).
    store = await get_object_storage_for_account(user["account_id"], tenant_db)
    company_folder = await resolve_company_folder(
        tenant_db, user["account_id"], wo.get("company_code", ""),
    )
    folder = work_order_folder(
        company_folder=company_folder,
        work_order_id=work_order_id,
        vehicle_name=wo.get("vehicle_name", ""),
        service_date=wo.get("service_date"),
        vendor_name=wo.get("vendor_name", ""),
    )
    for att in await tenant_db.list_work_order_attachments(work_order_id):
        try:
            store.delete(folder, att.get("file_name", ""))
        except Exception:
            pass

    deleted = await tenant_db.delete_work_order(
        work_order_id, account_id=user["account_id"],
    )
    group_id = new_group_id()
    if deleted:
        # The recovery record: the whole row body, {from, to: null}.
        # ``group_id`` (a group of one) gives the UI its undo handle.
        await record_simple(
            tenant_db, user["account_id"], await resolve_user_id(user),
            "delete", "work_order", work_order_id,
            changes=delete_changes(wo or {}), group_id=group_id,
        )
    return {"deleted": deleted, "group_id": group_id}


# ── Parts (line items) ───────────────────────────────────────────────────────


@router.post("/{work_order_id}/parts")
async def add_part(
    work_order_id: int,
    body: PartCreate,
    user: dict = Depends(require_permission("can_work_orders_all")),
    tenant_db=Depends(get_tenant_db),
):
    await _require_visible_work_order(work_order_id, user, tenant_db)
    payload = body.model_dump()
    # Parts-catalog auto-link: every saved line resolves-or-creates its
    # catalog row (exact-normalized, alias-aware) so per-part analytics
    # work no matter how the name was typed.  ``part_name`` stays the
    # invoice-truth snapshot.
    if payload.get("part_name"):
        _cp = await tenant_db.resolve_or_create_part(
            user["account_id"], payload["part_name"],
            part_number=payload.get("part_number") or "",
        )
        if _cp:
            payload["part_id"] = _cp["id"]
    pid = await tenant_db.add_work_order_part(work_order_id, **payload)
    return {"id": pid}


@router.delete("/{work_order_id}/parts/{part_id}")
async def delete_part(
    work_order_id: int, part_id: int,
    user: dict = Depends(require_permission("can_work_orders_all")),
    tenant_db=Depends(get_tenant_db),
):
    await _require_visible_work_order(work_order_id, user, tenant_db)
    ok = await tenant_db.delete_work_order_part(part_id)
    return {"ok": ok}


# ── Labor lines (Tier-2 B1) ──────────────────────────────────────────────────


class LaborCreate(BaseModel):
    description: str = Field(..., min_length=1, max_length=300)
    hours: float = Field(0.0, ge=0)
    rate: float = Field(0.0, ge=0)
    total_cost: float = Field(0.0, ge=0)
    service_task: str = Field("", max_length=100)


@router.post("/{work_order_id}/labor")
async def add_labor(
    work_order_id: int,
    body: LaborCreate,
    user: dict = Depends(require_permission("can_work_orders_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Add an itemized labor charge.  The parent's labor_cost (and
    total_cost) recompute server-side — lines are the derived truth
    whenever any exist."""
    await _require_visible_work_order(work_order_id, user, tenant_db)
    lid = await tenant_db.add_work_order_labor(
        work_order_id, user["account_id"], **body.model_dump(),
    )
    return {"id": lid}


@router.delete("/{work_order_id}/labor/{line_id}")
async def delete_labor(
    work_order_id: int, line_id: int,
    user: dict = Depends(require_permission("can_work_orders_all")),
    tenant_db=Depends(get_tenant_db),
):
    await _require_visible_work_order(work_order_id, user, tenant_db)
    ok = await tenant_db.delete_work_order_labor(
        line_id, user["account_id"], work_order_id,
    )
    return {"ok": ok}


# ── Attachments (invoice PDFs, shop photos, warranty docs) ───────────────────


@router.post("/{work_order_id}/attachments")
async def upload_attachment(
    work_order_id: int,
    file: UploadFile = File(...),
    kind: str = "other",
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Upload an attachment to a work order.

    Drivers (``can_work_orders_vehicle``) can upload to drafts for their
    own truck only — supports the bot/photo workflow where the driver
    photographs the invoice in the field and a manager fills in the
    cost details later.  Managers can upload to any work order.
    """
    from adapters.storage.object_storage import get_object_storage_for_account
    from capabilities.object_storage.tracking import track_for_sync_if_hybrid
    wo = await _require_visible_work_order(work_order_id, user, tenant_db)

    # Drivers may upload only while the WO is still ACTIVE (open /
    # in_progress) for their truck — that's the photo-first window (at
    # the shop, invoice in hand).  Once it's closed the manager owns
    # the record.
    if not await can_for_account(user["account_id"], Role(user["role"]), "can_work_orders_all"):
        if wo.get("status") not in ("open", "in_progress"):
            raise HTTPException(
                status_code=403,
                detail="Drivers can only attach files to open work orders.",
            )

    if kind not in _ALLOWED_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of {sorted(_ALLOWED_KINDS)}",
        )

    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {content_type or 'unknown'}",
        )

    raw = await file.read()
    if len(raw) > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {_MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB limit.",
        )

    safe_name = safe_attachment_name(file.filename or "attachment")
    company_folder = await resolve_company_folder(
        tenant_db, user["account_id"], wo.get("company_code", ""),
    )
    folder = work_order_folder(
        company_folder=company_folder,
        work_order_id=work_order_id,
        vehicle_name=wo.get("vehicle_name", ""),
        service_date=wo.get("service_date"),
        vendor_name=wo.get("vendor_name", ""),
    )
    store = await get_object_storage_for_account(user["account_id"], tenant_db)
    file_path = store.put(folder, safe_name, raw)

    aid = await tenant_db.add_work_order_attachment(
        work_order_id,
        file_path=file_path, file_name=safe_name,
        file_size=len(raw), content_type=content_type, kind=kind,
        uploaded_by=await resolve_user_id(user),
    )
    # Enqueue for cloud sync AFTER the row exists — the queue carries the
    # attachment id so the worker can repoint that row before it frees
    # the local copy.  No-op unless the account is on the hybrid backend.
    await track_for_sync_if_hybrid(
        store, folder, safe_name, file_path,
        entity_type="work_order_attachment", entity_id=int(aid),
        file_size=len(raw),
    )
    await record_simple(
        tenant_db, user["account_id"], await resolve_user_id(user),
        "attachment_add", "work_order", work_order_id,
        note=f"{kind}: {safe_name} ({len(raw)} bytes)",
    )
    return {
        "id": aid, "file_name": safe_name, "size": len(raw),
        "content_type": content_type, "kind": kind,
    }


@router.get("/{work_order_id}/attachments/{attachment_id}")
async def download_attachment(
    work_order_id: int, attachment_id: int,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Stream an attachment's bytes back to the client.

    Read-through ``ObjectStorage.get`` so the route works for any backend
    — Disk, Google Drive, S3 — without route changes.  Content-Disposition
    set to ``inline`` so PDFs open in the browser tab instead of forcing
    a download; the dashboard can override with a download button if
    needed.
    """
    from adapters.storage.object_storage import get_object_storage_for_account
    wo = await _require_visible_work_order(work_order_id, user, tenant_db)
    att = await tenant_db.get_work_order_attachment(attachment_id)
    if not att or att.get("work_order_id") != work_order_id:
        raise HTTPException(status_code=404, detail="Attachment not found")

    company_folder = await resolve_company_folder(
        tenant_db, user["account_id"], wo.get("company_code", ""),
    )
    folder = work_order_folder(
        company_folder=company_folder,
        work_order_id=work_order_id,
        vehicle_name=wo.get("vehicle_name", ""),
        service_date=wo.get("service_date"),
        vendor_name=wo.get("vendor_name", ""),
    )
    store = await get_object_storage_for_account(user["account_id"], tenant_db)
    data = store.get(folder, att["file_name"])
    if data is None:
        raise HTTPException(status_code=404, detail="File not found in storage")

    return StreamingResponse(
        iter([data]),
        media_type=att.get("content_type") or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{att["file_name"]}"',
            "Cache-Control": "private, max-age=600",
        },
    )


@router.delete("/{work_order_id}/attachments/{attachment_id}")
async def delete_attachment(
    work_order_id: int, attachment_id: int,
    user: dict = Depends(require_permission("can_work_orders_all")),
    tenant_db=Depends(get_tenant_db),
):
    from adapters.storage.object_storage import get_object_storage_for_account
    wo = await _require_visible_work_order(work_order_id, user, tenant_db)
    att = await tenant_db.get_work_order_attachment(attachment_id)
    if not att or att.get("work_order_id") != work_order_id:
        raise HTTPException(status_code=404, detail="Attachment not found")

    company_folder = await resolve_company_folder(
        tenant_db, user["account_id"], wo.get("company_code", ""),
    )
    folder = work_order_folder(
        company_folder=company_folder,
        work_order_id=work_order_id,
        vehicle_name=wo.get("vehicle_name", ""),
        service_date=wo.get("service_date"),
        vendor_name=wo.get("vendor_name", ""),
    )
    try:
        store = await get_object_storage_for_account(user["account_id"], tenant_db)
        store.delete(folder, att["file_name"])
    except Exception:
        # Continue with DB delete even if storage delete fails — better
        # to have an orphan file than an orphan DB row pointing at
        # already-deleted bytes.
        pass
    ok = await tenant_db.delete_work_order_attachment(attachment_id)
    return {"ok": ok}


# ── Maintenance-task linking ─────────────────────────────────────────────────


@router.post("/{work_order_id}/link-tasks")
async def link_tasks(
    work_order_id: int,
    body: LinkTasks,
    user: dict = Depends(require_permission("can_work_orders_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Attach N maintenance tasks to this work order so cost
    aggregation (by task_type) can join through."""
    await _require_visible_work_order(work_order_id, user, tenant_db)
    n = await tenant_db.link_maintenance_tasks_to_work_order(
        user["account_id"], work_order_id, body.task_ids,
    )
    return {"linked": n}


# ── Cost aggregation reports ─────────────────────────────────────────────────


@router.get("/reports/per-vehicle")
async def report_per_vehicle(
    days: int = Query(90, ge=1, le=3650),
    user: dict = Depends(require_permission("can_cost_reports")),
    tenant_db=Depends(get_tenant_db),
):
    """Spend per vehicle over ``days`` (default 90)."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await tenant_db.cost_by_vehicle(user["account_id"], since=since)
    return {"days": days, "rows": rows}


@router.get("/reports/per-task-type")
async def report_per_task_type(
    days: int = Query(90, ge=1, le=3650),
    user: dict = Depends(require_permission("can_cost_reports")),
    tenant_db=Depends(get_tenant_db),
):
    """Spend per maintenance task_type (joins through work_order_id)."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await tenant_db.cost_by_task_type(user["account_id"], since=since)
    return {"days": days, "rows": rows}


@router.get("/reports/per-service-task")
async def report_per_service_task(
    days: int = Query(90, ge=1, le=3650),
    user: dict = Depends(require_permission("can_cost_reports")),
    tenant_db=Depends(get_tenant_db),
):
    """Parts spend per service-task tag, summed at the PART level so a
    mixed invoice splits correctly across tasks.  ``untagged`` rows
    keep unclassified spend visible."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await tenant_db.cost_by_service_task(user["account_id"], since=since)
    return {"days": days, "rows": rows}


@router.get("/reports/per-system")
async def report_per_system(
    days: int = Query(90, ge=1, le=3650),
    user: dict = Depends(require_permission("can_cost_reports")),
    tenant_db=Depends(get_tenant_db),
):
    """Spend per SYSTEM — "what are brakes costing us?".  Rows whose
    task has no system land in 'Unassigned' so the total reconciles."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return {"days": days,
            "rows": await tenant_db.cost_by_system(user["account_id"], since=since)}


@router.get("/reports/per-assembly")
async def report_per_assembly(
    system: str,
    days: int = Query(90, ge=1, le=3650),
    user: dict = Depends(require_permission("can_cost_reports")),
    tenant_db=Depends(get_tenant_db),
):
    """Assemblies within one system — the drill-down under a system
    bar.  PARTS ONLY (labor has no part and never reaches level 2 —
    the UI labels this permanently); 'Unassigned' stays a visible row
    so the parts total reconciles."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return {"days": days, "system": system,
            "rows": await tenant_db.cost_by_assembly(
                user["account_id"], system, since=since)}


@router.get("/reports/per-part")
async def report_per_part(
    days: int = Query(90, ge=1, le=3650),
    user: dict = Depends(require_permission("can_cost_reports")),
    tenant_db=Depends(get_tenant_db),
):
    """Usage + spend per part name — the "which part keeps costing us"
    early-warning list (a part recurring across many work orders flags
    a failing-component pattern)."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await tenant_db.cost_by_part(user["account_id"], since=since)
    return {"days": days, "rows": rows}


@router.get("/reports/per-vendor")
async def report_per_vendor(
    days: int = Query(90, ge=1, le=3650),
    user: dict = Depends(require_permission("can_cost_reports")),
    tenant_db=Depends(get_tenant_db),
):
    """Spend per vendor — feeds the 'who do we send the most money to'
    list useful for bulk-rate negotiation."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await tenant_db.cost_by_vendor(user["account_id"], since=since)
    return {"days": days, "rows": rows}


@router.get("/reports/summary")
async def report_summary(
    days: int = Query(90, ge=1, le=3650),
    user: dict = Depends(require_permission("can_cost_reports")),
    tenant_db=Depends(get_tenant_db),
):
    """Headline totals for the current window AND the equivalent-length
    prior window, side-by-side, so the dashboard can render % delta
    chips on the summary cards.

    Example: ``days=90`` returns spend/count/vendors for the last 90
    days AND for days 91-180 (the "prior quarter") so the user sees
    "Total spend $24,887 (+12% vs prior 90d)".
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    current_since = (now - timedelta(days=days)).isoformat()
    prior_since = (now - timedelta(days=days * 2)).isoformat()
    prior_until = current_since   # inclusive/exclusive boundary

    current = await tenant_db.cost_summary_in_window(
        user["account_id"], since=current_since,
    )
    prior = await tenant_db.cost_summary_in_window(
        user["account_id"], since=prior_since, until=prior_until,
    )

    # Compute deltas server-side so the dashboard doesn't need to
    # divide-by-zero guard.  ``None`` for "prior period had nothing
    # to compare to" — the UI renders that as a different chip (just
    # the current value, no arrow).
    def _delta_pct(curr: float, prev: float) -> Optional[float]:
        if prev <= 0:
            return None
        return round(((curr - prev) / prev) * 100.0, 1)

    avg_curr = (current["total_spent"] / current["work_order_count"]) if current["work_order_count"] else 0
    avg_prev = (prior["total_spent"] / prior["work_order_count"]) if prior["work_order_count"] else 0

    return {
        "days": days,
        "current": current,
        "prior": prior,
        "delta_pct": {
            "total_spent": _delta_pct(current["total_spent"], prior["total_spent"]),
            "work_order_count": _delta_pct(current["work_order_count"], prior["work_order_count"]),
            "avg_per_wo": _delta_pct(avg_curr, avg_prev),
            "vendor_count": _delta_pct(current["vendor_count"], prior["vendor_count"]),
        },
        "avg_per_wo": {
            "current": round(avg_curr, 2),
            "prior": round(avg_prev, 2),
        },
    }


@router.get("/reports/monthly-trend")
async def report_monthly_trend(
    days: int = Query(365, ge=30, le=3650),
    user: dict = Depends(require_permission("can_cost_reports")),
    tenant_db=Depends(get_tenant_db),
):
    """Spend grouped by calendar month — drives the trend chart on the
    Cost Reports page.  Default window is 12 months because a meaningful
    trend needs at least 6-12 data points.  Below 30 days the chart
    would be one or two bars; below 7 it would be a single point.
    """
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await tenant_db.cost_by_month(user["account_id"], since=since)
    return {"days": days, "rows": rows}
