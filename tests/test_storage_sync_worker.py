"""Phase 3 tests for the storage sync worker.

A stub ``DriveLike`` impersonates ``GDriveObjectStore``'s minimal
surface (``put(bucket, key, data) -> str``, ``last_error`` attribute).
Real Google API calls never happen in this file.

What we lock down:

  * Happy path — bytes land in Drive, media row flips to ``remote``
    with a Drive ID, local file is deleted, queue row removed.
  * Permanent failures (``invalid_grant`` / Drive quota / 403)
    short-circuit to ``stuck`` — the queue stops retrying.
  * Transient failures (5xx, 429) flow through ``mark_sync_failed``,
    keeping the row in the queue for the backoff schedule.
  * Drive-build failure: ENTIRE account batch parked as stuck without
    a Drive API call per row (the circuit breaker).
  * Orphan rows (file vanished from disk) park as stuck instead of
    looping.
  * Disk and Drive paths stay in lockstep (no Drive-side rewriting).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

os.environ.setdefault("ENCRYPTION_KEY", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from adapters.storage import Role
from adapters.storage.object_store import DiskObjectStore
from adapters.storage.storage_sync import (
    ERR_FORBIDDEN, ERR_QUOTA_EXCEEDED, ERR_RATE_LIMITED, ERR_TOKEN_EXPIRED,
    ERR_TRANSIENT,
    STATE_LOCAL, STATE_REMOTE,
)
from capabilities.storage import sync_worker


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
async def db(pg_db):
    yield pg_db


@pytest.fixture
def isolated_disk(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="sync_worker_")
    monkeypatch.setenv("OBJECT_STORE_ROOT", tmp)
    import infra.config as cfg
    monkeypatch.setattr(cfg, "OBJECT_STORE_ROOT", tmp, raising=False)
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


# ── Stub Drive ─────────────────────────────────────────────────────


class StubDrive:
    """Minimal Drive backend for tests — records put-calls + injectable
    outcomes.  Surface matches the subset the worker touches."""

    def __init__(self, *, fail_with: str | None = None, success_id: str = "DRIVE_ID"):
        self._fail_with = fail_with
        self._success_id = success_id
        self.last_error: str | None = None
        self.puts: list[tuple[str, str, int]] = []

    def put(self, bucket: str, key: str, data: bytes) -> str:
        self.puts.append((bucket, key, len(data)))
        if self._fail_with is not None:
            self.last_error = self._fail_with
            return ""
        self.last_error = None
        return self._success_id


async def _make_inspection_with_media(db, *, telegram_id: int, bytes_: bytes,
                                      bucket: str, filename: str,
                                      isolated_disk: str):
    """Build a real inspection + media row + queue row + on-disk file
    so the worker has everything it needs."""
    acct = await db.create_account("Acme")
    driver = await db.create_user(telegram_id, acct.id, role=Role.DRIVER)
    tpl = await db.get_active_template(acct.id, "truck")
    ins_id = await db.create_inspection(
        account_id=acct.id, user_id=driver.id,
        vehicle_name="42", template_id=int(tpl["id"]),
        template_version=int(tpl["version"]),
    )
    mid = await db.attach_inspection_media(
        ins_id, media_type="photo",
        file_path=f"data/{bucket}/{filename}",
        file_name=filename, file_size=len(bytes_),
        content_type="image/jpeg",
        uploaded_by=driver.telegram_id,
    )
    # Write the bytes to disk.
    disk = DiskObjectStore()
    disk.put(bucket, filename, bytes_)
    # Enqueue + flip media state to 'local' (what HybridObjectStore
    # would do via track_for_sync).
    await db.enqueue_storage_sync(
        account_id=acct.id, entity_type="pti_media", entity_id=mid,
        bucket=bucket, filename=filename,
        local_path=f"data/{bucket}/{filename}", file_size=len(bytes_),
    )
    await db.set_media_storage_state(
        mid, state=STATE_LOCAL,
        local_path=f"data/{bucket}/{filename}",
        file_path=f"data/{bucket}/{filename}",
    )
    return acct, mid, disk


# ── Error classification (pure) ────────────────────────────────────


class TestErrorClassification:
    def test_invalid_grant_is_token_expired(self):
        assert sync_worker.classify_drive_error(
            "invalid_grant: Token has been expired or revoked.",
        ) == ERR_TOKEN_EXPIRED

    def test_storage_quota_is_quota_exceeded(self):
        assert sync_worker.classify_drive_error(
            "storageQuotaExceeded",
        ) == ERR_QUOTA_EXCEEDED

    def test_403_forbidden_is_forbidden(self):
        assert sync_worker.classify_drive_error(
            "HTTP 403 Forbidden: insufficient permissions",
        ) == ERR_FORBIDDEN

    def test_503_is_transient(self):
        assert sync_worker.classify_drive_error(
            "503 Service Unavailable temporarily",
        ) == ERR_TRANSIENT

    def test_rate_limit(self):
        assert sync_worker.classify_drive_error(
            "429 rate limit exceeded",
        ) == ERR_RATE_LIMITED

    def test_empty_or_unknown(self):
        assert sync_worker.classify_drive_error("") == "unknown"
        assert sync_worker.classify_drive_error(
            "something nobody has ever seen before",
        ) == "unknown"


# ── Per-row pipeline ───────────────────────────────────────────────


class TestSuccessPath:
    async def test_happy_path_full_pipeline(self, db, isolated_disk):
        acct, mid, disk = await _make_inspection_with_media(
            db, telegram_id=70001, bytes_=b"a real photo's bytes here",
            bucket="inspections/1/1", filename="front.jpg",
            isolated_disk=isolated_disk,
        )
        stub = StubDrive(success_id="DRIVE_FRONT_123")

        async def builder(account_id, _db):
            assert account_id == acct.id
            return stub

        summary = await sync_worker.sync_pending_storage(
            db=db, drive_builder=builder, disk=disk,
        )

        assert summary["succeeded"] == 1
        assert summary["failed"] == 0
        assert summary["stuck"] == 0
        # Drive saw the SAME bucket the caller wrote on disk — no
        # path rewriting.  Keeps disk and Drive in lockstep so the
        # ``HybridObjectStore.get`` fallback lands on the right file.
        assert len(stub.puts) == 1
        bucket, key, size = stub.puts[0]
        assert bucket == "inspections/1/1"
        assert key == "front.jpg"
        assert size > 0

        # Media row advanced to 'remote' with the Drive ID.
        row = await db.get_inspection_media(mid)
        assert row["storage_state"] == STATE_REMOTE
        assert row["remote_path"] == "DRIVE_FRONT_123"
        assert row["file_path"] == "DRIVE_FRONT_123"

        # Local file gone.
        assert disk.get("inspections/1/1", "front.jpg") is None

        # Queue empty.
        assert await db.list_pending_sync(acct.id) == []

    async def test_auto_constructs_per_account_disk_store(self, db, isolated_disk):
        """Production path regression: when ``disk`` is None, the worker
        must construct a per-account ``DiskObjectStore(account_id=...)``
        so the per-tenant prefix ``account-{id}/`` resolves the same
        way ``HybridObjectStore`` wrote the file.  Without this fix,
        the worker would read the un-prefixed path and silently mark
        every hybrid row as stuck.
        """
        acct = await db.create_account("Acme account-aware")
        driver = await db.create_user(70999, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="55", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        # The bucket the caller would pass — no account_id in it.  The
        # account-aware DiskObjectStore prepends ``account-{id}/``
        # internally so the file lands at
        # ``<root>/account-{acct.id}/{bucket}/{filename}``.
        bucket = "PREMIER/inspections/55"
        filename = "front.jpg"
        data = b"production-path bytes"

        per_acct_disk = DiskObjectStore(account_id=acct.id)
        stored_url = per_acct_disk.put(bucket, filename, data)
        assert per_acct_disk.local_path(bucket, filename) is not None, (
            f"file did not land where expected; url={stored_url}"
        )

        mid = await db.attach_inspection_media(
            ins_id, media_type="photo",
            file_path=stored_url, file_name=filename,
            file_size=len(data), content_type="image/jpeg",
            uploaded_by=driver.telegram_id,
        )
        await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=mid,
            bucket=bucket, filename=filename,
            local_path=stored_url, file_size=len(data),
        )
        await db.set_media_storage_state(
            mid, state=STATE_LOCAL,
            local_path=stored_url, file_path=stored_url,
        )

        stub = StubDrive(success_id="DRIVE_AUTO_DISK")

        async def builder(account_id, _db):
            return stub

        # ``disk=None`` exercises the per-account auto-construct branch.
        summary = await sync_worker.sync_pending_storage(
            db=db, drive_builder=builder,
        )
        assert summary["succeeded"] == 1, summary
        assert summary["failed"] == 0
        assert summary["stuck"] == 0
        # Drive bucket matches what was on disk — same bucket, no rewrite.
        assert len(stub.puts) == 1
        bucket_seen, key_seen, _ = stub.puts[0]
        assert bucket_seen == bucket
        assert key_seen == filename


# ── Permanent failures (circuit breaker + classification) ──────────


class TestPermanentFailures:
    async def test_drive_build_failure_parks_entire_batch(self, db, isolated_disk):
        # If GDriveObjectStore.connect raises, every row for that
        # account in the batch must be stuck immediately — no per-row
        # retry of the same dead Drive.
        acct, mid_a, disk = await _make_inspection_with_media(
            db, telegram_id=71001, bytes_=b"A",
            bucket="inspections/1/1", filename="a.jpg",
            isolated_disk=isolated_disk,
        )
        # Enqueue a second media for the same account.
        ins2 = await db.create_inspection(
            account_id=acct.id, user_id=1,
            vehicle_name="42",
            template_id=int((await db.get_active_template(acct.id, "truck"))["id"]),
            template_version=1,
        )
        mid_b = await db.attach_inspection_media(
            ins2, media_type="photo",
            file_path="data/inspections/1/2/b.jpg",
            file_name="b.jpg", file_size=1,
            uploaded_by=0,
        )
        disk.put("inspections/1/2", "b.jpg", b"B")
        await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=mid_b,
            bucket="inspections/1/2", filename="b.jpg",
            local_path="data/inspections/1/2/b.jpg", file_size=1,
        )

        async def builder(_account_id, _db):
            raise RuntimeError(
                "invalid_grant: Token has been expired or revoked.",
            )

        summary = await sync_worker.sync_pending_storage(
            db=db, drive_builder=builder, disk=disk,
        )
        assert summary["succeeded"] == 0
        assert summary["stuck"] == 2

        # Both queue rows still visible but parked far in the future.
        rows = await db.list_pending_sync(acct.id)
        assert len(rows) == 2
        assert all(r["error_code"] == ERR_TOKEN_EXPIRED for r in rows)

    async def test_invalid_grant_on_put_marks_stuck(self, db, isolated_disk):
        acct, mid, disk = await _make_inspection_with_media(
            db, telegram_id=71002, bytes_=b"x",
            bucket="inspections/1/1", filename="x.jpg",
            isolated_disk=isolated_disk,
        )

        async def builder(_a, _b):
            return StubDrive(
                fail_with="invalid_grant: Token has been expired or revoked.",
            )

        summary = await sync_worker.sync_pending_storage(
            db=db, drive_builder=builder, disk=disk,
        )
        assert summary["stuck"] == 1
        rows = await db.list_pending_sync(acct.id)
        assert rows[0]["error_code"] == ERR_TOKEN_EXPIRED
        # Media row stays 'local' — the file IS still on disk and the
        # sync wasn't successful.
        row = await db.get_inspection_media(mid)
        assert row["storage_state"] == STATE_LOCAL
        # Local file NOT deleted (sync didn't succeed).
        assert disk.get("inspections/1/1", "x.jpg") == b"x"

    async def test_quota_exceeded_marks_stuck(self, db, isolated_disk):
        acct, _mid, disk = await _make_inspection_with_media(
            db, telegram_id=71003, bytes_=b"y",
            bucket="inspections/1/1", filename="y.jpg",
            isolated_disk=isolated_disk,
        )

        async def builder(_a, _b):
            return StubDrive(fail_with="storageQuotaExceeded")

        summary = await sync_worker.sync_pending_storage(
            db=db, drive_builder=builder, disk=disk,
        )
        assert summary["stuck"] == 1
        rows = await db.list_pending_sync(acct.id)
        assert rows[0]["error_code"] == ERR_QUOTA_EXCEEDED


# ── Transient failures + recovery ──────────────────────────────────


class TestTransientFailures:
    async def test_503_keeps_row_in_queue_with_backoff(self, db, isolated_disk):
        acct, _mid, disk = await _make_inspection_with_media(
            db, telegram_id=72001, bytes_=b"z",
            bucket="inspections/1/1", filename="z.jpg",
            isolated_disk=isolated_disk,
        )

        async def builder(_a, _b):
            return StubDrive(fail_with="503 Service Unavailable temporarily")

        summary = await sync_worker.sync_pending_storage(
            db=db, drive_builder=builder, disk=disk,
        )
        assert summary["failed"] == 1
        assert summary["stuck"] == 0
        rows = await db.list_pending_sync(acct.id)
        assert rows[0]["error_code"] == ERR_TRANSIENT
        # Local file still present, ready for the next attempt.
        assert disk.get("inspections/1/1", "z.jpg") == b"z"


# ── Orphan rows ────────────────────────────────────────────────────


class TestOrphanRow:
    async def test_missing_local_file_marks_stuck(self, db, isolated_disk):
        # Queue row with no on-disk file (e.g. someone deleted the
        # cache by hand).  Worker must NOT loop forever — park it.
        acct = await db.create_account("Acme")
        driver = await db.create_user(73001, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="42", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        mid = await db.attach_inspection_media(
            ins_id, media_type="photo",
            file_path="data/x/y.jpg",
            file_name="ghost.jpg", file_size=10,
            uploaded_by=0,
        )
        await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=mid,
            bucket="inspections/1/1", filename="ghost.jpg",
            local_path="data/missing", file_size=10,
        )

        async def builder(_a, _b):
            return StubDrive()  # would succeed, but we never get there

        disk = DiskObjectStore()
        summary = await sync_worker.sync_pending_storage(
            db=db, drive_builder=builder, disk=disk,
        )
        assert summary["stuck"] == 1
        rows = await db.list_pending_sync(acct.id)
        assert rows[0]["error_code"] == "missing"


# ── Empty queue is a no-op ─────────────────────────────────────────


class TestNoOp:
    async def test_empty_queue_returns_zero_summary(self, db, isolated_disk):
        # No queue rows → worker should not even call drive_builder.
        called = {"count": 0}

        async def builder(_a, _b):
            called["count"] += 1
            return StubDrive()

        disk = DiskObjectStore()
        summary = await sync_worker.sync_pending_storage(
            db=db, drive_builder=builder, disk=disk,
        )
        assert summary == {"succeeded": 0, "failed": 0, "stuck": 0, "accounts": 0}
        assert called["count"] == 0
