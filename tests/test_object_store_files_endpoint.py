"""Phase 6 tests — storage dashboard endpoints.

The frontend file-manager (StorageFileTable) consumes these three:

    GET  /storage/files?only=…
    POST /storage/files/{queue_id}/retry
    POST /storage/files/retry-stuck

We test the underlying helpers + the route bodies (direct call,
no FastAPI stack-up needed) so the tight loop runs fast.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from fastapi import HTTPException

from adapters.storage import Role
from adapters.storage.object_store_sync import ERR_TOKEN_EXPIRED, ERR_TRANSIENT
from capabilities.object_store.router import (
    list_storage_files, retry_storage_file, retry_all_stuck_files,
)


@pytest.fixture
async def db(pg_db):
    yield pg_db


# ── list_storage_queue helper (the data contract) ──────────────────


class TestListStorageQueue:
    async def test_enriches_pti_media_rows_with_inspection(self, db):
        # Queue rows linked to PTI media must surface the
        # inspection_id + vehicle_name via the join — the file table
        # needs that context for "Vehicle 42 · Insp #N".
        acct = await db.create_account("Acme")
        driver = await db.create_user(90001, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="42", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        mid = await db.attach_inspection_media(
            ins_id, media_type="photo",
            file_path="p", file_name="front.jpg", file_size=2048,
            content_type="image/jpeg",
            uploaded_by=driver.telegram_id,
        )
        await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=mid,
            bucket="b", filename="front.jpg",
            local_path="p", file_size=2048,
        )

        rows = await db.list_storage_queue(acct.id)
        assert len(rows) == 1
        r = rows[0]
        assert r["filename"] == "front.jpg"
        assert int(r["file_size"]) == 2048
        assert r["media_type"] == "photo"
        assert int(r["inspection_id"]) == ins_id
        assert r["vehicle_name"] == "42"

    async def test_filter_only_pending_excludes_stuck(self, db):
        acct = await db.create_account("Acme")
        # One stuck + one pending.
        q_stuck = await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="stuck.jpg", local_path="p", file_size=10,
        )
        await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=2,
            bucket="b", filename="pending.jpg", local_path="p", file_size=10,
        )
        await db.mark_sync_stuck(
            q_stuck, error="invalid_grant", error_code=ERR_TOKEN_EXPIRED,
        )

        pending = await db.list_storage_queue(acct.id, only="pending")
        names = [r["filename"] for r in pending]
        assert names == ["pending.jpg"]

        stuck = await db.list_storage_queue(acct.id, only="stuck")
        names = [r["filename"] for r in stuck]
        assert names == ["stuck.jpg"]

        all_rows = await db.list_storage_queue(acct.id, only="all")
        assert len(all_rows) == 2

    async def test_tenant_isolation(self, db):
        a = await db.create_account("Acme A")
        b = await db.create_account("Acme B")
        await db.enqueue_storage_sync(
            account_id=a.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="a.jpg", local_path="p", file_size=1,
        )
        await db.enqueue_storage_sync(
            account_id=b.id, entity_type="pti_media", entity_id=2,
            bucket="b", filename="b.jpg", local_path="p", file_size=1,
        )
        # Each side sees only its own row.
        rows_a = await db.list_storage_queue(a.id)
        rows_b = await db.list_storage_queue(b.id)
        assert [r["filename"] for r in rows_a] == ["a.jpg"]
        assert [r["filename"] for r in rows_b] == ["b.jpg"]


# ── retry_all_stuck_sync ───────────────────────────────────────────


class TestBulkRetry:
    async def test_un_sticks_stuck_rows_only(self, db):
        acct = await db.create_account("Acme")
        # Two stuck + one transient-failed.
        q1 = await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="s1.jpg", local_path="p", file_size=1,
        )
        q2 = await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=2,
            bucket="b", filename="s2.jpg", local_path="p", file_size=1,
        )
        q3 = await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=3,
            bucket="b", filename="t1.jpg", local_path="p", file_size=1,
        )
        await db.mark_sync_stuck(q1, error="invalid_grant", error_code=ERR_TOKEN_EXPIRED)
        await db.mark_sync_stuck(q2, error="invalid_grant", error_code=ERR_TOKEN_EXPIRED)
        await db.mark_sync_failed(q3, error="503", error_code=ERR_TRANSIENT)

        count = await db.retry_all_stuck_sync(acct.id)
        assert count == 2

        # Stuck rows now have error_code cleared.
        stuck_after = await db.list_storage_queue(acct.id, only="stuck")
        assert stuck_after == []
        # The transient row is untouched.
        rows = await db.list_storage_queue(acct.id, only="all")
        t = next(r for r in rows if r["filename"] == "t1.jpg")
        assert t["error_code"] == ERR_TRANSIENT


# ── Endpoint shape ─────────────────────────────────────────────────


class TestListEndpoint:
    async def test_projects_into_client_contract(self, db):
        acct = await db.create_account("Acme")
        driver = await db.create_user(90101, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="42", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        mid = await db.attach_inspection_media(
            ins_id, media_type="photo",
            file_path="p", file_name="x.jpg", file_size=999,
            content_type="image/jpeg",
            uploaded_by=driver.telegram_id,
        )
        qid = await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=mid,
            bucket="b", filename="x.jpg", local_path="p", file_size=999,
        )
        await db.mark_sync_stuck(qid, error="invalid_grant", error_code=ERR_TOKEN_EXPIRED)

        body = await list_storage_files(
            only="all", limit=50,
            user={"account_id": acct.id, "sub": driver.telegram_id},
            tenant_db=db,
        )
        assert body["count"] == 1
        r = body["items"][0]
        # Stable contract the dashboard table renders.
        for k in (
            "queue_id", "entity_type", "entity_id", "filename", "file_size",
            "media_type", "content_type",
            "inspection_id", "vehicle_name",
            "attempts", "next_attempt_at", "last_error", "error_code",
            "is_stuck", "enqueued_at", "updated_at",
        ):
            assert k in r, f"endpoint missing key {k}"
        assert r["is_stuck"] is True
        assert r["error_code"] == ERR_TOKEN_EXPIRED
        assert r["vehicle_name"] == "42"

    async def test_invalid_filter_422(self, db):
        acct = await db.create_account("Acme")
        with pytest.raises(HTTPException) as exc:
            await list_storage_files(
                only="bogus", limit=10,
                user={"account_id": acct.id, "sub": 1}, tenant_db=db,
            )
        assert exc.value.status_code == 422


# ── Retry endpoints ────────────────────────────────────────────────


class TestRetryEndpoints:
    async def test_retry_re_enters_queue(self, db):
        acct = await db.create_account("Acme")
        qid = await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="x.jpg", local_path="p", file_size=1,
        )
        await db.mark_sync_stuck(qid, error="invalid_grant", error_code=ERR_TOKEN_EXPIRED)
        body = await retry_storage_file(
            qid, user={"account_id": acct.id, "sub": 1}, tenant_db=db,
        )
        assert body == {"retried": True}
        # Row no longer stuck.
        rows = await db.list_storage_queue(acct.id, only="stuck")
        assert rows == []

    async def test_retry_refuses_other_account_rows(self, db):
        # Defence-in-depth: queue_ids from another account must 404,
        # not silently succeed and mutate someone else's row.
        a = await db.create_account("Acme A")
        b = await db.create_account("Acme B")
        qid = await db.enqueue_storage_sync(
            account_id=b.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="x.jpg", local_path="p", file_size=1,
        )
        with pytest.raises(HTTPException) as exc:
            await retry_storage_file(
                qid, user={"account_id": a.id, "sub": 1}, tenant_db=db,
            )
        assert exc.value.status_code == 404

    async def test_bulk_retry_returns_count(self, db):
        acct = await db.create_account("Acme")
        for i in range(1, 4):
            qid = await db.enqueue_storage_sync(
                account_id=acct.id, entity_type="pti_media", entity_id=i,
                bucket="b", filename=f"{i}.jpg", local_path="p", file_size=1,
            )
            await db.mark_sync_stuck(qid, error="x", error_code=ERR_TOKEN_EXPIRED)
        body = await retry_all_stuck_files(
            user={"account_id": acct.id, "sub": 1}, tenant_db=db,
        )
        assert body == {"retried": 3}
