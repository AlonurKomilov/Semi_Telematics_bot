"""Phase 4 tests for the per-account local-storage quota gate.

The helper lives in the inspections route, but the data contract is
``ObjectStorageSyncMixin.account_has_quota_for`` from Phase 1.  We exercise
both via the public surface: the gate's helper, the structured 413
shape, and the boundary conditions (zero-byte, exact-fit, over-by-one).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from fastapi import HTTPException

from features.inspections.router import _check_quota_or_413


@pytest.fixture
async def db(pg_db):
    yield pg_db


# ── account_has_quota_for boundaries ───────────────────────────────


class TestQuotaMath:
    async def test_unlimited_under_default_quota(self, db):
        acct = await db.create_account("Acme")
        ok, used, quota = await db.account_has_quota_for(acct.id, 5_000_000)
        assert ok is True
        assert used == 0
        assert quota > 0   # default DEFAULT_DISK_QUOTA_BYTES

    async def test_exact_fit(self, db):
        acct = await db.create_account("Acme")
        await db.set_account_disk_quota(acct.id, 10_000)
        await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="x", local_path="p", file_size=7000,
        )
        ok, used, quota = await db.account_has_quota_for(acct.id, 3000)
        assert (ok, used, quota) == (True, 7000, 10_000)

    async def test_over_by_one_blocks(self, db):
        acct = await db.create_account("Acme")
        await db.set_account_disk_quota(acct.id, 10_000)
        await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="x", local_path="p", file_size=7000,
        )
        ok, _, _ = await db.account_has_quota_for(acct.id, 3001)
        assert ok is False


# ── _check_quota_or_413 helper (the route gate) ────────────────────


class TestQuotaGate:
    async def test_passes_when_under(self, db):
        acct = await db.create_account("Acme")
        # No bytes pending; default quota is generous.
        await _check_quota_or_413(db, acct.id, 1_000_000)
        # Did not raise.

    async def test_zero_byte_uploads_skip_the_check(self, db):
        # A 0-byte upload is a no-op for quota — short-circuit before
        # touching the DB (one less query on the hot path).
        await _check_quota_or_413(db, account_id=12345, additional_bytes=0)

    async def test_blocks_with_structured_413(self, db):
        acct = await db.create_account("Acme")
        await db.set_account_disk_quota(acct.id, 10_000)
        await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="x", local_path="p", file_size=9_500,
        )
        with pytest.raises(HTTPException) as exc:
            await _check_quota_or_413(db, acct.id, 1_000)
        e = exc.value
        assert e.status_code == 413
        d = e.detail
        # Structured shape the dashboard + mini app consume.
        assert isinstance(d, dict)
        assert d["error_code"] == "account_storage_full"
        assert d["used_bytes"] == 9_500
        assert d["quota_bytes"] == 10_000
        assert d["requested_bytes"] == 1_000
        assert "Reconnect Google Drive" in d["message"]
        assert "10,000" in d["message"]   # number formatted with commas

    async def test_blocks_account_with_no_remaining_room(self, db):
        # Pending sync already fills the quota — even a 1-byte upload
        # must be refused.
        acct = await db.create_account("Acme")
        await db.set_account_disk_quota(acct.id, 5_000)
        await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="x", local_path="p", file_size=5_000,
        )
        with pytest.raises(HTTPException) as exc:
            await _check_quota_or_413(db, acct.id, 1)
        assert exc.value.status_code == 413
        assert exc.value.detail["error_code"] == "account_storage_full"

    async def test_separate_accounts_have_independent_quotas(self, db):
        # An over-quota account A must not block account B's uploads.
        a = await db.create_account("Acme A")
        b = await db.create_account("Acme B")
        await db.set_account_disk_quota(a.id, 5_000)
        await db.set_account_disk_quota(b.id, 5_000)
        await db.enqueue_storage_sync(
            account_id=a.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="x", local_path="p", file_size=5_000,
        )
        # A is full
        with pytest.raises(HTTPException):
            await _check_quota_or_413(db, a.id, 1)
        # B is empty → passes
        await _check_quota_or_413(db, b.id, 4_000)


# ── Quota draining via sync ────────────────────────────────────────


class TestQuotaDrain:
    async def test_quota_frees_when_queue_row_removed(self, db):
        # When the Phase 3 worker syncs a file (queue row deleted),
        # the account's local_bytes drops and quota math sees the
        # freed space.  Lock that in.
        acct = await db.create_account("Acme")
        await db.set_account_disk_quota(acct.id, 10_000)
        qid = await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="x", local_path="p", file_size=8_000,
        )
        # Over-allocate is blocked while the row is pending.
        with pytest.raises(HTTPException):
            await _check_quota_or_413(db, acct.id, 3_000)
        # Worker reports success → queue row removed → space frees.
        await db.mark_sync_succeeded(qid)
        await _check_quota_or_413(db, acct.id, 3_000)   # passes now
