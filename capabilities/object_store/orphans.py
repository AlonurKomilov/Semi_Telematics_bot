"""Server-local orphan-file scan — REPORT ONLY (Phase 1).

Finds files under an account's LOCAL object-store subtree that no database
row references — leaked blobs left behind when a parent record was deleted
without cleaning up its file.  This is the audit that must run and be
reviewed BEFORE any deletion is enabled (Phase 2); it deletes nothing.

Two hard constraints:

  * **Server-local only.**  It walks our own disk
    (``data/userdata/account-{id}/...``) and never touches a customer's
    external cloud (their Google Drive).  Files synced to Drive have their
    local copy removed by the sync worker, so they aren't on our disk and
    the scan can't see them — by construction.  See
    ``feedback_prune_server_local_only``.
  * **No false positives.**  A file counts as referenced if any DECLARED
    reference column (``capabilities.object_store.references``) holds its
    path, if the legacy name-pattern net catches it, or if it is sitting
    in the hybrid sync queue awaiting upload.  Over-matching is harmless
    — a value that isn't an account-local path simply never matches a
    walked file.

    This claim was FALSE until the declared registry replaced pure
    name-matching.  A column nobody had anticipated contributed no
    references, so every file it pointed at became a deletion candidate:
    measured on live data, 437 of 601 files (95.5 MB) — driver medical
    certificates, CDL scans, unsafe-parking evidence — were candidates
    purely because ``map_image_path`` / ``image_path`` / ``docs_json``
    didn't match a substring list.  Only the grace window held them
    back, and grace windows expire.  See references.py for why the fix
    is a declaration plus a drift test rather than more patterns.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field

from capabilities.object_store.references import (
    declared_for_tables,
    json_leaf_paths,
)

logger = logging.getLogger(__name__)

# Substrings / suffixes that mark a TEXT column as holding an object-store
# reference.  Matched case-insensitively against information_schema.
_REF_COL_SUBSTRINGS = (
    "file_path", "object_id", "object_path", "media_path", "photo_path",
    "doc_path", "storage_path", "remote_path", "attachment", "logo",
)
_REF_COL_SUFFIXES = ("_url",)  # reference_image_url, image_url, file_url, ...

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class LocalFile:
    relpath: str   # normalized "account-{id}/bucket/key"
    abspath: str
    size: int
    mtime: float


@dataclass(frozen=True)
class OrphanReport:
    account_id: int
    scanned_files: int
    referenced: int
    grace_days: int
    candidate_count: int
    candidate_bytes: int
    sample: list[str] = field(default_factory=list)  # up to N candidate relpaths


@dataclass(frozen=True)
class OrphanPurgeResult:
    account_id: int
    grace_days: int
    dry_run: bool
    candidate_count: int
    candidate_bytes: int
    deleted: int
    deleted_bytes: int
    sample: list[str] = field(default_factory=list)


def _account_root(account_id: int, root: str | None = None) -> str:
    """Absolute path of the account's local object-store subtree."""
    from adapters.storage.object_store import DiskObjectStore

    base = root if root is not None else DiskObjectStore(account_id=account_id)._root
    prefix = f"account-{account_id}"
    if os.path.isabs(base):
        return os.path.join(base, prefix)
    return os.path.join(DiskObjectStore._PROJECT_ROOT, base, prefix)


def _normalize(s: str, account_id: int) -> str:
    """Reduce a stored reference / disk path to its ``account-{id}/...``
    tail so DB values and walked file paths compare regardless of the root
    form (absolute vs project-relative, ``\\`` vs ``/``)."""
    if not s:
        return ""
    s = s.replace("\\", "/")
    marker = f"account-{account_id}/"
    i = s.find(marker)
    return s[i:] if i >= 0 else s


def _walk_account_files(account_id: int, root: str | None = None) -> list[LocalFile]:
    """Every file under the account's local subtree (server-local only)."""
    base = _account_root(account_id, root)
    out: list[LocalFile] = []
    if not os.path.isdir(base):
        return out
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            ap = os.path.join(dirpath, name)
            try:
                st = os.stat(ap)
            except OSError:
                continue
            out.append(LocalFile(_normalize(ap, account_id), ap, st.st_size, st.st_mtime))
    return out


async def _collect_referenced(tenant_db, account_id: int) -> set[str]:
    """Normalized tails of every object-store reference an account's rows
    hold.

    Sources, unioned — a file counted twice costs nothing, a file counted
    zero times gets deleted:

      1. THE DECLARED REGISTRY (``capabilities.object_store.references``) —
         authoritative.  Name-matching alone missed
         ``parking_events.map_image_path`` (4,185 rows),
         ``camera_checks.image_path`` (17,689 rows) and could never match
         ``driver_applications.docs_json`` at all, which made 73% of one
         account's files look orphaned.
      2. The legacy name-pattern sweep — kept purely as a safety net for
         a column nobody has registered yet.  It can only ADD refs.
      3. The hybrid sync queue — a file queued for Drive upload has no
         feature row pointing at it yet, so without this it reads as an
         orphan and would be deleted before it ever reached the
         customer's cloud.
    """
    cur = await tenant_db._db.execute(
        """
        SELECT table_name, column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND data_type IN ('text', 'character varying', 'varchar')
        """
    )
    all_cols = [dict(r) for r in await cur.fetchall()]
    cur = await tenant_db._db.execute(
        "SELECT DISTINCT table_name FROM information_schema.columns "
        "WHERE column_name = 'account_id'"
    )
    acct_tables = {dict(r)["table_name"] for r in await cur.fetchall()}

    refs: set[str] = set()

    # ── 1. declared registry ────────────────────────────────────────
    existing = {c["table_name"] for c in all_cols}
    for ref in declared_for_tables(existing):
        # Some reference tables are scoped through a PARENT rather than
        # carrying account_id (``work_order_attachments`` via its work
        # order, ``pti_inspection_media`` via its inspection).  The old
        # name-matcher skipped every such table outright — a third way it
        # manufactured orphans.  Reading them unfiltered is safe: paths
        # are normalized account-relative, so another tenant's value
        # carries a different ``account-{id}`` prefix and can never match
        # a file walked under THIS account's subtree.  Costs a wider read,
        # buys correctness without a join per table.
        scoped = ref.table in acct_tables
        try:
            cur = await tenant_db._db.execute(
                f"SELECT DISTINCT {ref.column} AS v FROM {ref.table} "
                f"WHERE {ref.column} IS NOT NULL AND {ref.column} <> ''"
                + (" AND account_id = ?" if scoped else ""),
                (account_id,) if scoped else (),
            )
            for r in await cur.fetchall():
                v = dict(r).get("v")
                if not v:
                    continue
                if ref.kind == "json_paths":
                    for leaf in json_leaf_paths(str(v)):
                        refs.add(_normalize(leaf, account_id))
                else:
                    refs.add(_normalize(str(v), account_id))
        except Exception:
            # A DECLARED column that cannot be read is not a shrug — the
            # referenced set is now incomplete, so deletion must not
            # proceed on it.  Raised to the caller, which fails the purge
            # closed rather than reaping files it could not account for.
            logger.exception(
                "orphan-scan: DECLARED reference %s.%s failed to read — "
                "referenced set is incomplete", ref.table, ref.column,
            )
            raise

    # ── 3. hybrid sync queue (pending uploads are not orphans) ──────
    if "storage_sync_queue" in existing:
        try:
            cur = await tenant_db._db.execute(
                "SELECT DISTINCT local_path AS v FROM storage_sync_queue "
                "WHERE account_id = ? AND local_path IS NOT NULL "
                "AND local_path <> ''",
                (account_id,),
            )
            for r in await cur.fetchall():
                v = dict(r).get("v")
                if v:
                    refs.add(_normalize(str(v), account_id))
        except Exception:
            logger.exception("orphan-scan: sync-queue read failed")
            raise

    # ── 2. legacy name-pattern net (additive only) ──────────────────
    for c in all_cols:
        table, col = c["table_name"], c["column_name"]
        if table not in acct_tables:
            continue
        low = col.lower()
        if not (any(p in low for p in _REF_COL_SUBSTRINGS)
                or any(low.endswith(sfx) for sfx in _REF_COL_SUFFIXES)):
            continue
        if not (_IDENT.match(table) and _IDENT.match(col)):
            continue  # defense-in-depth even though these come from the catalog
        try:
            cur = await tenant_db._db.execute(
                f"SELECT DISTINCT {col} AS v FROM {table} "
                f"WHERE account_id = ? AND {col} IS NOT NULL AND {col} <> ''",
                (account_id,),
            )
            for r in await cur.fetchall():
                v = dict(r).get("v")
                if v:
                    refs.add(_normalize(str(v), account_id))
        except Exception:
            logger.debug("orphan-scan: skipped %s.%s", table, col, exc_info=True)
    return refs


def _select_candidates(
    files: list[LocalFile], referenced: set[str], cutoff_mtime: float,
) -> list[LocalFile]:
    """Local files that are unreferenced AND older than the grace cutoff."""
    return [
        f for f in files
        if f.relpath not in referenced and f.mtime < cutoff_mtime
    ]


async def _find(
    tenant_db, account_id: int, grace_days: int, root: str | None,
) -> tuple[list[LocalFile], list[LocalFile], int]:
    """Shared core: (all local files, orphan candidates largest-first,
    referenced-set size)."""
    files = _walk_account_files(account_id, root)
    if not files:
        return [], [], 0
    referenced = await _collect_referenced(tenant_db, account_id)
    cutoff = time.time() - grace_days * 86400
    candidates = _select_candidates(files, referenced, cutoff)
    candidates.sort(key=lambda f: f.size, reverse=True)
    return files, candidates, len(referenced)


async def scan_account_orphans(
    tenant_db,
    account_id: int,
    *,
    grace_days: int = 7,
    sample: int = 20,
    root: str | None = None,
) -> OrphanReport:
    """Report-only scan: which local files for an account look orphaned.

    ``grace_days`` ignores recently-written files (an in-flight upload
    whose DB row hasn't committed isn't an orphan).  Deletes nothing.
    """
    files, candidates, referenced = await _find(tenant_db, account_id, grace_days, root)
    report = OrphanReport(
        account_id=account_id,
        scanned_files=len(files),
        referenced=referenced,
        grace_days=grace_days,
        candidate_count=len(candidates),
        candidate_bytes=sum(f.size for f in candidates),
        sample=[f.relpath for f in candidates[:sample]],
    )
    logger.info(
        "orphan-scan acct=%d scanned=%d referenced=%d candidates=%d bytes=%d (REPORT ONLY)",
        account_id, report.scanned_files, report.referenced,
        report.candidate_count, report.candidate_bytes,
    )
    return report


async def delete_account_orphans(
    tenant_db,
    account_id: int,
    *,
    grace_days: int = 7,
    dry_run: bool = True,
    sample: int = 20,
    root: str | None = None,
) -> OrphanPurgeResult:
    """Operator-triggered deletion of an account's orphaned LOCAL files.

    SAFE BY DEFAULT:
      * ``dry_run=True`` (the default) deletes nothing — it just reports
        what *would* be removed, so a missing ``confirm`` can never delete.
      * Re-scans at call time and removes only files STILL orphaned now
        (no acting on a stale report).
      * Server-local ONLY — it ``os.remove``s files under our disk and
        never touches the customer's external cloud (their Google Drive).
    """
    _files, candidates, _referenced = await _find(tenant_db, account_id, grace_days, root)
    deleted = 0
    deleted_bytes = 0
    if not dry_run:
        for f in candidates:
            try:
                os.remove(f.abspath)  # server-local path only
                deleted += 1
                deleted_bytes += f.size
            except FileNotFoundError:
                deleted += 1  # already gone — converged
            except OSError:
                logger.warning(
                    "orphan-purge: could not remove %s", f.abspath, exc_info=True,
                )
    logger.info(
        "orphan-purge acct=%d candidates=%d deleted=%d bytes=%d dry_run=%s",
        account_id, len(candidates), deleted, deleted_bytes, dry_run,
    )
    return OrphanPurgeResult(
        account_id=account_id,
        grace_days=grace_days,
        dry_run=dry_run,
        candidate_count=len(candidates),
        candidate_bytes=sum(f.size for f in candidates),
        deleted=deleted,
        deleted_bytes=deleted_bytes,
        sample=[f.relpath for f in candidates[:sample]],
    )


# The folder names features write into.  Matched anywhere in the path
# because the segment DIRECTLY above a file is not always the kind:
# applications nest one folder PER APPLICATION
# (``{COMPANY}/applications/APP-4C7A0A/medical.pdf``), so grouping by the
# parent produced 109 single-file buckets named after reference codes
# instead of one "applications" row.
_KNOWN_KINDS = (
    "parking-maps", "camera-images", "applications", "branding",
    "work-orders", "inspections", "knowledge", "avatars",
    "company-banners", "company-logos", "driver-docs", "maintenance",
)


def _classify_kind(relpath: str) -> str:
    """Which feature's folder does this file live in."""
    segments = set(relpath.split("/"))
    for kind in _KNOWN_KINDS:
        if kind in segments:
            return kind
    parts = relpath.split("/")
    return parts[-2] if len(parts) >= 2 else "other"


@dataclass(frozen=True)
class UsageBucket:
    kind: str          # "parking-maps", "camera-images", ...
    files: int
    bytes: int
    orphan_files: int
    orphan_bytes: int


async def account_usage(
    tenant_db, account_id: int, *, grace_days: int = 7, root: str | None = None,
) -> list[UsageBucket]:
    """What is this account actually storing, by kind — and how much of it
    is unreferenced.

    Exists because the storage page had no answer to "what am I storing".
    ``/storage/health`` reports ``used_bytes`` as the PENDING-SYNC
    footprint, which is ~0 whenever the queue is drained, and
    ``/storage/files`` lists queue rows.  So an account with 950 files on
    disk and an empty queue saw zeroes and an empty table, which reads as
    "storage is broken" rather than "everything is synced".

    Reuses the orphan walk rather than a second traversal, so the
    referenced/orphaned split is computed from the SAME declared registry
    the purge uses — the number a user sees and the number the delete
    acts on cannot disagree.
    """
    files, candidates, _referenced = await _find(
        tenant_db, account_id, grace_days, root,
    )
    orphan_paths = {f.relpath for f in candidates}

    by_kind: dict[str, dict[str, int]] = {}
    for f in files:
        kind = _classify_kind(f.relpath)
        b = by_kind.setdefault(
            kind, {"files": 0, "bytes": 0, "orphan_files": 0, "orphan_bytes": 0},
        )
        b["files"] += 1
        b["bytes"] += f.size
        if f.relpath in orphan_paths:
            b["orphan_files"] += 1
            b["orphan_bytes"] += f.size

    return sorted(
        (UsageBucket(k, v["files"], v["bytes"], v["orphan_files"], v["orphan_bytes"])
         for k, v in by_kind.items()),
        key=lambda u: u.bytes, reverse=True,
    )
