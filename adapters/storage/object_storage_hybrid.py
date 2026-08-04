"""``HybridObjectStorage`` — disk-primary, cloud-async storage tier.

Phase 2 of the tiered-storage rollout.  The hybrid is the backend an
account selects when they want the **resilience of local disk** for the
write path and the **cost / per-tenant ownership** of cloud storage for
long-term retention.  Concrete behaviour:

  * **Writes** go straight to local disk (always works, fast, never
    blocks the driver on a slow Drive call).  ``track_for_sync`` then
    drops a row into ``object_storage_sync_queue`` — the durable outbox the
    Phase 3 worker drains in the background.
  * **Reads** check disk first (where freshly-written + not-yet-synced
    files live).  After the worker successfully uploads to Drive and
    deletes the local copy, reads transparently fall through to the
    Drive backend.  The caller never sees the tier transition.

The whole thing is just a composition of the two existing stores plus
the outbox machinery from ``ObjectStorageSyncMixin``.  No new transport
code; no new protocol — `HybridObjectStorage` satisfies the same
``ObjectStorage`` Protocol that ``DiskObjectStorage`` and
``GDriveObjectStorage`` do, so every existing caller (work orders, PTI
media, reference photos, annotations, signatures) keeps working
unchanged.

The duck-typed ``track_for_sync`` method is the only extension callers
need to opt into.  Routes call it post-attach to register the new
file's entity id; Disk/GDrive stores simply don't have the method, so
the route's ``hasattr`` check skips them silently.
"""

from __future__ import annotations

import logging
from typing import Optional

from .object_storage import DiskObjectStorage
from .object_storage_sync import STATE_LOCAL

logger = logging.getLogger(__name__)


class HybridObjectStorage:
    """Per-tenant disk-primary + cloud-async object store.

    Built once per account by ``get_object_storage_for_account`` and
    cached for the process lifetime.  Holds:

      * a ``DiskObjectStorage`` for the local cache layer (stateless,
        shared safely across tenants by the account-scoped folder
        paths the callers already pass in).
      * a lazily-built ``GDriveObjectStorage`` for the cloud layer — None
        until the first read miss / write that needs it.  An account
        with the hybrid backend selected but Drive not yet connected
        still works perfectly for writes; the queue just accumulates
        rows that drain when Drive comes online.
      * a reference to the per-tenant ``tenant_db`` so
        ``track_for_sync`` can enqueue inline with the upload, and
        the Phase 3 worker can call ``sync_one`` against a captured
        queue row.
    """

    def __init__(self, account_id: int, tenant_db):
        self._account_id = account_id
        self._tenant_db = tenant_db
        # Disk leg is account-aware so every local write lands inside
        # the per-tenant subtree (``data/userdata/account-{id}/...``).
        # The caller-provided bucket stays "Drive-shaped" — no account
        # segment — so the Drive leg writes to the matching clean path
        # inside the customer's own Drive root.
        self._disk = DiskObjectStorage(account_id=account_id)
        # GDrive is lazy — never built unless we actually need it.
        # Reads that hit disk return early; writes only enqueue.  This
        # keeps "hybrid backend selected but Drive not yet connected"
        # a useful state instead of a hard error.
        self._drive = None  # type: ignore[var-annotated]
        # Surface the most-recent failure reason for the route layer's
        # ``_storage_put_error`` helper, mirroring GDriveObjectStorage.
        self.last_error: Optional[str] = None

    # ── Lazy drive ──────────────────────────────────────────────────

    async def _drive_or_none(self):
        """Build the Drive backend on first need.  Caches both success
        and failure so we don't slam Google for every read on an
        account that hasn't connected Drive."""
        if self._drive is not None:
            return self._drive if self._drive is not False else None
        try:
            from .object_storage_gdrive import GDriveObjectStorage
            self._drive = await GDriveObjectStorage.connect(
                self._account_id, self._tenant_db,
            )
            return self._drive
        except Exception as e:
            # No Drive yet (or token dead) — that's fine for the
            # hybrid; the queue holds files locally until Drive lives.
            self.last_error = str(e)
            self._drive = False   # sentinel: tried, not available
            logger.info(
                "HybridObjectStorage: Drive unavailable for account %d (%s) — "
                "files will queue locally", self._account_id, e,
            )
            return None

    # ── ObjectStorage protocol — writes ───────────────────────────────

    def put(self, bucket: str, key: str, data: bytes) -> str:
        """Disk-only write.  Returns the disk URL immediately.

        The route layer follows up with ``track_for_sync`` so the
        Phase 3 worker can push the bytes to Drive in the background.
        We deliberately don't write to Drive here — that's the slow,
        fragile call we're decoupling from the user-facing upload.
        """
        url = self._disk.put(bucket, key, data)
        if not url:
            self.last_error = "local disk write failed"
        return url

    # ── ObjectStorage protocol — reads ────────────────────────────────

    def get(self, bucket: str, key: str) -> Optional[bytes]:
        """Disk-first, drive-fallback.

        Synchronous because that's what the Protocol promises and what
        every caller (StreamingResponse, PDF builder, AI vision) needs.
        The Drive fallback runs the existing synchronous Drive client;
        callers that want it off the loop (PDF builder) already wrap
        in ``asyncio.to_thread``.
        """
        data = self._disk.get(bucket, key)
        if data is not None:
            return data
        # Disk miss → the file may have been synced + locally cleaned.
        # Hit Drive if we have it.  We avoid the async build path here
        # by checking the cached state only; the route + worker paths
        # that *need* Drive call ``_drive_or_none`` from async code.
        drive = self._drive if (self._drive and self._drive is not False) else None
        if drive is not None:
            return drive.get(bucket, key)
        return None

    def get_by_id(self, object_id: str) -> Optional[bytes]:
        """Stable read by backend-native ID.

        On the hybrid tier the media row's ``file_path`` becomes a Drive
        ID once the sync worker uploads the file (see
        ``capabilities/object_storage/sync_worker._sync_one_row``).  Routes
        that pass that ``file_path`` here get the bytes regardless of
        whether the user reorganised folders in Drive's UI — Drive ID
        is the stable handle.

        Pre-sync rows (still on disk) fall through to a path-style
        lookup so the same call works for both states.
        """
        if not object_id:
            return None
        # Pre-sync: file_path looks like ``data/inspections/1/78/x.jpg``
        # or is an absolute path under the project.  Disk handles both.
        if "/" in object_id or "\\" in object_id:
            return self._disk.get_by_id(object_id)
        # Post-sync: opaque Drive file ID.
        drive = self._drive if (self._drive and self._drive is not False) else None
        if drive is not None:
            return drive.get_by_id(object_id)
        return None

    def exists(self, bucket: str, key: str) -> bool:
        if self._disk.exists(bucket, key):
            return True
        drive = self._drive if (self._drive and self._drive is not False) else None
        return drive.exists(bucket, key) if drive else False

    def delete(self, bucket: str, key: str) -> bool:
        """Best-effort delete from both tiers.  Returns True if either
        succeeded — Drive may legitimately not hold the file yet (still
        local + queued), and disk may already be cleaned post-sync."""
        disk_ok = self._disk.delete(bucket, key)
        drive_ok = False
        drive = self._drive if (self._drive and self._drive is not False) else None
        if drive is not None:
            drive_ok = drive.delete(bucket, key)
        return disk_ok or drive_ok

    def purge_account(self) -> int:
        """Purge ONLY the account's server-local subtree.

        Files already synced to the customer's Google Drive are left
        intact — their cloud, their data, theirs to keep.  We never reach
        into an external client-owned cloud to delete, even on account
        hard-purge; we only reclaim storage on OUR own server.  Drive is
        deliberately NOT connected here.  Returns the local files removed.
        """
        return self._disk.purge_account()

    def url(self, bucket: str, key: str) -> str:
        """Always returns the disk URL — the file's location immediately
        after a put.  Once the worker syncs the row, the *media row's*
        ``file_path`` column is updated to the Drive ID; that's what
        the dashboard reads next time it lists the inspection."""
        return self._disk.url(bucket, key)

    def local_path(self, bucket: str, key: str) -> Optional[str]:
        return self._disk.local_path(bucket, key)

    def move_folder(self, src_bucket: str, dst_bucket: str) -> bool:
        # Only disk supports folder moves; Drive ID-based storage
        # doesn't reorganise that way.  If a file has already synced
        # to Drive its remote_path stays valid regardless of the
        # local bucket renaming.
        return self._disk.move_folder(src_bucket, dst_bucket)

    # ── Hybrid extension: register a fresh upload for async sync ──

    async def track_for_sync(
        self,
        bucket: str,
        key: str,
        file_path: str,
        *,
        entity_type: str,
        entity_id: int,
        file_size: int = 0,
        sha256: Optional[str] = None,
    ) -> None:
        """Enqueue a freshly-written file for async upload to Drive.

        Called by the route layer right after the media row is
        inserted, so the queue row carries the entity id the worker
        needs to update on success.  Idempotent — re-calling for the
        same ``(entity_type, entity_id)`` resets the schedule, which
        is exactly what we want for the annotation flow (driver re-
        saves over the same media → the existing queue row resets +
        re-uploads the new bytes).

        Also flips ``pti_inspection_media.storage_state`` to
        ``'local'`` so the worker knows this row is fresh-local and
        eligible to advance.  Other entity types (work orders, etc.)
        get queued without a state flip — those tables don't have the
        column yet, and Phase 2 doesn't need them to.
        """
        try:
            await self._tenant_db.enqueue_storage_sync(
                account_id=self._account_id,
                entity_type=entity_type, entity_id=entity_id,
                bucket=bucket, filename=key,
                local_path=file_path, file_size=int(file_size or 0),
            )
            if entity_type == "pti_media":
                await self._tenant_db.set_media_storage_state(
                    entity_id,
                    state=STATE_LOCAL,
                    local_path=file_path,
                    file_path=file_path,
                    sha256=sha256,
                )
        except Exception:
            # An enqueue failure must NOT lose the file — it's safely
            # on disk and we'll log loudly.  The next manual upload
            # (or a future enqueue-sweep job) will pick it up.
            logger.exception(
                "HybridObjectStorage: failed to track %s/%s for account %d "
                "(file is on disk; sync will be retried)",
                bucket, key, self._account_id,
            )
