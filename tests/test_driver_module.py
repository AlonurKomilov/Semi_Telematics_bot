"""Driver Module — profile + vehicle-assignment + document store tests."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Database, Role
from adapters.storage.drivers import VALID_DOC_TYPES


@pytest.fixture
async def db(pg_db):
    yield pg_db


class TestDriverProfile:
    """Profile fields live on the ``users`` table — drivers stay a
    special role, not a separate entity."""

    async def test_get_profile_returns_none_for_unknown_user(self, db):
        prof = await db.get_driver_profile(99999)
        assert prof is None

    async def test_update_and_read_back_cdl_info(self, db):
        acct = await db.create_account("Test Co")
        user = await db.create_user(101, acct.id, role=Role.DRIVER)
        updated = await db.update_driver_profile(
            user.id,
            cdl_number="DL-12345",
            cdl_state="CA",
            cdl_class="A",
            cdl_expires="2027-01-15",
            med_card_expires="2026-08-01",
            hire_date="2024-03-01",
            phone="+1-555-0000",
        )
        assert updated is True

        prof = await db.get_driver_profile(user.id)
        assert prof is not None
        assert prof.cdl_number == "DL-12345"
        assert prof.cdl_state == "CA"
        assert prof.cdl_class == "A"
        assert prof.cdl_expires == "2027-01-15"
        assert prof.med_card_expires == "2026-08-01"
        assert prof.hire_date == "2024-03-01"
        assert prof.phone == "+1-555-0000"

    async def test_update_ignores_unknown_fields(self, db):
        """Defensive — API callers can't poke unrelated user columns
        (password_hash, email) via the driver-update path."""
        acct = await db.create_account("Test")
        user = await db.create_user(102, acct.id, role=Role.DRIVER)
        # ``password_hash`` is NOT in the allow-list
        updated = await db.update_driver_profile(
            user.id,
            cdl_number="X",
            password_hash="HIJACKED",  # should be silently dropped
        )
        assert updated is True
        # Verify the password wasn't touched.  We call execute() then
        # iterate the cursor directly rather than use it as an async
        # context manager — the SQLite cursor supported __aenter__ but
        # the PG cursor shim doesn't, so the explicit two-step works
        # against both back-ends.
        cur = await db._db.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user.id,),
        )
        row = await cur.fetchone()
        assert row[0] != "HIJACKED"

    async def test_list_drivers_excludes_terminated_by_default(self, db):
        acct = await db.create_account("X")
        active = await db.create_user(201, acct.id, role=Role.DRIVER)
        terminated = await db.create_user(202, acct.id, role=Role.DRIVER)
        await db.update_driver_profile(
            terminated.id, termination_date="2025-01-01"
        )
        names = [d.user_id for d in await db.list_drivers(acct.id)]
        assert active.id in names
        assert terminated.id not in names
        # ``include_terminated=True`` returns both
        all_names = [
            d.user_id for d in await db.list_drivers(acct.id, include_terminated=True)
        ]
        assert terminated.id in all_names


class TestVehicleAssignments:
    """``driver_vehicle_assignments`` is the new single source of truth
    for who drives what.  Replaces ``driver_trucks`` (kept as legacy
    alias one release; we still write through to ``users.truck_num``
    so the alert pipeline driver-filter stays in sync)."""

    async def test_assign_creates_active_row_and_syncs_truck_num(self, db):
        acct = await db.create_account("X")
        user = await db.create_user(301, acct.id, role=Role.DRIVER)

        assignment = await db.assign_vehicle_to_driver(
            account_id=acct.id, user_id=user.id,
            vehicle_name="107", is_primary=True,
            assigned_by=0,
        )
        assert assignment.is_primary is True
        assert assignment.unassigned_at is None

        # ``users.truck_num`` is the denormalized cache for legacy readers.
        cur = await db._db.execute(
            "SELECT truck_num FROM users WHERE id = ?", (user.id,),
        )
        row = await cur.fetchone()
        assert row[0] == "107"

    async def test_promoting_new_primary_demotes_old(self, db):
        acct = await db.create_account("X")
        user = await db.create_user(302, acct.id, role=Role.DRIVER)
        await db.assign_vehicle_to_driver(
            account_id=acct.id, user_id=user.id,
            vehicle_name="100", is_primary=True,
        )
        await db.assign_vehicle_to_driver(
            account_id=acct.id, user_id=user.id,
            vehicle_name="200", is_primary=True,
        )
        actives = await db.list_active_assignments(user.id)
        primaries = [a for a in actives if a.is_primary]
        assert len(primaries) == 1
        assert primaries[0].vehicle_name == "200"

    async def test_unassign_preserves_history(self, db):
        acct = await db.create_account("X")
        user = await db.create_user(303, acct.id, role=Role.DRIVER)
        a = await db.assign_vehicle_to_driver(
            account_id=acct.id, user_id=user.id,
            vehicle_name="107", is_primary=True,
        )
        ok = await db.end_vehicle_assignment(a.id)
        assert ok is True

        assert await db.list_active_assignments(user.id) == []
        history = await db.list_assignment_history(user.id)
        assert len(history) == 1
        assert history[0].unassigned_at is not None
        # Cache cleared — driver has no truck assigned anymore.
        cur = await db._db.execute(
            "SELECT truck_num FROM users WHERE id = ?", (user.id,),
        )
        assert (await cur.fetchone())[0] is None


class TestDriverDocuments:
    """Per-driver document store backed by the existing ``ObjectStorage``.
    Tests use the in-memory DB only — no real file upload here; the
    upload pipeline integration test lives in the next phase."""

    async def test_add_and_list_document(self, db):
        acct = await db.create_account("X")
        user = await db.create_user(401, acct.id, role=Role.DRIVER)

        doc = await db.add_document(
            account_id=acct.id, user_id=user.id,
            doc_type="cdl",
            bucket="driver_documents",
            object_key=f"acct_{acct.id}/user_{user.id}/cdl_xxx.pdf",
            file_name="CDL_2027.pdf",
            file_size=128_000,
            mime_type="application/pdf",
            expires_at="2027-01-15",
            uploaded_by=0,
        )
        assert doc.id > 0
        assert doc.status == "active"

        docs = await db.list_documents(user.id)
        assert len(docs) == 1
        assert docs[0].doc_type == "cdl"

    async def test_invalid_doc_type_rejected(self, db):
        acct = await db.create_account("X")
        user = await db.create_user(402, acct.id, role=Role.DRIVER)
        with pytest.raises(ValueError, match="Unknown doc_type"):
            await db.add_document(
                account_id=acct.id, user_id=user.id,
                doc_type="not_a_real_type",
                bucket="driver_documents",
                object_key="x", file_name="x.pdf",
            )

    async def test_delete_decrements_storage_usage(self, db):
        acct = await db.create_account("X")
        user = await db.create_user(403, acct.id, role=Role.DRIVER)
        doc = await db.add_document(
            account_id=acct.id, user_id=user.id,
            doc_type="cdl",
            bucket="driver_documents",
            object_key="k", file_name="f.pdf", file_size=10_000,
        )
        used_before, _ = await db.get_storage_usage(acct.id)
        assert used_before == 10_000
        await db.delete_document(doc.id)
        used_after, _ = await db.get_storage_usage(acct.id)
        assert used_after == 0

    async def test_get_expiring_documents(self, db):
        import datetime
        acct = await db.create_account("X")
        user = await db.create_user(404, acct.id, role=Role.DRIVER)
        today = datetime.date.today()
        # Doc expiring in 15 days — should be in the 30-day window.
        soon = (today + datetime.timedelta(days=15)).isoformat()
        far = (today + datetime.timedelta(days=180)).isoformat()
        await db.add_document(
            account_id=acct.id, user_id=user.id, doc_type="cdl",
            bucket="b", object_key="k1", file_name="cdl.pdf",
            expires_at=soon,
        )
        await db.add_document(
            account_id=acct.id, user_id=user.id, doc_type="medical_card",
            bucket="b", object_key="k2", file_name="med.pdf",
            expires_at=far,
        )
        expiring = await db.get_expiring_documents(acct.id, within_days=30)
        assert len(expiring) == 1
        assert expiring[0].doc_type == "cdl"


class TestStorageQuota:
    """Local-disk-fallback quota.  Drive-connected accounts skip this
    check (their Drive enforces its own quota)."""

    async def test_quota_defaults_to_500mb(self, db):
        acct = await db.create_account("X")
        used, quota = await db.get_storage_usage(acct.id)
        assert used == 0
        assert quota == 500 * 1024 * 1024  # 500 MB

    async def test_enforce_raises_when_over_cap(self, db):
        acct = await db.create_account("X")
        await db.set_storage_quota(acct.id, 1000)  # tight 1KB cap
        # First upload of 600 bytes succeeds
        await db.enforce_storage_quota(acct.id, 600)
        # Pretend we used it
        async with db.transaction():
            await db._db.execute(
                "UPDATE accounts SET storage_used_bytes = ? WHERE id = ?",
                (600, acct.id),
            )
        # Next upload of 500 bytes would push us over → raises
        with pytest.raises(db.StorageQuotaExceeded):
            await db.enforce_storage_quota(acct.id, 500)

    async def test_doc_type_whitelist_matches_constant(self, db):
        """Sanity — the allow-list is what we documented in the plan."""
        assert "cdl" in VALID_DOC_TYPES
        assert "medical_card" in VALID_DOC_TYPES
        assert "mvr" in VALID_DOC_TYPES
        assert "drug_screen" in VALID_DOC_TYPES
        assert "background_check" in VALID_DOC_TYPES
        assert "training_cert" in VALID_DOC_TYPES
        assert "other" in VALID_DOC_TYPES
        assert "random_type" not in VALID_DOC_TYPES
