"""Stateless signed tokens for the email channel's connection lifecycle.

Two links a user follows from their inbox, neither of which can assume a
logged-in session:

  • **verify**      — proves the person controls the address before it can
                      receive this account's alerts (24 h expiry).
  • **unsubscribe**  — the RFC 8058 one-click target; must work forever
                      without a login (no expiry).

Both are HMAC-signed (SHA-256) over the payload, so no token table / no
retention sweep — the signature IS the proof.  ``purpose`` is folded into
the signed material for domain separation, so a verify token can never be
replayed as an unsubscribe token even though they share a key.

The key is a DEDICATED ``NOTIFICATION_SIGNING_SECRET`` — deliberately NOT
``JWT_SECRET``.  Decoupling the blast radii is the point: rotating
JWT_SECRET (a routine session-compromise response) must not silently
break every in-inbox unsubscribe link, and a JWT_SECRET leak must not let
anyone forge notification tokens.  Rotating THIS secret invalidates all
outstanding verify + unsubscribe tokens by design (containment); future
mail self-heals via fresh per-message links, and an invalid link falls
through to the graceful "manage preferences" page.

Failure posture: this secret gates only the email-notification links (an
inert, opt-in feature), so a missing/weak secret fails the token path
CLOSED (raises here, no token minted or accepted) rather than refusing
boot the way JWT_SECRET does — downing the whole API over one env var
would be disproportionate to the blast radius.  A boot-time warning gives
operators visibility.  Read from env so this stays in the capability
layer, never importing ``interfaces``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time

logger = logging.getLogger("bot.notifications")

VERIFY_PURPOSE = "notif_verify"
UNSUB_PURPOSE = "notif_unsub"
VERIFY_TTL_SECONDS = 24 * 60 * 60
_MIN_SECRET_LEN = 32


def _secret() -> bytes:
    s = (os.getenv("NOTIFICATION_SIGNING_SECRET") or "").strip()
    if len(s) < _MIN_SECRET_LEN:
        # Fail CLOSED (never mint/accept a token under a weak/absent key —
        # a never-expiring token gives brute-force unlimited time), but
        # scoped to this feature, not the whole process.
        raise RuntimeError(
            "NOTIFICATION_SIGNING_SECRET is unset or shorter than "
            f"{_MIN_SECRET_LEN} chars — email notification links are disabled "
            "until it is set (openssl rand -hex 32). It is separate from "
            "JWT_SECRET on purpose and must not be rotated casually.")
    return s.encode()


def signing_secret_ok() -> bool:
    """Whether the signing secret is present + strong enough — lets the API
    warn at boot without importing token internals or raising."""
    return len((os.getenv("NOTIFICATION_SIGNING_SECRET") or "").strip()) >= _MIN_SECRET_LEN


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(purpose: str, body: str) -> str:
    return _b64e(hmac.new(
        _secret(), f"{purpose}.{body}".encode(), hashlib.sha256).digest())


def make_token(
    purpose: str, *, account_id: int, recipient_type: str,
    recipient_id: str, channel: str, address: str = "",
    ttl_seconds: int | None = None,
) -> str:
    """Sign a recipient+channel claim.  ``ttl_seconds=None`` → never
    expires (unsubscribe); a value sets ``exp`` (verify)."""
    payload: dict = {
        "p": purpose, "a": int(account_id), "rt": recipient_type,
        "ri": str(recipient_id), "ch": channel, "ad": address,
    }
    if ttl_seconds is not None:
        payload["exp"] = int(time.time()) + int(ttl_seconds)
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_sign(purpose, body)}"


def verify_token(purpose: str, token: str) -> dict | None:
    """Return the payload iff the signature matches THIS purpose and the
    token has not expired; ``None`` on any failure (never raises)."""
    try:
        body, _, sig = (token or "").partition(".")
        if not body or not sig:
            return None
        if not hmac.compare_digest(sig, _sign(purpose, body)):
            return None
        payload = json.loads(_b64d(body))
        if payload.get("p") != purpose:
            return None
        exp = payload.get("exp")
        if exp is not None and time.time() > float(exp):
            return None
        return payload
    except Exception:
        return None
