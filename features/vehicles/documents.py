"""Vehicle documents — the paperwork one truck carries.

Registration, title, insurance, annual-inspection certificates: files
that belong to a REGISTRY row and live in the company's own folder tree
(``{COMPANY}/vehicles/{unit}/`` — capabilities/object_storage/LAYOUT.md
is the law).  Mirrors the driver-documents flow deliberately, cap for
cap and rail for rail, because it is the same product idea for a
different entity.

Its own module, like ``config.py``: a self-contained sub-resource with
its own permission shape, kept out of the 1,700-line feature router.
Every route here has ≥2 path segments after the prefix, so none can be
shadowed by the feature router's parametric ``/{vehicle_name}`` —
verified by ``test_vehicle_documents.py`` resolving them.

Archive/restore integration: retiring a truck moves its folder to
``vehicles/_archive/{date}/{unit}/`` and restore moves it back — the
same move-folder-then-rewrite-rows recipe the driver company-change
uses, so a retired truck's paperwork stays reachable from its (still
readable) detail page and the original location frees up for a future
truck that reuses the number.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from adapters.storage.vehicle_documents import VEHICLE_DOC_TYPES
from infra.platform import get_tenant_db as _get_tenant_db
from interfaces.api.deps import (
    get_platform_db,
    get_tenant_db,
    require_permission,
    require_permission_any,
    resolve_user_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vehicles", tags=["vehicles"])

_manage = require_permission("can_manage_vehicles")
_view = require_permission_any("can_faults", "can_vehicle_vehicle")

# Layer 1 of the three-layer cap (route → middleware → nginx) — same
# number as driver documents: paperwork is PDFs and phone photos.
_MAX_DOC_BYTES = 20 * 1024 * 1024
_ALLOWED_MIME = frozenset({
    "application/pdf",
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
})


def _safe_filename(name: str) -> str:
    keep = "".join(
        c if c.isalnum() or c in "._- " else "_" for c in (name or "")
    ).strip()
    return keep[:120] or "document.bin"


async def _vehicle_or_404(tenant, account_id: int, vehicle_id: int):
    v = await tenant.get_vehicle(account_id, vehicle_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return v


async def _bucket_for(tenant, account_id: int, v) -> str:
    from features.work_orders.storage import (
        resolve_company_folder, vehicle_docs_bucket,
    )
    company_folder = await resolve_company_folder(
        tenant, account_id, v.company_code,
    )
    return vehicle_docs_bucket(company_folder, v.unit_number)


@router.get("/registry/{vehicle_id}/documents")
async def list_vehicle_documents(
    vehicle_id: int,
    user: dict = Depends(_view),
):
    """The truck's paperwork — including an ARCHIVED truck's.  Keeping
    this readable after retirement is the reason archiving keeps the
    row instead of deleting it."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    await _vehicle_or_404(tenant, account_id, vehicle_id)
    docs = await tenant.list_vehicle_documents(account_id, vehicle_id)
    return {
        "documents": [
            {
                "id": d.id, "doc_type": d.doc_type,
                "file_name": d.file_name, "file_size": d.file_size,
                "mime_type": d.mime_type,
                "issued_at": d.issued_at, "expires_at": d.expires_at,
                "uploaded_at": d.uploaded_at, "notes": d.notes,
            }
            for d in docs
        ],
        "doc_types": list(VEHICLE_DOC_TYPES),
    }


@router.post("/registry/{vehicle_id}/documents")
async def upload_vehicle_document(
    vehicle_id: int,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    issued_at: str = Form(""),
    expires_at: str = Form(""),
    notes: str = Form(""),
    user: dict = Depends(_manage),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    from adapters.storage.object_storage import get_object_storage_for_account

    account_id = int(user["account_id"])
    if doc_type not in VEHICLE_DOC_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"doc_type must be one of {sorted(VEHICLE_DOC_TYPES)}",
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

    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    v = await _vehicle_or_404(tenant, account_id, vehicle_id)

    # Quota rail — meaningful for the local-disk backend; Drive accounts
    # cap on Google's side.  Same rail, same wording as driver docs.
    try:
        await platform_db.enforce_storage_quota(account_id, len(raw))
    except Exception as e:
        raise HTTPException(status_code=507, detail=str(e))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    object_key = f"{doc_type}_{stamp}_{_safe_filename(file.filename or '')}"
    bucket = await _bucket_for(tenant, account_id, v)

    store = await get_object_storage_for_account(account_id, tenant_db)
    try:
        stored = store.put(bucket, object_key, raw)
    except Exception:
        logger.exception("vehicle doc store failed acct=%d v=%d",
                         account_id, vehicle_id)
        raise HTTPException(
            status_code=502,
            detail="Storage backend could not save the document.",
        )

    doc = await tenant.add_vehicle_document(
        account_id, vehicle_id,
        doc_type=doc_type, bucket=bucket, object_key=object_key,
        drive_file_id=getattr(stored, "file_id", None),
        file_name=_safe_filename(file.filename or ""),
        file_size=len(raw), mime_type=content_type,
        issued_at=issued_at or None, expires_at=expires_at or None,
        uploaded_by=await resolve_user_id(user),
        notes=notes or None,
    )
    return {"id": doc.id, "doc_type": doc.doc_type,
            "file_name": doc.file_name, "uploaded_at": doc.uploaded_at}


@router.get("/documents/{doc_id}/download")
async def download_vehicle_document(
    doc_id: int,
    user: dict = Depends(_view),
    tenant_db=Depends(get_tenant_db),
):
    from adapters.storage.object_storage import get_object_storage_for_account

    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    doc = await tenant.get_vehicle_document(account_id, doc_id)
    if doc is None or doc.status != "active":
        raise HTTPException(status_code=404, detail="Document not found")

    store = await get_object_storage_for_account(account_id, tenant_db)
    try:
        if doc.drive_file_id and hasattr(store, "get_by_id"):
            data = store.get_by_id(doc.drive_file_id)
        else:
            data = store.get(doc.bucket, doc.object_key)
    except Exception:
        logger.exception("vehicle doc fetch failed doc=%d", doc_id)
        raise HTTPException(
            status_code=502,
            detail="Storage backend could not retrieve the document.",
        )
    if not data:
        raise HTTPException(status_code=404,
                            detail="Document file not found in storage")
    return StreamingResponse(
        io.BytesIO(data),
        media_type=doc.mime_type or "application/octet-stream",
        headers={"Content-Disposition":
                 f'inline; filename="{doc.file_name}"'},
    )


@router.delete("/documents/{doc_id}")
async def delete_vehicle_document(
    doc_id: int,
    user: dict = Depends(_manage),
    tenant_db=Depends(get_tenant_db),
):
    from adapters.storage.object_storage import get_object_storage_for_account

    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    doc = await tenant.delete_vehicle_document(account_id, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    # Object removal is best-effort AFTER the row flip: a file that
    # outlives its row is orphaned bytes; a row that outlives its file
    # is a download that 404s — the first is the cheaper failure.
    try:
        store = await get_object_storage_for_account(account_id, tenant_db)
        store.delete(doc.bucket, doc.object_key)
    except Exception:
        logger.warning("vehicle doc object not removed doc=%d", doc_id,
                       exc_info=True)
    return {"deleted": True, "id": doc_id}


async def move_documents_on_archive(
    tenant, account_id: int, vehicle_id: int,
) -> str | None:
    """Move a retiring truck's folder to the archive tree.

    Best-effort by contract: the archive itself already happened, and a
    folder that failed to move is a cosmetic misfiling, not a data
    loss — the rows still point at the old bucket, so downloads keep
    working either way.  Returns the new bucket, or None."""
    return await _move_documents(tenant, account_id, vehicle_id,
                                 to_archive=True)


async def move_documents_on_restore(
    tenant, account_id: int, vehicle_id: int,
) -> str | None:
    """The reverse — a restored truck's paperwork comes home."""
    return await _move_documents(tenant, account_id, vehicle_id,
                                 to_archive=False)


async def _move_documents(
    tenant, account_id: int, vehicle_id: int, *, to_archive: bool,
) -> str | None:
    from adapters.storage.object_storage import get_object_storage_for_account
    from features.work_orders.storage import (
        resolve_company_folder, vehicle_docs_archive_bucket,
        vehicle_docs_bucket,
    )

    docs = await tenant.list_vehicle_documents(account_id, vehicle_id)
    if not docs:
        return None
    v = await tenant.get_vehicle(account_id, vehicle_id)
    if v is None:
        return None
    company_folder = await resolve_company_folder(
        tenant, account_id, v.company_code,
    )
    live = vehicle_docs_bucket(company_folder, v.unit_number)
    if to_archive:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        src, dst = live, vehicle_docs_archive_bucket(
            company_folder, v.unit_number, date)
    else:
        # Restore: the CURRENT bucket on the rows is the archive
        # location (whatever date it carries); home is the live path.
        src, dst = docs[0].bucket, live
        if src == dst:
            return None
    store = await get_object_storage_for_account(account_id, tenant)
    moved = store.move_folder(src, dst)
    if moved:
        await tenant.move_vehicle_documents_bucket(
            account_id, vehicle_id, src, dst)
        return dst
    return None
