"""Round-trip tests for the ``account_settings`` table.

This is where the BYO Drive OAuth refresh token, root folder ID, and
user email live — every Drive read after a customer connects their
account goes through ``get_account_setting``.  The same mixin methods
back per-company alert prefs, AI feature flags, and the storage-
backend selector.  We exercise the upsert path explicitly so a
regression in the SQL translator (Postgres ``ON CONFLICT`` vs SQLite
``ON CONFLICT(... )``) shows up as a test failure rather than a
silent overwrite or duplicate row.
"""

from __future__ import annotations

import pytest_asyncio

from adapters.storage import Database, Role


@pytest_asyncio.fixture
async def seeded(db: Database):
    """One account, one user — enough for settings round-trip tests."""
    account = await db.create_account("Settings Test")
    user = await db.create_user(
        telegram_id=400001,
        account_id=account.id,
        role=Role.OWNER,
    )
    return {"db": db, "account": account, "user": user}


class TestAccountSettingsRoundtrip:
    async def test_default_returned_when_unset(self, seeded):
        d = seeded
        v = await d["db"].get_account_setting(d["account"].id, "nope", default="fallback")
        assert v == "fallback"

    async def test_empty_default_when_unset(self, seeded):
        d = seeded
        v = await d["db"].get_account_setting(d["account"].id, "missing")
        assert v == ""

    async def test_set_then_get(self, seeded):
        d = seeded
        await d["db"].set_account_setting(d["account"].id, "alerts_chat_id", "12345")
        v = await d["db"].get_account_setting(d["account"].id, "alerts_chat_id")
        assert v == "12345"

    async def test_upsert_overwrites_existing(self, seeded):
        """Second set() on the same key must REPLACE, not duplicate."""
        d = seeded
        await d["db"].set_account_setting(d["account"].id, "tz", "America/New_York")
        await d["db"].set_account_setting(d["account"].id, "tz", "America/Los_Angeles")
        v = await d["db"].get_account_setting(d["account"].id, "tz")
        assert v == "America/Los_Angeles"

    async def test_tenant_isolation_on_same_key(self, db: Database):
        """Two accounts can hold the same key with different values —
        the WHERE clause must scope by account_id."""
        a = await db.create_account("Acct A")
        b = await db.create_account("Acct B")
        await db.set_account_setting(a.id, "default_tz", "UTC")
        await db.set_account_setting(b.id, "default_tz", "Asia/Tashkent")
        assert await db.get_account_setting(a.id, "default_tz") == "UTC"
        assert await db.get_account_setting(b.id, "default_tz") == "Asia/Tashkent"

    async def test_storage_backend_keys_roundtrip(self, seeded):
        """The exact key prefixes used by the BYO Drive flow.  If these
        round-trip, the Drive OAuth callback can persist + the Drive
        backend factory can read."""
        from adapters.storage.object_storage import (
            OBJECT_STORAGE_BACKEND_KEY,
            OBJECT_STORAGE_GDRIVE_REFRESH_TOKEN,
            OBJECT_STORAGE_GDRIVE_ROOT_FOLDER_ID,
            OBJECT_STORAGE_GDRIVE_USER_EMAIL,
        )
        d = seeded
        a = d["account"].id
        # Simulate a successful OAuth callback writing the four keys
        await d["db"].set_account_setting(a, OBJECT_STORAGE_BACKEND_KEY, "gdrive")
        await d["db"].set_account_setting(a, OBJECT_STORAGE_GDRIVE_REFRESH_TOKEN, "encrypted_blob_xyz")
        await d["db"].set_account_setting(a, OBJECT_STORAGE_GDRIVE_ROOT_FOLDER_ID, "1AbCdEfGh")
        await d["db"].set_account_setting(a, OBJECT_STORAGE_GDRIVE_USER_EMAIL, "user@example.com")

        assert await d["db"].get_account_setting(a, OBJECT_STORAGE_BACKEND_KEY) == "gdrive"
        assert await d["db"].get_account_setting(a, OBJECT_STORAGE_GDRIVE_REFRESH_TOKEN) == "encrypted_blob_xyz"
        assert await d["db"].get_account_setting(a, OBJECT_STORAGE_GDRIVE_ROOT_FOLDER_ID) == "1AbCdEfGh"
        assert await d["db"].get_account_setting(a, OBJECT_STORAGE_GDRIVE_USER_EMAIL) == "user@example.com"

    async def test_disconnect_clears_token(self, seeded):
        """Disconnect overwrites the refresh token with an empty string
        and flips the backend — the get_account_setting reader must
        observe both transitions in a single round trip."""
        from adapters.storage.object_storage import (
            OBJECT_STORAGE_BACKEND_KEY, OBJECT_STORAGE_GDRIVE_REFRESH_TOKEN,
        )
        d = seeded
        a = d["account"].id

        await d["db"].set_account_setting(a, OBJECT_STORAGE_GDRIVE_REFRESH_TOKEN, "tok")
        await d["db"].set_account_setting(a, OBJECT_STORAGE_BACKEND_KEY, "gdrive")

        # Disconnect flow
        await d["db"].set_account_setting(a, OBJECT_STORAGE_GDRIVE_REFRESH_TOKEN, "")
        await d["db"].set_account_setting(a, OBJECT_STORAGE_BACKEND_KEY, "disk")

        assert await d["db"].get_account_setting(a, OBJECT_STORAGE_GDRIVE_REFRESH_TOKEN) == ""
        assert await d["db"].get_account_setting(a, OBJECT_STORAGE_BACKEND_KEY) == "disk"

    async def test_unicode_key_and_value(self, seeded):
        """Settings table should tolerate non-ASCII payloads — display
        names in i18n environments include cyrillic / arabic glyphs."""
        d = seeded
        await d["db"].set_account_setting(d["account"].id, "label", "Премьер Trucking — груп")
        v = await d["db"].get_account_setting(d["account"].id, "label")
        assert v == "Премьер Trucking — груп"
