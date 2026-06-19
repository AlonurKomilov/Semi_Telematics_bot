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
  * **No false positives.**  A file counts as referenced if ANY DB column
    that looks like an object-store reference holds its path.  Reference
    columns are discovered dynamically by name pattern across
    ``information_schema`` so a new feature's column is picked up
    automatically (drift-resistant).  Over-matching is harmless — a value
    that isn't an account-local path simply never matches a walked file.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field

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
    hold, gathered from all reference-shaped TEXT columns on tenant tables."""
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
    files = _walk_account_files(account_id, root)
    if not files:
        return OrphanReport(account_id, 0, 0, grace_days, 0, 0, [])
    referenced = await _collect_referenced(tenant_db, account_id)
    cutoff = time.time() - grace_days * 86400
    candidates = _select_candidates(files, referenced, cutoff)
    candidates.sort(key=lambda f: f.size, reverse=True)
    report = OrphanReport(
        account_id=account_id,
        scanned_files=len(files),
        referenced=len(referenced),
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
