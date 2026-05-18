"""Object-storage adapter — uniform blob persistence across backends.

Architecture
------------
of the DB-as-SSOT roadmap separates blobs (camera images,
parking maps, user avatars) from telemetry data.  Telemetry lives in
the per-tenant SQLite warehouse; blobs live behind this Protocol.

Default backend is ``DiskObjectStore`` writing under
``OBJECT_STORE_ROOT`` (``data/`` by default), preserving the
``data/<bucket>/<key>`` layout that the existing FastAPI static
mount already serves.  Set ``OBJECT_STORE_BACKEND=gdrive`` to route
camera images through the Google Drive adapter (reports/billing
already go through ``adapters/storage/gdrive.py`` directly — that
path is unchanged).

Buckets used by the codebase
----------------------------
- ``camera_images`` — dashcam screenshots saved by media.service
- ``parking_maps``  — static parking-event map PNGs
- ``avatars``       — Telegram profile photos cached for 24 h

Migration path
--------------
Existing rows in ``camera_checks.image_path`` and
``parking_events.map_image_path`` already store the
``data/<bucket>/<filename>`` form, which is exactly what
``DiskObjectStore.url()`` returns — so flipping callers over is
a straight refactor with **no schema or path changes**.

S3/GCS adapters can be added later behind the same Protocol and
swapped via the ``OBJECT_STORE_BACKEND`` env var.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

from infra import config

logger = logging.getLogger(__name__)


class ObjectStore(Protocol):
    """Pluggable blob storage Protocol.

    Implementations must be safe for concurrent calls (the bot and
    APScheduler share an event loop and may invoke ``put`` from
    multiple coroutines simultaneously).
    """

    def put(self, bucket: str, key: str, data: bytes) -> str:
        """Persist ``data`` under ``bucket/key`` and return a URL or
        relative path that callers store in the DB and that the
        FastAPI static mount can serve back to clients.

        Returns ``""`` on failure.
        """
        ...

    def get(self, bucket: str, key: str) -> bytes | None:
        """Return blob bytes, or ``None`` if missing.  Used by routes
        that want to stream the blob themselves rather than redirect."""
        ...

    def exists(self, bucket: str, key: str) -> bool:
        ...

    def delete(self, bucket: str, key: str) -> bool:
        ...

    def url(self, bucket: str, key: str) -> str:
        """Return the URL/relative-path form (without writing).  Same
        shape as ``put`` returns."""
        ...

    def local_path(self, bucket: str, key: str) -> str | None:
        """Return an absolute filesystem path when one is available
        (so ``FileResponse`` can stream directly).  Backends without a
        local representation return ``None``."""
        ...

    def move_folder(self, src_bucket: str, dst_bucket: str) -> bool:
        """Re-parent / move the folder at ``src_bucket`` to ``dst_bucket``.

        Used by the driver-company-change archive flow: when a driver
        is reassigned, their ``{company}/drivers/user-{id}/`` folder
        is moved to ``{company}/drivers/_archive/{date}/user-{id}/``.

        Returns ``True`` on success, ``False`` when the source doesn't
        exist (idempotent — already-archived users don't break the
        flow).  Backends without folder-level operations are free to
        no-op + return False; callers fall back gracefully.
        """
        ...


# ── Disk-backed implementation ──────────────────────────────────────

class DiskObjectStore:
    """Writes to ``<root>/<bucket>/<key>`` and returns the
    project-relative path (e.g. ``data/camera_images/foo.jpg``)
    so the existing static mount serves the file unchanged."""

    # Anchor writes to the project root (parent of ``adapters/``) so the
    # file location is independent of the caller's current working
    # directory.  Without this, a coroutine running from any other cwd
    # would create ``./data/`` in the wrong folder, leaving the FastAPI
    # static mount (rooted at the project) returning 404.
    _PROJECT_ROOT = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    def __init__(self, root: str | None = None):
        self._root = root or config.OBJECT_STORE_ROOT or "data"

    def _full(self, bucket: str, key: str) -> str:
        # Defensive: strip any path separators the caller might have
        # included in ``key`` so we cannot escape the bucket dir.
        safe_key = key.replace("/", "_").replace("\\", "_")
        if os.path.isabs(self._root):
            bucket_dir = os.path.join(self._root, bucket)
        else:
            bucket_dir = os.path.join(self._PROJECT_ROOT, self._root, bucket)
        return os.path.join(bucket_dir, safe_key)

    def put(self, bucket: str, key: str, data: bytes) -> str:
        try:
            full = self._full(bucket, key)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(data)
            return self.url(bucket, key)
        except Exception as e:
            logger.debug("DiskObjectStore.put failed for %s/%s: %s", bucket, key, e)
            return ""

    def get(self, bucket: str, key: str) -> bytes | None:
        try:
            with open(self._full(bucket, key), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.debug("DiskObjectStore.get failed for %s/%s: %s", bucket, key, e)
            return None

    def exists(self, bucket: str, key: str) -> bool:
        return os.path.exists(self._full(bucket, key))

    def delete(self, bucket: str, key: str) -> bool:
        try:
            os.remove(self._full(bucket, key))
            return True
        except FileNotFoundError:
            return True
        except Exception as e:
            logger.debug("DiskObjectStore.delete failed for %s/%s: %s", bucket, key, e)
            return False

    def url(self, bucket: str, key: str) -> str:
        # Project-relative path that the FastAPI static mount serves.
        safe_key = key.replace("/", "_").replace("\\", "_")
        return f"{self._root}/{bucket}/{safe_key}"

    def local_path(self, bucket: str, key: str) -> str | None:
        full = self._full(bucket, key)
        return full if os.path.exists(full) else None

    def move_folder(self, src_bucket: str, dst_bucket: str) -> bool:
        """Move ``<root>/<src_bucket>`` to ``<root>/<dst_bucket>``.

        Creates any missing intermediate directories in the destination
        path.  Returns False when the source doesn't exist (caller
        treats it as idempotent — driver had no docs yet).
        """
        import shutil
        if os.path.isabs(self._root):
            base = self._root
        else:
            base = os.path.join(self._PROJECT_ROOT, self._root)
        src = os.path.join(base, src_bucket)
        dst = os.path.join(base, dst_bucket)
        if not os.path.exists(src):
            return False
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            return True
        except Exception as e:
            logger.warning(
                "DiskObjectStore.move_folder failed %s -> %s: %s",
                src_bucket, dst_bucket, e,
            )
            return False


# ── Module singletons ────────────────────────────────────────────────

_store: ObjectStore | None = None

# Per-account cache.  Keyed by ``account_id``.  Stays for the process
# lifetime — backend choice is immutable until the account reconfigures
# via the Settings page, at which point ``invalidate_object_store_for_account``
# is called to flush the entry.  Disk is cheap to recreate; the GDrive
# backend caches folder IDs internally so re-creating it loses that
# cache — invalidate sparingly.
_per_account_stores: dict[int, ObjectStore] = {}


# Convention: settings keys under ``account_settings`` for the storage
# backend choice.  Module-level constants so callers stay typo-safe.
STORAGE_BACKEND_KEY = "storage.backend"           # "disk" | "gdrive"
STORAGE_GDRIVE_REFRESH_TOKEN = "storage.gdrive.refresh_token"  # encrypted
STORAGE_GDRIVE_ROOT_FOLDER_ID = "storage.gdrive.root_folder_id"
STORAGE_GDRIVE_USER_EMAIL = "storage.gdrive.user_email"  # for display only


def get_object_store() -> ObjectStore:
    """Return the platform-wide default object store.

    Used by callers that don't have an account context — startup
    routines, system-level operations.  For per-account uploads
    (work-orders, maintenance attachments, etc.) use
    ``get_object_store_for_account`` so each tenant can plug in their
    own Google Drive without affecting other accounts.
    """
    global _store
    if _store is None:
        backend = (config.OBJECT_STORE_BACKEND or "disk").lower()
        if backend == "disk":
            _store = DiskObjectStore()
        else:
            # Unknown backend — fall back to disk so callers never crash.
            logger.warning(
                "Unknown OBJECT_STORE_BACKEND=%r — falling back to disk", backend,
            )
            _store = DiskObjectStore()
    return _store


async def get_object_store_for_account(account_id: int, tenant_db) -> ObjectStore:
    """Return the object store configured for one tenant account.

    Reads ``account_settings.storage.backend`` to decide which backend
    to spin up.  Defaults to ``DiskObjectStore`` (the safe platform
    fallback) when no preference is recorded — every account works
    out-of-the-box without configuration.

    Caches the result for the process lifetime so each account pays
    the connect/credential cost once.  Use
    ``invalidate_object_store_for_account`` after a Settings change to
    flush the cache.

    Args:
        account_id: tenant identifier (account)
        tenant_db: ``Database`` instance with ``get_account_setting`` —
            passed in so this module doesn't import the adapter at
            module load time (would create a circular dep at startup).
    """
    cached = _per_account_stores.get(account_id)
    if cached is not None:
        return cached

    backend = (
        await tenant_db.get_account_setting(account_id, STORAGE_BACKEND_KEY, "disk")
    ) or "disk"
    backend = backend.lower()

    if backend == "disk":
        # DiskObjectStore is stateless; safe to share a single instance
        # across accounts because the account_id-prefixed path keeps
        # tenants isolated on disk.
        store: ObjectStore = DiskObjectStore()
    elif backend == "gdrive":
        # Late import so DiskObjectStore-only setups don't pay the
        # google-api-python-client import cost on every startup.
        try:
            from .object_store_gdrive import GDriveObjectStore
            store = await GDriveObjectStore.connect(account_id, tenant_db)
        except Exception as e:
            logger.error(
                "GDrive backend unavailable for account %d (falling back to disk): %s",
                account_id, e,
            )
            store = DiskObjectStore()
    else:
        logger.warning(
            "Unknown storage backend %r for account %d — using disk",
            backend, account_id,
        )
        store = DiskObjectStore()

    _per_account_stores[account_id] = store
    return store


def invalidate_object_store_for_account(account_id: int) -> None:
    """Drop the cached ObjectStore for an account.

    Called after the user changes their storage backend in Settings or
    refreshes their Google Drive credentials.  The next
    ``get_object_store_for_account`` call rebuilds from current
    ``account_settings``.
    """
    _per_account_stores.pop(account_id, None)


def reset_object_store() -> None:
    """Test hook: clear all caches so the next call rebuilds from
    current ``config`` + ``account_settings`` values."""
    global _store
    _store = None
    _per_account_stores.clear()
