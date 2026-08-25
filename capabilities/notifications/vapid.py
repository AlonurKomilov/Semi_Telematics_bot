"""VAPID keypair for web push — generated once, persisted, zero-config.

Web push requires the server to identify itself with a stable P-256
keypair (RFC 8292): browsers bind every subscription to the PUBLIC key,
so if the key changes, every existing subscription dies.  That makes the
keypair a persist-forever artifact — exactly the kind of thing this
deployment prefers NOT to manage as an env var — so it is generated on
first use and stored in ``platform_settings``:

  ``webpush_vapid_keypair``  ONE platform_settings row holding a JSON
                             object with both halves: the private key PEM
                             (encrypted at rest via ``infra.crypto`` when
                             ENCRYPTION_KEY is set, same posture as bot
                             tokens) and the b64url application-server key
                             the browser needs (public by definition).
                             One row, because a half-written pair is a
                             pair that kills every live subscription.

Regenerating (deleting the rows) is possible but invalidates every
subscribed device — they'd each need to re-enable.  The claims ``sub``
uses the deployment's contact address (SMTP_FROM → fallback constant);
push services use it to reach the operator about misbehaving senders.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("bot.notifications")

# ONE settings row holding both halves as JSON — a single atomic value, so
# two concurrent first-callers can't interleave partial writes.
_KV_KEY = "webpush_vapid_keypair"

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
    keypair on first call.

    Concurrency contract: the write is ``set_platform_setting_if_absent``
    (single-writer-wins) and EVERY caller — including the one that just
    generated a pair — re-reads the persisted row before caching.  So two
    concurrent first-callers converge on the one stored pair; a locally
    generated loser pair is discarded before its public key could ever be
    handed to a browser.  Without this, a subscription bound to the losing
    key would fail VAPID auth forever and never self-heal (the failure is
    not a 404/410, so the pruner correctly keeps the device)."""
    global _cache
    if _cache is not None:
        return _cache

    import json

    from infra.crypto import decrypt, encrypt

    stored = await db.get_platform_setting(_KV_KEY)
    if not stored:
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid02, b64urlencode

        v = Vapid02()
        v.generate_keys()
        pem = v.private_pem().decode()
        raw = v.public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint)
        created = await db.set_platform_setting_if_absent(_KV_KEY, json.dumps({
            "private": encrypt(pem), "public": b64urlencode(raw),
        }))
        if created:
            logger.info("web push: generated VAPID keypair (stable from now on)")
        # Re-read UNCONDITIONALLY — if a concurrent caller won the insert,
        # our locally generated pair must be discarded, not cached.
        stored = await db.get_platform_setting(_KV_KEY)

    data = json.loads(stored)
    _cache = {"private_pem": decrypt(data["private"]),
              "public_key": data["public"]}
    return _cache


def reset_cache_for_tests() -> None:
    global _cache
    _cache = None
