"""Object-storage adapter — uniform blob persistence across backends.

Architecture
------------
of the DB-as-SSOT roadmap separates blobs (camera images,
parking maps, user avatars) from telemetry data.  Telemetry lives in
the per-tenant Postgres warehouse tables; blobs live behind this Protocol.

Default backend is ``DiskObjectStorage`` writing under
``OBJECT_STORE_ROOT`` (``data/userdata/`` by default).  The split
between ``data/userdata/`` (tenant blobs) and ``data/system/``
(legacy SQLite / backups / anything the platform itself owns) keeps
the on-disk layout clean and makes "mount blob storage on a
different volume" a one-line config change in the future.  Set
``OBJECT_STORE_BACKEND=gdrive`` to route camera images through the
Google Drive adapter (reports/billing already go through
``adapters/storage/gdrive.py`` directly — that path is unchanged).

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
``DiskObjectStorage.url()`` returns — so flipping callers over is
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


def _allowed_disk_roots(project_root: str) -> list[str]:
    """Directories a stored reference is permitted to resolve inside.

    The DATA root — not the project root.  Containing to the project is
    not enough: the account-prefix fallback below builds candidates like
    ``data/userdata/account-N/<stored>``, so a stored value of
    ``../../../.env`` climbs exactly three levels back to the project
    root and lands on the secrets file while still being "inside the
    project".  Nothing legitimate lives outside ``data/``, so that is
    the boundary.

    ``OBJECT_STORE_ROOT`` is added when absolute — deployments may park
    the store on a mounted volume outside the checkout — and its
    project-relative form is already covered by ``<project>/data``.
    """
    roots = [os.path.realpath(os.path.join(project_root, "data"))]
    configured = getattr(config, "OBJECT_STORE_ROOT", "") or ""
    if configured and os.path.isabs(configured):
        roots.append(os.path.realpath(configured))
    return roots


def _within_allowed_root(path: str, project_root: str) -> bool:
    """True when ``path`` resolves inside an allowed root.

    Compared on ``realpath`` with a trailing separator so ``/data-evil``
    cannot pass as a child of ``/data``.
    """
    real = os.path.realpath(path)
    for root in _allowed_disk_roots(project_root):
        if real == root or real.startswith(root + os.sep):
            return True
    return False


def _disk_path_candidates(object_id: str, project_root: str) -> list[str]:
    """Resolve a DB-stored file path to the candidate locations on disk.

    Returns a list of absolute paths to try, in order.  Handles three
    layout transitions so reads survive a partially-applied migration:

    * Pre-userdata → userdata: ``data/X`` → ``data/userdata/X``
    * userdata → pre-userdata: ``data/userdata/X`` → ``data/X``
    * Pre-account-prefix → account-prefixed (when the DB still says
      ``data/userdata/X`` but the file has moved into
      ``data/userdata/account-N/X``): we can't know N from path alone,
      so we glob the ``data/userdata/account-*/`` directories at runtime
      ONLY when nothing else matches.  This keeps the common case
      (post-migration direct hit) at O(1) and reserves the glob for the
      edge case of an unmigrated row pointing at a moved file.

    The post-migration steady state hits the literal-path candidate
    every time — fallbacks are pure safety nets.
    """
    if os.path.isabs(object_id):
        # Still containment-checked: an absolute DB value is exactly the
        # case where returning it verbatim would serve /etc/passwd.
        return [object_id] if _within_allowed_root(object_id, project_root) else []

    candidates: list[str] = []
    # Primary: the path as stored.
    primary = os.path.join(project_root, object_id)
    candidates.append(primary)

    # Legacy → new: ``data/X`` → ``data/userdata/X``
    if object_id.startswith("data/") and not object_id.startswith("data/userdata/"):
        rewritten = "data/userdata/" + object_id[len("data/"):]
        candidates.append(os.path.join(project_root, rewritten))

    # New → legacy: ``data/userdata/X`` → ``data/X``
    elif object_id.startswith("data/userdata/"):
        rewritten = "data/" + object_id[len("data/userdata/"):]
        candidates.append(os.path.join(project_root, rewritten))

    # Account-prefix transition: a DB row whose file_path is
    # ``data/userdata/X`` (still has not been rewritten by migration 077)
    # may point at a file that has already moved to
    # ``data/userdata/account-N/X`` on disk.  Try each
    # ``account-N/`` sibling that currently exists.  Cached at import
    # would be premature optimisation; reality is one or two accounts
    # per box during the transition window.
    if object_id.startswith("data/userdata/") and "/userdata/account-" not in object_id:
        userdata_root = os.path.join(project_root, "data", "userdata")
        try:
            for entry in os.listdir(userdata_root):
                if not entry.startswith("account-"):
                    continue
                rewritten = "data/userdata/" + entry + "/" + object_id[len("data/userdata/"):]
                candidates.append(os.path.join(project_root, rewritten))
        except OSError:
            # FileNotFoundError, PermissionError, NotADirectoryError,
            # EIO on a flaky mount — all are best-effort fallback
            # failures.  Swallow so a transient FS issue doesn't
            # convert a missing-fallback into a 500.
            pass

    # CONTAINMENT, applied once here so every read path inherits it.
    # ``os.path.join`` does not neutralise ``..`` and an absolute
    # object_id is returned verbatim above, so a DB value of
    # ``../../etc/passwd`` (or an absolute path) would otherwise be read
    # and served.  Cameras and parking each carried their own realpath
    # check at the call site; ``get_by_id`` — used by inspections,
    # drivers, knowledge and applications — had none at all.  Filtering
    # the candidate list covers both entry points and every current and
    # future caller, instead of asking each route to remember.
    return [c for c in candidates if _within_allowed_root(c, project_root)]


def resolve_disk_path(object_id: str, project_root: str | None = None) -> str | None:
    """Return the on-disk path for a DB-stored file path, or ``None`` if
    the file doesn't exist at any candidate location.

    Used by FastAPI routes that ``FileResponse(...)`` a stored path
    directly (parking maps, dashcam screenshots, avatars).  Encapsulates
    the data/ ↔ data/userdata/ transition so the routes don't repeat the
    fallback logic.
    """
    if not object_id:
        return None
    root = project_root or DiskObjectStorage._PROJECT_ROOT
    for candidate in _disk_path_candidates(object_id, root):
        if os.path.isfile(candidate):
            return candidate
    return None


class ObjectStorage(Protocol):
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

    def get_by_id(self, object_id: str) -> bytes | None:
        """Return blob bytes for a backend-native object identifier.

        For backends that return an opaque ID from ``put`` (GDrive
        returns a Drive file ID), reads through ``get_by_id`` survive
        the user reorganising their folders in the backend UI — the
        ID is the stable handle, the folder path is not.

        Backends without IDs (DiskObjectStorage) treat ``object_id`` as
        a relative path and try to resolve it under ``<root>``; missing
        files return ``None``.
        """
        ...

    def exists(self, bucket: str, key: str) -> bool:
        ...

    def delete(self, bucket: str, key: str) -> bool:
        ...

    def purge_account(self) -> int:
        """Delete this store's account files that live on OUR server — the
        terminal step of an account hard-purge.  Server-local ONLY: a
        backend whose files live in the customer's own external cloud
        (Google Drive) is a no-op here, because that cloud data is theirs
        to keep and isn't ours to erase.  Returns the count removed."""
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

class DiskObjectStorage:
    """Writes to ``<root>/<account-prefix>/<bucket>/<key>`` and returns
    the project-relative path.

    The optional ``account_id`` constructor argument makes the store
    *tenant-aware*: every operation transparently prefixes the bucket
    with ``account-{id}/``.  Callers pass a "Drive-shaped" bucket like
    ``ACME/inspections/80`` and the disk store maps it to
    ``data/userdata/account-10000001/ACME/inspections/80/``.  The
    Google Drive backend writes the same caller-provided bucket
    directly under the tenant's own ``4truck/`` root — disk and Drive
    paths stay in lockstep inside the tenant boundary.

    Account-less construction (``DiskObjectStorage()``) is reserved for
    platform-level caches like ``data/system/cache/avatars/`` — the
    platform ``get_object_storage()`` factory uses this form.
    """

    # Anchor writes to the project root (parent of ``adapters/``) so the
    # file location is independent of the caller's current working
    # directory.  Without this, a coroutine running from any other cwd
    # would create ``./data/`` in the wrong folder, leaving the FastAPI
    # static mount (rooted at the project) returning 404.
    _PROJECT_ROOT = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    def __init__(
        self,
        root: str | None = None,
        *,
        account_id: int | None = None,
    ):
        self._root = root or config.OBJECT_STORE_ROOT or "data"
        # When set, ``account-{id}/`` is prepended to every bucket
        # transparently.  None means "no account prefix" — used for
        # platform-shared caches that aren't tenant data (avatars).
        self._account_id = account_id

    def _account_prefix(self) -> str:
        """The bucket-prefix segment for the tenant boundary, or empty
        string for the platform-level store."""
        return f"account-{self._account_id}" if self._account_id is not None else ""

    def _full(self, bucket: str, key: str) -> str:
        # Defensive: strip any path separators the caller might have
        # included in ``key`` so we cannot escape the bucket dir.
        safe_key = key.replace("/", "_").replace("\\", "_")
        # Inject the per-account prefix between root and the
        # caller-provided bucket.  Drive-side stays clean (no prefix);
        # disk-side gets the tenant isolation.
        prefix = self._account_prefix()
        bucket_with_prefix = f"{prefix}/{bucket}" if prefix else bucket
        if os.path.isabs(self._root):
            bucket_dir = os.path.join(self._root, bucket_with_prefix)
        else:
            bucket_dir = os.path.join(self._PROJECT_ROOT, self._root, bucket_with_prefix)
        return os.path.join(bucket_dir, safe_key)

    def put(self, bucket: str, key: str, data: bytes) -> str:
        try:
            full = self._full(bucket, key)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(data)
            return self.url(bucket, key)
        except Exception as e:
            # warning (not debug) — a failed write is operationally
            # significant (disk full, perms) and must be visible in
            # prod logs, not swallowed.
            logger.warning("DiskObjectStorage.put failed for %s/%s: %s", bucket, key, e)
            return ""

    def get(self, bucket: str, key: str) -> bytes | None:
        try:
            with open(self._full(bucket, key), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.debug("DiskObjectStorage.get failed for %s/%s: %s", bucket, key, e)
            return None

    def get_by_id(self, object_id: str) -> bytes | None:
        """Disk has no stable ID separate from the path.  ``object_id``
        is treated as the project-relative URL form returned by
        ``url()`` / ``put()`` — e.g. ``data/userdata/ACME/inspections/78/photo.jpg``.
        Resolved under the project root so it survives a cwd change.

        Transition safety: also tries the pre-``data/userdata/`` path
        form so legacy DB rows still resolve even if the ``073_userdata``
        migration hasn't run yet.  Both lookups are constant-time so the
        fallback adds no measurable read latency.
        """
        if not object_id:
            return None
        for candidate in _disk_path_candidates(object_id, self._PROJECT_ROOT):
            try:
                with open(candidate, "rb") as f:
                    return f.read()
            except FileNotFoundError:
                continue
            except Exception as e:
                logger.debug("DiskObjectStorage.get_by_id failed for %s: %s", object_id, e)
                return None
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
            logger.debug("DiskObjectStorage.delete failed for %s/%s: %s", bucket, key, e)
            return False

    def purge_account(self) -> int:
        """Delete the account's entire on-disk subtree — the terminal,
        irreversible step of an account hard-purge.  Returns the number of
        files removed (0 if nothing was there).

        Refuses on the platform store (``account_id is None``) so we can
        never wipe the shared ``data/system/cache`` tree.
        """
        if self._account_id is None:
            return 0
        import shutil
        prefix = self._account_prefix()  # "account-{id}"
        if os.path.isabs(self._root):
            acct_root = os.path.join(self._root, prefix)
        else:
            acct_root = os.path.join(self._PROJECT_ROOT, self._root, prefix)
        if not os.path.isdir(acct_root):
            return 0
        n = sum(len(files) for _, _, files in os.walk(acct_root))
        shutil.rmtree(acct_root, ignore_errors=True)
        logger.info(
            "DiskObjectStorage.purge_account: removed %d file(s) under %s", n, acct_root,
        )
        return n

    def url(self, bucket: str, key: str) -> str:
        # Project-relative path that the FastAPI static mount serves.
        # Includes the account prefix so the DB-stored path round-trips
        # correctly through ``get_by_id``.
        safe_key = key.replace("/", "_").replace("\\", "_")
        prefix = self._account_prefix()
        bucket_with_prefix = f"{prefix}/{bucket}" if prefix else bucket
        return f"{self._root}/{bucket_with_prefix}/{safe_key}"

    def local_path(self, bucket: str, key: str) -> str | None:
        full = self._full(bucket, key)
        return full if os.path.exists(full) else None

    def move_folder(self, src_bucket: str, dst_bucket: str) -> bool:
        """Move ``<root>/<account-prefix>/<src_bucket>`` to the equivalent
        destination.

        Creates any missing intermediate directories.  Returns False
        when the source doesn't exist (caller treats it as idempotent —
        driver had no docs yet).
        """
        import shutil
        if os.path.isabs(self._root):
            base = self._root
        else:
            base = os.path.join(self._PROJECT_ROOT, self._root)
        # Prepend the account prefix to both sides so both stay inside
        # the same tenant boundary.
        prefix = self._account_prefix()
        src_path = f"{prefix}/{src_bucket}" if prefix else src_bucket
        dst_path = f"{prefix}/{dst_bucket}" if prefix else dst_bucket
        src = os.path.join(base, src_path)
        dst = os.path.join(base, dst_path)
        if not os.path.exists(src):
            return False
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            return True
        except Exception as e:
            logger.warning(
                "DiskObjectStorage.move_folder failed %s -> %s: %s",
                src_bucket, dst_bucket, e,
            )
            return False


# ── Module singletons ────────────────────────────────────────────────

_store: ObjectStorage | None = None

# Per-account cache.  Keyed by ``account_id``.  Stays for the process
# lifetime — backend choice is immutable until the account reconfigures
# via the Settings page, at which point ``invalidate_object_storage_for_account``
# is called to flush the entry.  Disk is cheap to recreate; the GDrive
# backend caches folder IDs internally so re-creating it loses that
# cache — invalidate sparingly.
_per_account_stores: dict[int, ObjectStorage] = {}


# Convention: settings keys under ``account_settings`` for the storage
# backend choice.  Module-level constants so callers stay typo-safe.
OBJECT_STORAGE_BACKEND_KEY = "object_storage.backend"           # "disk" | "gdrive"
OBJECT_STORAGE_GDRIVE_REFRESH_TOKEN = "object_storage.gdrive.refresh_token"  # encrypted
OBJECT_STORAGE_GDRIVE_ROOT_FOLDER_ID = "object_storage.gdrive.root_folder_id"
OBJECT_STORAGE_GDRIVE_USER_EMAIL = "object_storage.gdrive.user_email"  # for display only


def get_object_storage() -> ObjectStorage:
    """Return the platform-wide default object store.

    Used by callers without an account context — Telegram avatar cache,
    startup routines, system-level operations.  This store writes
    under ``data/system/cache/`` (NOT ``data/userdata/``) so platform
    files are visibly separated from tenant blobs.

    For per-account uploads (work-orders, maintenance attachments,
    PTI photos, etc.) use ``get_object_storage_for_account`` so each
    tenant lands in their own ``account-{id}/`` subtree and can plug
    in their own Google Drive backend.
    """
    global _store
    if _store is None:
        # Platform store: rooted at data/system/cache so platform-shared
        # bytes never mix with tenant blobs under data/userdata/.
        # ``account_id=None`` skips the per-account prefix injection.
        _store = DiskObjectStorage(root="data/system/cache", account_id=None)
    return _store


async def get_object_storage_for_account(account_id: int, tenant_db) -> ObjectStorage:
    """Return the object store configured for one tenant account.

    Reads ``account_settings.object_storage.backend`` to decide which backend
    to spin up.  Defaults to ``DiskObjectStorage`` (the safe platform
    fallback) when no preference is recorded — every account works
    out-of-the-box without configuration.

    Caches the result for the process lifetime so each account pays
    the connect/credential cost once.  Use
    ``invalidate_object_storage_for_account`` after a Settings change to
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
        await tenant_db.get_account_setting(account_id, OBJECT_STORAGE_BACKEND_KEY, "disk")
    ) or "disk"
    backend = backend.lower()

    if backend == "disk":
        # Per-account DiskObjectStorage: the account_id makes every write
        # land under ``data/userdata/account-{id}/{bucket}/...``.  Each
        # account gets its own root, even though all share the same
        # DiskObjectStorage class — the prefix is injected internally.
        store: ObjectStorage = DiskObjectStorage(account_id=account_id)
    elif backend == "gdrive":
        # Late import so DiskObjectStorage-only setups don't pay the
        # google-api-python-client import cost on every startup.
        try:
            from .object_storage_gdrive import GDriveObjectStorage
            store = await GDriveObjectStorage.connect(account_id, tenant_db)
        except Exception as e:
            logger.error(
                "GDrive backend unavailable for account %d (falling back to disk): %s",
                account_id, e,
            )
            store = DiskObjectStorage(account_id=account_id)
    elif backend == "hybrid":
        # Disk-primary + cloud-async tier.  Always succeeds at
        # construction — Drive is lazy-built on first need so an
        # account that has selected hybrid but not yet connected
        # Drive still gets a working write path (files accumulate
        # locally + in the sync queue, which drains once Drive is
        # connected).  See ``object_store_hybrid.HybridObjectStorage``.
        from .object_storage_hybrid import HybridObjectStorage
        store = HybridObjectStorage(account_id, tenant_db)
    else:
        logger.warning(
            "Unknown storage backend %r for account %d — using disk",
            backend, account_id,
        )
        store = DiskObjectStorage(account_id=account_id)

    _per_account_stores[account_id] = store
    return store


def invalidate_object_storage_for_account(account_id: int) -> None:
    """Drop the cached ObjectStorage for an account.

    Called after the user changes their storage backend in Settings or
    refreshes their Google Drive credentials.  The next
    ``get_object_storage_for_account`` call rebuilds from current
    ``account_settings``.
    """
    _per_account_stores.pop(account_id, None)


def reset_object_store() -> None:
    """Test hook: clear all caches so the next call rebuilds from
    current ``config`` + ``account_settings`` values."""
    global _store
    _store = None
    _per_account_stores.clear()


async def purge_account_files(account_id: int, tenant_db) -> int:
    """Delete an account's files that live on OUR server — the file-side
    counterpart of ``purge_account_data``.

    Server-local ONLY by policy: files stored in the customer's own
    external cloud (their Google Drive) are NEVER deleted, even on account
    hard-purge — that cloud data is theirs to keep.  Each backend enforces
    this (disk removes its local subtree; gdrive is a no-op; hybrid removes
    only the local subtree).

    Call it BEFORE the DB rows that hold the account's storage backend
    choice + Drive credentials are deleted, so the correct backend can
    still be resolved.  Best-effort and fully isolated: never raises into
    the purge loop.  Returns the number of local files removed (0 on any
    failure or nothing to purge).  Backends may be sync or async; both
    shapes are handled here.
    """
    import inspect
    try:
        store = await get_object_storage_for_account(account_id, tenant_db)
    except Exception:
        logger.warning(
            "purge_account_files: could not resolve store for acct=%s",
            account_id, exc_info=True,
        )
        return 0
    fn = getattr(store, "purge_account", None)
    if fn is None:
        return 0
    try:
        res = fn()
        if inspect.isawaitable(res):
            res = await res
        n = int(res or 0)
    except Exception:
        logger.warning(
            "purge_account_files: purge failed for acct=%s", account_id, exc_info=True,
        )
        n = 0
    # The account is gone — drop any cached backend handle for it.
    invalidate_object_storage_for_account(account_id)
    return n
