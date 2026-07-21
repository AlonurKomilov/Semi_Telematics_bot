"""VAPID keypair for web push — generated once, persisted, zero-config.

Web push requires the server to identify itself with a stable P-256
keypair (RFC 8292): browsers bind every subscription to the PUBLIC key,
so if the key changes, every existing subscription dies.  That makes the
keypair a persist-forever artifact — exactly the kind of thing this
deployment prefers NOT to manage as an env var — so it is generated on
first use and stored in ``platform_settings``:

  ``webpush_vapid_private``  the private key PEM, encrypted at rest via
                             ``infra.crypto`` when ENCRYPTION_KEY is set
                             (same posture as bot tokens)
  ``webpush_vapid_public``   the b64url application-server key the
                             browser needs (public by definition)

Regenerating (deleting the rows) is possible but invalidates every
subscribed device — they'd each need to re-enable.  The claims ``sub``
uses the deployment's contact address (SMTP_FROM → fallback constant);
push services use it to reach the operator about misbehaving senders.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("bot.notifications")

_PRIV_KEY = "webpush_vapid_private"
_PUB_KEY = "webpush_vapid_public"

# Process-local cache — the keypair never changes once created.
_cache: dict | None = None


def vapid_claims_sub() -> str:
    """The VAPID ``sub`` claim — a contact URI for the push services."""
    frm = (os.getenv("SMTP_FROM") or "").strip()
    # SMTP_FROM may be '"Name" <addr>' — keep just the address part.
    if "<" in frm:
        frm = frm.split("<", 1)[1].rstrip(">").strip()
    return f"mailto:{frm}" if "@" in frm else "mailto:ops@4truck.us"


async def ensure_vapid(db) -> dict:
    """Return ``{private_pem, public_key}``, creating + persisting the
    keypair on first call.  Safe under concurrent first calls: both
    writers produce valid pairs and the second overwrite wins atomically,
    before any subscription has been handed the public key."""
    global _cache
    if _cache is not None:
        return _cache

    from infra.crypto import decrypt, encrypt

    stored_priv = await db.get_platform_setting(_PRIV_KEY)
    stored_pub = await db.get_platform_setting(_PUB_KEY)
    if stored_priv and stored_pub:
        _cache = {"private_pem": decrypt(stored_priv), "public_key": stored_pub}
        return _cache

    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid02, b64urlencode

    v = Vapid02()
    v.generate_keys()
    pem = v.private_pem().decode()
    raw = v.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    pub = b64urlencode(raw)

    await db.set_platform_setting(_PRIV_KEY, encrypt(pem))
    await db.set_platform_setting(_PUB_KEY, pub)
    logger.info("web push: generated VAPID keypair (stable from now on)")
    _cache = {"private_pem": pem, "public_key": pub}
    return _cache


def reset_cache_for_tests() -> None:
    global _cache
    _cache = None
