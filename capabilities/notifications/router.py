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
from typing import Literal

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


# ── Per-type preferences for matrix channels (email today) ───────────

# Email defaults to a batched cadence: per-alert email at fleet volume is
# unusable, so a newly-enabled type digests daily until the user says
# otherwise (docs §8).
_EMAIL_DEFAULT_CADENCE = "daily"


@router.get("/prefs/email")
@limiter.limit("30/minute")
async def get_email_prefs(request: Request, user: dict = Depends(get_current_user)):
    """The caller's EMAIL channel preferences for the settings page: the
    role-tailored alert types, which are on for email, the channel
    cadence, and the connection state."""
    from capabilities.alerting.relevance import alert_types_for_role
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"relevant_types": [], "email": {}}

    relevant = alert_types_for_role(db_user.role)
    rows = await db.list_recipient_notification_prefs(
        db_user.account_id, "user", db_user.id)
    email_rows = [r for r in rows if r["channel"] == "email"]
    types = {r["alert_type"]: bool(r["enabled"]) for r in email_rows}
    # Channel cadence = the shared cadence of the email rows (kept uniform
    # by set_channel_cadence); default when nothing is enabled yet.
    cadence = next((r["cadence"] for r in email_rows), _EMAIL_DEFAULT_CADENCE)
    conn = await db.get_notification_channel(
        db_user.account_id, "user", db_user.id, "email")

    return {
        "relevant_types": relevant,
        "email": {
            "connected": bool(conn),
            "verified": bool(conn and conn.get("verified")),
            "address": (conn or {}).get("address", ""),
            "enabled_master": bool(conn.get("enabled_master")) if conn else True,
            "cadence": cadence,
            "types": {t: types.get(t, False) for t in relevant},
        },
    }


class EmailTypeRequest(BaseModel):
    alert_type: str
    enabled: bool


@router.put("/prefs/email/type")
@limiter.limit("60/minute")
async def set_email_type(
    request: Request, body: EmailTypeRequest,
    user: dict = Depends(get_current_user),
):
    """Toggle one alert type for the caller's email channel.  Role-gated:
    a type the caller's role can't see is silently dropped (defence in
    depth — the UI wouldn't render it).

    Cadence is NOT taken from the client: a new type inherits the channel's
    CURRENT cadence (or the default) so every email row stays uniform — the
    UI models cadence as one channel-level choice, and letting a per-type
    override slip in would desync that."""
    from capabilities.alerting.relevance import alert_types_for_role
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"ok": False, "error": "user_not_found"}
    if body.alert_type not in alert_types_for_role(db_user.role):
        return {"ok": False, "error": "irrelevant_type"}
    rows = await db.list_recipient_notification_prefs(
        db_user.account_id, "user", db_user.id)
    cadence = next((r["cadence"] for r in rows if r["channel"] == "email"),
                   _EMAIL_DEFAULT_CADENCE)
    await db.set_notification_pref(
        db_user.account_id, "user", db_user.id, "email",
        body.alert_type, enabled=body.enabled, cadence=cadence)
    return {"ok": True}


class EmailCadenceRequest(BaseModel):
    cadence: Literal["immediate", "hourly", "daily"]


@router.put("/prefs/email/cadence")
@limiter.limit("60/minute")
async def set_email_cadence(
    request: Request, body: EmailCadenceRequest,
    user: dict = Depends(get_current_user),
):
    """Set the caller's email delivery cadence (applies to every email
    type at once)."""
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"ok": False, "error": "user_not_found"}
    try:
        await db.set_channel_cadence(
            db_user.account_id, "user", db_user.id, "email", body.cadence)
    except ValueError:
        return {"ok": False, "error": "bad_cadence"}
    return {"ok": True, "cadence": body.cadence}


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
