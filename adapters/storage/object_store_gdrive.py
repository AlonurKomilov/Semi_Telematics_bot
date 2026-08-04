"""``GDriveObjectStore`` — per-account Google Drive backend.

Implements the ``ObjectStore`` Protocol so any caller that uses
``get_object_store_for_account`` works unchanged when the account has
opted into BYO Drive.

How it differs from the platform ``adapters/storage/gdrive.py`` module:

* That module uses a single service-account credential and a shared
  root folder for ALL accounts.  Useful for cron-ish platform tasks
  but not "BYO" — the platform owns the data.
* This module uses **per-account OAuth refresh tokens** so each
  customer's data lives in their own Drive under a single root folder
  they control.  The platform never sees the bytes after upload; only
  the file IDs.

State stored per account (under ``account_settings``):

* ``object_store.gdrive.refresh_token`` — encrypted OAuth refresh token
* ``object_store.gdrive.root_folder_id`` — Drive folder ID of the
  ``4truck`` root the user authorised us to write to
* ``object_store.gdrive.user_email`` — for display only (Settings page)

Folder layout mirrors ``DiskObjectStore`` exactly so a user inspecting
their Drive sees the same hierarchy as the local-disk fallback.  The
sub-folder names are intentionally role-neutral (work-orders, camera-
images, driver-documents, parking-maps) so the same root serves every
operator role — fleet, driver, safety, dispatch, maintenance::

    My Drive/
      └─ 4truck/
         └─ account-7/
            └─ work-orders/
               └─ 2026/
                  └─ 04-april/
                     └─ WO-00128_truck221_2026-04-12_BobsDieselShop/
                        ├─ invoice.pdf
                        └─ shop-photo-1.jpg

Folder IDs are CACHED in-memory per backend instance — an account-wide
upload pass otherwise re-runs the ``find_or_create`` chain dozens of
times.  The cache survives until ``invalidate_object_store_for_account``
is called or the process restarts.
"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING, Optional

from infra.crypto import decrypt

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

logger = logging.getLogger(__name__)

# OAuth scope — Drive file-level access only (cannot see files the
# user didn't grant explicitly).  Narrowest viable scope for the BYO
# use case; ``drive.file`` is preferred over ``drive`` because Google's
# verification process treats the broader scope as sensitive.
GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class GDriveObjectStore:
    """Google Drive-backed ``ObjectStore`` for a single tenant account.

    Construct via the async ``connect()`` classmethod — it pulls the
    account's stored OAuth refresh token, exchanges it for a short-
    lived access token, and builds a Drive API service handle.
    """

    def __init__(
        self,
        service: "Resource",
        root_folder_id: str,
        account_id: int,
    ):
        self._service = service
        self._root_folder_id = root_folder_id
        self._account_id = account_id
        # Last failure reason from a put/get/delete — lets the API layer
        # surface an actionable message (e.g. "reconnect Google Drive")
        # instead of a generic 500 when the OAuth token is expired.
        self.last_error: str | None = None
        # bucket-path → Drive folder ID.  Keyed by the structured path
        # the work-orders module passes in (``account-7/work-orders/
        # 2026/04-april/WO-00128_...``).  Populated lazily as folders
        # are walked; survives until the backend instance is dropped
        # from the per-account cache.
        self._folder_cache: dict[str, str] = {}

    # ── Construction ─────────────────────────────────────────────────────────

    @classmethod
    async def connect(cls, account_id: int, tenant_db) -> "GDriveObjectStore":
        """Build a Drive client for an account from stored OAuth creds.

        Raises ``RuntimeError`` if the account hasn't completed the
        OAuth setup yet — the factory in ``object_store.py`` catches
        this and falls back to ``DiskObjectStore`` with a warning.
        """
        from .object_store import (
            OBJECT_STORE_GDRIVE_REFRESH_TOKEN, OBJECT_STORE_GDRIVE_ROOT_FOLDER_ID,
        )

        encrypted_token = await tenant_db.get_account_setting(
            account_id, OBJECT_STORE_GDRIVE_REFRESH_TOKEN, "",
        )
        root_folder_id = await tenant_db.get_account_setting(
            account_id, OBJECT_STORE_GDRIVE_ROOT_FOLDER_ID, "",
        )
        if not encrypted_token or not root_folder_id:
            raise RuntimeError(
                f"Account {account_id} has not connected Google Drive."
            )

        refresh_token = decrypt(encrypted_token)
        service = _build_drive_service(refresh_token)
        return cls(service, root_folder_id, account_id)

    # ── ObjectStore protocol ─────────────────────────────────────────────────

    def put(self, bucket: str, key: str, data: bytes) -> str:
        """Upload bytes to ``<bucket>/<key>`` on the user's Drive.

        Returns the Drive file ID, which callers persist as
        ``file_path`` in the attachments DB.  ``get`` and ``delete``
        look up by ID, so a manual rename in Drive doesn't break the
        link.
        """
        # Defensive sanitation mirrors DiskObjectStore: strip any
        # path separators a caller might have left in ``key`` so the
        # filename can't smuggle subdirectory structure into Drive.
        # Every current caller pre-sanitises, but mirroring the disk
        # behaviour keeps the two backends produce identical names
        # for the same input.
        key = key.replace("/", "_").replace("\\", "_")
        try:
            folder_id = self._resolve_folder_chain(bucket)
            # ``create_missing=True`` (default) means the chain is
            # always built — None can't legitimately come back here.
            # Narrow for mypy + give a clearer error if Drive returns
            # an unexpected empty response.
            if not folder_id:
                logger.error("GDrive put: folder chain resolved to None for %s", bucket)
                return ""
            existing_id = self._find_file_in_folder(folder_id, key)

            from googleapiclient.http import MediaIoBaseUpload
            media = MediaIoBaseUpload(
                io.BytesIO(data),
                mimetype="application/octet-stream",
                resumable=False,
            )

            if existing_id:
                # Same-name uploads update in place rather than creating
                # a "(1)" suffix file — matches the disk-backed
                # semantics where writing the same path overwrites.
                self._service.files().update(
                    fileId=existing_id, media_body=media, fields="id",
                ).execute()
                return existing_id

            meta = {"name": key, "parents": [folder_id]}
            file = self._service.files().create(
                body=meta, media_body=media, fields="id",
            ).execute()
            self.last_error = None
            return file["id"]
        except Exception as e:
            self.last_error = str(e)
            logger.error("GDrive put failed for %s/%s: %s", bucket, key, e)
            return ""

    def get(self, bucket: str, key: str) -> Optional[bytes]:
        """Download bytes for an existing ``<bucket>/<key>`` file.

        Drive doesn't preserve a stable path → ID mapping if a folder
        gets renamed in the user's Drive UI.  This method walks the
        folder chain by name; if the chain is broken (user renamed a
        year folder, say), the file is treated as missing.  The route
        handler should prefer ``get_by_id`` when the attachment row
        has a stored Drive file ID.
        """
        try:
            folder_id = self._resolve_folder_chain(bucket, create_missing=False)
            if not folder_id:
                return None
            file_id = self._find_file_in_folder(folder_id, key)
            if not file_id:
                return None
            return self._download_by_id(file_id)
        except Exception as e:
            logger.debug("GDrive get failed for %s/%s: %s", bucket, key, e)
            return None

    def get_by_id(self, file_id: str) -> Optional[bytes]:
        """Direct download by Drive file ID — the preferred read path.

        Stable even if the user reorganises their Drive folders.  The
        work-orders adapter stores ``file_path = <drive_id>`` so the
        attachment route can call this directly.
        """
        try:
            return self._download_by_id(file_id)
        except Exception as e:
            logger.debug("GDrive get_by_id failed for %s: %s", file_id, e)
            return None

    def exists(self, bucket: str, key: str) -> bool:
        try:
            folder_id = self._resolve_folder_chain(bucket, create_missing=False)
            if not folder_id:
                return False
            return bool(self._find_file_in_folder(folder_id, key))
        except Exception:
            return False

    def delete(self, bucket: str, key: str) -> bool:
        try:
            folder_id = self._resolve_folder_chain(bucket, create_missing=False)
            if not folder_id:
                return False
            file_id = self._find_file_in_folder(folder_id, key)
            if not file_id:
                return False
            self._service.files().delete(fileId=file_id).execute()
            return True
        except Exception as e:
            logger.debug("GDrive delete failed for %s/%s: %s", bucket, key, e)
            return False

    def purge_account(self) -> int:
        """No-op BY POLICY.

        Files on this backend live in the CUSTOMER'S OWN Google Drive —
        their cloud, their data.  We never delete from an external,
        client-owned cloud, even on account hard-purge: that data is
        theirs to keep (and isn't ours to erase).  Account purge only
        reclaims storage on OUR server (see ``DiskObjectStore.purge_account``
        and ``HybridObjectStore.purge_account``, which touch local disk
        only).  Returns 0 — nothing on our side to remove.
        """
        return 0

    def url(self, bucket: str, key: str) -> str:
        """Return the ``webViewLink`` for the file (opens in Drive UI).

        Used by callers that want to offer the user a "view in Drive"
        link.  Falls back to an empty string when the file is missing
        — DB writes typically use the file ID instead (returned by
        ``put``), so this method is rarely the primary handle.
        """
        try:
            folder_id = self._resolve_folder_chain(bucket, create_missing=False)
            if not folder_id:
                return ""
            file_id = self._find_file_in_folder(folder_id, key)
            if not file_id:
                return ""
            meta = self._service.files().get(
                fileId=file_id, fields="webViewLink",
            ).execute()
            return meta.get("webViewLink", "")
        except Exception:
            return ""

    def local_path(self, bucket: str, key: str) -> Optional[str]:
        """No local representation — bytes live in Drive.  Callers that
        need a path on disk must stream via ``get`` to a tempfile."""
        return None

    def move_folder(self, src_bucket: str, dst_bucket: str) -> bool:
        """Re-parent a Drive folder from ``src_bucket`` to ``dst_bucket``.

        Drive doesn't have a true "move" operation — folders are
        re-parented by adding the new parent and removing the old one
        in a single ``files.update`` call.  Files inside the folder
        come along for free (they're children of the folder, not of
        its parent).

        If the basename of ``dst_bucket`` differs from ``src_bucket``
        we also rename the folder so the leaf segment matches the
        caller-requested path.  Used by the driver-archive flow to
        move ``{company}/drivers/user-7`` under
        ``{company}/drivers/_archive/{date}/user-7`` while keeping
        the ``user-7`` leaf name.

        Returns False when the source doesn't exist (idempotent: a
        driver with no docs has no folder to move).
        """
        try:
            src_id = self._resolve_folder_chain(src_bucket, create_missing=False)
            if not src_id:
                return False

            # Compute current parent — Drive needs both addParents and
            # removeParents to atomically re-parent.
            src_parent_path, _, src_leaf = src_bucket.rpartition("/")
            if src_parent_path:
                src_parent_id = self._resolve_folder_chain(
                    src_parent_path, create_missing=False,
                )
            else:
                src_parent_id = self._root_folder_id
            if not src_parent_id:
                return False

            dst_parent_path, _, dst_leaf = dst_bucket.rpartition("/")
            dst_parent_id = self._resolve_folder_chain(
                dst_parent_path, create_missing=True,
            )
            if not dst_parent_id:
                return False

            body: dict = {}
            if dst_leaf and dst_leaf != src_leaf:
                body["name"] = dst_leaf

            self._service.files().update(
                fileId=src_id,
                addParents=dst_parent_id,
                removeParents=src_parent_id,
                body=body or None,
                fields="id, parents",
            ).execute()

            # Cache invalidation — the old path is now stale, and the
            # new path resolves to the same folder ID.  Drop any cache
            # entries that touch the moved subtree so future lookups
            # re-walk Drive rather than returning ghosts.
            stale = [k for k in self._folder_cache
                     if k == src_bucket or k.startswith(src_bucket + "/")]
            for k in stale:
                self._folder_cache.pop(k, None)
            return True
        except Exception as e:
            logger.warning(
                "GDrive move_folder failed %s -> %s: %s",
                src_bucket, dst_bucket, e,
            )
            return False

    # ── Drive folder traversal + cache ───────────────────────────────────────

    def _resolve_folder_chain(
        self, bucket: str, *, create_missing: bool = True,
    ) -> Optional[str]:
        """Walk a slash-separated bucket path to a Drive folder ID.

        ``bucket`` looks like
        ``account-7/work-orders/2026/04-april/WO-00128_…``.  We start
        from ``self._root_folder_id`` and find-or-create each segment.
        Folder IDs are cached so repeated uploads to the same
        directory pay one Drive round-trip total.
        """
        cached = self._folder_cache.get(bucket)
        if cached is not None:
            return cached

        parent_id = self._root_folder_id
        running_path = ""
        for segment in bucket.split("/"):
            if not segment:
                continue
            running_path = f"{running_path}/{segment}" if running_path else segment
            cached_seg = self._folder_cache.get(running_path)
            if cached_seg is not None:
                parent_id = cached_seg
                continue

            existing = self._find_folder_in_parent(parent_id, segment)
            if existing:
                parent_id = existing
            elif create_missing:
                parent_id = self._create_folder(parent_id, segment)
            else:
                return None
            self._folder_cache[running_path] = parent_id

        self._folder_cache[bucket] = parent_id
        return parent_id

    def _find_folder_in_parent(
        self, parent_id: str, name: str,
    ) -> Optional[str]:
        safe = name.replace("'", "\\'")
        q = (
            f"name = '{safe}' and '{parent_id}' in parents "
            f"and mimeType = 'application/vnd.google-apps.folder' "
            f"and trashed = false"
        )
        res = self._service.files().list(
            q=q, spaces="drive", fields="files(id)", pageSize=1,
        ).execute()
        items = res.get("files", [])
        return items[0]["id"] if items else None

    def _create_folder(self, parent_id: str, name: str) -> str:
        meta = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        folder = self._service.files().create(body=meta, fields="id").execute()
        return folder["id"]

    def _find_file_in_folder(
        self, folder_id: str, name: str,
    ) -> Optional[str]:
        safe = name.replace("'", "\\'")
        q = (
            f"name = '{safe}' and '{folder_id}' in parents "
            f"and trashed = false"
        )
        res = self._service.files().list(
            q=q, spaces="drive", fields="files(id)", pageSize=1,
        ).execute()
        items = res.get("files", [])
        return items[0]["id"] if items else None

    def _download_by_id(self, file_id: str) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload
        request = self._service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()


# ── Service builder ─────────────────────────────────────────────────────────


def _build_drive_service(refresh_token: str):
    """Construct a Drive API service from a refresh token.

    The refresh token + client credentials (read from env) get
    exchanged for a short-lived access token at every service
    construction.  We rebuild the service per backend instance, which
    is fine because the per-account ObjectStore cache keeps the
    instance alive for the process lifetime — one token exchange per
    account per process.
    """
    import os
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    client_id = os.getenv("GDRIVE_OAUTH_CLIENT_ID", "")
    client_secret = os.getenv("GDRIVE_OAUTH_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "GDRIVE_OAUTH_CLIENT_ID and GDRIVE_OAUTH_CLIENT_SECRET must be "
            "set to use the GDrive object store backend."
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=GDRIVE_SCOPES,
    )
    # build() does NOT trigger an initial token refresh — that happens
    # lazily on the first API call.  Pass ``cache_discovery=False`` to
    # silence the noisy oauth2client cache warning from googleapiclient.
    return build("drive", "v3", credentials=creds, cache_discovery=False)
