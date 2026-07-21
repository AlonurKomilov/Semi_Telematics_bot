"""Notification channel API — connect / verify / unsubscribe.

router.py is interface-layer code co-located with its capability
(docs/FEATURES.md): ONLY router.py may import ``interfaces.api.deps``.
The delivery core (channels / service / email) stays framework-free; this
is the thin HTTP skin over ``lifecycle.py``.

Two public, tokenless endpoints back email links an inbox follows with no
session — the signed token IS the authorization (``tokens.py``):
``/verify`` (confirm an address) and ``/unsubscribe`` (RFC 8058
one-click).  Both are per-IP rate-limited and give a uniform response for
bad tokens, so a guesser learns nothing.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from interfaces.api.deps import get_current_db_user, get_current_user
from interfaces.api.rate_limit import limiter

from capabilities.notifications.lifecycle import (
    apply_unsubscribe,
    confirm_channel_verification,
    start_email_connection,
)

logger = logging.getLogger("api.notifications")

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:480px;margin:48px auto;padding:24px;color:#1f2937">
<h1 style="font-size:18px">{title}</h1>
<p style="font-size:14px;color:#6b7280">{body}</p>
</body></html>""")


# ── Connect (authed — a user manages their OWN address) ──────────────

class ConnectEmailRequest(BaseModel):
    address: str


@router.post("/channels/email")
@limiter.limit("5/minute")
async def connect_email(
    request: Request, body: ConnectEmailRequest,
    user: dict = Depends(get_current_user),
):
    """Store the caller's alert email (unverified) + send a verification
    link.  Self-scoped: every role manages its own address, no extra
    grant.  Capped per IP so it can't be used to blast verification mail."""
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"ok": False, "error": "user_not_found"}
    return await start_email_connection(
        db, db_user.account_id, db_user.id, body.address)


@router.get("/channels")
@limiter.limit("30/minute")
async def list_channels(request: Request, user: dict = Depends(get_current_user)):
    """The caller's channel connections (address + verified + master), for
    the preferences UI to render 'connected ✓' vs 'connect →'."""
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"channels": {}}
    out: dict[str, dict] = {}
    for ch in ("email", "telegram_dm"):
        conn = await db.get_notification_channel(
            db_user.account_id, "user", db_user.id, ch)
        out[ch] = conn or {"address": "", "verified": False, "enabled_master": True}
    return {"channels": out}


# ── Verify (public — the token authorizes) ───────────────────────────

@router.get("/verify")
@limiter.limit("10/minute")
async def verify_email(request: Request, token: str):
    """Redeem an email-verification link.  Uniform message on a bad/expired
    token — no oracle for whether a token was ever valid."""
    from infra.platform import get_platform_db
    ok = await confirm_channel_verification(get_platform_db(), token)
    if not ok:
        return _page(
            "Link expired",
            "This confirmation link is invalid or has expired. Open your "
            "notification settings to send a fresh one.")
    return _page(
        "Email confirmed",
        "Thanks — this address will now receive the notifications you "
        "chose. You can close this window.")


# ── Unsubscribe (public one-click, RFC 8058) ─────────────────────────

@router.post("/unsubscribe")
@limiter.limit("10/minute")
async def unsubscribe_post(request: Request, token: str):
    """Gmail/Yahoo One-Click List-Unsubscribe POST target.  The token IS
    the authorization (an inbox can't authenticate the recipient)."""
    from infra.platform import get_platform_db
    await apply_unsubscribe(get_platform_db(), token)
    return {"ok": True}


@router.get("/unsubscribe")
@limiter.limit("10/minute")
async def unsubscribe_get(request: Request, token: str):
    """Human-click unsubscribe (the in-body link issues a GET).

    Does NOT mutate on GET: corporate mail-security scanners
    (Proofpoint/Mimecast/Defender Safe Links) pre-fetch every URL in a
    message, and a GET that turned alerts off would silently unsubscribe a
    fleet from its SAFETY alerts.  So GET renders a one-button confirm that
    POSTs the actual opt-out; the empty form action re-posts to this same
    URL (token in the query) → the One-Click POST handler below."""
    import html as _html
    safe = _html.escape(token, quote=True)
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Turn off email notifications?</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:480px;margin:48px auto;padding:24px;color:#1f2937">
<h1 style="font-size:18px">Turn off email notifications?</h1>
<p style="font-size:14px;color:#6b7280">This stops notification emails to this address. You can turn them back on anytime from your settings.</p>
<form method="post" action="unsubscribe-confirmed?token={safe}">
<button type="submit" style="padding:10px 16px;background:#111827;color:#fff;border:0;border-radius:8px;font-size:14px;cursor:pointer">Turn off email notifications</button>
</form>
</body></html>""")


@router.post("/unsubscribe-confirmed")
@limiter.limit("10/minute")
async def unsubscribe_confirmed(request: Request, token: str):
    """The confirm form's POST target (a human clicked 'Turn off')."""
    from infra.platform import get_platform_db
    await apply_unsubscribe(get_platform_db(), token)
    return _page(
        "Unsubscribed",
        "Email notifications are now off for this address. You can turn "
        "them back on anytime from your notification settings.")
