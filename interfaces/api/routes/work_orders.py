"""Work Orders API — shop-invoice records, parts, attachments, cost reports.

Permissions mirror the Maintenance module so a fleet manager sees the
full picture and a driver sees only their own truck's records:

* ``can_maintenance_all`` — create / edit / delete any work order;
  upload + delete attachments.
* ``can_maintenance_own`` — read work orders for the driver's assigned
  truck; upload attachments to drafts for that truck (driver-from-the-
  shop workflow).

Object-store backend is account-agnostic at this layer — the route
calls ``get_object_store()`` and hands it the structured path returned
by ``capabilities/work_orders/storage.py``.  When per-account BYO
Drive ships, only the factory function changes; this code is unchanged.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from interfaces.api.deps import (
    get_current_user, get_platform_db, get_tenant_db,
    get_user_vehicle_nums, require_permission, resolve_user_id,
)
from capabilities.iam.permissions import can
from capabilities.maintenance.service import has_maintenance_access
from capabilities.work_orders.storage import (
    resolve_company_folder, safe_attachment_name, work_order_folder,
)

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
    company_code: str = ""
    vehicle_id: str = ""
    vendor_name: str = ""
    vendor_address: str = ""
    vendor_phone: str = ""
    service_date: Optional[str] = None
    odometer_at_service: Optional[float] = Field(None, ge=0)
    engine_hours_at_service: Optional[float] = Field(None, ge=0)
    labor_cost: float = Field(0.0, ge=0)
    parts_cost: float = Field(0.0, ge=0)
    tax_amount: float = Field(0.0, ge=0)
    total_cost: float = Field(0.0, ge=0)
    invoice_number: str = ""
    payment_method: str = ""
    payment_status: str = Field("unpaid", pattern=r"^(unpaid|paid|partial|void)$")
    status: str = Field("draft", pattern=r"^(draft|submitted|paid|void)$")
    notes: str = ""


class WorkOrderUpdate(BaseModel):
    vehicle_name: Optional[str] = Field(None, min_length=1)
    company_code: Optional[str] = None
    vehicle_id: Optional[str] = None
    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    vendor_phone: Optional[str] = None
    service_date: Optional[str] = None
    odometer_at_service: Optional[float] = Field(None, ge=0)
    engine_hours_at_service: Optional[float] = Field(None, ge=0)
    labor_cost: Optional[float] = Field(None, ge=0)
    parts_cost: Optional[float] = Field(None, ge=0)
    tax_amount: Optional[float] = Field(None, ge=0)
    total_cost: Optional[float] = Field(None, ge=0)
    invoice_number: Optional[str] = None
    payment_method: Optional[str] = None
    payment_status: Optional[str] = Field(None, pattern=r"^(unpaid|paid|partial|void)$")
    status: Optional[str] = Field(None, pattern=r"^(draft|submitted|paid|void)$")
    notes: Optional[str] = None


class PartCreate(BaseModel):
    part_name: str = Field(..., min_length=1, max_length=200)
    part_number: str = ""
    quantity: float = Field(1.0, gt=0)
    unit_cost: float = Field(0.0, ge=0)
    total_cost: float = Field(0.0, ge=0)
    warranty_months: int = Field(0, ge=0, le=1200)
    notes: str = ""


class LinkTasks(BaseModel):
    task_ids: list[int] = Field(..., min_length=1, max_length=200)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _driver_owns_vehicle(user: dict, vehicle_name: str, trucks: list[str]) -> bool:
    """Driver-side ownership check used by every read/write path."""
    if can(user["role"], "can_maintenance_all"):
        return True
    if not trucks:
        return False
    needles = {t.lower() for t in trucks}
    return any(n in (vehicle_name or "").lower() for n in needles)


async def _require_visible_work_order(
    work_order_id: int, user: dict, tenant_db,
) -> dict:
    """Fetch a work order and enforce per-truck visibility for drivers.

    Returns the work-order dict.  Raises 404 (not 403) for cross-account
    or cross-truck access so we don't leak existence to drivers who
    aren't supposed to see other trucks' records.
    """
    if not has_maintenance_access(user["role"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    wo = await tenant_db.get_work_order(work_order_id, account_id=user["account_id"])
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    if not can(user["role"], "can_maintenance_all"):
        trucks = await get_user_vehicle_nums(user)
        if not _driver_owns_vehicle(user, wo.get("vehicle_name", ""), trucks or []):
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
    if not has_maintenance_access(user["role"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    rows = await tenant_db.list_work_orders(
        user["account_id"],
        status=status, payment_status=payment_status, vehicle_name=vehicle,
    )
    if not can(user["role"], "can_maintenance_all"):
        trucks = await get_user_vehicle_nums(user)
        rows = [r for r in rows
                if _driver_owns_vehicle(user, r.get("vehicle_name", ""), trucks or [])]
    return {"work_orders": rows, "count": len(rows)}


@router.post("")
async def create_work_order(
    body: WorkOrderCreate,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Create a new work order.  Manager-only — drivers use the bot
    /invoice flow (which creates a draft on their behalf)."""
    internal_uid = await resolve_user_id(user)
    wo_id = await tenant_db.add_work_order(
        account_id=user["account_id"], created_by=internal_uid,
        **body.model_dump(),
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "work_order_create",
        target_type="work_order", target_id=str(wo_id),
        details=f"{body.vendor_name}: ${body.total_cost:.2f}",
    )
    return {"id": wo_id, "status": "created"}


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
        "attachments": attachments,
        "linked_tasks": tasks,
    }


@router.put("/{work_order_id}")
async def update_work_order(
    work_order_id: int,
    body: WorkOrderUpdate,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Update mutable fields on a work order."""
    wo = await tenant_db.get_work_order(work_order_id, account_id=user["account_id"])
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    ok = await tenant_db.update_work_order(
        work_order_id, account_id=user["account_id"], **updates,
    )
    if ok:
        await tenant_db.add_audit_log(
            user["account_id"], int(user["sub"]),
            "work_order_update",
            target_type="work_order", target_id=str(work_order_id),
            details=str(list(updates.keys())),
        )
    return {"ok": ok}


@router.delete("/{work_order_id}")
async def delete_work_order(
    work_order_id: int,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Delete a work order, its parts, its attachments (DB rows only —
    files in the object store are cleared first), and unlink any
    maintenance tasks that pointed at it."""
    from adapters.storage.object_store import get_object_store_for_account
    wo = await tenant_db.get_work_order(work_order_id, account_id=user["account_id"])
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")

    # Drop physical files first so deleting the row doesn't orphan them.
    # Best-effort — a missing object is fine (idempotent delete).
    store = await get_object_store_for_account(user["account_id"], tenant_db)
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
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "work_order_delete",
        target_type="work_order", target_id=str(work_order_id),
    )
    return {"deleted": deleted}


# ── Parts (line items) ───────────────────────────────────────────────────────


@router.post("/{work_order_id}/parts")
async def add_part(
    work_order_id: int,
    body: PartCreate,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    wo = await tenant_db.get_work_order(work_order_id, account_id=user["account_id"])
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    pid = await tenant_db.add_work_order_part(work_order_id, **body.model_dump())
    return {"id": pid}


@router.delete("/{work_order_id}/parts/{part_id}")
async def delete_part(
    work_order_id: int, part_id: int,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    wo = await tenant_db.get_work_order(work_order_id, account_id=user["account_id"])
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    ok = await tenant_db.delete_work_order_part(part_id)
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

    Drivers (``can_maintenance_own``) can upload to drafts for their
    own truck only — supports the bot/photo workflow where the driver
    photographs the invoice in the field and a manager fills in the
    cost details later.  Managers can upload to any work order.
    """
    from adapters.storage.object_store import get_object_store_for_account
    wo = await _require_visible_work_order(work_order_id, user, tenant_db)

    # Drivers locked to uploading on DRAFTS for their truck — once
    # paid/submitted, the manager owns the record.
    if not can(user["role"], "can_maintenance_all"):
        if wo.get("status") != "draft":
            raise HTTPException(
                status_code=403,
                detail="Drivers can only attach files to draft work orders.",
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
    store = await get_object_store_for_account(user["account_id"], tenant_db)
    file_path = store.put(folder, safe_name, raw)

    aid = await tenant_db.add_work_order_attachment(
        work_order_id,
        file_path=file_path, file_name=safe_name,
        file_size=len(raw), content_type=content_type, kind=kind,
        uploaded_by=await resolve_user_id(user),
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "work_order_attachment_upload",
        target_type="work_order", target_id=str(work_order_id),
        details=f"{kind}: {safe_name} ({len(raw)} bytes)",
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

    Read-through ``ObjectStore.get`` so the route works for any backend
    — Disk, Google Drive, S3 — without route changes.  Content-Disposition
    set to ``inline`` so PDFs open in the browser tab instead of forcing
    a download; the dashboard can override with a download button if
    needed.
    """
    from adapters.storage.object_store import get_object_store_for_account
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
    store = await get_object_store_for_account(user["account_id"], tenant_db)
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
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    from adapters.storage.object_store import get_object_store_for_account
    wo = await tenant_db.get_work_order(work_order_id, account_id=user["account_id"])
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
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
        store = await get_object_store_for_account(user["account_id"], tenant_db)
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
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Attach N maintenance tasks to this work order so cost
    aggregation (by task_type) can join through."""
    wo = await tenant_db.get_work_order(work_order_id, account_id=user["account_id"])
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    n = await tenant_db.link_maintenance_tasks_to_work_order(
        user["account_id"], work_order_id, body.task_ids,
    )
    return {"linked": n}


# ── Cost aggregation reports ─────────────────────────────────────────────────


@router.get("/reports/per-vehicle")
async def report_per_vehicle(
    days: int = Query(90, ge=1, le=3650),
    user: dict = Depends(require_permission("can_maintenance_all")),
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
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Spend per maintenance task_type (joins through work_order_id)."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await tenant_db.cost_by_task_type(user["account_id"], since=since)
    return {"days": days, "rows": rows}


@router.get("/reports/per-vendor")
async def report_per_vendor(
    days: int = Query(90, ge=1, le=3650),
    user: dict = Depends(require_permission("can_maintenance_all")),
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
    user: dict = Depends(require_permission("can_maintenance_all")),
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
    user: dict = Depends(require_permission("can_maintenance_all")),
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
