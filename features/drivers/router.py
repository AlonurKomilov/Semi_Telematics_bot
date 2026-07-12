"""Driver Module — profile + vehicle-assignment + document API.

The Driver Module mirrors the Vehicle module shape but lives on the
``users`` table (drivers stay a special role, not a separate entity).
Routes:

  Profile / list
    GET    /api/drivers                          — list all drivers
    GET    /api/drivers/{user_id}                — detail + assignments
    PUT    /api/drivers/{user_id}                — update profile fields

  Vehicle assignments (single source of truth, history-preserving)
    GET    /api/drivers/{user_id}/assignments    — active + history
    POST   /api/drivers/{user_id}/assignments    — assign a vehicle
    DELETE /api/drivers/assignments/{id}         — end an active assignment

  Documents (Google Drive when linked, local-disk fallback otherwise)
    GET    /api/drivers/{user_id}/documents      — list
    POST   /api/drivers/{user_id}/documents      — multipart upload
    GET    /api/drivers/documents/{doc_id}       — download (resolve via DB)
    DELETE /api/drivers/documents/{doc_id}       — delete (admin only)
    GET    /api/drivers/documents/expiring       — account-wide expiring list
"""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): ONLY router.py may import interfaces.api.deps;
# service/alert/ai_tool/signal modules never do.


from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from interfaces.api.deps import (
    get_platform_db, get_tenant_db, get_current_db_user,
    require_permission, require_permission_any,
    get_user_company_codes, filter_by_company_map,
)
from adapters.storage import Role
from adapters.storage.drivers import VALID_DOC_TYPES
from capabilities.permissions.roles import can, role_rank

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/drivers", tags=["drivers"])


# Upload constraints — same shape as work_orders for consistency.
_MAX_DOC_BYTES = 20 * 1024 * 1024   # 20 MB per file
_ALLOWED_MIME = frozenset({
    "application/pdf",
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
})


# ── Request bodies ─────────────────────────────────────────────


class ProfileUpdate(BaseModel):
    """Driver-profile fields admins can update.  Same allow-list as
    the adapter's ``update_driver_profile``; the adapter silently
    drops unknown keys, but Pydantic gives the API caller an
    immediate 422 with a clear error."""
    cdl_number:       Optional[str] = None
    cdl_state:        Optional[str] = Field(None, max_length=4)
    cdl_class:        Optional[str] = Field(None, max_length=2)
    cdl_expires:      Optional[str] = None   # ISO date
    med_card_expires: Optional[str] = None
    hire_date:        Optional[str] = None
    termination_date: Optional[str] = None
    dob:              Optional[str] = None
    phone:            Optional[str] = Field(None, max_length=32)
    home_address:     Optional[str] = None
    driver_notes:     Optional[str] = None
    samsara_driver_id: Optional[str] = None


class AssignmentCreate(BaseModel):
    vehicle_name: str = Field(..., min_length=1)
    vehicle_id:   Optional[str] = None
    is_primary:   bool = False
    notes:        Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────


def _driver_visibility_check(target_user_id: int, caller: dict) -> bool:
    """Is the caller allowed to see this driver?

    Admins / fleet managers / safety with ``can_manage_driver_docs``
    see everyone in their account.  Drivers with ``can_driver_docs_own``
    see only their own row.  Anyone else: 404 (don't leak existence).
    """
    if caller.get("_matched_perm") == "can_driver_docs_own":
        return int(caller["sub"]) == 0  # placeholder — resolved below
    return True


async def _resolve_caller_user_id(caller: dict, platform_db) -> int:
    """Map JWT subject (telegram_id) → platform users.id."""
    db_user = await platform_db.get_user_by_telegram_id(int(caller["sub"]))
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    return db_user.id


async def _require_driver_visibility(
    target_user_id: int, caller: dict, platform_db,
) -> dict:
    """Resolve the target driver + enforce visibility rules.

    Returns the target ``users`` row.  Raises 404 if the target
    doesn't exist, doesn't belong to the caller's account, or the
    caller is a driver and the target isn't them.

    The admin check uses ``can(role, ...)`` rather than
    ``caller["_matched_perm"]`` because the latter is only populated
    by ``require_permission_any``; routes that use the singular
    ``require_permission`` (the assign / upload / delete admin
    paths) don't carry the hint.
    """
    caller_id = await _resolve_caller_user_id(caller, platform_db)
    target = await platform_db.get_user_by_id(target_user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Driver not found")
    if target.account_id != caller["account_id"]:
        # Don't reveal existence cross-account.
        raise HTTPException(status_code=404, detail="Driver not found")
    is_admin = can(caller["role"], "can_manage_driver_docs")
    if not is_admin and caller_id != target_user_id:
        raise HTTPException(status_code=404, detail="Driver not found")
    # Company scope: a company-restricted admin can't reach a driver
    # outside their companies by guessing the id (the list already hides
    # them).  Fail-open: a driver with no company assignment stays visible.
    allowed = await get_user_company_codes(caller)
    if allowed:
        driver_cos = await platform_db.get_user_company_codes(target_user_id)
        if driver_cos and not (
            {c.upper() for c in driver_cos} & {c.upper() for c in allowed}
        ):
            raise HTTPException(status_code=404, detail="Driver not found")
    return target


def _profile_to_dict(p) -> dict:
    """``DriverProfile`` dataclass → JSON dict (snake_case)."""
    return {
        "user_id":           p.user_id,
        "account_id":        p.account_id,
        "display_name":      p.display_name,
        "telegram_id":       p.telegram_id,
        "samsara_driver_id": p.samsara_driver_id,
        "cdl_number":        p.cdl_number,
        "cdl_state":         p.cdl_state,
        "cdl_class":         p.cdl_class,
        "cdl_expires":       p.cdl_expires,
        "med_card_expires":  p.med_card_expires,
        "hire_date":         p.hire_date,
        "termination_date":  p.termination_date,
        "dob":               p.dob,
        "phone":             p.phone,
        "home_address":      p.home_address,
        "driver_notes":      p.driver_notes,
        # Primary vehicle assignment (denormalised cache from
        # ``driver_vehicle_assignments``).  PTI's "New Inspection"
        # picker uses this to pre-fill the vehicle field when a driver
        # is selected; consumers that don't need it can ignore.
        "vehicle_name":      getattr(p, "vehicle_name", None),
    }


def _assignment_to_dict(a) -> dict:
    return {
        "id":            a.id,
        "account_id":    a.account_id,
        "user_id":       a.user_id,
        "vehicle_name":  a.vehicle_name,
        "vehicle_id":    a.vehicle_id,
        "is_primary":    a.is_primary,
        "assigned_by":   a.assigned_by,
        "assigned_at":   a.assigned_at,
        "unassigned_at": a.unassigned_at,
        "notes":         a.notes,
        "active":        a.unassigned_at is None,
    }


def _document_to_dict(d) -> dict:
    return {
        "id":           d.id,
        "account_id":   d.account_id,
        "user_id":      d.user_id,
        "doc_type":     d.doc_type,
        "file_name":    d.file_name,
        "file_size":    d.file_size,
        "mime_type":    d.mime_type,
        "issued_at":    d.issued_at,
        "expires_at":   d.expires_at,
        "status":       d.status,
        "uploaded_by":  d.uploaded_by,
        "uploaded_at":  d.uploaded_at,
        "notes":        d.notes,
        # Object-store fields exposed read-only so the UI can render a
        # "View in Drive" deep-link when the backend is gdrive.
        "drive_file_id": d.drive_file_id,
    }


_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str) -> str:
    """Strip dangerous characters from a user-supplied filename."""
    cleaned = _SAFE_FILENAME_RE.sub("_", name.strip())
    return cleaned[:120] or "doc"


# ── Profile / list ─────────────────────────────────────────────


@router.get("")
async def list_drivers(
    include_terminated: bool = Query(False),
    user: dict = Depends(require_permission("can_manage_driver_docs")),
    platform_db=Depends(get_platform_db),
):
    """List drivers in the caller's account.  Admin-only."""
    drivers = await platform_db.list_drivers(
        user["account_id"], include_terminated=include_terminated,
    )
    rows = [_profile_to_dict(p) for p in drivers]
    # Company scoping — a restricted user sees only their companies' drivers.
    # Fail-open: a driver with NO company assignment stays visible (so HR can
    # still onboard / assign them); only known-other-company drivers drop.
    allowed = await get_user_company_codes(user)
    if allowed:
        user_map = await platform_db.get_all_user_company_codes(user["account_id"])
        rows = filter_by_company_map(rows, allowed, user_map, key="user_id")
    return {"drivers": rows, "count": len(rows)}


@router.get("/samsara")
async def list_samsara_drivers(
    user: dict = Depends(require_permission("can_manage_driver_docs")),
    platform_db=Depends(get_platform_db),
):
    """Return the caller account's Samsara driver roster, with each
    driver tagged by company.  Powers the dashboard's Samsara-driver
    picker and the auto-match suggestion in the bot invite wizard.

    Also includes which of those Samsara drivers are already linked to
    a local user row (``linked_user_id``) so the UI can grey them out
    in the picker — admin shouldn't accidentally double-link.
    """
    from infra.services import get_client as _get_samsara

    try:
        samsara_client = await _get_samsara(user["account_id"])
        drivers = await samsara_client.get_drivers()
    except Exception as e:
        logger.warning(
            "Samsara driver list unavailable for account %s: %s",
            user["account_id"], e,
        )
        # Soft fail — picker falls back to free-text entry.
        return {"drivers": [], "error": "samsara_unavailable"}

    # Build the in-use map so the UI can de-duplicate.
    linked = await platform_db.list_drivers(
        user["account_id"], include_terminated=True,
    )
    linked_by_sid = {
        (d.samsara_driver_id or "").strip(): d.user_id
        for d in linked if (d.samsara_driver_id or "").strip()
    }

    def _shape(d: dict) -> dict:
        sid = str(d.get("id", "") or "")
        return {
            "samsara_driver_id": sid,
            "name":              d.get("name") or "",
            "username":          d.get("username") or "",
            "phone":             d.get("phone") or "",
            "company_code":      d.get("_org") or "",
            "deactivated":       bool(d.get("deactivatedAtMs")),
            "linked_user_id":    linked_by_sid.get(sid),
        }

    return {
        "drivers": [_shape(d) for d in drivers],
        "count":   len(drivers),
    }


@router.get("/me")
async def get_my_driver(
    user: dict = Depends(require_permission_any(
        "can_manage_driver_docs", "can_driver_docs_own",
    )),
    platform_db=Depends(get_platform_db),
):
    """Driver self-view — same shape as ``/drivers/{user_id}`` but
    resolves the caller's own users row so the miniapp doesn't need
    to know its own user_id.

    Returns 404 with ``"Driver not found"`` if the caller isn't a
    driver (admins hitting this endpoint without a driver record get
    the same response — they should use the list view instead)."""
    caller_id = await _resolve_caller_user_id(user, platform_db)
    profile = await platform_db.get_driver_profile(caller_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Driver not found")
    assignments = await platform_db.list_active_assignments(caller_id)
    documents = await platform_db.list_documents(caller_id, status="active")
    return {
        "profile":     _profile_to_dict(profile),
        "assignments": [_assignment_to_dict(a) for a in assignments],
        "documents":   [_document_to_dict(d) for d in documents],
    }


@router.get("/{user_id}")
async def get_driver(
    user_id: int,
    user: dict = Depends(require_permission_any(
        "can_manage_driver_docs", "can_driver_docs_own",
    )),
    platform_db=Depends(get_platform_db),
):
    """Driver detail — profile + active vehicle assignments + active
    documents in one round-trip (the drawer needs all three)."""
    await _require_driver_visibility(user_id, user, platform_db)

    profile = await platform_db.get_driver_profile(user_id)
    assignments = await platform_db.list_active_assignments(user_id)
    documents = await platform_db.list_documents(user_id, status="active")

    return {
        "profile":     _profile_to_dict(profile),
        "assignments": [_assignment_to_dict(a) for a in assignments],
        "documents":   [_document_to_dict(d) for d in documents],
    }


@router.put("/{user_id}")
async def update_driver(
    user_id: int,
    body: ProfileUpdate,
    user: dict = Depends(require_permission_any(
        "can_manage_driver_docs", "can_driver_docs_own",
    )),
    platform_db=Depends(get_platform_db),
):
    """Update driver profile.  Admins (``can_manage_driver_docs``) can
    edit any field; drivers (``can_driver_docs_own``) can only edit
    their own contact info (phone + home_address).  Everything else
    is silently ignored from a driver-self call."""
    await _require_driver_visibility(user_id, user, platform_db)

    is_admin = user.get("_matched_perm") == "can_manage_driver_docs"
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if not is_admin:
        # Driver-self: lock to contact fields only.
        fields = {
            k: v for k, v in fields.items()
            if k in ("phone", "home_address")
        }
    if not fields:
        raise HTTPException(
            status_code=422,
            detail="No editable fields supplied (or driver-self limited to phone + home_address).",
        )

    ok = await platform_db.update_driver_profile(user_id, **fields)
    if not ok:
        # Field allow-list is enforced by Pydantic above so this only
        # fires if the user_id row vanished between visibility check
        # and update — race condition, surface as 404.
        raise HTTPException(status_code=404, detail="Driver not found")
    return {"status": "updated", "fields": list(fields.keys())}


# ── Vehicle assignments ────────────────────────────────────────


@router.get("/{user_id}/assignments")
async def list_assignments(
    user_id: int,
    history: bool = Query(False),
    user: dict = Depends(require_permission_any(
        "can_manage_driver_docs", "can_driver_docs_own",
    )),
    platform_db=Depends(get_platform_db),
):
    await _require_driver_visibility(user_id, user, platform_db)
    if history:
        rows = await platform_db.list_assignment_history(user_id)
    else:
        rows = await platform_db.list_active_assignments(user_id)
    return {
        "assignments": [_assignment_to_dict(a) for a in rows],
        "count": len(rows),
    }


@router.post("/{user_id}/assignments")
async def create_assignment(
    user_id: int,
    body: AssignmentCreate,
    user: dict = Depends(require_permission("can_manage_driver_docs")),
    platform_db=Depends(get_platform_db),
):
    """Assign a vehicle to a driver.  Admin-only — drivers don't
    self-assign.  Auto-demotes the previous primary if
    ``is_primary=true`` so there's always at most one primary."""
    await _require_driver_visibility(user_id, user, platform_db)
    caller_id = await _resolve_caller_user_id(user, platform_db)
    a = await platform_db.assign_vehicle_to_driver(
        account_id=user["account_id"],
        user_id=user_id,
        vehicle_name=body.vehicle_name,
        vehicle_id=body.vehicle_id,
        is_primary=body.is_primary,
        assigned_by=caller_id,
        notes=body.notes,
    )
    return {"status": "assigned", "assignment": _assignment_to_dict(a)}


@router.delete("/assignments/{assignment_id}")
async def end_assignment(
    assignment_id: int,
    user: dict = Depends(require_permission("can_manage_driver_docs")),
    platform_db=Depends(get_platform_db),
):
    """End an active assignment.  Preserves the row for history; sets
    ``unassigned_at`` so the active-list query no longer returns it."""
    caller_id = await _resolve_caller_user_id(user, platform_db)
    ok = await platform_db.end_vehicle_assignment(assignment_id, by=caller_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Assignment not found or already ended")
    return {"status": "unassigned"}


# ── Documents ──────────────────────────────────────────────────


@router.get("/documents/expiring")
async def expiring_documents(
    within_days: int = Query(30, ge=1, le=365),
    user: dict = Depends(require_permission("can_manage_driver_docs")),
    platform_db=Depends(get_platform_db),
):
    """Account-wide list of documents expiring within ``within_days``.
    Powers the daily expiration-alert scheduler and the dashboard's
    "Expiring soon" panel."""
    docs = await platform_db.get_expiring_documents(
        user["account_id"], within_days=within_days,
    )
    return {
        "documents": [_document_to_dict(d) for d in docs],
        "count": len(docs),
        "within_days": within_days,
    }


@router.get("/{user_id}/documents")
async def list_driver_documents(
    user_id: int,
    status: Optional[str] = Query(None, pattern="^(active|expired|archived)$"),
    doc_type: Optional[str] = Query(None),
    user: dict = Depends(require_permission_any(
        "can_manage_driver_docs", "can_driver_docs_own",
    )),
    platform_db=Depends(get_platform_db),
):
    await _require_driver_visibility(user_id, user, platform_db)
    docs = await platform_db.list_documents(
        user_id, status=status, doc_type=doc_type,
    )
    return {
        "documents": [_document_to_dict(d) for d in docs],
        "count": len(docs),
    }


@router.post("/{user_id}/documents")
async def upload_driver_document(
    user_id: int,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    expires_at: Optional[str] = Form(None),
    issued_at: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    user: dict = Depends(require_permission("can_manage_driver_docs")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Upload a driver document.  Admin-only — drivers don't
    self-upload in MVP (they request a re-upload via the miniapp
    Profile page in a future phase).

    Pipeline:
      1. Validate doc_type + content-type + size cap
      2. Enforce per-account storage quota (local backend only)
      3. Upload bytes via ``get_object_store_for_account`` — picks
         Google Drive if connected, local disk otherwise
      4. Record DB row with object_key + drive_file_id
    """
    from adapters.storage.object_store import get_object_store_for_account

    await _require_driver_visibility(user_id, user, platform_db)

    if doc_type not in VALID_DOC_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"doc_type must be one of {sorted(VALID_DOC_TYPES)}",
        )

    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_MIME:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {content_type or 'unknown'}",
        )

    raw = await file.read()
    if len(raw) > _MAX_DOC_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {_MAX_DOC_BYTES // (1024 * 1024)} MB limit.",
        )

    # Quota check is meaningful only for the local-disk backend.
    # Drive-connected accounts let Google enforce the user's own
    # Drive quota and don't hit this rail.
    try:
        await platform_db.enforce_storage_quota(user["account_id"], len(raw))
    except platform_db.StorageQuotaExceeded as e:
        used, quota = await platform_db.get_storage_usage(user["account_id"])
        raise HTTPException(
            status_code=429,
            detail={
                "message": "Account storage quota exceeded — delete old documents or upgrade.",
                "used_bytes": used,
                "quota_bytes": quota,
                "uploading_bytes": len(raw),
            },
        ) from e

    safe_name = _safe_filename(file.filename or f"{doc_type}.bin")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    # Resolve the driver's company → Drive-safe folder name.
    # A driver's personal documents (CDL, medical card) live under
    # the company that employs them.  When a driver is reassigned,
    # the admin route triggers the archive flow which moves this
    # folder to ``{company}/drivers/_archive/{date}/user-{id}/`` and
    # rewrites bucket paths on existing rows.
    from features.work_orders.storage import (
        driver_docs_bucket, resolve_company_folder,
    )
    user_companies = await platform_db.get_user_companies(user_id)
    driver_company_code = user_companies[0].company_code if user_companies else ""
    company_folder = await resolve_company_folder(
        tenant_db, user["account_id"], driver_company_code,
    )
    folder = driver_docs_bucket(company_folder, user_id)
    object_key = f"{doc_type}_{stamp}_{safe_name}"

    store = await get_object_store_for_account(user["account_id"], tenant_db)
    try:
        store.put(folder, object_key, raw)
    except Exception:
        logger.exception("Driver doc upload failed: acct=%d user=%d", user["account_id"], user_id)
        raise HTTPException(
            status_code=502,
            detail="Storage backend rejected the upload.  Check Google Drive auth (Admin → Storage) or contact support.",
        )

    # Look up Drive file_id when the backend is gdrive so future
    # reads can hit it directly without the folder-chain walk.
    drive_file_id: Optional[str] = None
    if hasattr(store, "drive_file_id_cache"):
        # Backend-specific lookup; tolerated as optional.
        try:
            drive_file_id = store.drive_file_id_cache.get(
                f"{folder}/{object_key}",
            )
        except Exception:
            drive_file_id = None

    caller_id = await _resolve_caller_user_id(user, platform_db)
    doc = await platform_db.add_document(
        account_id=user["account_id"],
        user_id=user_id,
        doc_type=doc_type,
        bucket=folder,
        object_key=object_key,
        drive_file_id=drive_file_id,
        file_name=safe_name,
        file_size=len(raw),
        mime_type=content_type,
        issued_at=issued_at,
        expires_at=expires_at,
        uploaded_by=caller_id,
        notes=notes,
    )
    return {"status": "uploaded", "document": _document_to_dict(doc)}


@router.get("/documents/{doc_id}")
async def download_driver_document(
    doc_id: int,
    user: dict = Depends(require_permission_any(
        "can_manage_driver_docs", "can_driver_docs_own",
    )),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Return the document bytes inline.  Drivers may only read their
    own documents; admins read any in their account."""
    from adapters.storage.object_store import get_object_store_for_account

    doc = await platform_db.get_document(doc_id)
    if not doc or doc.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="Document not found")
    # Driver-self visibility check.
    await _require_driver_visibility(doc.user_id, user, platform_db)

    store = await get_object_store_for_account(user["account_id"], tenant_db)
    try:
        if doc.drive_file_id and hasattr(store, "get_by_id"):
            data = store.get_by_id(doc.drive_file_id)
        else:
            data = store.get(doc.bucket, doc.object_key)
    except Exception:
        logger.exception("Driver doc fetch failed: doc_id=%d", doc_id)
        raise HTTPException(
            status_code=502,
            detail="Storage backend could not retrieve the document.",
        )
    if not data:
        raise HTTPException(status_code=404, detail="Document file not found in storage")

    return StreamingResponse(
        io.BytesIO(data),
        media_type=doc.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{doc.file_name}"'},
    )


@router.delete("/documents/{doc_id}")
async def delete_driver_document(
    doc_id: int,
    user: dict = Depends(require_permission("can_manage_driver_docs")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Delete a document.  Admin-only.  Removes the DB row first,
    then attempts to delete the underlying object — if storage
    delete fails the DB row is still gone but an orphan stays in
    the bucket (cleaned by a future ``purge_orphan_docs`` task)."""
    from adapters.storage.object_store import get_object_store_for_account

    doc = await platform_db.get_document(doc_id)
    if not doc or doc.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="Document not found")

    await platform_db.delete_document(doc_id)
    # Best-effort object delete — DB is the source of truth for
    # "this row was deleted"; orphan cleanup is a separate concern.
    try:
        store = await get_object_store_for_account(user["account_id"], tenant_db)
        store.delete(doc.bucket, doc.object_key)
    except Exception:
        logger.warning(
            "Orphan in storage after DB delete: doc_id=%d key=%s/%s",
            doc_id, doc.bucket, doc.object_key,
        )
    return {"status": "deleted"}


# ── Driver self-service: request a document re-upload ─────────


@router.post("/me/documents/{doc_id}/request-reupload")
async def request_doc_reupload(
    doc_id: int,
    user: dict = Depends(require_permission_any(
        "can_manage_driver_docs", "can_driver_docs_own",
    )),
    platform_db=Depends(get_platform_db),
):
    """Ping the account's admins that the driver wants this document
    re-uploaded (their CDL renewal arrived, medical card was updated,
    etc.).  In the MVP, drivers themselves can't upload — admin does.

    Returns ``{"status": "notified", "admins": N}`` so the miniapp can
    confirm the request actually reached someone.  When no admin has
    a Telegram chat (rare — fresh account), returns
    ``{"status": "no_admins"}`` so the UI can show a fallback message.
    """
    caller_id = await _resolve_caller_user_id(user, platform_db)
    doc = await platform_db.get_document(doc_id)
    if not doc or doc.user_id != caller_id:
        # Owners can still hit this endpoint, but only for their own
        # documents — admins managing other drivers have the normal
        # upload UI, no re-upload-request needed.
        raise HTTPException(status_code=404, detail="Document not found")

    # Notify admins via the bot.  Imported lazily so the API doesn't
    # pull in telegram-bot deps at module-load time on non-bot hosts.
    try:
        from infra.bot_registry import get_app_for_account
        from telegram.constants import ParseMode
    except Exception as e:
        logger.warning("Re-upload request: bot deps unavailable: %s", e)
        raise HTTPException(status_code=503, detail="Bot not available")

    bot_app = get_app_for_account(user["account_id"])
    if bot_app is None:
        raise HTTPException(status_code=503, detail="Bot not available")

    driver = await platform_db.get_user_by_id(caller_id)
    driver_name = (driver.display_name if driver else f"Driver #{caller_id}")
    doc_label = doc.doc_type.replace("_", " ").title()

    text = (
        f"📝 <b>Document re-upload requested</b>\n\n"
        f"  👤 {driver_name}\n"
        f"  📄 {doc_label} — {doc.file_name}\n"
        + (f"  📅 Current expiry: {doc.expires_at}\n" if doc.expires_at else "")
        + "\nOpen the Drivers page to upload the renewal."
    )

    users = await platform_db.list_account_users(user["account_id"])
    sent = 0
    for u in users:
        if u.role not in (Role.OWNER, Role.ADMIN):
            continue
        if not u.telegram_id:
            continue
        try:
            await bot_app.bot.send_message(
                chat_id=u.telegram_id, text=text, parse_mode=ParseMode.HTML,
            )
            sent += 1
        except Exception as e:
            logger.debug("Re-upload admin notify to %d failed: %s", u.telegram_id, e)

    if sent == 0:
        return {"status": "no_admins"}
    return {"status": "notified", "admins": sent}


# ─────────────────────────────────────────────────────────────────────
# Driver ROSTER admin — the Drivers feature's own management surface.
#
# These endpoints moved here from Settings · Team Management so the driver
# lifecycle (invite, assign trucks, link Samsara/Datatruck/loads, provision-
# as-pending) lives WITH the Drivers feature instead of the generic staff
# admin.  URLs are byte-identical to the old ones (prefix "/admin/users/…") so
# the miniapp/bot/existing callers are unaffected.  Gates: driver INVITE is
# roster-only (``can_manage_drivers``); everything Team Management still
# calls (trucks, integration links/sources, identity linking, provision)
# accepts EITHER flag — staff admins keep their pre-split powers, roster
# admins (fleet/HR) get theirs without staff admin.
# Staff administration (roles, tiers, ownership, deactivation) stays in Team
# Management.
# ─────────────────────────────────────────────────────────────────────

admin_router = APIRouter(prefix="/admin", tags=["drivers"])

_MANAGE = "can_manage_drivers"


@admin_router.get("/users/integration-links")
async def integration_links(
    user: dict = Depends(require_permission_any("can_manage_users", _MANAGE)),
    tenant=Depends(get_tenant_db),
):
    """External-INTEGRATION identities awaiting a member link — currently the
    Datatruck driver import plan (link/create/review buckets).

    Load-sheet dispatcher/driver NAMES are deliberately NOT surfaced here.
    ``loads`` is its own feature (rows can be source='manual' OR 'datatruck'),
    so a free-text name on a load is operational-data attribution, not an
    integration identity.  Loads attribute to a member automatically when their
    Datatruck driver is linked; the manual case belongs in a Loads-side tool."""
    account_id = int(user["account_id"])
    return {
        "datatruck_drivers": await tenant.plan_datatruck_driver_import(account_id),
    }


class LinkDatatruckDriverBody(BaseModel):
    external_id: str = ""       # '' unlinks


@admin_router.post("/users/{user_id}/link-datatruck-driver")
async def link_datatruck_driver(
    user_id: int,
    body: LinkDatatruckDriverBody,
    user: dict = Depends(require_permission_any("can_manage_users", _MANAGE)),
    tenant=Depends(get_tenant_db),
):
    """Bind a synced Datatruck driver to this member + backfill their loads
    (matched by the staged driver's display name)."""
    account_id = int(user["account_id"])
    try:
        await tenant.link_datatruck_driver(account_id, user_id, body.external_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    backfilled = 0
    if body.external_id:
        staged_name = await tenant.get_datatruck_driver_name(account_id, body.external_id)
        if staged_name:
            backfilled = await tenant.assign_load_person_by_name(
                account_id, user_id, staged_name, field="driver",
            )
    return {"linked": bool(body.external_id), "loads_backfilled": backfilled}


class LinkNameBody(BaseModel):
    name: str
    field: str = "dispatcher"   # 'dispatcher' | 'driver'


@admin_router.post("/users/{user_id}/link-load-name")
async def link_load_name(
    user_id: int,
    body: LinkNameBody,
    user: dict = Depends(require_permission_any("can_manage_users", _MANAGE)),
    tenant=Depends(get_tenant_db),
):
    """Associate every unlinked load carrying this dispatcher/driver name
    with the member (name association — the manager's explicit decision)."""
    if body.field not in ("dispatcher", "driver"):
        raise HTTPException(400, "field must be dispatcher or driver")
    account_id = int(user["account_id"])
    n = await tenant.assign_load_person_by_name(account_id, user_id, body.name, field=body.field)
    return {"loads_backfilled": n}


class ProvisionBody(BaseModel):
    kind: str                    # 'driver' | 'dispatcher'
    name: str = Field(..., min_length=1, max_length=120)
    email: str = Field("", max_length=200)
    phone: str = Field("", max_length=40)
    datatruck_driver_id: str = Field("", max_length=64)
    load_name: str = Field("", max_length=120)


@admin_router.post("/users/provision")
async def provision_member(
    body: ProvisionBody,
    user: dict = Depends(require_permission_any("can_manage_users", _MANAGE)),
    tenant=Depends(get_tenant_db),
):
    """Create a PENDING member from a synced identity — activates when the
    person signs in (Telegram link or email password).  Links + loads
    backfill happen in the same step."""
    if body.kind not in ("driver", "dispatcher"):
        raise HTTPException(400, "kind must be driver or dispatcher")
    account_id = int(user["account_id"])
    role = Role.DRIVER if body.kind == "driver" else Role.DISPATCHER
    uid = await tenant.create_pending_user(
        account_id, role, body.name.strip(), email=body.email or None,
    )
    if body.kind == "driver" and body.phone:
        await tenant.update_driver_profile(uid, phone=body.phone)
    backfilled = 0
    if body.kind == "driver" and body.datatruck_driver_id:
        try:
            await tenant.link_datatruck_driver(account_id, uid, body.datatruck_driver_id)
        except ValueError as e:
            raise HTTPException(409, str(e))
        staged_name = await tenant.get_datatruck_driver_name(account_id, body.datatruck_driver_id)
        if staged_name:
            backfilled = await tenant.assign_load_person_by_name(
                account_id, uid, staged_name, field="driver",
            )
    if body.load_name:
        backfilled += await tenant.assign_load_person_by_name(
            account_id, uid, body.load_name, field=body.kind,
        )
    return {"id": uid, "lifecycle": "pending", "loads_backfilled": backfilled}


@admin_router.get("/users/integration-sources")
async def integration_sources(
    user: dict = Depends(require_permission_any("can_manage_users", _MANAGE)),
    platform_db=Depends(get_platform_db),
    tenant=Depends(get_tenant_db),
):
    """Rosters for the roster link pickers: staged Datatruck drivers + live
    Samsara drivers, each carrying which member (if any) already holds that
    ref so the UI can grey it out.  Samsara soft-fails (empty list + error
    marker) when the account has no working Samsara connection."""
    account_id = int(user["account_id"])
    datatruck = await tenant.list_datatruck_driver_options(account_id)

    samsara: list[dict] = []
    samsara_error: Optional[str] = None
    try:
        from infra.services import get_client as _get_samsara
        client = await _get_samsara(account_id)
        drivers = await client.get_drivers()
        linked = {
            (getattr(u, "samsara_driver_id", None) or "").strip(): u.id
            for u in await platform_db.list_account_users(account_id)
            if getattr(u, "samsara_driver_id", None)
        }
        for d in drivers:
            sid = str(d.get("id", "") or "")
            samsara.append({
                "samsara_driver_id": sid,
                "name": d.get("name") or "",
                "company_code": d.get("_org") or "",
                "deactivated": bool(d.get("deactivatedAtMs")),
                "linked_user_id": linked.get(sid),
            })
    except Exception as e:
        logger.warning("Samsara roster unavailable for account %s: %s", account_id, e)
        samsara_error = "samsara_unavailable"
    return {"datatruck": datatruck, "samsara": samsara, "samsara_error": samsara_error}


class SamsaraDriverIdUpdate(BaseModel):
    samsara_driver_id: Optional[str] = Field(default=None, max_length=128)


@admin_router.put("/users/{user_id}/samsara-driver-id")
async def update_user_samsara_driver_id(
    user_id: int,
    body: SamsaraDriverIdUpdate,
    user: dict = Depends(require_permission_any("can_manage_users", _MANAGE)),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Bind a member to a Samsara driver_id (gates their /coaching/me and
    /payroll/me self-service data).  Setting NULL unbinds."""
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    caller_rank = role_rank(user["role"])
    existing_rank = role_rank(target.role.value if hasattr(target.role, "value") else target.role)
    if existing_rank >= caller_rank:
        raise HTTPException(status_code=403, detail="Cannot modify a user with equal or higher role")

    new_did = (body.samsara_driver_id or "").strip() or None
    try:
        await tenant_db.link_samsara_driver(user["account_id"], user_id, new_did or "")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "user_samsara_driver_id_set",
        target_type="user", target_id=str(user_id),
        details=f"driver_id={new_did or '(unset)'}",
    )
    return {"ok": True, "samsara_driver_id": new_did}


@admin_router.get("/users/{user_id}/trucks")
async def get_user_vehicles(
    user_id: int,
    user: dict = Depends(require_permission_any("can_manage_users", _MANAGE)),
    platform_db=Depends(get_platform_db),
):
    """Get all vehicle assignments for a user."""
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")
    vehicles = await platform_db.get_user_vehicles(user_id)
    return {
        "user_id": user_id,
        "trucks": [
            {"vehicle_num": t.vehicle_num, "is_primary": t.is_primary, "assigned_at": t.assigned_at}
            for t in vehicles
        ],
        "legacy_truck_num": target.truck_num,
    }


class VehicleAssignment(BaseModel):
    trucks: list[str] = Field(..., max_length=200)  # kept as 'trucks' for API compat


@admin_router.put("/users/{user_id}/trucks")
async def set_user_vehicles(
    user_id: int,
    body: VehicleAssignment,
    user: dict = Depends(require_permission_any("can_manage_users", _MANAGE)),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Set vehicle assignments for a user. First vehicle in list is primary."""
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")
    cleaned = [t.strip() for t in body.trucks if t.strip()]
    if len(cleaned) != len(set(cleaned)):
        raise HTTPException(status_code=400, detail="Duplicate vehicle numbers")
    vehicles = await platform_db.set_user_vehicles(
        user_id, target.account_id, cleaned, assigned_by=int(user["sub"]),
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "vehicle_assignment",
        target_type="user", target_id=str(user_id),
        details=f"Vehicles: {', '.join(cleaned) or 'none'}",
    )
    return {
        "ok": True,
        "trucks": [{"vehicle_num": t.vehicle_num, "is_primary": t.is_primary} for t in vehicles],
    }


class DriverInviteBody(BaseModel):
    truck_num: Optional[str] = Field(default=None, max_length=64)
    hours: int = Field(default=168, ge=1, le=720)   # link TTL — default 7 days


@admin_router.post("/drivers/invite")
async def invite_driver(
    body: DriverInviteBody,
    user: dict = Depends(require_permission(_MANAGE)),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Create a driver onboarding invite (link-channel).  Role is HARD-LOCKED
    to DRIVER and an optional truck is bound at claim time.  Gated on
    can_manage_drivers (NOT can_invite): a fleet/HR lead onboards drivers
    without staff-invite power, and the target can never be anything but a
    driver.  Reuses the shared invite engine, so the invite lands in the same
    invites table — visible + revocable in the Invites surface like any other.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    invite = await platform_db.create_invite(
        account_id=user["account_id"],
        created_by=db_user.id,
        role=Role.DRIVER,                       # hard-locked — never staff
        truck_num=(body.truck_num or None),
        hours=body.hours,
        recipient_email=None,                   # link the manager hands over
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "invite_create",
        target_type="invite", target_id=str(invite.id),
        details="Role: driver, channel: link (driver roster)",
    )
    return {
        "id": invite.id,
        "code": invite.code,
        "role": invite.role,
        "truck_num": invite.truck_num,
        "expires_at": invite.expires_at,
    }
