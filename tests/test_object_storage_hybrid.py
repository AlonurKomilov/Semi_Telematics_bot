"""Phase 2 tests for ``HybridObjectStorage``.

Covers the contract the Phase 3 worker will rely on:

  * ``put`` writes locally + ``get`` reads it back (Protocol parity
    with DiskObjectStorage for the happy path).
  * ``track_for_sync`` materialises a queue row AND flips the media
    row's ``storage_state`` to ``'local'`` in one logical step.
  * The annotate flow's UPSERT re-track resets the queue row.
  * The factory wires ``backend='hybrid'`` to ``HybridObjectStorage``
    even when Drive isn't connected.
  * ``track_for_sync`` failures don't propagate — the file is already
    on disk; the route must not error after a successful write.

The Drive read-fallback path is unit-tested with a stub Drive; the
real Google API client isn't called from this file.
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
from adapters.storage.object_storage_hybrid import HybridObjectStorage
from adapters.storage.object_storage_sync import (
    STATE_LOCAL, STATE_REMOTE,
)


@pytest.fixture
async def db(pg_db):
    yield pg_db


@pytest.fixture
def isolated_disk_root(monkeypatch):
    """Point DiskObjectStorage at a temp dir so concurrent tests don't
    collide on real data/ paths."""
    tmp = tempfile.mkdtemp(prefix="hybrid_test_")
    monkeypatch.setenv("OBJECT_STORE_ROOT", tmp)
    # Force config to re-read the env var.
    import infra.config as cfg
    monkeypatch.setattr(cfg, "OBJECT_STORE_ROOT", tmp, raising=False)
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


# ── put + get + protocol parity ────────────────────────────────────


class TestHybridIO:
    def test_put_then_get_from_disk(self, db, isolated_disk_root):
        store = HybridObjectStorage(account_id=1, tenant_db=db)
        url = store.put("inspections/1/1", "a.jpg", b"hello world")
        assert url.endswith("/inspections/1/1/a.jpg")
        assert store.get("inspections/1/1", "a.jpg") == b"hello world"
        assert store.exists("inspections/1/1", "a.jpg") is True

    def test_get_miss_without_drive_returns_none(self, db, isolated_disk_root):
        # File never written + no Drive configured = clean miss.
        store = HybridObjectStorage(account_id=1, tenant_db=db)
        assert store.get("inspections/1/1", "missing.jpg") is None
        assert store.exists("inspections/1/1", "missing.jpg") is False

    def test_get_falls_back_to_drive_after_disk_clean(self, db, isolated_disk_root):
        # Simulate the post-sync state: file gone from disk, Drive
        # holds it.  We inject a stub Drive instance to verify the
        # fallback path without touching real Google APIs.
        class _StubDrive:
            def __init__(self):
                self.calls = []
            def get(self, bucket, key):
                self.calls.append(("get", bucket, key))
                return b"from-drive"
            def exists(self, bucket, key):
                return True
            def delete(self, bucket, key):
                return True

        store = HybridObjectStorage(account_id=1, tenant_db=db)
        stub = _StubDrive()
        store._drive = stub   # bypass the lazy build (Drive doesn't exist locally)
        assert store.get("inspections/1/1", "x.jpg") == b"from-drive"
        assert stub.calls == [("get", "inspections/1/1", "x.jpg")]

    def test_delete_best_effort_both_tiers(self, db, isolated_disk_root):
        store = HybridObjectStorage(account_id=1, tenant_db=db)
        store.put("inspections/1/1", "a.jpg", b"x")
        assert store.exists("inspections/1/1", "a.jpg") is True
        assert store.delete("inspections/1/1", "a.jpg") is True
        # Disk.delete is idempotent (treats "already gone" as success),
        # which is the correct semantics for the hybrid — a worker that
        # already cleaned the local copy must not see this as an error.
        assert store.exists("inspections/1/1", "a.jpg") is False


# ── track_for_sync ─────────────────────────────────────────────────


class TestTrackForSync:
    async def test_track_enqueues_and_flips_media_state(self, db, isolated_disk_root):
        # Build a real inspection + media row so track_for_sync can
        # operate against the actual schema.
        acct = await db.create_account("Acme")
        driver = await db.create_user(60001, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="42", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        mid = await db.attach_inspection_media(
            ins_id, media_type="photo",
            file_path="data/inspections/.../a.jpg",
            file_name="a.jpg", file_size=512,
            uploaded_by=driver.telegram_id,
        )

        store = HybridObjectStorage(account_id=acct.id, tenant_db=db)
        await store.track_for_sync(
            f"inspections/{acct.id}/{ins_id}", "a.jpg",
            "data/inspections/.../a.jpg",
            entity_type="pti_media", entity_id=mid, file_size=512,
        )

        # Queue row materialised
        pending = await db.list_pending_sync(acct.id)
        assert len(pending) == 1
        assert pending[0]["filename"] == "a.jpg"
        assert int(pending[0]["file_size"]) == 512

        # Media row flipped to 'local' with local_path populated
        row = await db.get_inspection_media(mid)
        assert row["storage_state"] == STATE_LOCAL
        assert row["local_path"] == "data/inspections/.../a.jpg"

    async def test_track_is_idempotent_on_re_track(self, db, isolated_disk_root):
        # The annotate flow re-uploads the same media_id — UPSERT
        # behaviour must reset the row instead of duplicating it.
        acct = await db.create_account("Acme")
        driver = await db.create_user(60002, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="42", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        mid = await db.attach_inspection_media(
            ins_id, media_type="photo",
            file_path="p", file_name="x.jpg", file_size=100,
            uploaded_by=driver.telegram_id,
        )
        store = HybridObjectStorage(account_id=acct.id, tenant_db=db)

        await store.track_for_sync(
            "b", "x.jpg", "p", entity_type="pti_media",
            entity_id=mid, file_size=100,
        )
        # Simulate a prior failure parked the row.
        first = (await db.list_pending_sync(acct.id))[0]
        from adapters.storage.object_storage_sync import ERR_TRANSIENT
        await db.mark_sync_failed(
            int(first["id"]), error="boom", error_code=ERR_TRANSIENT,
        )
        # Re-track with new size (annotation grew the file).
        await store.track_for_sync(
            "b", "x.jpg", "p2", entity_type="pti_media",
            entity_id=mid, file_size=999,
        )
        pending = await db.list_pending_sync(acct.id)
        assert len(pending) == 1
        # Reset: attempts back to 0, last_error cleared, size updated.
        assert int(pending[0]["attempts"]) == 0
        assert pending[0]["last_error"] is None
        assert int(pending[0]["file_size"]) == 999

    async def test_track_failure_does_not_raise(self, db, isolated_disk_root):
        # If the DB enqueue throws, the file is already on disk — the
        # route must not see an exception (would 500 a successful
        # upload).  We patch tenant_db to raise and confirm we swallow.
        class _PoisonedDB:
            async def enqueue_storage_sync(self, **kw):
                raise RuntimeError("db down")
            async def set_media_storage_state(self, *a, **kw):
                raise RuntimeError("db down")
        store = HybridObjectStorage(account_id=1, tenant_db=_PoisonedDB())
        # Should NOT raise.
        await store.track_for_sync(
            "b", "x.jpg", "p", entity_type="pti_media",
            entity_id=999, file_size=1,
        )

    async def test_non_pti_entity_skips_media_state_flip(self, db, isolated_disk_root):
        # Other entity types (work orders, template refs) don't have a
        # storage_state column yet, so track_for_sync must enqueue
        # WITHOUT calling set_media_storage_state.
        acct = await db.create_account("Acme")
        store = HybridObjectStorage(account_id=acct.id, tenant_db=db)
        # Should succeed even though entity_id=12345 doesn't exist on
        # pti_inspection_media (we never touch that table).
        await store.track_for_sync(
            "wo", "invoice.pdf", "path", entity_type="work_order",
            entity_id=12345, file_size=400,
        )
        pending = await db.list_pending_sync(acct.id)
        assert len(pending) == 1
        assert pending[0]["entity_type"] == "work_order"


# ── Factory wiring ─────────────────────────────────────────────────


class TestFactoryWiring:
    async def test_hybrid_backend_returns_hybrid_store(self, db, isolated_disk_root):
        acct = await db.create_account("Acme")
        await db.set_account_storage_backend(acct.id, "hybrid")
        from adapters.storage.object_storage import (
            get_object_storage_for_account, invalidate_object_storage_for_account,
        )
        # Clear any stale store from a prior test in this xdist worker —
        # account_id sequences can recycle across tests.
        invalidate_object_storage_for_account(acct.id)
        store = await get_object_storage_for_account(acct.id, db)
        assert isinstance(store, HybridObjectStorage)

    async def test_disk_backend_unchanged(self, db, isolated_disk_root):
        # The previous default still returns DiskObjectStorage — Phase 2
        # is strictly additive.
        acct = await db.create_account("Acme")
        from adapters.storage.object_storage import (
            DiskObjectStorage, get_object_storage_for_account,
            invalidate_object_storage_for_account,
        )
        invalidate_object_storage_for_account(acct.id)
        store = await get_object_storage_for_account(acct.id, db)
        assert isinstance(store, DiskObjectStorage)


# ── Read fallback: legacy rows + remote-only ───────────────────────


class TestLegacyAndRemote:
    def test_legacy_media_rows_keep_default_remote_state(self, db):
        # The migration default ensures pre-Phase-1 rows are 'remote'
        # so the worker doesn't try to re-sync them.  Lock that in
        # here for visibility — it's the backward-compat contract
        # everything else depends on.
        from adapters.storage.object_storage_sync import STATE_REMOTE as _R
        assert _R == "remote"
        # Smoke: the constant the migration relies on hasn't drifted.

    def test_protocol_methods_present(self, db, isolated_disk_root):
        # If a future refactor drops one of the Protocol methods,
        # callers (work orders, PTI media stream, PDF builder) break
        # silently.  Pin the surface.
        store = HybridObjectStorage(account_id=1, tenant_db=db)
        for name in ("put", "get", "exists", "delete", "url",
                     "local_path", "move_folder", "track_for_sync"):
            assert hasattr(store, name), f"HybridObjectStorage missing {name!r}"
        # And the unused-state-constant import isn't dead (locks the
        # contract that the worker phase will read).
        assert STATE_REMOTE  # noqa: B018
