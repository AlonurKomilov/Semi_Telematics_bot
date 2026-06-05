"""Phase B verification — PII at-rest encryption + login-attempt log."""
from __future__ import annotations

import os
import sys

# Real encryption key so the round-trip exercises the actual Fernet path,
# not the plaintext-passthrough fallback.  The conftest sets
# ENCRYPTION_KEY="" via setdefault BEFORE this file runs, so we override
# it here and explicitly re-run ``init_encryption`` to rebuild the
# module-level Fernet cipher with the test key.
os.environ["ENCRYPTION_KEY"] = "test-key-for-pii-roundtrip-tests-please-rotate"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from adapters.storage import Role
from adapters.storage.drivers import _maybe_decrypt, _maybe_encrypt
from infra.crypto import init_encryption as _init_encryption

# Rebuild the Fernet singleton against the env we just set.
_init_encryption()


@pytest.fixture(autouse=True)
def _ensure_encryption_key_set():
    """Re-establish the test encryption key + rebuild the Fernet
    singleton before every test in this file.

    Under xdist with parallel workers, other test files imported on
    the same worker process can call ``init_encryption()`` after this
    module's import-time setup ran, rebuilding the singleton against
    whichever ``ENCRYPTION_KEY`` value happened to be in os.environ
    at that moment (commonly ``""`` set by the many conftest /
    test-module ``os.environ.setdefault("ENCRYPTION_KEY", "")``
    statements).  That left the Fernet cipher inert and broke our
    round-trip / ciphertext-at-rest assertions non-deterministically
    depending on worker assignment.

    This fixture is autouse + function-scoped so every test starts
    from the same known good encryption state regardless of what
    happened on the worker before us.
    """
    os.environ["ENCRYPTION_KEY"] = "test-key-for-pii-roundtrip-tests-please-rotate"
    _init_encryption()
    yield


@pytest.fixture
async def db(pg_db):
    yield pg_db


# ── Helper round-trip (no DB) ─────────────────────────────────────


class TestEncryptionHelpers:
    def test_empty_passthrough(self):
        # None and empty strings shouldn't be touched.
        assert _maybe_decrypt(None) is None
        assert _maybe_decrypt("") == ""
        assert _maybe_encrypt(None) is None
        assert _maybe_encrypt("") == ""

    def test_round_trip(self):
        from infra.crypto import is_enabled
        # With the env key set at module load, encryption should be on.
        assert is_enabled()
        plaintext = "DL-12345-CA"
        ciphertext = _maybe_encrypt(plaintext)
        # The Fernet token has an ``enc::`` prefix added by our wrapper.
        assert ciphertext.startswith("enc::")
        assert plaintext not in ciphertext  # actually scrambled
        assert _maybe_decrypt(ciphertext) == plaintext

    def test_legacy_plaintext_passthrough_on_read(self):
        # A row that was inserted BEFORE the migration ran has plain
        # text without the ``enc::`` marker.  decrypt() returns it as-is.
        assert _maybe_decrypt("legacy-plaintext-value") == "legacy-plaintext-value"


# ── End-to-end: write encrypted, read decrypted ───────────────────


class TestDriverProfilePIIAtRest:
    async def test_pii_round_trip_through_storage(self, db):
        acct = await db.create_account("Encrypt Co")
        user = await db.create_user(
            telegram_id=90001, account_id=acct.id, role=Role.DRIVER,
            display_name="Test Driver",
        )
        # Plaintext values written through the public mixin.
        await db.update_driver_profile(
            user.id,
            cdl_number="CDL-9876543",
            dob="1990-05-15",
            phone="+14155551212",
            home_address="123 Main St, Springfield, IL 62701",
            driver_notes="Prefers manual transmission",
            # Non-PII fields ride along without encryption.
            cdl_state="CA", cdl_class="A",
        )
        # Read back via the public API — PII should be plaintext again.
        profile = await db.get_driver_profile(user.id)
        assert profile is not None
        assert profile.cdl_number == "CDL-9876543"
        assert profile.dob == "1990-05-15"
        assert profile.phone == "+14155551212"
        assert profile.home_address == "123 Main St, Springfield, IL 62701"
        assert profile.driver_notes == "Prefers manual transmission"
        # Non-PII passes through unchanged.
        assert profile.cdl_state == "CA"
        assert profile.cdl_class == "A"

    async def test_raw_db_row_holds_ciphertext(self, db):
        """Direct SQL read should see the ``enc::`` prefix — confirms
        the encryption happened at the storage layer, not just in our
        wrapper functions."""
        acct = await db.create_account("Cipher Co")
        user = await db.create_user(
            telegram_id=90002, account_id=acct.id, role=Role.DRIVER,
            display_name="Cipher Driver",
        )
        await db.update_driver_profile(
            user.id, cdl_number="SECRET-NUMBER-9999",
        )
        cur = await db._db.execute(
            "SELECT cdl_number FROM users WHERE id = ?", (user.id,),
        )
        row = await cur.fetchone()
        raw = dict(row)["cdl_number"]
        assert raw.startswith("enc::")
        assert "SECRET-NUMBER-9999" not in raw


# ── Login-attempt log ─────────────────────────────────────────────


class TestLoginAttemptLog:
    async def test_records_success_and_failure(self, db):
        acct = await db.create_account("Logger Co")
        user = await db.create_user(
            telegram_id=80001, account_id=acct.id, role=Role.OWNER,
            display_name="Audit Owner",
        )
        # Mix of success + failure + email-only.
        await db.record_login_attempt(
            user_id=user.id, email="audit@example.com", success=True,
            ip_address="10.0.0.1", user_agent="test-agent/1.0",
        )
        await db.record_login_attempt(
            user_id=user.id, email="audit@example.com", success=False,
            failure_reason="bad_password",
            ip_address="10.0.0.2", user_agent="test-agent/2.0",
        )
        await db.record_login_attempt(
            user_id=None, email="never-existed@example.com", success=False,
            failure_reason="no_such_email",
            ip_address="10.0.0.3", user_agent="test-agent/3.0",
        )
        rows = await db.list_my_login_attempts(user.id, limit=10)
        # The two with user_id=user.id should be returned, ordered most-recent first.
        assert len(rows) == 2
        # Newest first.
        assert int(rows[0]["success"]) == 0
        assert rows[0]["failure_reason"] == "bad_password"
        assert int(rows[1]["success"]) == 1
        assert rows[1]["ip_address"] == "10.0.0.1"

    async def test_limit_clamps(self, db):
        acct = await db.create_account("Limit Co")
        user = await db.create_user(
            telegram_id=80002, account_id=acct.id, role=Role.OWNER,
            display_name="Limit Owner",
        )
        for _ in range(5):
            await db.record_login_attempt(
                user_id=user.id, email="x@x.com", success=True,
            )
        rows = await db.list_my_login_attempts(user.id, limit=3)
        assert len(rows) == 3
