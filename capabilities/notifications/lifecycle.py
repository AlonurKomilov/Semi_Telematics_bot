"""Email channel connection lifecycle (docs §7): connect → verify → send,
plus one-click unsubscribe.

Framework-free service functions the API route layer calls.  They touch
the preference matrix (verified-address gate), the signed-token helpers,
and ``capabilities/email`` for the actual send — nothing web-framework or
``capabilities.alerting`` specific, so this stays in the capability layer.

Why an address of its own: a user's alert email is NOT their login email
(they may want alerts in a different inbox), so the channel verifies its
own address rather than trusting ``users.email_verified``.
"""

from __future__ import annotations

import logging
import os
import re

from capabilities.notifications.tokens import (
    UNSUB_PURPOSE,
    VERIFY_PURPOSE,
    VERIFY_TTL_SECONDS,
    make_token,
    verify_token,
)

logger = logging.getLogger("bot.notifications")

# Same shape the auth layer validates addresses with (interfaces/api/auth
# _EMAIL_RE) — stricter than "has an @", and it rejects the CR/LF that a
# header-injection attempt would smuggle through ``send_email``'s ``to``.
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _dash_base() -> str:
    return (os.getenv("AUTH_BASE_URL") or os.getenv("DASHBOARD_BASE_URL")
            or "https://4truck.us").rstrip("/")


def _api_base() -> str:
    # Apex serves /api/v1 via reverse proxy on default deploys; a split
    # api./dash. deploy overrides via API_BASE_URL (same as auth emails).
    return (os.getenv("API_BASE_URL") or _dash_base()).rstrip("/")


def _brand() -> str:
    return os.getenv("EMAIL_BRAND_NAME") or "4truck"


def unsubscribe_url(
    account_id: int, recipient_type: str, recipient_id: str, channel: str,
) -> str:
    """The RFC 8058 one-click target — a signed, never-expiring link that
    turns this channel off without a login."""
    token = make_token(
        UNSUB_PURPOSE, account_id=account_id, recipient_type=recipient_type,
        recipient_id=recipient_id, channel=channel, ttl_seconds=None,
    )
    return f"{_api_base()}/api/v1/notifications/unsubscribe?token={token}"


def _verify_url(token: str) -> str:
    return f"{_api_base()}/api/v1/notifications/verify?token={token}"


async def start_email_connection(
    db, account_id: int, recipient_id: str, address: str,
    *, recipient_type: str = "user",
) -> dict:
    """Store the address (unverified) + email a verification link.  Returns
    ``{ok, sent}``.  Re-connecting a DIFFERENT address re-issues a fresh
    link; re-submitting the SAME already-verified address is a no-op so a
    settings-form re-save can't silently un-verify a working channel."""
    address = (address or "").strip()
    if not _EMAIL_RE.match(address) or len(address) > 254:
        return {"ok": False, "error": "bad_email"}

    existing = await db.get_notification_channel(
        account_id, recipient_type, recipient_id, "email")
    if existing and existing.get("verified") and existing.get("address") == address:
        # Already proven — don't reset verified and don't spam another
        # link.  (Master switch is a separate toggle, untouched here.)
        return {"ok": True, "sent": False, "already_verified": True}

    # Upsert as UNVERIFIED — until the link is followed, this address
    # cannot receive anything (get_notification_subscribers gates on
    # verified_at).  Keeps the master switch on so verifying is enough.
    await db.upsert_notification_channel(
        account_id, recipient_type, recipient_id, "email",
        address=address, verified=False, enabled_master=True,
    )
    token = make_token(
        VERIFY_PURPOSE, account_id=account_id, recipient_type=recipient_type,
        recipient_id=recipient_id, channel="email", address=address,
        ttl_seconds=VERIFY_TTL_SECONDS,
    )
    sent = await _send_verification_email(address, _verify_url(token))
    return {"ok": True, "sent": sent}


async def confirm_channel_verification(db, token: str) -> dict | None:
    """Redeem a verify link.  Marks the connection verified only if the
    stored address still matches the token's (a later address change
    invalidates the old link).  Returns the resolved claim or ``None``."""
    payload = verify_token(VERIFY_PURPOSE, token)
    if not payload:
        return None
    ok = await db.verify_notification_channel(
        payload["a"], payload["rt"], payload["ri"], payload["ch"], payload["ad"],
    )
    return payload if ok else None


async def apply_unsubscribe(db, token: str) -> bool:
    """Redeem a one-click unsubscribe link — flip the channel master off.
    Kept idempotent (a second click is a harmless no-op)."""
    payload = verify_token(UNSUB_PURPOSE, token)
    if not payload:
        return False
    await db.disable_notification_channel(
        payload["a"], payload["rt"], payload["ri"], payload["ch"],
    )
    return True


async def _send_verification_email(address: str, verify_url: str) -> bool:
    import asyncio
    import html as _html

    from capabilities.email import is_email_configured, send_email

    if not is_email_configured():
        logger.debug("channel verification email skipped: SMTP not configured")
        return False

    brand = _brand()
    safe_url = _html.escape(verify_url)
    text_body = (
        f"Confirm this address to receive {brand} notifications.\n\n"
        f"{verify_url}\n\n"
        "This link expires in 24 hours.  If you didn't request it, you can "
        "ignore this email — nothing will be sent here.\n"
    )
    html_body = (
        '<div style="font-family:system-ui,Arial,sans-serif;font-size:14px;'
        'color:#111827;line-height:1.5">'
        f"<p>Confirm this address to receive <b>{_html.escape(brand)}</b> "
        "notifications.</p>"
        f'<p><a href="{safe_url}" style="display:inline-block;padding:10px 16px;'
        'background:#111827;color:#fff;border-radius:8px;text-decoration:none">'
        "Confirm email</a></p>"
        f'<p style="font-size:12px;color:#6b7280">Or paste this link:<br>'
        f'<span style="word-break:break-all">{safe_url}</span></p>'
        '<p style="font-size:12px;color:#6b7280">This link expires in 24 hours. '
        "If you didn't request it, ignore this email — nothing will be sent "
        "here.</p></div>"
    )
    try:
        return await asyncio.to_thread(
            lambda: send_email(
                to=address, subject=f"Confirm your {brand} notification email",
                body=text_body, html_body=html_body,
                extra_headers={"Auto-Submitted": "auto-generated"},
            )
        )
    except Exception as e:                    # never let a bad address 500 the caller
        logger.warning("channel verification email failed: %s", e)
        return False
