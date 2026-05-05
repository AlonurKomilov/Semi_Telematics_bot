"""File storage API — Google Drive backed object storage.

Folder structure in Drive:
    4truck/
    └── {company_name}/
        ├── reports/
        ├── documents/
        ├── images/
        ├── billing/
        └── maintenance/

All endpoints require authentication.  Company access is enforced:
  - Owners / admins may access any company.
  - Other roles may only access companies they are assigned to.

Upload limit: 50 MB per file (enforced by nginx and FastAPI body-size limit).
"""

from __future__ import annotations

import mimetypes
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from interfaces.api.deps import (
    require_permission_any,
    get_user_company_codes,
    validate_company_access,
)
import adapters.storage.gdrive as _gdrive

router = APIRouter(prefix="/files", tags=["files"])

_MAX_UPLOAD_BYTES = int(os.getenv("GDRIVE_MAX_UPLOAD_MB", "50")) * 1024 * 1024


def _drive_available() -> bool:
    return _gdrive._is_configured()


# ── List files ────────────────────────────────────────────────────────────────

@router.get("/")
async def list_files(
    company: str = Query(..., description="Company code / display name"),
    category: str | None = Query(None, description="Sub-folder: reports, documents, billing, maintenance, images"),
    page_size: int = Query(50, ge=1, le=100),
    page_token: str | None = Query(None),
    user: dict = Depends(require_permission_any(
        "can_faults", "can_fuel", "can_health", "can_vehicle_all",
        "can_vehicle_own", "can_efficiency",
    )),
):
    """List files stored in Drive for a company."""
    if not _drive_available():
        raise HTTPException(status_code=503, detail="Google Drive storage is not configured on this server.")

    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)

    if category and category not in _gdrive.VALID_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"category must be one of: {', '.join(sorted(_gdrive.VALID_CATEGORIES))}",
        )

    try:
        result = await _gdrive.list_files(
            company=company,
            category=category,
            page_size=page_size,
            page_token=page_token,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Drive error: {exc}") from exc

    return result


# ── Upload ─────────────────────────────────────────────────────────────────────

@router.post("/upload", status_code=201)
async def upload_file(
    company: str = Form(..., description="Company code / display name"),
    category: str = Form(..., description="Sub-folder: reports, documents, billing, maintenance, images"),
    file: UploadFile = File(...),
    user: dict = Depends(require_permission_any(
        "can_faults", "can_fuel", "can_health", "can_vehicle_all",
        "can_vehicle_own", "can_efficiency",
    )),
):
    """Upload a file to Google Drive under company/category/."""
    if not _drive_available():
        raise HTTPException(status_code=503, detail="Google Drive storage is not configured on this server.")

    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)

    if category not in _gdrive.VALID_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"category must be one of: {', '.join(sorted(_gdrive.VALID_CATEGORIES))}",
        )

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large — maximum upload size is {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    mime_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    filename = file.filename or "upload"

    try:
        metadata = await _gdrive.upload_file(
            company=company,
            category=category,
            filename=filename,
            content=content,
            mime_type=mime_type,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Drive upload failed: {exc}") from exc

    return {
        "id": metadata.get("id"),
        "name": metadata.get("name"),
        "size": metadata.get("size"),
        "webViewLink": metadata.get("webViewLink"),
        "createdTime": metadata.get("createdTime"),
    }


# ── File metadata ──────────────────────────────────────────────────────────────

@router.get("/{file_id}")
async def get_file(
    file_id: str,
    user: dict = Depends(require_permission_any(
        "can_faults", "can_fuel", "can_health", "can_vehicle_all",
        "can_vehicle_own", "can_efficiency",
    )),
):
    """Return metadata for a single Drive file (id, name, size, webViewLink)."""
    if not _drive_available():
        raise HTTPException(status_code=503, detail="Google Drive storage is not configured.")

    try:
        metadata = await _gdrive.get_file_metadata(file_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"File not found: {exc}") from exc

    return metadata


# ── Delete ─────────────────────────────────────────────────────────────────────

@router.delete("/{file_id}", status_code=204)
async def delete_file(
    file_id: str,
    user: dict = Depends(require_permission_any("can_vehicle_all", "can_faults")),
):
    """Permanently delete a file from Drive. Requires admin-level permission."""
    if not _drive_available():
        raise HTTPException(status_code=503, detail="Google Drive storage is not configured.")

    try:
        await _gdrive.delete_file(file_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Drive delete failed: {exc}") from exc

    return JSONResponse(status_code=204, content=None)


# ── Status ─────────────────────────────────────────────────────────────────────

@router.get("/_status")
async def drive_status(
    user: dict = Depends(require_permission_any("can_vehicle_all", "can_faults")),
):
    """Return whether Google Drive storage is configured and reachable."""
    configured = _drive_available()
    return {
        "configured": configured,
        "root_folder_set": bool(os.getenv("GDRIVE_ROOT_FOLDER_ID")),
        "credentials_file_found": bool(
            os.getenv("GDRIVE_CREDENTIALS_FILE", "semi-telematics-6db56ba1f512.json")
            and os.path.isfile(
                os.getenv("GDRIVE_CREDENTIALS_FILE", "semi-telematics-6db56ba1f512.json")
            )
        ),
    }
