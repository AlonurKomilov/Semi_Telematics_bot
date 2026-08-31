"""The vehicle-documents endpoints.

Registration, title, insurance, annual-inspection certificates: files
that belong to a REGISTRY row and live in the company's own folder tree
(``{COMPANY}/vehicles/{unit}/`` — capabilities/object_storage/docs/LAYOUT.md
is the law).  Mirrors the driver-documents flow deliberately, cap for
cap and rail for rail, because it is the same product idea for a
different entity.

Its own module, like ``config.py``: a self-contained sub-resource with
its own permission shape, kept out of the 1,700-line feature router.

``GET /vehicles/documents`` is a ONE-segment route, so it lives or dies
by MOUNT ORDER: this router is included before the vehicles router,
whose parametric ``/vehicles/{vehicle_name}`` would otherwise swallow
it and answer with a truck named "documents".  That is the trap that
once hid ``/vehicles/config``, and ``test_vehicle_documents.py`` pins
the order so a re-ordering in app.py fails there rather than in
production.

The archive/restore folder moves live in ``service.py`` — they are
called by the vehicles router's lifecycle routes, not by these.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.responses import StreamingResponse

from adapters.storage.vehicle_documents import VEHICLE_DOC_TYPES
from infra.platform import get_tenant_db as _get_tenant_db
from features.vehicles.scope import company_allows
from interfaces.api.rate_limit import limiter
from interfaces.api.deps import (
    get_platform_db,
    get_tenant_db,
    get_user_company_codes,
    require_permission,
    require_permission_any,
    resolve_user_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vehicles", tags=["vehicles"])

# Its own pair, not can_manage_vehicles: that grant renames and
# ARCHIVES trucks, and filing an insurance certificate should not
# require the power to retire a tractor.  Seeded to exactly today's
# access, so this split took nothing from anybody — it made the
# narrowing possible.
_manage = require_permission("can_manage_vehicle_docs")
_view = require_permission("can_vehicle_docs")

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


async def _vehicle_or_404(tenant, account_id: int, vehicle_id: int, user):
    """The vehicle this caller may act on, or 404.

    Account scope alone is NOT the wall: a company-restricted operator
    must not reach another company's paperwork by guessing a registry
    id.  404 rather than 403, matching every other id-referencing
    vehicle route — a 403 would confirm the row exists, which is the
    disclosure the wall exists to prevent.
    """
    v = await tenant.get_vehicle(account_id, vehicle_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    allowed = await get_user_company_codes(user)
    if not company_allows(getattr(v, "company_code", "") or "", allowed):
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return v


async def _trail(
    tenant, account_id: int, user: dict, vehicle_id: int,
    action: str, note: str, **context,
) -> None:
    """Record one document act on the TRUCK's activity trail.

    Not a trail of its own: the operator asking "what happened to unit
    110?" wants its papers, its VIN changes and its archive in one
    list, and a second trail would answer half the question in a place
    they have to know to look.

    Best-effort — a lost audit line must never fail an upload the
    operator watched succeed — but logged loudly, because a delete
    nobody recorded is the case this exists for.
    """
    try:
        from capabilities.activity_trail.recorder import record_simple

        await record_simple(
            tenant, account_id, await resolve_user_id(user),
            action, "vehicle", vehicle_id, note=note, context=context,
        )
    except Exception:
        logger.warning("document trail not recorded: %s v=%d acct=%d",
                       action, vehicle_id, account_id, exc_info=True)


async def _bucket_for(tenant, account_id: int, v) -> str:
    from capabilities.object_storage.paths import resolve_company_folder
    from features.vehicles.documents.paths import vehicle_docs_bucket
    company_folder = await resolve_company_folder(
        tenant, account_id, v.company_code,
    )
    return vehicle_docs_bucket(company_folder, v.unit_number)


@router.get("/documents")
async def list_account_documents(user: dict = Depends(_view)):
    """Every document across the account's live trucks — the fleet-wide
    view behind the Documents page.

    Mounted BEFORE the vehicles router, which is what keeps
    ``/vehicles/documents`` from being swallowed by its parametric
    ``/vehicles/{vehicle_name}`` — the trap that once hid
    ``/vehicles/config``.
    """
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    rows = await tenant.list_account_vehicle_documents(account_id)
    # The company wall applies to a LIST the same way it applies to a
    # row: a restricted operator sees their own companies' paperwork.
    allowed = await get_user_company_codes(user)
    rows = [r for r in rows
            if company_allows(r.get("company_code") or "", allowed)]
    return {"documents": rows, "doc_types": list(VEHICLE_DOC_TYPES)}


@router.post("/documents/extract")
@limiter.limit("30/hour")
async def extract_document_fields(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(_manage),
):
    """Read a vehicle document (photo/PDF) → the fields the form asks for.

    TRANSIENT by design, the same contract the invoice scanner follows:
    nothing is stored, no document row is created.  The dialog pre-fills
    from the reply, the operator reviews it against the paper in their
    hand, and only Upload writes.  The expiry date is the field the
    whole warning chain reads, so it must never arrive unseen.

    Gated on ``can_manage_vehicle_docs`` — you may only scan what you
    could file — and rate-limited because every call is a paid
    vision-model request.

    Declared ahead of ``/documents/{doc_id}/…`` so the literal path
    wins the match.
    """
    from features.vehicles.documents.extraction import (
        EXTRACT_MIMES, extract_document,
    )
    from infra.file_safety import sniff_mime

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="Empty file.")
    if len(raw) > _MAX_DOC_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {_MAX_DOC_BYTES // (1024 * 1024)} MB limit.",
        )

    # Magic bytes decide, not the declared Content-Type: a paid model
    # call earns the same rigor as the public intake.  HEIC is not in
    # the sniffer's vocabulary, so verify its ISO-BMFF ``ftyp`` box and
    # keep iPhone photos first-class.
    declared = (file.content_type or "").lower()
    mime = sniff_mime(raw)
    if mime is None and declared in ("image/heic", "image/heif") \
            and len(raw) > 12 and raw[4:8] == b"ftyp":
        mime = declared
    if mime not in EXTRACT_MIMES:
        raise HTTPException(
            status_code=415,
            detail="Please upload a document photo (JPG/PNG/WEBP/HEIC) or PDF.",
        )

    result = await extract_document(
        raw, mime,
        account_id=int(user["account_id"]),
        user_id=int(user["sub"]) if user.get("sub") else None,
        role=user.get("role"),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=502,
                            detail=result.get("error", "Extraction failed."))
    return result


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
    await _vehicle_or_404(tenant, account_id, vehicle_id, user)
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
    v = await _vehicle_or_404(tenant, account_id, vehicle_id, user)

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
    await _trail(
        tenant, account_id, user, vehicle_id, "document.upload",
        f"Uploaded {doc.doc_type.replace('_', ' ')}: {doc.file_name}",
        doc_id=doc.id, doc_type=doc.doc_type, file_name=doc.file_name,
        expires_at=doc.expires_at or "",
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
    # The document id is the caller's only input, so the wall has to be
    # applied to the vehicle BEHIND it — otherwise a company-restricted
    # operator reads another company's title by guessing a number.
    await _vehicle_or_404(tenant, account_id, doc.vehicle_id, user)

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


@router.post("/documents/{doc_id}/archive")
async def archive_vehicle_document(
    doc_id: int,
    user: dict = Depends(_manage),
    tenant_db=Depends(get_tenant_db),
):
    """Step a superseded paper aside instead of deleting it.

    This year's registration is filed; last year's still proves the
    truck was legal last year, which is what an audit asks.  It moves
    to ``…/vehicles/{unit}/documents/_archive/`` — inside the truck,
    because the TRUCK has not gone anywhere; only this paper stopped
    being current.
    """
    from adapters.storage.object_storage import get_object_storage_for_account
    from capabilities.object_storage.paths import resolve_company_folder
    from features.vehicles.documents.paths import vehicle_docs_archive_bucket

    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    peek = await tenant.get_vehicle_document(account_id, doc_id)
    if peek is None or peek.status != "active":
        raise HTTPException(status_code=404, detail="Document not found")
    v = await _vehicle_or_404(tenant, account_id, peek.vehicle_id, user)

    company_folder = await resolve_company_folder(
        tenant, account_id, v.company_code)
    dst = vehicle_docs_archive_bucket(company_folder, v.unit_number)

    # FILE first, ROW second — a row pointing at a file that has not
    # moved yet recovers on the next line; a row left behind after the
    # file moved is a download that 404s with nothing to explain it.
    store = await get_object_storage_for_account(account_id, tenant_db)
    try:
        data = store.get(peek.bucket, peek.object_key)
        if data:
            store.put(dst, peek.object_key, data)
            store.delete(peek.bucket, peek.object_key)
    except Exception:
        logger.warning("archive: document file not moved doc=%d", doc_id,
                       exc_info=True)

    doc = await tenant.archive_vehicle_document(
        account_id, doc_id, bucket=dst)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    await _trail(
        tenant, account_id, user, peek.vehicle_id, "document.archive",
        f"Archived {doc.doc_type.replace('_', ' ')}: {doc.file_name}",
        doc_id=doc_id, doc_type=doc.doc_type, file_name=doc.file_name,
    )
    return {"archived": True, "id": doc_id}


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
    # Walled before the delete, not after: the flip is the damage.
    peek = await tenant.get_vehicle_document(account_id, doc_id)
    if peek is None:
        raise HTTPException(status_code=404, detail="Document not found")
    await _vehicle_or_404(tenant, account_id, peek.vehicle_id, user)
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
    await _trail(
        tenant, account_id, user, peek.vehicle_id, "document.delete",
        f"Deleted {doc.doc_type.replace('_', ' ')}: {doc.file_name}",
        doc_id=doc_id, doc_type=doc.doc_type, file_name=doc.file_name,
    )
    return {"deleted": True, "id": doc_id}
