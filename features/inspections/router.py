"""PTI (Pre-Trip Inspection) REST surface.

Three logical groups:

  * **Driver-facing** (``/me/...`` paths, ``can_inspections_vehicle``) —
    fetch current inspection, walk items, attach media, submit.
  * **Fleet-facing** (``/...`` paths, ``can_inspections_all``) —
    list submissions, view detail, review.
  * **Template editor** (``/template/...`` paths,
    ``can_inspections_all``) — fleet edits the checklist their drivers
    will use.  PTI is a fleet-ops responsibility, not a global account
    setting, so the gate is the same permission used for reviewing
    submissions.  Account owners + admins inherit it via the default
    role grants.

All write paths go through ``features.inspections.service`` rather than
the storage mixin directly so business rules (required items,
required media, status transitions) can't be bypassed by hitting an
endpoint with crafted payloads.
"""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may;
# service/alert/ai_tool/signal modules never do.


from __future__ import annotations

import logging
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from capabilities.permissions.roles import can
from features.inspections import service as pti_service
from features.inspections.templates import (
    STANDARD_DOT_TRAILER_ITEMS,
    STANDARD_DOT_TRUCK_ITEMS,
    VALID_ITEM_STATUSES,
    VALID_REVIEW_STATUSES,
)
from interfaces.api.deps import (
    get_current_db_user,
    get_platform_db,
    get_tenant_db,
    get_user_company_codes,
    require_permission,
    require_permission_any,
    resolve_user_id,
)
from infra.platform import get_router as _get_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inspections", tags=["inspections"])


# ── Limits + allowlists ───────────────────────────────────────────


_MAX_MEDIA_BYTES = 25 * 1024 * 1024   # 25 MB per upload; videos capped tighter
_MAX_VIDEO_BYTES = 50 * 1024 * 1024   # 50 MB video ceiling (post-resize)
_ALLOWED_IMAGE_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic",
}
_ALLOWED_VIDEO_TYPES = {
    "video/mp4", "video/quicktime", "video/webm",
}


# ── Pydantic schemas ──────────────────────────────────────────────


class ItemUpdate(BaseModel):
    status: Optional[str] = Field(default=None, description="ok|minor|major|oos|na|pending")
    notes:  Optional[str] = Field(default=None, max_length=500)


class ReviewBody(BaseModel):
    review_status: str = Field(..., description="approved|needs_service|rejected|revision_required")
    review_notes:  str = Field(default="", max_length=1000)


class TemplateItemBody(BaseModel):
    label:          str  = Field(..., min_length=1, max_length=120)
    category:       str  = Field(default="", max_length=40)
    requires_media: bool = False
    required:       bool = True
    sort_order:     int  = 0
    item_type:      str  = Field(default="check", description="check|photo|document")


class TemplateReorderBody(BaseModel):
    vehicle_type:  str        = Field(..., description="truck|trailer")
    ordered_keys:  list[str]


class TemplateResetBody(BaseModel):
    vehicle_type:  str = Field(..., description="truck|trailer")


# ── Helpers ───────────────────────────────────────────────────────


def _map_pti_error(exc: Exception) -> HTTPException:
    """Translate the service-layer error hierarchy into HTTP codes."""
    if isinstance(exc, pti_service.NotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (pti_service.WrongStatus, pti_service.IncompleteInspection, pti_service.MissingMedia, pti_service.MissingSignature)):
        # 409 conveys "your state is wrong" better than 400; the client
        # uses ``error_code`` from the JSON body to decide which UI
        # toast to render.
        return HTTPException(
            status_code=409,
            detail={
                "message":    str(exc),
                "error_code": getattr(exc, "error_code", "pti_error"),
            },
        )
    if isinstance(exc, pti_service.PTIError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc  # bubble up — caller's @router error_handler will 500


def _storage_put_error(store) -> HTTPException:
    """Build an actionable HTTP error when an ObjectStorage.put returns "".

    The common cause on Google-Drive-backed accounts is an expired or
    revoked OAuth token (Google expires Drive refresh tokens after 7
    days while the consent screen is in "Testing").  Surface that
    clearly so the fleet reconnects Drive instead of seeing a generic
    500 — and tag an ``error_code`` the dashboard can act on.
    """
    last = (getattr(store, "last_error", "") or "").lower()
    backend = type(store).__name__
    if "invalid_grant" in last or "expired" in last or "revoked" in last:
        return HTTPException(
            status_code=502,
            detail={
                "message": (
                    "Google Drive connection has expired or been revoked. "
                    "Reconnect Google Drive in Settings → Storage, then retry."
                ),
                "error_code": "storage_auth_expired",
            },
        )
    if "GDrive" in backend:
        return HTTPException(
            status_code=502,
            detail={
                "message": (
                    "Google Drive upload failed. Check the Drive connection "
                    "in Settings → Storage."
                ),
                "error_code": "storage_unavailable",
            },
        )
    return HTTPException(
        status_code=500,
        detail={
            "message": "Failed to store the file. Please retry.",
            "error_code": "storage_failed",
        },
    )


async def _check_quota_or_413(tenant_db, account_id: int, additional_bytes: int) -> None:
    """Pre-upload quota gate.

    Refuses writes that would push the account past its local-cache
    quota.  ``ObjectStorageSyncMixin.account_has_quota_for`` returns
    ``(allowed, used, quota)`` so the 413 carries the real numbers —
    the dashboard renders an accurate "1.2 GB of 5 GB" message and
    can offer the "Reconnect Drive to free space" CTA.

    Backend-agnostic: the queue is empty on disk-only and gdrive
    accounts so the check naturally passes for them.  Hybrid accounts
    are protected; that's where the quota actually matters.
    """
    if additional_bytes <= 0:
        return
    ok, used, quota = await tenant_db.account_has_quota_for(account_id, additional_bytes)
    if ok:
        return
    raise HTTPException(
        status_code=413,
        detail={
            "message": (
                "Account local storage is full. "
                f"{used:,} of {quota:,} bytes used; this upload would exceed the limit. "
                "Reconnect Google Drive in Settings → Storage to free space, "
                "or contact support to raise the quota."
            ),
            "error_code": "account_storage_full",
            "used_bytes": int(used),
            "quota_bytes": int(quota),
            "requested_bytes": int(additional_bytes),
        },
    )


async def _track_for_sync_if_hybrid(
    store,
    bucket: str,
    key: str,
    file_path: str,
    *,
    entity_type: str,
    entity_id: int,
    file_size: int = 0,
) -> None:
    """Tell a hybrid-backend store to enqueue this file for cloud sync.

    Duck-typed by ``hasattr`` so Disk + GDrive stores are no-ops — they
    don't have a queue concept.  Failures here never propagate: the
    bytes are already safely on disk and the file's still attached to
    the inspection; we just log + carry on.  A future sweep job (or
    the next upload) re-enqueues missing rows.
    """
    method = getattr(store, "track_for_sync", None)
    if method is None:
        return
    try:
        await method(
            bucket, key, file_path,
            entity_type=entity_type, entity_id=entity_id,
            file_size=file_size,
        )
    except Exception:
        logger.exception(
            "track_for_sync failed for %s/%s entity=%s:%d "
            "(file is on disk; sync will be retried)",
            bucket, key, entity_type, entity_id,
        )


async def _inspection_company_folder(
    tenant_db, account_id: int, inspection: dict,
) -> str:
    """Resolve the ``{COMPANY}/`` folder root for an inspection.

    Mirrors the convention every other module uses (work orders,
    maintenance, driver docs, camera images, parking maps) so the
    user's Drive shows one consistent tree per company instead of a
    mix of company-named and account-id-prefixed folders.

    Source of truth: the driver's first ``user_companies`` entry —
    same pattern ``interfaces/api/routes/drivers.py`` uses for driver
    docs.  Drivers without a company assignment (legacy / unassigned)
    land under the generic holding folder so writes never fail.
    """
    from features.work_orders.storage import resolve_company_folder
    user_id = int(inspection.get("user_id") or 0)
    company_code = ""
    if user_id:
        try:
            user_companies = await tenant_db.get_user_companies(user_id)
            if user_companies:
                company_code = user_companies[0].company_code
        except Exception:
            logger.debug(
                "PTI: could not resolve company for inspection driver",
                exc_info=True,
            )
    return await resolve_company_folder(tenant_db, account_id, company_code)


def _inspection_folder(company_folder: str, inspection_id: int) -> str:
    """Compose ``{COMPANY}/inspections/{id}`` — the per-inspection bucket."""
    return f"{company_folder}/inspections/{inspection_id}"


def _legacy_inspection_folder(account_id: int, inspection_id: int) -> str:
    """The pre-migration bucket path.

    Used as a fallback when reading rows that were uploaded before
    the layout moved under ``{COMPANY}/``.  Their stored ``file_path``
    still resolves via ``get_by_id`` (it carries the legacy disk URL
    or the original Drive ID), so this fallback only fires when the
    file_path lookup fails — a rare edge case.
    """
    return f"inspections/{account_id}/{inspection_id}"


def _looks_like_drive_id(file_path: str) -> bool:
    """Heuristic: is ``file_path`` an opaque GDrive file ID rather than
    a folder-relative path?

    Drive IDs are ~30+ characters of URL-safe base64 (letters, digits,
    ``_``, ``-``).  Paths returned by Disk look like
    ``data/{COMPANY}/inspections/…`` (always contain a slash).  This
    split is enough to pick the right read path: ``store.get_by_id``
    for IDs, ``store.get(folder, name)`` for paths.
    """
    if not file_path:
        return False
    if "/" in file_path or "\\" in file_path:
        return False
    return len(file_path) >= 20 and all(
        c.isalnum() or c in "_-" for c in file_path
    )


def _media_bytes(
    store, media: dict, folder: str, legacy_folder: Optional[str] = None,
) -> Optional[bytes]:
    """Pick the right read path for an inspection media row.

    Read priority:
      1. ``store.get_by_id(file_path)`` — works for both Drive IDs
         (post-sync hybrid / gdrive) AND for stored disk URL paths
         (pre-sync hybrid / disk).  The single most-correct read.
      2. ``store.get(folder, file_name)`` — current-layout fallback
         when ``file_path`` is empty or the ID lookup misses.
      3. ``store.get(legacy_folder, file_name)`` — pre-migration
         fallback for inspections written under the old
         ``inspections/{acct}/{id}`` tree.

    Same helper for every backend + every state so the read code path
    stays transition-agnostic.
    """
    fp = (media.get("file_path") or "").strip()
    if fp:
        getter = getattr(store, "get_by_id", None)
        if getter is not None:
            data = getter(fp)
            if data is not None:
                return data
    file_name = media.get("file_name") or ""
    data = store.get(folder, file_name)
    if data is not None:
        return data
    if legacy_folder and legacy_folder != folder:
        return store.get(legacy_folder, file_name)
    return None


async def _enrich_inspection_names(detail: dict, platform_db) -> None:
    """Populate ``reviewed_by_name`` + ``driver_name`` on a detail dict.

    Shared by the JSON detail endpoint and the PDF report so both render
    real names.  Lookups fall back to "user <id>" for deleted users so
    historical records stay legible.  Mutates ``detail`` in place.
    """
    async def _resolve_tid(telegram_id: object) -> str:
        try:
            tid = int(telegram_id) if isinstance(telegram_id, (int, str)) else 0
        except (TypeError, ValueError):
            return ""
        if not tid:
            return ""
        u = await platform_db.get_user_by_telegram_id(tid)
        return (u.display_name if u and u.display_name else f"user {tid}")

    if detail.get("reviewed_by"):
        detail["reviewed_by_name"] = await _resolve_tid(detail["reviewed_by"])
    # ``user_id`` is the driver's internal id (not telegram_id).
    if detail.get("user_id"):
        try:
            driver = await platform_db.get_user_by_id(int(detail["user_id"]))
            detail["driver_name"] = (
                driver.display_name if driver and driver.display_name
                else f"user {detail['user_id']}"
            )
        except (TypeError, ValueError):
            pass


async def _resolve_internal_user_id(user: dict, tenant_db) -> int:
    """JWT ``sub`` is the telegram_id; PTI rows reference the internal
    ``users.id``.  Resolve via ``get_user_by_telegram_id`` so routes
    can compare apples-to-apples.  Cached for the request because
    every driver-facing route needs it."""
    cached = user.get("_internal_user_id")
    if cached is not None:
        return int(cached)
    db_user = await get_current_db_user(user, tenant_db)
    if not db_user:
        raise HTTPException(status_code=401, detail="User not found")
    user["_internal_user_id"] = int(db_user.id)
    return int(db_user.id)


async def _require_visible_inspection(
    inspection_id: int, user: dict, tenant_db,
) -> dict:
    """Fetch the inspection or 404.  Drivers can only see their own
    rows; fleet roles see everything in their account."""
    ins = await tenant_db.get_inspection(
        inspection_id, account_id=user["account_id"],
    )
    if not ins:
        raise HTTPException(status_code=404, detail="Inspection not found")
    if not can(user["role"], "can_inspections_all"):
        internal_uid = await _resolve_internal_user_id(user, tenant_db)
        if int(ins.get("user_id") or 0) != internal_uid:
            raise HTTPException(status_code=404, detail="Inspection not found")
    return ins


async def _resolve_active_template(
    tenant_db, account_id: int, vehicle_type: str,
):
    tpl = await tenant_db.get_active_template_with_items(
        account_id, vehicle_type, "weekly",
    )
    if not tpl:
        raise HTTPException(
            status_code=404,
            detail=f"No active {vehicle_type} template",
        )
    return tpl


# ══════════════════════════════════════════════════════════════════
# DRIVER-FACING
# ══════════════════════════════════════════════════════════════════


@router.get("/me/current")
async def get_my_current_inspection(
    user: dict = Depends(require_permission_any("can_inspections_vehicle", "can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Driver's currently-actionable inspection (or null when none)."""
    internal_uid = await _resolve_internal_user_id(user, tenant_db)
    open_ins = await tenant_db.get_open_inspection_for_user(internal_uid)
    if not open_ins:
        return {"inspection": None}
    # Return the full detail (items + media) so the mini-app doesn't
    # need a second round-trip.
    detail = await tenant_db.get_inspection_detail(
        int(open_ins["id"]), account_id=user["account_id"],
    )
    return {"inspection": detail}


@router.get("/me/history")
async def list_my_history(
    days: int = Query(90, ge=1, le=365),
    user: dict = Depends(require_permission_any("can_inspections_vehicle", "can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Driver's past inspections — used by the miniapp's PTI tab."""
    internal_uid = await _resolve_internal_user_id(user, tenant_db)
    rows = await tenant_db.list_inspections_for_user(
        internal_uid, limit=100,
    )
    return {"inspections": rows}


@router.patch("/{inspection_id}/items/{item_key}")
async def update_item(
    inspection_id: int,
    item_key: str,
    body: ItemUpdate,
    user: dict = Depends(require_permission_any("can_inspections_vehicle", "can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Driver updates one checklist item's status / notes."""
    if body.status is not None and body.status not in VALID_ITEM_STATUSES:
        raise HTTPException(status_code=422, detail="invalid status")
    ins = await _require_visible_inspection(inspection_id, user, tenant_db)
    if not can(user["role"], "can_inspections_all"):
        internal_uid = await _resolve_internal_user_id(user, tenant_db)
        if int(ins["user_id"]) != internal_uid:
            raise HTTPException(status_code=404, detail="Inspection not found")
    try:
        item = await pti_service.update_item_status(
            inspection_id, item_key,
            user_id=int(ins["user_id"]),
            status=body.status, notes=body.notes,
            tenant_db=tenant_db,
        )
    except pti_service.PTIError as e:
        raise _map_pti_error(e)
    return {"item": item}


@router.post("/{inspection_id}/media")
async def upload_media(
    inspection_id: int,
    item_key: Optional[str] = Query(default=None),
    file: UploadFile = File(...),
    user: dict = Depends(require_permission_any("can_inspections_vehicle", "can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Attach a photo or video to an inspection.

    When ``item_key`` is provided the media is linked to that item;
    when omitted it's stored as general inspection media (used for
    the catch-all "any other photos" slot on the mini-app)."""
    from adapters.storage.object_storage import get_object_storage_for_account

    ins = await _require_visible_inspection(inspection_id, user, tenant_db)
    # ``_require_visible_inspection`` already enforces own/all scope
    # for drivers; no second check needed here.

    if (ins.get("status") or "").lower() not in ("scheduled", "in_progress", "revision_required"):
        raise HTTPException(
            status_code=409,
            detail={"message": "Inspection is read-only", "error_code": "wrong_status"},
        )

    # Resolve the linked item first — a 'document' item changes which
    # MIME types we accept (PDFs allowed) and how the row is tagged.
    item_id: Optional[int] = None
    item_type = "check"
    if item_key:
        item = await tenant_db.get_inspection_item_by_key(inspection_id, item_key)
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {item_key} not found")
        item_id = int(item["id"])
        item_type = (item.get("item_type") or "check").lower()

    content_type = (file.content_type or "").lower()
    if item_type == "document":
        # A document slot (annual report, registration) accepts a PDF
        # or a photo of the paper; either way it's tagged 'document'
        # so the submit gate + dashboard treat it as a doc, not a
        # defect photo.
        if content_type not in _ALLOWED_IMAGE_TYPES and content_type != "application/pdf":
            raise HTTPException(
                status_code=415,
                detail=f"Document must be a PDF or image, got {content_type or 'unknown'}",
            )
        media_type = "document"
        max_bytes = _MAX_MEDIA_BYTES
    elif content_type in _ALLOWED_IMAGE_TYPES:
        media_type = "photo"
        max_bytes = _MAX_MEDIA_BYTES
    elif content_type in _ALLOWED_VIDEO_TYPES:
        media_type = "video"
        max_bytes = _MAX_VIDEO_BYTES
    else:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {content_type or 'unknown'}",
        )

    raw = await file.read()
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {max_bytes // (1024 * 1024)} MB limit.",
        )

    # Per-account local-storage quota.  Refuses writes that would push
    # the account over its disk limit BEFORE we touch the filesystem —
    # the bytes never hit disk, the 413 carries the real used/quota
    # numbers, and the dashboard can offer "Reconnect Drive to free
    # space" as the actionable fix.
    await _check_quota_or_413(tenant_db, int(user["account_id"]), len(raw))

    # ObjectStorage folder convention: per-company / inspections / id.
    # Matches the work-orders + maintenance + driver-docs layout so a
    # user browsing Drive sees one consistent ``{COMPANY}/...`` tree.
    company_folder = await _inspection_company_folder(
        tenant_db, int(user["account_id"]), dict(ins),
    )
    folder = _inspection_folder(company_folder, inspection_id)
    # Mark the inspection in-progress on first media upload (mirrors
    # the auto-flip done by ``update_item_status`` for the first item
    # touch).
    if (ins.get("status") or "").lower() == "scheduled":
        await tenant_db.mark_inspection_in_progress(inspection_id)

    store = await get_object_storage_for_account(user["account_id"], tenant_db)
    safe_name = (file.filename or f"upload.{media_type}").replace("/", "_")[:128]
    file_path = store.put(folder, safe_name, raw)
    if not file_path:
        # Storage rejected the upload (e.g. expired Google Drive token).
        # Do NOT create a media row — an empty file_path would look like
        # a successful upload but the bytes are gone, 404-ing later.
        raise _storage_put_error(store)

    media_id = await tenant_db.attach_inspection_media(
        inspection_id,
        item_id=item_id,
        media_type=media_type,
        file_path=file_path,
        file_name=safe_name,
        file_size=len(raw),
        content_type=content_type,
        uploaded_by=await resolve_user_id(user),
    )

    # On a hybrid backend, register the file for async cloud sync.
    # Duck-typed — Disk/GDrive stores don't have the method so this is
    # a no-op for them.  The hybrid enqueues + flips the media row's
    # storage_state to 'local'; the Phase 3 worker will drain it.
    await _track_for_sync_if_hybrid(
        store, folder, safe_name, file_path,
        entity_type="pti_media", entity_id=media_id, file_size=len(raw),
    )

    return {
        "id":           media_id,
        "media_type":   media_type,
        "file_name":    safe_name,
        "file_size":    len(raw),
        "content_type": content_type,
        "item_id":      item_id,
    }


@router.delete("/{inspection_id}/media/{media_id}")
async def delete_media(
    inspection_id: int,
    media_id: int,
    user: dict = Depends(require_permission_any("can_inspections_vehicle", "can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Remove a media row + best-effort blob deletion."""
    from adapters.storage.object_storage import get_object_storage_for_account

    ins = await _require_visible_inspection(inspection_id, user, tenant_db)
    # ``_require_visible_inspection`` enforces own/all scope for drivers.
    if (ins.get("status") or "").lower() not in ("scheduled", "in_progress", "revision_required"):
        raise HTTPException(
            status_code=409,
            detail={"message": "Inspection is read-only", "error_code": "wrong_status"},
        )

    media = await tenant_db.get_inspection_media(media_id)
    if not media or int(media.get("inspection_id") or 0) != inspection_id:
        raise HTTPException(status_code=404, detail="Media not found")

    # Best-effort blob delete first so the row removal can't orphan
    # the file.  Try the current ``{COMPANY}/inspections/{id}`` folder
    # and the pre-migration ``inspections/{acct}/{id}`` folder; deletes
    # are idempotent so hitting both is cheap and covers rows from
    # before the layout move.
    try:
        store = await get_object_storage_for_account(user["account_id"], tenant_db)
        company_folder = await _inspection_company_folder(
            tenant_db, int(user["account_id"]), dict(ins),
        )
        new_folder = _inspection_folder(company_folder, inspection_id)
        legacy_folder = _legacy_inspection_folder(
            int(user["account_id"]), inspection_id,
        )
        file_name = media.get("file_name") or ""
        store.delete(new_folder, file_name)
        if legacy_folder != new_folder:
            store.delete(legacy_folder, file_name)
    except Exception:
        logger.debug("inspection media blob delete skipped", exc_info=True)

    ok = await tenant_db.delete_inspection_media(media_id)
    return {"deleted": ok}


class AnnotateBody(BaseModel):
    image_data: str = Field(..., description="PNG data URL with the annotation baked in")


@router.post("/{inspection_id}/media/{media_id}/annotate")
async def annotate_media(
    inspection_id: int,
    media_id: int,
    body: AnnotateBody,
    user: dict = Depends(require_permission_any("can_inspections_vehicle", "can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Overwrite a photo media row with a baked-overlay version.

    Driver-only path: the annotator runs in the Mini App, composites
    photo + red-marker overlay onto a single canvas, and POSTs the
    resulting PNG.  Server validates, rewrites the same ObjectStorage
    path, and stamps ``annotated_at``.

    Refused when the inspection is past the writable state — once
    submitted or reviewed the photo evidence is frozen.  Videos can't
    be annotated; the route refuses to touch a media row whose type
    isn't ``photo``.
    """
    from adapters.storage.object_storage import get_object_storage_for_account

    ins = await _require_visible_inspection(inspection_id, user, tenant_db)
    if (ins.get("status") or "").lower() not in ("scheduled", "in_progress", "revision_required"):
        raise HTTPException(
            status_code=409,
            detail={"message": "Inspection is read-only", "error_code": "wrong_status"},
        )

    media = await tenant_db.get_inspection_media(media_id)
    if not media or int(media.get("inspection_id") or 0) != inspection_id:
        raise HTTPException(status_code=404, detail="Media not found")
    if (media.get("media_type") or "").lower() != "photo":
        raise HTTPException(
            status_code=409,
            detail={"message": "Only photos can be annotated", "error_code": "wrong_media_type"},
        )

    try:
        raw = pti_service.decode_annotated_png(body.image_data)
    except pti_service.PTIError as e:
        raise _map_pti_error(e)
    if len(raw) > _MAX_MEDIA_BYTES:
        # Defence in depth — the service-layer cap is the same value
        # but enforcing both keeps surprises out if either changes.
        raise HTTPException(
            status_code=413,
            detail=f"Annotated image exceeds {_MAX_MEDIA_BYTES // (1024 * 1024)} MB limit.",
        )
    # Annotation overwrites the existing blob with new bytes — the
    # delta on local disk is roughly ``len(raw) - old_size``.  Treating
    # it as a fresh upload is slightly over-protective (the old bytes
    # will be freed by the rewrite) but the safe direction; in practice
    # the annotated PNG is similar size to the original.
    await _check_quota_or_413(tenant_db, int(user["account_id"]), len(raw))

    file_name = media.get("file_name") or ""
    if not file_name:
        # An orphan row without a stored key — refuse rather than write
        # to a synthesized path the static mount can't find.
        raise HTTPException(status_code=500, detail="Media has no file_name on record")

    store = await get_object_storage_for_account(user["account_id"], tenant_db)
    company_folder = await _inspection_company_folder(
        tenant_db, int(user["account_id"]), dict(ins),
    )
    folder = _inspection_folder(company_folder, inspection_id)
    new_url = store.put(folder, file_name, raw)
    if not new_url:
        raise _storage_put_error(store)

    await tenant_db.mark_media_annotated(
        media_id,
        file_size=len(raw),
        content_type="image/png",
    )

    # Annotation overwrote the disk file with new bytes — on a hybrid
    # backend we re-track the row so the queue resets attempts and
    # re-uploads the annotated version to Drive (UPSERT semantics).
    await _track_for_sync_if_hybrid(
        store, folder, file_name, new_url,
        entity_type="pti_media", entity_id=media_id, file_size=len(raw),
    )

    return {
        "id":            media_id,
        "file_size":     len(raw),
        "content_type":  "image/png",
        "annotated":     True,
    }


@router.post("/{inspection_id}/media/{media_id}/ai-check")
async def ai_check_media(
    inspection_id: int,
    media_id: int,
    user: dict = Depends(require_permission_any("can_inspections_vehicle", "can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Run a single-photo AI vision review of one media row.

    Fired automatically by the Mini App right after a checklist photo
    uploads, so the driver gets instant feedback ("looks OK" vs
    "possible damage") and the verdict rolls into the final PTI report.

    Returns the verdict dict.  Idempotent-ish: re-checking simply
    overwrites the prior verdict (a driver who retakes a photo gets a
    fresh read).  Degrades gracefully — when AI isn't configured the
    route returns ``{"status": "unavailable"}`` with HTTP 200 so the
    wizard can quietly skip the badge rather than erroring the upload
    flow.
    """
    import capabilities.ai as ai
    from features.inspections import ai_review
    from adapters.storage.object_storage import get_object_storage_for_account

    ins = await _require_visible_inspection(inspection_id, user, tenant_db)
    media = await tenant_db.get_inspection_media(media_id)
    if not media or int(media.get("inspection_id") or 0) != inspection_id:
        raise HTTPException(status_code=404, detail="Media not found")
    if (media.get("media_type") or "").lower() != "photo":
        # Only stills are reviewable; videos quietly no-op.
        return {"status": "skipped", "reason": "not_a_photo"}

    if not ai.is_configured():
        return {"status": "unavailable"}

    # Pull the image bytes from the same ObjectStorage path the stream
    # route uses.  Pass the legacy folder too so rows uploaded before
    # the company-rooted layout still resolve.
    store = await get_object_storage_for_account(user["account_id"], tenant_db)
    company_folder = await _inspection_company_folder(
        tenant_db, int(user["account_id"]), dict(ins),
    )
    folder = _inspection_folder(company_folder, int(ins["id"]))
    legacy = _legacy_inspection_folder(
        int(user["account_id"]), int(ins["id"]),
    )
    data = _media_bytes(store, dict(media), folder, legacy_folder=legacy)
    if data is None:
        raise HTTPException(status_code=404, detail="Image not found in storage")

    # Resolve the component context from the linked checklist item so
    # the model knows WHAT it's looking at.
    item_label = ""
    category = ""
    reported_status = ""
    if media.get("item_id"):
        item = await tenant_db.get_inspection_item(int(media["item_id"]))
        if item:
            item_label = item.get("label") or ""
            category = item.get("category") or ""
            reported_status = item.get("status") or ""

    # Load the account's preferred vision model (falls back to default
    # inside the vision wrapper if unset).
    try:
        await ai.ensure_account_vision_model(int(user["account_id"]))
    except Exception:
        logger.debug("vision model preload skipped", exc_info=True)

    try:
        verdict = await ai_review.review_photo(
            data,
            item_label=item_label,
            category=category,
            reported_status=reported_status,
            account_id=int(user["account_id"]),
        )
    except Exception:
        logger.exception("pti ai-check failed for media_id=%d", media_id)
        await tenant_db.save_media_ai_review(
            media_id,
            status=ai_review.STATUS_ERROR,
            result_json=ai_review.serialize({"verdict": "unclear", "summary": "AI check failed"}),
        )
        # 200 with an error status — the upload flow must not break.
        return {"status": ai_review.STATUS_ERROR}

    await tenant_db.save_media_ai_review(
        media_id,
        status=ai_review.STATUS_COMPLETED,
        result_json=ai_review.serialize(verdict),
    )
    return {"status": ai_review.STATUS_COMPLETED, **verdict}


class SignatureBody(BaseModel):
    role:           str = Field(..., description="driver|reviewer")
    signature_data: str = Field(..., description="PNG data URL")


@router.post("/{inspection_id}/signature")
async def sign(
    inspection_id: int,
    body: SignatureBody,
    user: dict = Depends(require_permission_any("can_inspections_vehicle", "can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Attach an e-signature to an inspection.

    Drivers sign their own inspection at submit time (the wizard
    routes here before ``/submit`` to satisfy the ``MissingSignature``
    gate).  Fleet reviewers can co-sign at review time when the
    account's compliance workflow needs a second human in the chain.

    Permission split:
      * ``role == "driver"`` requires ``can_inspections_vehicle`` (also
        granted via the catch-all ``can_inspections_all``).
      * ``role == "reviewer"`` requires ``can_inspections_all`` —
        drivers can't co-sign their own work.
    """
    if body.role not in ("driver", "reviewer"):
        raise HTTPException(status_code=422, detail="invalid role")
    if body.role == "reviewer" and not can(user["role"], "can_inspections_all"):
        raise HTTPException(
            status_code=403,
            detail="only fleet reviewers can co-sign",
        )

    # Driver-role check is enforced inside the service layer (it must
    # match the inspection's user_id).  We just need the internal id
    # to hand it down.
    internal_uid = await _resolve_internal_user_id(user, tenant_db)
    try:
        refreshed = await pti_service.set_signature(
            inspection_id,
            role=body.role,
            signature_data=body.signature_data,
            user_id=internal_uid,
            account_id=int(user["account_id"]),
            tenant_db=tenant_db,
        )
    except pti_service.PTIError as e:
        raise _map_pti_error(e)
    # Hand the timestamp back so the Mini App can show "Signed at HH:MM"
    # without an immediate re-fetch.
    if body.role == "driver":
        return {
            "role":      "driver",
            "signed_at": refreshed.get("driver_signed_at"),
        }
    return {
        "role":      "reviewer",
        "signed_at": refreshed.get("reviewer_signed_at"),
    }


class SubmitBody(BaseModel):
    # Optional browser-GPS fallback the Mini App resolves in the
    # background.  Used only when the vehicle has no recent telematics
    # position; null/omitted is the normal case.
    device_lat: Optional[float] = None
    device_lon: Optional[float] = None


@router.post("/{inspection_id}/submit")
async def submit(
    inspection_id: int,
    body: Optional[SubmitBody] = None,
    user: dict = Depends(require_permission_any("can_inspections_vehicle", "can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Driver finalizes the inspection.

    Business rules enforced by ``pti_service.submit_inspection``:
      * All required ``check`` items have a non-pending status.
      * All photo/media + document items have their required upload.
      * The driver has captured an e-signature (``MissingSignature``
        otherwise).  The Mini App routes through ``/signature`` first.

    Captures inspection location (vehicle telematics first, optional
    device GPS fallback from the body).
    """
    ins = await _require_visible_inspection(inspection_id, user, tenant_db)
    device_location: Optional[tuple[float, float]] = None
    if body and body.device_lat is not None and body.device_lon is not None:
        device_location = (body.device_lat, body.device_lon)
    try:
        result = await pti_service.submit_inspection(
            inspection_id, int(ins["user_id"]), tenant_db,
            device_location=device_location,
        )
    except pti_service.PTIError as e:
        raise _map_pti_error(e)
    return result


# ══════════════════════════════════════════════════════════════════
# TEMPLATE EDITOR
#
# Declared BEFORE the ``/{inspection_id}`` routes so FastAPI matches
# the literal ``/template`` segments first; otherwise "template"
# would be coerced to int and 422 every request.
# ══════════════════════════════════════════════════════════════════


@router.get("/template")
async def get_template(
    user: dict = Depends(require_permission("can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Return the active truck + trailer templates for this account.
    Fleet-side editor consumes this to render the checklist editor."""
    truck = await tenant_db.get_active_template_with_items(
        user["account_id"], "truck", "weekly",
    )
    trailer = await tenant_db.get_active_template_with_items(
        user["account_id"], "trailer", "weekly",
    )
    return {"truck": truck, "trailer": trailer}


@router.put("/template/{vehicle_type}/items/{item_key}")
async def upsert_template_item(
    vehicle_type: str,
    item_key: str,
    body: TemplateItemBody,
    user: dict = Depends(require_permission("can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Add or update one item on a template."""
    if vehicle_type not in ("truck", "trailer"):
        raise HTTPException(status_code=422, detail="vehicle_type must be truck|trailer")
    if body.item_type not in ("check", "photo", "document"):
        raise HTTPException(status_code=422, detail="item_type must be check|photo|document")
    tpl = await _resolve_active_template(
        tenant_db, user["account_id"], vehicle_type,
    )
    row_id = await tenant_db.upsert_template_item(
        int(tpl["id"]),
        item_key=item_key,
        label=body.label,
        category=body.category,
        requires_media=body.requires_media,
        required=body.required,
        sort_order=body.sort_order,
        item_type=body.item_type,
    )
    return {"id": row_id, "item_key": item_key}


@router.delete("/template/{vehicle_type}/items/{item_key}")
async def delete_template_item(
    vehicle_type: str,
    item_key: str,
    user: dict = Depends(require_permission("can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    if vehicle_type not in ("truck", "trailer"):
        raise HTTPException(status_code=422, detail="vehicle_type must be truck|trailer")
    tpl = await _resolve_active_template(
        tenant_db, user["account_id"], vehicle_type,
    )
    ok = await tenant_db.delete_template_item(int(tpl["id"]), item_key)
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"deleted": True}


@router.post("/template/{vehicle_type}/items/{item_key}/reference-image")
async def upload_reference_image(
    vehicle_type: str,
    item_key: str,
    file: UploadFile = File(...),
    user: dict = Depends(require_permission("can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Attach a reference photo to a template item — the example shot
    the driver sees in the wizard ("here's what a correct Mudflap photo
    looks like").  Fleet-only; stored under the account's
    ``template_refs`` ObjectStorage folder."""
    from adapters.storage.object_storage import get_object_storage_for_account

    if vehicle_type not in ("truck", "trailer"):
        raise HTTPException(status_code=422, detail="vehicle_type must be truck|trailer")
    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Reference image must be an image, got {content_type or 'unknown'}",
        )
    raw = await file.read()
    if len(raw) > _MAX_MEDIA_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Reference image exceeds {_MAX_MEDIA_BYTES // (1024 * 1024)} MB limit.",
        )
    await _check_quota_or_413(tenant_db, int(user["account_id"]), len(raw))

    tpl = await _resolve_active_template(
        tenant_db, user["account_id"], vehicle_type,
    )
    # Stable, opaque filename per (vehicle_type, item_key) so re-uploads
    # overwrite the prior reference rather than orphaning blobs.
    ext = "jpg" if "jpeg" in content_type or "jpg" in content_type else content_type.split("/")[-1]
    safe_key = item_key.replace("/", "_")[:64]
    filename = f"ref_{vehicle_type}_{safe_key}.{ext}"
    # Bucket is account-agnostic: DiskObjectStorage prefixes with
    # account-{id}/ internally, and on Drive it lands cleanly at
    # ``4truck/template_refs/`` (no redundant account-id segment).
    folder = "template_refs"

    store = await get_object_storage_for_account(user["account_id"], tenant_db)
    if not store.put(folder, filename, raw):
        raise _storage_put_error(store)

    ok = await tenant_db.set_template_item_reference_image(
        int(tpl["id"]), item_key, filename,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Template item not found")
    return {"item_key": item_key, "reference_image_url": filename}


@router.delete("/template/{vehicle_type}/items/{item_key}/reference-image")
async def delete_reference_image(
    vehicle_type: str,
    item_key: str,
    user: dict = Depends(require_permission("can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Clear a template item's reference photo."""
    if vehicle_type not in ("truck", "trailer"):
        raise HTTPException(status_code=422, detail="vehicle_type must be truck|trailer")
    tpl = await _resolve_active_template(
        tenant_db, user["account_id"], vehicle_type,
    )
    await tenant_db.set_template_item_reference_image(int(tpl["id"]), item_key, None)
    return {"item_key": item_key, "reference_image_url": None}


@router.get("/template-ref/{filename}")
async def stream_reference_image(
    filename: str,
    user: dict = Depends(require_permission_any("can_inspections_vehicle", "can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Stream a template reference image.  Account-scoped by the JWT —
    the DiskObjectStorage transparently prefixes the bucket with
    ``account-{id}/``, so one tenant can't read another's references
    even if filenames collide.  Readable by drivers too (own scope) so
    the Mini App wizard can show the example above the camera."""
    from adapters.storage.object_storage import get_object_storage_for_account

    # Defensive: a filename is an opaque key, never a path.
    safe_name = filename.replace("/", "_").replace("\\", "_")
    store = await get_object_storage_for_account(user["account_id"], tenant_db)
    # Bucket is account-agnostic: DiskObjectStorage prefixes with
    # account-{id}/ internally, and on Drive it lands cleanly at
    # ``4truck/template_refs/`` (no redundant account-id segment).
    folder = "template_refs"
    data = store.get(folder, safe_name)
    if data is None:
        raise HTTPException(status_code=404, detail="Reference image not found")
    return StreamingResponse(
        iter([data]),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post("/template/reorder")
async def reorder_template(
    body: TemplateReorderBody,
    user: dict = Depends(require_permission("can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    if body.vehicle_type not in ("truck", "trailer"):
        raise HTTPException(status_code=422, detail="vehicle_type must be truck|trailer")
    tpl = await _resolve_active_template(
        tenant_db, user["account_id"], body.vehicle_type,
    )
    n = await tenant_db.reorder_template_items(int(tpl["id"]), body.ordered_keys)
    return {"reordered": n}


@router.post("/template/reset")
async def reset_template(
    body: TemplateResetBody,
    user: dict = Depends(require_permission("can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Bump the template version + repopulate from Standard DOT."""
    if body.vehicle_type not in ("truck", "trailer"):
        raise HTTPException(status_code=422, detail="vehicle_type must be truck|trailer")
    new_id = await pti_service.reset_template_to_default(
        user["account_id"], body.vehicle_type, tenant_db,
    )
    items = (
        STANDARD_DOT_TRUCK_ITEMS
        if body.vehicle_type == "truck"
        else STANDARD_DOT_TRAILER_ITEMS
    )
    return {"template_id": new_id, "items_count": len(items)}


# ══════════════════════════════════════════════════════════════════
# FLEET-FACING
# ══════════════════════════════════════════════════════════════════


class CreateInspectionRequest(BaseModel):
    """Fleet-driven ad-hoc inspection creation."""
    driver_id:       int
    vehicle_name:    str
    inspection_type: str = "weekly"
    vehicle_type:    str = "truck"
    due_by:          Optional[str] = None  # ISO-8601; defaults to now + cadence


@router.post("")
async def create_inspection(
    body: CreateInspectionRequest,
    user: dict = Depends(require_permission("can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Fleet creates an ad-hoc PTI outside the weekly cron cadence.

    Validates driver belongs to the account, target template exists,
    and the driver doesn't already have an open inspection.  Fires
    an immediate Telegram ping to the driver.
    """
    if body.vehicle_type not in ("truck", "trailer"):
        raise HTTPException(
            status_code=400, detail="vehicle_type must be 'truck' or 'trailer'",
        )
    if not (body.vehicle_name or "").strip():
        raise HTTPException(
            status_code=400, detail="vehicle_name is required",
        )
    try:
        return await pti_service.create_ad_hoc_inspection(
            account_id=user["account_id"],
            driver_id=body.driver_id,
            vehicle_name=body.vehicle_name.strip(),
            inspection_type=body.inspection_type,
            vehicle_type=body.vehicle_type,
            due_by=body.due_by,
            tenant_db=tenant_db,
            platform_db=platform_db,
        )
    except pti_service.PTIError as e:
        raise _map_pti_error(e)


@router.post("/{inspection_id}/remind")
async def resend_inspection_reminder(
    inspection_id: int,
    user: dict = Depends(require_permission("can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Re-ping the driver assigned to an open inspection.

    Throttled to once per hour per inspection so a fleet member tapping
    the button repeatedly doesn't spam the driver.
    """
    try:
        return await pti_service.resend_reminder(
            inspection_id,
            account_id=user["account_id"],
            tenant_db=tenant_db,
        )
    except pti_service.PTIError as e:
        raise _map_pti_error(e)


async def _drivers_in_my_companies(user: dict) -> list[int] | None:
    """User ids the caller's Team-Management companies permit, or None
    when unrestricted.

    ``driver_inspections`` carries no company column — an inspection
    belongs to a DRIVER, and Team Management is where a user's companies
    are set, so the wall resolves through the person rather than the
    vehicle.  Keying on the driver also sidesteps the trap the alerting
    map documents: vehicle NAMES collide across companies, user ids do
    not.

    Returns ``[]`` (deny-all) rather than None when the caller is
    restricted but no driver falls in their companies — the two are
    different answers and the query treats them differently.
    """
    allowed = await get_user_company_codes(user)
    if not allowed:
        return None
    platform_db = _get_router().platform
    by_user = await platform_db.get_all_user_company_codes(user["account_id"])
    allowed_upper = {c.upper() for c in allowed}
    return [
        uid for uid, codes in (by_user or {}).items()
        if any((c or "").upper() in allowed_upper for c in (codes or []))
    ]


@router.get("")
async def list_inspections(
    review_status: Optional[str] = Query(default=None),
    status:        Optional[str] = Query(default=None),
    vehicle_name:  Optional[str] = Query(default=None),
    user_id:       Optional[int] = Query(default=None),
    days:          int           = Query(default=30, ge=1, le=365),
    page:          int           = Query(default=1, ge=1),
    page_size:     int           = Query(default=50, ge=1, le=200),
    user: dict = Depends(require_permission("can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Dashboard list — paginated, filterable."""
    return await tenant_db.list_inspections_for_account(
        user["account_id"],
        review_status=review_status,
        status=status,
        vehicle_name=vehicle_name,
        user_id=user_id,
        only_user_ids=await _drivers_in_my_companies(user),
        days=days,
        page=page,
        page_size=page_size,
    )


@router.get("/{inspection_id}")
async def get_inspection(
    inspection_id: int,
    user: dict = Depends(require_permission_any("can_inspections_vehicle", "can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Inspection detail (items + media list).  Drivers can fetch
    their own; fleet can fetch any.

    Enriches the row with ``reviewed_by_name`` + ``driver_name`` so
    the dashboard can render "Reviewed by Jane Doe" without a second
    round trip per row.  Lookups are scoped to the account so a
    cross-tenant id collision can't leak a name.
    """
    ins = await _require_visible_inspection(inspection_id, user, tenant_db)
    detail = await tenant_db.get_inspection_detail(
        int(ins["id"]), account_id=user["account_id"],
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    await _enrich_inspection_names(detail, platform_db)
    return detail


@router.get("/{inspection_id}/media/{media_id}")
async def stream_media(
    inspection_id: int,
    media_id: int,
    user: dict = Depends(require_permission_any("can_inspections_vehicle", "can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Stream a media file back to the client.  Same auth shape as
    work-order attachments — read-through ObjectStorage.get."""
    from adapters.storage.object_storage import get_object_storage_for_account
    ins = await _require_visible_inspection(inspection_id, user, tenant_db)
    media = await tenant_db.get_inspection_media(media_id)
    if not media or int(media.get("inspection_id") or 0) != inspection_id:
        raise HTTPException(status_code=404, detail="Media not found")
    store = await get_object_storage_for_account(user["account_id"], tenant_db)
    company_folder = await _inspection_company_folder(
        tenant_db, int(user["account_id"]), dict(ins),
    )
    folder = _inspection_folder(company_folder, int(ins["id"]))
    legacy = _legacy_inspection_folder(
        int(user["account_id"]), int(ins["id"]),
    )
    data = _media_bytes(store, dict(media), folder, legacy_folder=legacy)
    if data is None:
        raise HTTPException(status_code=404, detail="File not found in storage")
    return StreamingResponse(
        iter([data]),
        media_type=media.get("content_type") or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{media.get("file_name") or "media"}"',
            "Cache-Control": "private, max-age=600",
        },
    )


@router.get("/{inspection_id}/report.pdf")
async def download_report(
    inspection_id: int,
    user: dict = Depends(require_permission_any("can_inspections_vehicle", "can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Render the whole inspection as one self-contained PDF.

    Bundles the checklist, per-item statuses + AI verdicts, the captured
    photos (embedded inline), documents, and signatures into a single
    file — the compliance artifact for DOT / insurance / carrier files.
    Drivers can download their own; fleet can download any.
    """
    import asyncio
    from adapters.storage.object_storage import get_object_storage_for_account
    from features.inspections import pdf as pti_pdf

    ins = await _require_visible_inspection(inspection_id, user, tenant_db)
    detail = await tenant_db.get_inspection_detail(
        int(ins["id"]), account_id=user["account_id"],
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    await _enrich_inspection_names(detail, platform_db)

    # Gather photo bytes up front (network I/O for Drive-backed stores)
    # so the synchronous PDF build doesn't touch storage.  A failed
    # fetch (e.g. expired token) just renders "[photo unavailable]" —
    # the report still generates.
    store = await get_object_storage_for_account(user["account_id"], tenant_db)
    company_folder = await _inspection_company_folder(
        tenant_db, int(user["account_id"]), dict(ins),
    )
    folder = _inspection_folder(company_folder, int(ins["id"]))
    legacy = _legacy_inspection_folder(
        int(user["account_id"]), int(ins["id"]),
    )
    images: dict[int, bytes] = {}
    for m in (detail.get("media") or []):
        mtype = (m.get("media_type") or "photo").lower()
        ctype = (m.get("content_type") or "").lower()
        # Only fetch embeddable stills (skip videos + PDF documents).
        if mtype == "video" or (mtype == "document" and "pdf" in ctype):
            continue
        try:
            data = await asyncio.to_thread(
                _media_bytes, store, dict(m), folder, legacy,
            )
        except Exception:
            data = None
        if data:
            images[int(m["id"])] = data

    try:
        pdf_bytes = await asyncio.to_thread(pti_pdf.build_inspection_pdf, detail, images)
    except Exception:
        logger.exception("PTI PDF build failed for inspection %d", inspection_id)
        raise HTTPException(status_code=500, detail="Failed to generate report PDF")

    vehicle = (detail.get("vehicle_name") or "vehicle").replace("/", "_")
    fname = f"PTI_{vehicle}_{inspection_id}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "private, max-age=60",
        },
    )


@router.post("/{inspection_id}/review")
async def review(
    inspection_id: int,
    body: ReviewBody,
    user: dict = Depends(require_permission("can_inspections_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Fleet records the review outcome."""
    if body.review_status not in VALID_REVIEW_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"review_status must be one of {sorted(VALID_REVIEW_STATUSES)}",
        )
    try:
        refreshed = await pti_service.review_inspection(
            inspection_id,
            account_id=user["account_id"],
            reviewer_id=int(user["sub"]),
            review_status=body.review_status,
            review_notes=body.review_notes,
            tenant_db=tenant_db,
        )
    except pti_service.PTIError as e:
        raise _map_pti_error(e)
    return {"inspection": refreshed}


