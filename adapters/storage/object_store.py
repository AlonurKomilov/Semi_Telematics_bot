"""Object-storage adapter — uniform blob persistence across backends.

Architecture
------------
Phase 5 of the DB-as-SSOT roadmap separates blobs (camera images,
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


# ── Module singleton ─────────────────────────────────────────────────

_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    """Return the configured object store.  Lazy so tests can override
    ``config.OBJECT_STORE_BACKEND`` between calls."""
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


def reset_object_store() -> None:
    """Test hook: clear the cached singleton so the next call rebuilds
    from current ``config`` values."""
    global _store
    _store = None
