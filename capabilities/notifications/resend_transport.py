"""Resend HTTP API transport for invite emails.

Why a separate transport
------------------------
The shared SMTP mailer at ``capabilities/notifications/email.py`` covers
auth flows (verify, password reset, lockout) via stdlib ``smtplib`` —
that's fine for those because we don't need bounce-state visibility,
and the auth recipient already has an account.

Invites are different:
  - We need ``email.bounced`` / ``email.complained`` / ``email.delivered``
    webhooks to surface delivery health to the operator.
  - The webhook handler needs a stable per-send identifier (Resend's
    ``data.email_id``) to match events to invites — recipient-address
    matching is a cross-account hijack vector.
  - SMTP responses give us "queued for delivery" but never tell us
    what the receiving MX did with the message afterwards.

So invite sends route through Resend's HTTP API when ``MAIL_PROVIDER``
is ``resend`` AND ``RESEND_API_KEY`` is set.  Falls back to the SMTP
path otherwise — the feature ships dark and degrades gracefully.

We don't pull in the ``resend`` Python SDK because:
  - It's a thin wrapper around the same HTTPS POST we do here.
  - Direct ``aiohttp`` gives us per-call timeout control without
    juggling the SDK's sync-bridging code.
  - One fewer transitive dependency to audit.

This module is async — call from FastAPI handlers and ARQ workers
directly without thread-pool hops.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Optional

import aiohttp

logger = logging.getLogger(__name__)

# Resend's documented per-request timeout SLA is ~10s; we cap below
# that so a stalled API call doesn't block the FastAPI worker thread
# for longer than the request's own SLA budget.
_RESEND_API_TIMEOUT = aiohttp.ClientTimeout(total=10)
_RESEND_API_URL = "https://api.resend.com/emails"


def is_resend_api_enabled() -> bool:
    """``True`` when invite emails should route through the Resend
    HTTP API instead of the SMTP fallback.  Three-part gate so a
    deploy that misses any one env var stays on SMTP rather than
    crashing on missing-credential errors at first request:

      MAIL_PROVIDER=resend     — opt-in switch
      RESEND_API_KEY=…         — API credential
      SMTP_FROM_INVITES=…      — invite-specific From address

    The SMTP_FROM_INVITES requirement (previously gated separately
    in auth_emails) is bundled into the same gate so misconfig
    can't silently degrade to SMTP — an operator who set
    MAIL_PROVIDER=resend + RESEND_API_KEY but forgot
    SMTP_FROM_INVITES previously routed all invites via SMTP with
    no warning AND no bounce visibility (defeating the whole
    point).  Now the gate fails closed: ``is_resend_api_enabled()``
    returns False, the route handler's combined-provider check
    fires the 503 'Email not configured', and the operator gets a
    clear error instead of silent fallback.
    """
    return (
        (os.getenv("MAIL_PROVIDER") or "smtp").lower() == "resend"
        and bool(os.getenv("RESEND_API_KEY"))
        and bool(os.getenv("SMTP_FROM_INVITES"))
    )


async def send_invite_via_resend(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str,
    from_address: str,
    from_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    headers: Optional[Mapping[str, str]] = None,
    tags: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """POST a single invite email via Resend's HTTPS API.

    Returns Resend's ``email_id`` on success (the per-send identifier
    we persist on the invite row for webhook-event lookup).  Returns
    None on any failure — caller logs and falls back to the SMTP path.
    Never raises — invite-create must continue even when the email
    layer is degraded.

    ``tags`` is forwarded to Resend's payload — set ``invite_code`` so
    the webhook handler has a SECOND identification path in case
    ``email_id`` lookups miss (rare but possible: race between webhook
    arrival and the row's ``set_invite_resend_email_id`` UPDATE).

    Headers passed via ``headers`` go to the recipient verbatim
    (List-Unsubscribe, Auto-Submitted, etc.).  Resend strips X-* tags
    from the webhook payload — only ``tags`` survive the round-trip.
    """
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        # is_resend_api_enabled() should have prevented this; log
        # loudly and return None so caller falls back.
        logger.warning("send_invite_via_resend called without RESEND_API_KEY")
        return None
    if not to or "@" not in to:
        logger.debug("send_invite_via_resend: invalid 'to' %r", to)
        return None

    # Construct From in the same display-name + address shape the
    # SMTP path uses, so recipient inbox columns look identical
    # across transports.
    from_field = (
        f"{from_name} <{from_address}>" if from_name else from_address
    )
    payload: dict[str, Any] = {
        "from": from_field,
        "to": [to],
        "subject": subject,
        "text": text_body,
        "html": html_body,
    }
    if reply_to:
        payload["reply_to"] = reply_to
    if headers:
        # Resend expects ``headers`` as a flat dict[str, str].  Any
        # CRLF in values would be rejected at the API layer — strip
        # defensively so we never POST a malformed header (would
        # produce 400 + waste retry budget).
        payload["headers"] = {
            k: v.replace("\r", "").replace("\n", "").strip()
            for k, v in headers.items()
            if v is not None
        }
    if tags:
        # Resend tag VALUES are restricted to ASCII alphanum + _- (the
        # invite code matches this constraint via _generate_invite_code).
        # KEY is restricted similarly.  Anything else and the API
        # returns 422 — sanitize defensively.
        import re as _re
        def _safe(s):
            return _re.sub(r"[^A-Za-z0-9_\-]", "_", str(s))[:256]
        payload["tags"] = [
            {"name": _safe(k), "value": _safe(v)}
            for k, v in tags.items()
        ]

    request_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession(timeout=_RESEND_API_TIMEOUT) as session:
            async with session.post(
                _RESEND_API_URL,
                json=payload,
                headers=request_headers,
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status >= 400:
                    # Log the API's error response but DON'T propagate
                    # — invite create flow already handles None as
                    # "send failed, surface to operator as fallback".
                    logger.warning(
                        "Resend API %s: %s",
                        resp.status, body,
                    )
                    return None
                # Successful response shape: {"id": "<uuid>"}.  Some
                # endpoints return additional fields; we only use id.
                email_id = body.get("id") if isinstance(body, dict) else None
                if not email_id:
                    logger.warning(
                        "Resend API success but no id in response: %s", body,
                    )
                    return None
                return str(email_id)
    except Exception as e:
        logger.warning("Resend API call failed: %s", e)
        return None
