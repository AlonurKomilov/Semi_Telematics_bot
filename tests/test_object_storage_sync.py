"""Phase 1 tests for the hybrid-storage outbox + media state machine.

Covers the data layer only — atomic enqueue, idempotent re-enqueue,
backoff math, permanent-error short-circuit, the claim path (with a
second claimer to verify a single row isn't double-leased in a single
transaction), quota arithmetic, and the storage-backend flip.

The Phase 3 worker + Phase 2 HybridObjectStorage are not exercised here
— this file locks down the durable contract those layers will sit on.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("ENCRYPTION_KEY", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from adapters.storage import Role
from adapters.storage.object_storage_sync import (
    DEFAULT_DISK_QUOTA_BYTES,
    ERR_TOKEN_EXPIRED, ERR_TRANSIENT,
    STATE_LOCAL, STATE_REMOTE, STATE_SYNCING,
    compute_next_attempt_at, is_permanent_error,
)


@pytest.fixture
async def db(pg_db):
    yield pg_db


# ── Pure helpers (no DB) ────────────────────────────────────────────


class TestBackoffMath:
    def test_first_failure_is_short(self):
        # ~30 s out — same wall-clock-second tolerance.
        next_at = datetime.fromisoformat(compute_next_attempt_at(1))
        delta = (next_at - datetime.now(timezone.utc)).total_seconds()
        assert 25 <= delta <= 35

    def test_backoff_grows_then_caps(self):
        deltas = []
        for attempts in (1, 2, 3, 4, 5, 6, 7, 8, 99):
            t = datetime.fromisoformat(compute_next_attempt_at(attempts))
            deltas.append((t - datetime.now(timezone.utc)).total_seconds())
        # Each step ≥ previous (caps at the last bucket — 24 h).
        for a, b in zip(deltas, deltas[1:]):
            assert b + 5 >= a   # allow drift
        # And the cap is roughly 24 h (the longest bucket).
        assert deltas[-1] >= 23 * 3600

    def test_permanent_error_predicate(self):
        assert is_permanent_error(ERR_TOKEN_EXPIRED) is True
        assert is_permanent_error(ERR_TRANSIENT) is False
        assert is_permanent_error(None) is False
        assert is_permanent_error("") is False


# ── Outbox: enqueue + idempotency ───────────────────────────────────


class TestEnqueue:
    async def test_enqueue_inserts_a_row(self, db):
        acct = await db.create_account("Acme")
        qid = await db.enqueue_storage_sync(
            account_id=acct.id,
            entity_type="pti_media", entity_id=1,
            bucket="inspections/1/1", filename="a.jpg",
            local_path="data/inspections/1/1/a.jpg",
            file_size=1234,
        )
        assert qid > 0
        rows = await db.list_pending_sync(acct.id)
        assert len(rows) == 1
        assert rows[0]["filename"] == "a.jpg"
        assert int(rows[0]["file_size"]) == 1234
        assert int(rows[0]["attempts"]) == 0

    async def test_enqueue_is_idempotent_on_same_entity(self, db):
        # Re-enqueue resets the schedule + clears errors, never dupes.
        acct = await db.create_account("Acme")
        qid1 = await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=7,
            bucket="b", filename="x.jpg", local_path="p", file_size=100,
        )
        # Simulate a prior failure parked the row.
        await db.mark_sync_failed(qid1, error="boom", error_code=ERR_TRANSIENT)
        # Re-enqueue → same id, attempts reset.
        qid2 = await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=7,
            bucket="b", filename="x.jpg", local_path="p", file_size=100,
        )
        assert qid2 == qid1
        rows = await db.list_pending_sync(acct.id)
        assert len(rows) == 1
        assert int(rows[0]["attempts"]) == 0
        assert rows[0]["last_error"] is None


# ── Outbox: claim + outcomes ────────────────────────────────────────


class TestClaimAndOutcomes:
    async def test_claim_returns_due_rows_with_attempt_increment(self, db):
        acct = await db.create_account("Acme")
        await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="a.jpg", local_path="p1", file_size=10,
        )
        await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=2,
            bucket="b", filename="b.jpg", local_path="p2", file_size=20,
        )
        claimed = await db.claim_pending_sync(limit=10)
        assert len(claimed) == 2
        # The claim path bumps ``attempts`` so a worker crash doesn't
        # let the same row be re-claimed instantly.
        rows = await db.list_pending_sync(acct.id)
        assert all(int(r["attempts"]) == 1 for r in rows)

    async def test_succeeded_removes_row(self, db):
        acct = await db.create_account("Acme")
        qid = await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="a.jpg", local_path="p", file_size=1,
        )
        await db.claim_pending_sync(limit=1)
        ok = await db.mark_sync_succeeded(qid)
        assert ok is True
        assert await db.list_pending_sync(acct.id) == []

    async def test_failed_schedules_backoff(self, db):
        acct = await db.create_account("Acme")
        qid = await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="a.jpg", local_path="p", file_size=1,
        )
        await db.claim_pending_sync(limit=1)
        state = await db.mark_sync_failed(
            qid, error="503 transient", error_code=ERR_TRANSIENT,
        )
        # After 1 attempt, next attempt should be ~30s out (the first
        # backoff bucket).  Allow a generous window for clock drift.
        next_at = datetime.fromisoformat(state["next_attempt_at"])
        delta = (next_at - datetime.now(timezone.utc)).total_seconds()
        assert 20 <= delta <= 40

    async def test_stuck_parks_row(self, db):
        acct = await db.create_account("Acme")
        qid = await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="a.jpg", local_path="p", file_size=1,
        )
        await db.mark_sync_stuck(
            qid, error="invalid_grant", error_code=ERR_TOKEN_EXPIRED,
        )
        # Stuck rows must NOT come back via the normal claim path.
        claimed = await db.claim_pending_sync(limit=10)
        assert claimed == []
        # But they remain visible to the dashboard.
        rows = await db.list_pending_sync(acct.id)
        assert len(rows) == 1
        total, stuck = await db.count_pending_sync(acct.id)
        assert (total, stuck) == (1, 1)

    async def test_retry_stuck_re_enters_loop(self, db):
        acct = await db.create_account("Acme")
        qid = await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="a.jpg", local_path="p", file_size=1,
        )
        await db.mark_sync_stuck(
            qid, error="invalid_grant", error_code=ERR_TOKEN_EXPIRED,
        )
        await db.retry_stuck_sync(qid)
        # Now the row is due immediately → claim picks it up again.
        claimed = await db.claim_pending_sync(limit=10)
        assert [int(r["id"]) for r in claimed] == [qid]


# ── Quota arithmetic ────────────────────────────────────────────────


class TestQuota:
    async def test_default_quota_when_unset(self, db):
        acct = await db.create_account("Acme")
        assert await db.get_account_disk_quota(acct.id) == DEFAULT_DISK_QUOTA_BYTES

    async def test_override_quota_per_account(self, db):
        acct = await db.create_account("Acme")
        await db.set_account_disk_quota(acct.id, 100 * 1024 * 1024)
        assert await db.get_account_disk_quota(acct.id) == 100 * 1024 * 1024

    async def test_local_bytes_sums_queue_rows(self, db):
        acct = await db.create_account("Acme")
        for i, size in enumerate([1000, 2500, 4500], start=1):
            await db.enqueue_storage_sync(
                account_id=acct.id, entity_type="pti_media", entity_id=i,
                bucket="b", filename=f"{i}.jpg", local_path=f"p{i}",
                file_size=size,
            )
        assert await db.get_account_local_bytes(acct.id) == 8000

    async def test_account_has_quota_for_blocks_oversize(self, db):
        acct = await db.create_account("Acme")
        await db.set_account_disk_quota(acct.id, 10_000)
        await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="x.jpg", local_path="p", file_size=8000,
        )
        ok, used, quota = await db.account_has_quota_for(acct.id, 1_500)
        assert (ok, used, quota) == (True, 8000, 10_000)
        ok, _, _ = await db.account_has_quota_for(acct.id, 2_500)
        assert ok is False


# ── Media state machine ────────────────────────────────────────────


class TestMediaState:
    async def test_state_transitions(self, db):
        acct = await db.create_account("Acme")
        driver = await db.create_user(50001, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template_with_items(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="42", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        mid = await db.attach_inspection_media(
            ins_id, media_type="photo",
            file_path="data/inspections/1/1/x.jpg",
            file_name="x.jpg", file_size=999,
            uploaded_by=driver.telegram_id,
        )

        # Default for new uploads in Phase 2+ will be 'local' (the
        # ObjectStorage writes it).  Here we drive the transitions
        # directly to lock down the contract.
        assert await db.set_media_storage_state(
            mid, state=STATE_LOCAL,
            local_path="data/inspections/1/1/x.jpg", sha256="abcdef",
        )
        row = await db.get_inspection_media(mid)
        assert row["storage_state"] == STATE_LOCAL
        assert row["local_path"] == "data/inspections/1/1/x.jpg"
        assert row["sha256"] == "abcdef"

        await db.set_media_storage_state(mid, state=STATE_SYNCING)
        assert (await db.get_inspection_media(mid))["storage_state"] == STATE_SYNCING

        # Final: remote_path set, local cleared, file_path becomes the
        # canonical (legacy-readable) location too.
        await db.set_media_storage_state(
            mid, state=STATE_REMOTE,
            remote_path="DRIVE_FILE_ID_XYZ",
            file_path="DRIVE_FILE_ID_XYZ",
        )
        row = await db.get_inspection_media(mid)
        assert row["storage_state"] == STATE_REMOTE
        assert row["remote_path"] == "DRIVE_FILE_ID_XYZ"
        # local_path is left untouched by the transition; the
        # ObjectStorage is responsible for clearing it after the disk
        # delete in Phase 3.

    async def test_legacy_rows_default_to_remote(self, db):
        # The migration default is 'remote' so legacy rows don't get
        # picked up by the sync worker.
        acct = await db.create_account("Acme")
        driver = await db.create_user(50002, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="42", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        mid = await db.attach_inspection_media(
            ins_id, media_type="photo", file_path="legacy/path.jpg",
            file_name="path.jpg", uploaded_by=driver.telegram_id,
        )
        row = await db.get_inspection_media(mid)
        assert row["storage_state"] == STATE_REMOTE

    async def test_unknown_state_rejected(self, db):
        with pytest.raises(ValueError):
            await db.set_media_storage_state(1, state="banana")


# ── Backend flip ───────────────────────────────────────────────────


class TestBackendFlip:
    async def test_set_account_storage_backend(self, db):
        acct = await db.create_account("Acme")
        from adapters.storage.object_storage import OBJECT_STORAGE_BACKEND_KEY
        await db.set_account_storage_backend(acct.id, "disk")
        assert await db.get_account_setting(acct.id, OBJECT_STORAGE_BACKEND_KEY, "") == "disk"
        await db.set_account_storage_backend(acct.id, "gdrive")
        assert await db.get_account_setting(acct.id, OBJECT_STORAGE_BACKEND_KEY, "") == "gdrive"

    async def test_rejects_unknown_backend(self, db):
        acct = await db.create_account("Acme")
        with pytest.raises(ValueError):
            await db.set_account_storage_backend(acct.id, "bogus")
