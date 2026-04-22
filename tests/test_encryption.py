"""Tests for the encryption module."""

import os
import pytest
from unittest.mock import patch

from adapters.crypto import (
    init_encryption,
    is_enabled,
    encrypt,
    decrypt,
    _ENC_PREFIX,
)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_encryption():
    """Ensure each test starts with a clean encryption state."""
    import adapters.crypto as encryption
    encryption._fernet = None
    yield
    encryption._fernet = None


@pytest.fixture
def enabled():
    """Enable encryption with a test passphrase."""
    with patch.dict(os.environ, {"ENCRYPTION_KEY": "test-passphrase-42"}):
        init_encryption()
    assert is_enabled()


@pytest.fixture
def disabled():
    """Ensure encryption is disabled."""
    with patch.dict(os.environ, {"ENCRYPTION_KEY": ""}):
        init_encryption()
    assert not is_enabled()


# ── Enabled mode ──────────────────────────────────────────────────

class TestEncryptionEnabled:

    def test_encrypt_produces_prefixed_ciphertext(self, enabled):
        ct = encrypt("my-secret-key")
        assert ct.startswith(_ENC_PREFIX)
        assert "my-secret-key" not in ct

    def test_decrypt_roundtrip(self, enabled):
        original = "samsara_api_Cuuvx5LCti_long_token"
        ct = encrypt(original)
        assert decrypt(ct) == original

    def test_decrypt_plaintext_passthrough(self, enabled):
        """Legacy plaintext values should pass through decrypt unchanged."""
        assert decrypt("samsara_api_plain") == "samsara_api_plain"

    def test_different_plaintexts_produce_different_ciphertexts(self, enabled):
        ct1 = encrypt("key-one")
        ct2 = encrypt("key-two")
        assert ct1 != ct2

    def test_empty_string_roundtrip(self, enabled):
        ct = encrypt("")
        assert decrypt(ct) == ""

    def test_unicode_roundtrip(self, enabled):
        val = "api_key_with_ünîcödé_🔑"
        ct = encrypt(val)
        assert decrypt(ct) == val


# ── Disabled mode (no ENCRYPTION_KEY) ─────────────────────────────

class TestEncryptionDisabled:

    def test_encrypt_returns_plaintext(self, disabled):
        assert encrypt("my-key") == "my-key"

    def test_decrypt_returns_plaintext(self, disabled):
        assert decrypt("my-key") == "my-key"

    def test_is_not_enabled(self, disabled):
        assert not is_enabled()


# ── Error cases ───────────────────────────────────────────────────

class TestEncryptionErrors:

    def test_wrong_key_raises_valueerror(self, enabled):
        ct = encrypt("secret")
        # Re-init with a different key
        import adapters.crypto as encryption
        encryption._fernet = None
        with patch.dict(os.environ, {"ENCRYPTION_KEY": "different-passphrase"}):
            init_encryption()
        with pytest.raises(ValueError, match="ENCRYPTION_KEY may have changed"):
            decrypt(ct)

    def test_encrypted_value_without_key_raises(self, enabled):
        ct = encrypt("secret")
        # Disable encryption
        import adapters.crypto as encryption
        encryption._fernet = None
        with pytest.raises(ValueError, match="ENCRYPTION_KEY is not configured"):
            decrypt(ct)

    def test_init_returns_true_when_enabled(self):
        with patch.dict(os.environ, {"ENCRYPTION_KEY": "something"}):
            assert init_encryption() is True

    def test_init_returns_false_when_disabled(self):
        with patch.dict(os.environ, {"ENCRYPTION_KEY": ""}):
            assert init_encryption() is False
