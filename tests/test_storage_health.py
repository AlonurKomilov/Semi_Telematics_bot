"""Phase 5 tests: storage-health observability surface.

Covers the new ``count_media_by_state`` aggregator + the
``GET /api/storage/health`` endpoint shape.  We exercise the helper
directly (cheaper) and the endpoint shape via a thin direct call to
the route function so we don't have to spin up the full FastAPI stack.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from adapters.storage import Role
from adapters.storage.object_store import (
    STORAGE_BACKEND_KEY, STORAGE_GDRIVE_REFRESH_TOKEN, STORAGE_GDRIVE_USER_EMAIL,
)
from adapters.storage.storage_sync import (
    STATE_LOCAL, STATE_REMOTE, STATE_STUCK, STATE_SYNCING,
)
from capabilities.object_store.router import get_storage_health


@pytest.fixture
async def db(pg_db):
    yield pg_db


# ── count_media_by_state ───────────────────────────────────────────


class TestMediaStateCount:
    async def test_zero_when_no_media(self, db):
        acct = await db.create_account("Acme")
        counts = await db.count_media_by_state(acct.id)
        assert counts == {STATE_LOCAL: 0, STATE_SYNCING: 0, STATE_REMOTE: 0, STATE_STUCK: 0}

    async def test_counts_per_state(self, db):
        acct = await db.create_account("Acme")
        driver = await db.create_user(80001, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="42", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        # 2 local, 1 remote, 1 stuck.
        states = [STATE_LOCAL, STATE_LOCAL, STATE_REMOTE, STATE_STUCK]
        for i, s in enumerate(states, 1):
            mid = await db.attach_inspection_media(
                ins_id, media_type="photo",
                file_path=f"p{i}", file_name=f"x{i}.jpg",
                file_size=100, uploaded_by=driver.telegram_id,
            )
            await db.set_media_storage_state(mid, state=s)
        counts = await db.count_media_by_state(acct.id)
        assert counts[STATE_LOCAL]  == 2
        assert counts[STATE_REMOTE] == 1
        assert counts[STATE_STUCK]  == 1
        assert counts[STATE_SYNCING] == 0

    async def test_tenant_isolation(self, db):
        # An over-counted media for account B must NOT leak into
        # account A's totals.  Locks down the JOIN scoping.
        a = await db.create_account("Acme A")
        b = await db.create_account("Acme B")
        d_a = await db.create_user(80101, a.id, role=Role.DRIVER)
        d_b = await db.create_user(80102, b.id, role=Role.DRIVER)
        tpl_a = await db.get_active_template(a.id, "truck")
        tpl_b = await db.get_active_template(b.id, "truck")
        ins_a = await db.create_inspection(
            account_id=a.id, user_id=d_a.id,
            vehicle_name="A1", template_id=int(tpl_a["id"]),
            template_version=int(tpl_a["version"]),
        )
        ins_b = await db.create_inspection(
            account_id=b.id, user_id=d_b.id,
            vehicle_name="B1", template_id=int(tpl_b["id"]),
            template_version=int(tpl_b["version"]),
        )
        # 3 local rows for B, 1 stuck for A — must not bleed.
        for _ in range(3):
            mid = await db.attach_inspection_media(
                ins_b, media_type="photo", file_path="p",
                file_name="b.jpg", file_size=10,
                uploaded_by=d_b.telegram_id,
            )
            await db.set_media_storage_state(mid, state=STATE_LOCAL)
        mid = await db.attach_inspection_media(
            ins_a, media_type="photo", file_path="p",
            file_name="a.jpg", file_size=10,
            uploaded_by=d_a.telegram_id,
        )
        await db.set_media_storage_state(mid, state=STATE_STUCK)

        a_counts = await db.count_media_by_state(a.id)
        b_counts = await db.count_media_by_state(b.id)
        assert a_counts[STATE_STUCK] == 1
        assert a_counts[STATE_LOCAL] == 0
        assert b_counts[STATE_LOCAL] == 3
        assert b_counts[STATE_STUCK] == 0


# ── /api/storage/health endpoint shape ─────────────────────────────


class TestStorageHealth:
    async def test_shape_disk_backend_no_drive(self, db):
        # Default disk account, no media, no quota override.
        acct = await db.create_account("Acme")
        body = await get_storage_health(
            user={"account_id": acct.id, "sub": 1},
            tenant_db=db,
        )
        # Stable contract — every key the dashboard renders.
        assert set(body.keys()) == {"backend", "drive", "quota", "queue", "media"}
        assert body["backend"] == "disk"
        assert body["drive"] == {"connected": False, "email": None}
        assert body["queue"] == {"pending": 0, "stuck": 0}
        assert body["media"] == {"local": 0, "syncing": 0, "remote": 0, "stuck": 0}
        assert body["quota"]["used_bytes"] == 0
        assert body["quota"]["quota_bytes"] > 0
        assert body["quota"]["percent"] == 0.0

    async def test_quota_percent_computed(self, db):
        acct = await db.create_account("Acme")
        await db.set_account_disk_quota(acct.id, 10_000)
        # 2,500 bytes pending in the queue → 25 % used.
        await db.enqueue_storage_sync(
            account_id=acct.id, entity_type="pti_media", entity_id=1,
            bucket="b", filename="x", local_path="p", file_size=2_500,
        )
        body = await get_storage_health(
            user={"account_id": acct.id, "sub": 1}, tenant_db=db,
        )
        assert body["quota"]["used_bytes"]  == 2_500
        assert body["quota"]["quota_bytes"] == 10_000
        assert body["quota"]["percent"]     == 25.0

    async def test_queue_pending_vs_stuck_split(self, db):
        acct = await db.create_account("Acme")
        # Enqueue 3 rows, then mark one stuck.
        qids = []
        for i in range(3):
            qid = await db.enqueue_storage_sync(
                account_id=acct.id, entity_type="pti_media", entity_id=i + 1,
                bucket="b", filename=f"x{i}", local_path="p", file_size=10,
            )
            qids.append(qid)
        from adapters.storage.storage_sync import ERR_TOKEN_EXPIRED
        await db.mark_sync_stuck(
            qids[0], error="invalid_grant", error_code=ERR_TOKEN_EXPIRED,
        )
        body = await get_storage_health(
            user={"account_id": acct.id, "sub": 1}, tenant_db=db,
        )
        assert body["queue"]["stuck"]   == 1
        assert body["queue"]["pending"] == 2

    async def test_drive_connected_state(self, db):
        acct = await db.create_account("Acme")
        # Pretend Drive is connected (refresh token + email present).
        # The route reads these settings only — it never actually
        # exchanges the token.
        await db.set_account_setting(acct.id, STORAGE_BACKEND_KEY, "gdrive")
        await db.set_account_setting(acct.id, STORAGE_GDRIVE_REFRESH_TOKEN, "encrypted-blob")
        await db.set_account_setting(acct.id, STORAGE_GDRIVE_USER_EMAIL, "adam@example.com")
        body = await get_storage_health(
            user={"account_id": acct.id, "sub": 1}, tenant_db=db,
        )
        assert body["backend"] == "gdrive"
        assert body["drive"] == {"connected": True, "email": "adam@example.com"}

    async def test_email_hidden_when_disconnected(self, db):
        # An account with a stale email setting but no refresh token
        # is NOT connected — email must not leak through.
        acct = await db.create_account("Acme")
        await db.set_account_setting(acct.id, STORAGE_GDRIVE_USER_EMAIL, "old@example.com")
        body = await get_storage_health(
            user={"account_id": acct.id, "sub": 1}, tenant_db=db,
        )
        assert body["drive"] == {"connected": False, "email": None}
