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

Key resolution: a dedicated ``NOTIFICATION_SIGNING_SECRET`` if set, else
``JWT_SECRET`` (already fail-fast-required at boot).  The single-secret
fallback is the operator default — one less thing to configure — with a
known, accepted trade-off: rotating ``JWT_SECRET`` (a routine
session-compromise response) then also invalidates every outstanding
verify + unsubscribe link, and a ``JWT_SECRET`` leak could forge
notification links.  A deployment that wants those blast radii separated
just sets the dedicated secret and it quietly takes over — isolating
notification signing from session-key rotation, no code change.

Either way an invalidated link isn't catastrophic: verify + unsubscribe
tokens self-heal via fresh per-message links on future mail, and an
invalid link falls through to the graceful "manage preferences" page.

Failure posture: if NEITHER secret is set the token path fails CLOSED
(raises here — no token minted or accepted) rather than refusing boot;
since ``JWT_SECRET`` is required at boot, that only happens in a
misconfigured environment.  Read from env so this stays in the capability
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


def _resolved_secret() -> str:
    # Dedicated secret wins; otherwise reuse JWT_SECRET (the operator
    # default — no separate secret to manage).
    return (os.getenv("NOTIFICATION_SIGNING_SECRET")
            or os.getenv("JWT_SECRET") or "").strip()


def _secret() -> bytes:
    s = _resolved_secret()
    if not s:
        # Fail CLOSED — never mint/accept a token without a key.  Only
        # reachable if BOTH secrets are unset, which can't happen once
        # JWT_SECRET's own boot fail-fast has run.
        raise RuntimeError(
            "no signing secret — set JWT_SECRET (or NOTIFICATION_SIGNING_SECRET)")
    return s.encode()


def signing_secret_ok() -> bool:
    """Whether a usable signing secret is present (dedicated or the
    JWT_SECRET fallback) — lets the API warn at boot without importing
    token internals or raising."""
    return len(_resolved_secret()) >= _MIN_SECRET_LEN


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
