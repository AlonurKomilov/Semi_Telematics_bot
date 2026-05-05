"""Google Drive object-storage adapter.

Architecture
------------
All files are stored under a single shared Drive folder (GDRIVE_ROOT_FOLDER_ID)
with company-level sub-folders:

    4truck/
    ├── PREMIER TRUCKING GROUP INC/
    │   ├── reports/
    │   └── documents/
    ├── GRAND ONE LLC/
    │   ├── reports/
    │   └── documents/
    └── …

The service account (GDRIVE_CREDENTIALS_FILE) must be granted at least
"Editor" access to the root folder in Google Drive.  Folder structure is
created lazily on first upload.

Environment variables
---------------------
GDRIVE_CREDENTIALS_FILE  Path to the service-account JSON key.
                         Defaults to "semi-telematics-6db56ba1f512.json".
GDRIVE_ROOT_FOLDER_ID    Drive folder ID of the "4truck" root.
                         Create the folder in Drive, share it with the
                         service-account email, then paste the folder ID
                         from its URL: drive.google.com/drive/folders/<ID>.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import IO

logger = logging.getLogger(__name__)

_CREDENTIALS_FILE = os.getenv(
    "GDRIVE_CREDENTIALS_FILE",
    "semi-telematics-6db56ba1f512.json",
)
_ROOT_FOLDER_ID = os.getenv("GDRIVE_ROOT_FOLDER_ID", "")

_SCOPES = ["https://www.googleapis.com/auth/drive"]

# Valid category sub-folders inside each company folder
VALID_CATEGORIES = {"reports", "documents", "images", "billing", "maintenance"}


def _is_configured() -> bool:
    """True when both credentials and a root folder are configured."""
    return bool(_CREDENTIALS_FILE and os.path.isfile(_CREDENTIALS_FILE) and _ROOT_FOLDER_ID)


def _build_service():
    """Build an authenticated Drive v3 service (sync — run in thread pool)."""
    # Import here so the module loads fine when google-api-python-client is
    # not installed (Drive features simply won't work).
    from google.oauth2 import service_account  # type: ignore[import]
    from googleapiclient.discovery import build  # type: ignore[import]

    creds = service_account.Credentials.from_service_account_file(
        _CREDENTIALS_FILE,
        scopes=_SCOPES,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


async def _run_sync(fn, *args, **kwargs):
    """Execute a blocking Drive API call in the default thread-pool executor."""
    return await asyncio.to_thread(fn, *args, **kwargs)


# ── Low-level folder helpers ──────────────────────────────────────────────────

def _find_or_create_folder_sync(service, name: str, parent_id: str) -> str:
    """Return the Drive folder ID for `name` under `parent_id`, creating if absent."""
    # Sanitise name: Drive folder names may not contain / or null bytes
    safe_name = name.replace("/", " ").replace("\x00", "").strip() or "unnamed"

    q = (
        f"name = {safe_name!r} "
        f"and '{parent_id}' in parents "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    results = service.files().list(
        q=q,
        spaces="drive",
        fields="files(id, name)",
        pageSize=1,
    ).execute()

    items = results.get("files", [])
    if items:
        return items[0]["id"]

    meta = {
        "name": safe_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def _resolve_folder_sync(service, company: str, category: str) -> str:
    """Return (creating if needed) the Drive folder ID for company/category."""
    company_id = _find_or_create_folder_sync(service, company, _ROOT_FOLDER_ID)
    return _find_or_create_folder_sync(service, category, company_id)


# ── Public async API ──────────────────────────────────────────────────────────

async def upload_file(
    *,
    company: str,
    category: str,
    filename: str,
    content: bytes | IO[bytes],
    mime_type: str = "application/octet-stream",
) -> dict:
    """Upload a file to Drive and return its metadata dict.

    Returns a dict with keys: id, name, webViewLink, size.
    Raises RuntimeError if Drive is not configured.
    """
    if not _is_configured():
        raise RuntimeError(
            "Google Drive not configured — set GDRIVE_ROOT_FOLDER_ID and "
            "ensure GDRIVE_CREDENTIALS_FILE points to the service-account JSON."
        )
    if category not in VALID_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(VALID_CATEGORIES)}")

    from googleapiclient.http import MediaIoBaseUpload  # type: ignore[import]

    def _upload():
        svc = _build_service()
        folder_id = _resolve_folder_sync(svc, company, category)

        raw = content if isinstance(content, (bytes, bytearray)) else content.read()
        media = MediaIoBaseUpload(
            io.BytesIO(raw),
            mimetype=mime_type,
            resumable=False,
        )
        meta = {
            "name": filename,
            "parents": [folder_id],
        }
        result = svc.files().create(
            body=meta,
            media_body=media,
            fields="id, name, size, webViewLink, createdTime",
        ).execute()

        # Make the file viewable by anyone with the link so users can open it
        # without a Google account — harmless because they already authenticated
        # to the 4truck dashboard to get this link.
        svc.permissions().create(
            fileId=result["id"],
            body={"type": "anyone", "role": "reader"},
        ).execute()

        return result

    return await _run_sync(_upload)


async def list_files(
    *,
    company: str,
    category: str | None = None,
    page_size: int = 50,
    page_token: str | None = None,
) -> dict:
    """List files for a company (optionally filtered to one category).

    Returns {"files": [...], "nextPageToken": str | None}.
    Each file dict has: id, name, size, mimeType, webViewLink, createdTime.
    """
    if not _is_configured():
        return {"files": [], "nextPageToken": None}

    def _list():
        svc = _build_service()
        # Build query: find files under company (and optionally category) folder
        company_id = _find_or_create_folder_sync(svc, company, _ROOT_FOLDER_ID)

        if category and category in VALID_CATEGORIES:
            folder_id = _find_or_create_folder_sync(svc, category, company_id)
            q = (
                f"'{folder_id}' in parents "
                "and mimeType != 'application/vnd.google-apps.folder' "
                "and trashed = false"
            )
        else:
            # All files recursively under the company folder — use Drive's
            # flat search by walking all category sub-folder ids
            category_ids: list[str] = []
            for cat in VALID_CATEGORIES:
                cat_q = (
                    f"name = {cat!r} "
                    f"and '{company_id}' in parents "
                    "and mimeType = 'application/vnd.google-apps.folder' "
                    "and trashed = false"
                )
                res = svc.files().list(q=cat_q, spaces="drive", fields="files(id)", pageSize=1).execute()
                for f in res.get("files", []):
                    category_ids.append(f["id"])

            if not category_ids:
                return {"files": [], "nextPageToken": None}

            parents = " or ".join(f"'{fid}' in parents" for fid in category_ids)
            q = (
                f"({parents}) "
                "and mimeType != 'application/vnd.google-apps.folder' "
                "and trashed = false"
            )

        kwargs: dict = dict(
            q=q,
            spaces="drive",
            fields="nextPageToken, files(id, name, size, mimeType, webViewLink, createdTime)",
            pageSize=min(page_size, 100),
            orderBy="createdTime desc",
        )
        if page_token:
            kwargs["pageToken"] = page_token

        result = svc.files().list(**kwargs).execute()
        return {
            "files": result.get("files", []),
            "nextPageToken": result.get("nextPageToken"),
        }

    return await _run_sync(_list)


async def delete_file(file_id: str) -> None:
    """Permanently delete a file by its Drive file ID."""
    if not _is_configured():
        raise RuntimeError("Google Drive not configured.")

    def _delete():
        svc = _build_service()
        svc.files().delete(fileId=file_id).execute()

    await _run_sync(_delete)


async def get_file_metadata(file_id: str) -> dict:
    """Return metadata for a single file (id, name, size, webViewLink)."""
    if not _is_configured():
        raise RuntimeError("Google Drive not configured.")

    def _get():
        svc = _build_service()
        return svc.files().get(
            fileId=file_id,
            fields="id, name, size, mimeType, webViewLink, createdTime",
        ).execute()

    return await _run_sync(_get)
