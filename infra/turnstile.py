"""Cloudflare Turnstile server-side token verification.

Turnstile is the modern reCAPTCHA replacement we run on the two public
signup endpoints (``/auth/register`` and ``/auth/register-account``).
The widget on the dashboard generates a single-use token; this module
exchanges that token with Cloudflare's siteverify API and returns the
boolean verdict the auth endpoints gate on.

Verification is OPT-IN: if ``TURNSTILE_SECRET_KEY`` is unset (dev, CI,
self-host without a Cloudflare account) we treat every request as
verified.  The frontend mirrors this — when ``TURNSTILE_SITE_KEY`` is
empty, the widget is hidden and submission proceeds unconditionally.

Reference: https://developers.cloudflare.com/turnstile/get-started/server-side-validation/
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_TIMEOUT_SECONDS = 5.0


def is_turnstile_enabled() -> bool:
    """True when a real Cloudflare secret is configured.

    Read at call time (not module import) so tests can flip the env
    var via monkeypatch without re-importing.
    """
    return bool((os.getenv("TURNSTILE_SECRET_KEY") or "").strip())


async def verify_turnstile(
    token: str | None,
    *,
    remote_ip: str | None = None,
) -> bool:
    """Validate a Turnstile token with Cloudflare.

    Returns True iff Cloudflare returns ``success: true``.  When the
    secret is unset we skip the round-trip and return True so dev and
    test environments don't need a Cloudflare account.

    Failure modes (all return False):
    - empty / missing token
    - HTTP error reaching siteverify
    - non-200 response
    - ``success: false`` (timeout-or-duplicate, invalid input, etc.)

    The ``remote_ip`` hint is optional but recommended — Cloudflare
    uses it to cross-check the token's origin and harden replay
    detection.  We pass through the X-Forwarded-For / client IP so the
    endpoint signature is consistent across direct and reverse-proxied
    deployments.
    """
    secret = (os.getenv("TURNSTILE_SECRET_KEY") or "").strip()
    if not secret:
        # Turnstile not configured — skip verification entirely.
        return True

    if not token:
        logger.info("Turnstile: empty token rejected")
        return False

    data = {"secret": secret, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.post(_VERIFY_URL, data=data)
    except httpx.HTTPError as e:
        logger.warning("Turnstile: siteverify HTTP error: %s", e)
        return False

    if r.status_code != 200:
        logger.warning("Turnstile: siteverify HTTP %s", r.status_code)
        return False

    try:
        body = r.json()
    except ValueError:
        logger.warning("Turnstile: siteverify returned non-JSON")
        return False

    success = bool(body.get("success"))
    if not success:
        # Common codes: missing-input-response, invalid-input-response,
        # timeout-or-duplicate.  Log the codes so an operator chasing a
        # spike can tell "user double-clicked" from "real abuse pattern".
        logger.info(
            "Turnstile: verification failed codes=%s",
            body.get("error-codes"),
        )
    return success
