"""
Symmetric encryption for secrets at rest (Samsara API keys).

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the `cryptography` library.
The encryption key is derived from the ENCRYPTION_KEY environment variable
using PBKDF2-HMAC-SHA256 with a fixed application-level salt.

If ENCRYPTION_KEY is not set, encryption is disabled — keys are stored and
returned as plaintext. This allows a smooth migration: existing plaintext
keys in the DB continue to work, and new keys are encrypted once the env
var is configured.

Migration helper `migrate_plaintext_keys()` can be called once to encrypt
all existing plaintext keys in the database.
"""

from __future__ import annotations

import base64
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

logger = logging.getLogger(__name__)

# Fixed application salt — not a secret, just prevents rainbow tables.
# Changing this invalidates all previously encrypted values.
_APP_SALT = b"semi-telematics-bot-v1"

_fernet: Fernet | None = None


def _derive_key(passphrase: str) -> bytes:
    """Derive a 32-byte Fernet key from a passphrase via PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_APP_SALT,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


def init_encryption() -> bool:
    """Initialise the Fernet cipher from ENCRYPTION_KEY env var.

    Returns True if encryption is active, False if disabled (no key set).
    """
    global _fernet
    passphrase = os.getenv("ENCRYPTION_KEY", "").strip()
    if not passphrase:
        logger.info("ENCRYPTION_KEY not set — API key encryption disabled")
        _fernet = None
        return False
    _fernet = Fernet(_derive_key(passphrase))
    logger.info("API key encryption enabled")
    return True


def is_enabled() -> bool:
    """Return True if encryption is currently active."""
    return _fernet is not None


# ── Encrypt / Decrypt ────────────────────────────────────────────

_ENC_PREFIX = "enc::"


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string. Returns prefixed ciphertext.

    If encryption is disabled, returns the plaintext unchanged.
    """
    if _fernet is None:
        return plaintext
    token = _fernet.encrypt(plaintext.encode())
    return f"{_ENC_PREFIX}{token.decode()}"


def decrypt(stored: str) -> str:
    """Decrypt a stored value. Handles both encrypted and legacy plaintext.

    - If the value starts with 'enc::' it is decrypted.
    - Otherwise it is returned as-is (legacy plaintext).
    - If decryption fails (wrong key), raises ValueError.
    """
    if not stored.startswith(_ENC_PREFIX):
        return stored  # legacy plaintext — pass through

    if _fernet is None:
        raise ValueError(
            "Encrypted value found but ENCRYPTION_KEY is not configured"
        )

    token = stored[len(_ENC_PREFIX):]
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        raise ValueError(
            "Failed to decrypt API key — ENCRYPTION_KEY may have changed"
        )


def blind_index(value: str, *, scope: str = "") -> str | None:
    """Deterministic, KEYED lookup hash for matching a sensitive value
    (e.g. an SSN) WITHOUT storing or exposing it.

    Why HMAC and not a plain hash: an SSN has only ~10^9 possible values,
    so a bare SHA-256 is trivially brute-forced / rainbow-tabled.  Keying
    the hash with the secret ENCRYPTION_KEY makes the digest unforgeable
    and un-reversible without the key.

    ``scope`` domain-separates the index — pass the account id so the same
    SSN hashes DIFFERENTLY per account: matches stay within one carrier and
    there's no cross-tenant correlation even with raw DB access.

    Normalises to alphanumerics (so "111-22-3333" == "111223333").  Returns
    None when ENCRYPTION_KEY is unset (matching disabled) or the value is
    empty — callers treat None as "no index, no match".
    """
    import hmac
    import hashlib

    passphrase = os.getenv("ENCRYPTION_KEY", "").strip()
    norm = "".join(ch for ch in (value or "") if ch.isalnum()).lower()
    if not passphrase or not norm:
        return None
    msg = f"{scope}\x00{norm}".encode("utf-8")
    return hmac.new(passphrase.encode("utf-8"), msg, hashlib.sha256).hexdigest()
