"""Account file-purge — the disk leg of the hard-purge file cleanup.

``purge_account`` is the terminal, irreversible file delete that runs when
an account's 90-day deletion grace elapses.  These tests pin the two
safety-critical properties: it removes ONLY the target account's subtree,
and the platform-shared store refuses to purge.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

import adapters.storage.object_store as ostore
from adapters.storage.object_store import DiskObjectStore


def test_purge_account_removes_only_that_accounts_files(tmp_path):
    root = str(tmp_path)
    a = DiskObjectStore(root=root, account_id=4242)
    b = DiskObjectStore(root=root, account_id=9999)

    a.put("ACME/drivers/user-1", "license.pdf", b"a")
    a.put("ACME/work-orders/2026", "photo.jpg", b"b")
    b.put("BETA/drivers/user-2", "keep.pdf", b"c")

    assert a.exists("ACME/drivers/user-1", "license.pdf")

    removed = a.purge_account()

    assert removed == 2
    assert not a.exists("ACME/drivers/user-1", "license.pdf")
    assert not a.exists("ACME/work-orders/2026", "photo.jpg")
    # The OTHER account's files are untouched.
    assert b.exists("BETA/drivers/user-2", "keep.pdf")


def test_purge_account_on_missing_tree_is_zero(tmp_path):
    store = DiskObjectStore(root=str(tmp_path), account_id=12345)
    assert store.purge_account() == 0  # nothing written yet → no-op


def test_platform_store_refuses_to_purge(tmp_path):
    """The account-less platform store must never wipe the shared cache."""
    store = DiskObjectStore(root=str(tmp_path), account_id=None)
    store.put("avatars", "u1.png", b"x")
    assert store.purge_account() == 0
    assert store.exists("avatars", "u1.png")


class _SyncFakeStore:
    def __init__(self):
        self.purged = False

    def purge_account(self):  # disk / gdrive backends are sync
        self.purged = True
        return 5


class _AsyncFakeStore:
    def __init__(self):
        self.purged = False

    async def purge_account(self):  # hybrid backend is async
        self.purged = True
        return 7


def test_gdrive_purge_account_never_touches_customer_drive():
    """Policy: the customer's own Google Drive is never deleted from, even
    on account purge — purge_account is a no-op that makes no Drive call."""
    from adapters.storage.object_store_gdrive import GDriveObjectStore

    class _ExplodingService:
        def files(self):  # any Drive API access is a policy violation
            raise AssertionError("purge_account must not call the customer's Drive")

    store = GDriveObjectStore(_ExplodingService(), "root-folder-123", account_id=1)
    assert store.purge_account() == 0  # nothing on our side; Drive untouched


def test_hybrid_purge_account_only_touches_local_disk():
    """Policy: hybrid purge reclaims local disk only and never connects to
    the customer's Drive."""
    from adapters.storage.object_store_hybrid import HybridObjectStore

    h = HybridObjectStore.__new__(HybridObjectStore)  # bypass __init__ (needs tenant_db)

    class _Disk:
        def purge_account(self):
            return 3

    def _drive_boom():
        raise AssertionError("hybrid purge must not reach the customer's Drive")

    h._disk = _Disk()
    h._drive_or_none = _drive_boom  # would raise if purge tried to connect Drive

    assert h.purge_account() == 3  # local only


@pytest.mark.asyncio
@pytest.mark.parametrize("fake,expected", [(_SyncFakeStore(), 5), (_AsyncFakeStore(), 7)])
async def test_purge_account_files_handles_sync_and_async(monkeypatch, fake, expected):
    """The helper awaits async backends (hybrid) and calls sync ones
    (disk/gdrive) directly, then drops the cached handle."""
    async def _resolve(account_id, tenant_db):
        return fake
    invalidated: dict[str, int] = {}
    monkeypatch.setattr(ostore, "get_object_store_for_account", _resolve)
    monkeypatch.setattr(
        ostore, "invalidate_object_store_for_account",
        lambda aid: invalidated.update(id=aid),
    )

    n = await ostore.purge_account_files(123, tenant_db=None)

    assert n == expected
    assert fake.purged
    assert invalidated["id"] == 123  # cached handle dropped after purge
