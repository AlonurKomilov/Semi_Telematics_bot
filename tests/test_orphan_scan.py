"""Orphan-file scan (report-only).

Pins the risk-critical behaviour: a referenced file is NEVER a candidate,
a recently-written file is held back by the grace window, and the
end-to-end scan (disk walk + dynamic reference discovery + normalization)
flags only the genuinely-orphaned, aged file.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import time

import pytest

from adapters.storage.object_store import DiskObjectStore
from capabilities.storage.orphans import (
    LocalFile,
    _normalize,
    _select_candidates,
    scan_account_orphans,
)


def test_select_candidates_excludes_referenced_and_recent():
    now = time.time()
    old = now - 30 * 86400
    recent = now - 1 * 86400
    cutoff = now - 7 * 86400
    files = [
        LocalFile("account-1/a/ref.pdf", "/x/ref.pdf", 10, old),        # referenced → exclude
        LocalFile("account-1/a/orphan.pdf", "/x/orphan.pdf", 20, old),  # orphan + aged → include
        LocalFile("account-1/a/fresh.pdf", "/x/fresh.pdf", 30, recent), # unreferenced but recent → grace
    ]
    referenced = {"account-1/a/ref.pdf"}
    cands = _select_candidates(files, referenced, cutoff)
    assert [c.relpath for c in cands] == ["account-1/a/orphan.pdf"]


def test_normalize_reduces_to_account_tail():
    assert _normalize("data/userdata/account-7/ACME/wo/f.jpg", 7) == "account-7/ACME/wo/f.jpg"
    assert _normalize("/abs/root/account-7/x/y.png", 7) == "account-7/x/y.png"
    assert _normalize("data\\userdata\\account-7\\b\\z.pdf", 7) == "account-7/b/z.pdf"
    # Drive ids / non-account values pass through unchanged (never match a file).
    assert _normalize("1a2b3cDriveFileId", 7) == "1a2b3cDriveFileId"


@pytest.mark.asyncio
async def test_scan_account_orphans_end_to_end(pg_db, tmp_path, monkeypatch):
    """Disk walk + dynamic DB-reference discovery + grace, against real PG."""
    from infra import config as _cfg
    monkeypatch.setattr(_cfg, "OBJECT_STORE_ROOT", str(tmp_path))

    db = pg_db
    acct = 1
    store = DiskObjectStore(root=str(tmp_path), account_id=acct)
    # With an absolute root, put() returns the absolute disk path.
    ref_path = store.put("ACME/wo", "ref.jpg", b"r" * 100)       # referenced
    orphan_path = store.put("ACME/wo", "orphan.jpg", b"o" * 200) # orphan, aged
    store.put("ACME/wo", "fresh.jpg", b"f" * 50)                 # orphan but recent → grace

    # Age the referenced + the orphan past the 7-day grace; leave fresh at "now".
    old = time.time() - 30 * 86400
    os.utime(ref_path, (old, old))
    os.utime(orphan_path, (old, old))

    # Seed a reference to ref.jpg in a column the dynamic discovery picks up
    # (any tenant table with account_id + a *file_path*-shaped TEXT column).
    await db._db.execute(
        "CREATE TABLE _orphan_ref_test (account_id INTEGER, file_path TEXT)"
    )
    await db._db.execute(
        "INSERT INTO _orphan_ref_test (account_id, file_path) VALUES (?, ?)",
        (acct, ref_path),
    )
    await db._db.commit()

    report = await scan_account_orphans(db, acct, grace_days=7, root=str(tmp_path))

    assert report.scanned_files == 3
    assert report.candidate_count == 1               # ref excluded, fresh held by grace
    assert report.sample == ["account-1/ACME/wo/orphan.jpg"]
    assert report.candidate_bytes == 200
