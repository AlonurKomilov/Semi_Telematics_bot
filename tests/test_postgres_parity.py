"""Parity tests: every mixin method that this suite exercises against
SQLite should produce identical observable behaviour against
PostgreSQL.

Production cutover from SQLite to Postgres happened on 2026-05-08.
The SQL translator in ``adapters/storage/pg_adapter.py`` is the seam
where divergence is most likely to creep in (``?`` → ``$1``,
``ON CONFLICT(col)``, ``datetime('now')`` → ``NOW()``, etc.), so we
run a parity sweep against a real PG instance via the ``pg_db``
fixture (testcontainers; see ``tests/conftest.py``).

These tests SKIP automatically when Docker is unavailable and
``POSTGRES_TEST_URL`` isn't set — opt in by exporting that URL in CI
or letting testcontainers spin up an ephemeral container locally.
"""

from __future__ import annotations

from adapters.storage import Database, Role


class TestAccountCompanyParity:
    """Core tenancy CRUD: account → companies → users → user_companies."""

    async def test_account_create_and_read(self, pg_db: Database):
        acct = await pg_db.create_account("PG Test Account")
        fetched = await pg_db.get_account(acct.id)
        assert fetched is not None
        assert fetched.name == "PG Test Account"
        assert fetched.id == acct.id

    async def test_company_unique_per_account(self, pg_db: Database):
        a = await pg_db.create_account("Acct A")
        b = await pg_db.create_account("Acct B")
        # Same code can live under TWO different accounts (UNIQUE is
        # (account_id, code), not code alone).
        await pg_db.add_company(a.id, "ACME", "key_a", "ACME Hauling")
        await pg_db.add_company(b.id, "ACME", "key_b", "ACME Express")
        a_cos = await pg_db.get_account_companies(a.id)
        b_cos = await pg_db.get_account_companies(b.id)
        assert len(a_cos) == 1
        assert len(b_cos) == 1
        assert a_cos[0].samsara_api_key != b_cos[0].samsara_api_key

    async def test_user_companies_junction(self, pg_db: Database):
        acct = await pg_db.create_account("Junction Test")
        await pg_db.add_company(acct.id, "CO1", "k1", "Company One")
        await pg_db.add_company(acct.id, "CO2", "k2", "Company Two")
        co1 = await pg_db.get_company_by_code(acct.id, "CO1")
        co2 = await pg_db.get_company_by_code(acct.id, "CO2")
        u = await pg_db.create_user(
            telegram_id=900001, account_id=acct.id, role=Role.FLEET,
        )

        await pg_db.assign_user_company(u.id, acct.id, co1.id)
        await pg_db.assign_user_company(u.id, acct.id, co2.id)
        assigns = await pg_db.get_user_companies(u.id)
        codes = sorted(a.company_code for a in assigns)
        assert codes == ["CO1", "CO2"]

        await pg_db.unassign_user_company(u.id, co1.id)
        assigns = await pg_db.get_user_companies(u.id)
        assert [a.company_code for a in assigns] == ["CO2"]


class TestAccountSettingsParity:
    """The exact suite from ``test_account_settings_roundtrip``, but
    run against Postgres so the upsert translator is exercised.  We
    inline rather than re-import so the parity lane is self-contained
    even if the SQLite test changes."""

    async def test_upsert_via_on_conflict(self, pg_db: Database):
        acct = await pg_db.create_account("Settings PG")
        await pg_db.set_account_setting(acct.id, "tz", "UTC")
        await pg_db.set_account_setting(acct.id, "tz", "Asia/Tashkent")
        assert await pg_db.get_account_setting(acct.id, "tz") == "Asia/Tashkent"

    async def test_drive_oauth_keys_roundtrip(self, pg_db: Database):
        from adapters.storage.object_store import (
            OBJECT_STORE_BACKEND_KEY,
            OBJECT_STORE_GDRIVE_REFRESH_TOKEN,
            OBJECT_STORE_GDRIVE_ROOT_FOLDER_ID,
            OBJECT_STORE_GDRIVE_USER_EMAIL,
        )
        acct = await pg_db.create_account("OAuth PG")
        a = acct.id
        await pg_db.set_account_setting(a, OBJECT_STORE_BACKEND_KEY, "gdrive")
        await pg_db.set_account_setting(a, OBJECT_STORE_GDRIVE_REFRESH_TOKEN, "tok-blob")
        await pg_db.set_account_setting(a, OBJECT_STORE_GDRIVE_ROOT_FOLDER_ID, "1Folder")
        await pg_db.set_account_setting(a, OBJECT_STORE_GDRIVE_USER_EMAIL, "u@x.com")

        assert await pg_db.get_account_setting(a, OBJECT_STORE_BACKEND_KEY) == "gdrive"
        assert await pg_db.get_account_setting(a, OBJECT_STORE_GDRIVE_REFRESH_TOKEN) == "tok-blob"
        assert await pg_db.get_account_setting(a, OBJECT_STORE_GDRIVE_ROOT_FOLDER_ID) == "1Folder"
        assert await pg_db.get_account_setting(a, OBJECT_STORE_GDRIVE_USER_EMAIL) == "u@x.com"


class TestDriverDocumentBucketParity:
    """The new ``move_user_documents_bucket`` method must produce the
    same row update on Postgres that it does on SQLite — backstops the
    driver-company-change archive flow."""

    async def test_move_bucket_updates_only_matching_rows(self, pg_db: Database):
        acct = await pg_db.create_account("Move Bucket PG")
        u = await pg_db.create_user(
            telegram_id=900002, account_id=acct.id, role=Role.DRIVER,
        )
        # Two docs in the same bucket — both should be re-pathed.
        await pg_db.add_document(
            account_id=acct.id, user_id=u.id, doc_type="cdl",
            bucket="ACME TRUCKING/drivers/user-1",
            object_key="cdl_a.pdf",
            drive_file_id=None, file_name="cdl_a.pdf", file_size=100,
        )
        await pg_db.add_document(
            account_id=acct.id, user_id=u.id, doc_type="medical_card",
            bucket="ACME TRUCKING/drivers/user-1",
            object_key="med_a.pdf",
            drive_file_id=None, file_name="med_a.pdf", file_size=200,
        )
        # A third doc under a DIFFERENT bucket — must NOT be touched
        await pg_db.add_document(
            account_id=acct.id, user_id=u.id, doc_type="mvr",
            bucket="OTHER/drivers/user-1",
            object_key="mvr_a.pdf",
            drive_file_id=None, file_name="mvr_a.pdf", file_size=50,
        )

        moved = await pg_db.move_user_documents_bucket(
            u.id,
            "ACME TRUCKING/drivers/user-1",
            "ACME TRUCKING/drivers/_archive/2026-05-15/user-1",
        )
        assert moved == 2

        docs = await pg_db.list_documents(u.id)
        archive_count = sum(1 for d in docs if "_archive" in d.bucket)
        other_count = sum(1 for d in docs if d.bucket == "OTHER/drivers/user-1")
        assert archive_count == 2
        assert other_count == 1
