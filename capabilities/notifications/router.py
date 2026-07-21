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


# ── Per-type preferences for matrix channels (email + web push) ──────

# Per-channel default cadence.  Email defaults to a batched digest —
# per-alert email at fleet volume is unusable (docs §8); push is a glance
# channel like Telegram, so it defaults to immediate.  This dict is ALSO
# the allowlist of matrix channels these endpoints may touch —
# telegram_dm stays on the legacy /user/me/alerts path until the matrix
# reader flip.
_CHANNEL_DEFAULT_CADENCE = {"email": "daily", "web_push": "immediate"}
_EMAIL_DEFAULT_CADENCE = _CHANNEL_DEFAULT_CADENCE["email"]

MatrixChannel = Literal["email", "web_push"]


@router.get("/prefs/{channel}")
@limiter.limit("30/minute")
async def get_channel_prefs(
    request: Request, channel: MatrixChannel,
    user: dict = Depends(get_current_user),
):
    """The caller's preferences for one matrix channel: the role-tailored
    alert types, which are on, the channel cadence, and the connection
    state.  The state is returned under the channel's own key (``email`` /
    ``web_push``) so each channel card reads ``body[channel]``."""
    from capabilities.alerting.relevance import alert_types_for_role
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"relevant_types": [], channel: {}}

    relevant = alert_types_for_role(db_user.role)
    rows = await db.list_recipient_notification_prefs(
        db_user.account_id, "user", db_user.id)
    ch_rows = [r for r in rows if r["channel"] == channel]
    types = {r["alert_type"]: bool(r["enabled"]) for r in ch_rows}
    # Channel cadence = the shared cadence of the channel's rows (kept
    # uniform by set_channel_cadence); default when nothing is enabled yet.
    cadence = next((r["cadence"] for r in ch_rows),
                   _CHANNEL_DEFAULT_CADENCE[channel])
    conn = await db.get_notification_channel(
        db_user.account_id, "user", db_user.id, channel)

    state = {
        "connected": bool(conn),
        "verified": bool(conn and conn.get("verified")),
        "address": (conn or {}).get("address", ""),
        "enabled_master": bool(conn.get("enabled_master")) if conn else True,
        "cadence": cadence,
        "types": {t: types.get(t, False) for t in relevant},
    }
    if channel == "web_push":
        devices = await db.list_push_subscriptions(db_user.account_id, db_user.id)
        state["devices"] = [
            {"id": d["id"], "endpoint": d["endpoint"],
             "device_label": d["device_label"], "created_at": d["created_at"],
             "last_ok_at": d["last_ok_at"]}
            for d in devices
        ]
    return {"relevant_types": relevant, channel: state}


class ChannelTypeRequest(BaseModel):
    alert_type: str
    enabled: bool


@router.put("/prefs/{channel}/type")
@limiter.limit("60/minute")
async def set_channel_type(
    request: Request, channel: MatrixChannel, body: ChannelTypeRequest,
    user: dict = Depends(get_current_user),
):
    """Toggle one alert type for a matrix channel.  Role-gated: a type the
    caller's role can't see is rejected (defence in depth — the UI
    wouldn't render it).

    Cadence is NOT taken from the client: a new type inherits the
    channel's CURRENT cadence (or its default) so every row stays uniform
    — the UI models cadence as one channel-level choice, and letting a
    per-type override slip in would desync that."""
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
    cadence = next((r["cadence"] for r in rows if r["channel"] == channel),
                   _CHANNEL_DEFAULT_CADENCE[channel])
    await db.set_notification_pref(
        db_user.account_id, "user", db_user.id, channel,
        body.alert_type, enabled=body.enabled, cadence=cadence)
    return {"ok": True}


class ChannelCadenceRequest(BaseModel):
    cadence: Literal["immediate", "hourly", "daily"]


@router.put("/prefs/{channel}/cadence")
@limiter.limit("60/minute")
async def set_channel_cadence_route(
    request: Request, channel: MatrixChannel, body: ChannelCadenceRequest,
    user: dict = Depends(get_current_user),
):
    """Set a matrix channel's delivery cadence (applies to every one of
    its types at once)."""
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"ok": False, "error": "user_not_found"}
    try:
        await db.set_channel_cadence(
            db_user.account_id, "user", db_user.id, channel, body.cadence)
    except ValueError:
        return {"ok": False, "error": "bad_cadence"}
    return {"ok": True, "cadence": body.cadence}


# ── Web push: device subscribe / unsubscribe (authed) ────────────────

def _device_label(request: Request) -> str:
    """Human-ish device name from the User-Agent — server-derived so the
    label can't be spoofed into something misleading, and good enough for
    a 'which device is this' list."""
    ua = request.headers.get("user-agent", "")
    browser = next((b for b in ("Edg", "OPR", "Firefox", "Chrome", "Safari")
                    if b in ua), "Browser")
    browser = {"Edg": "Edge", "OPR": "Opera"}.get(browser, browser)
    os_name = next((o for o, marker in (
        ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
        ("Windows", "Windows"), ("macOS", "Mac OS X"), ("Linux", "Linux"),
    ) if marker in ua), "device")
    return f"{browser} · {os_name}"


@router.get("/push/vapid-key")
@limiter.limit("30/minute")
async def get_vapid_key(request: Request, user: dict = Depends(get_current_user)):
    """The application-server public key the browser needs to subscribe.
    Generated once and stable forever (subscriptions are bound to it)."""
    from infra.platform import get_platform_db

    from capabilities.notifications.vapid import ensure_vapid
    vapid = await ensure_vapid(get_platform_db())
    return {"public_key": vapid["public_key"]}


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeRequest(BaseModel):
    endpoint: str
    keys: PushKeys


@router.post("/push/subscribe")
@limiter.limit("10/minute")
async def push_subscribe(
    request: Request, body: PushSubscribeRequest,
    user: dict = Depends(get_current_user),
):
    """Store this browser's push subscription for the caller.  The
    permission grant in the browser IS the verification (unlike email
    there is no address to prove), so the channel row goes verified as
    soon as one device exists.  The user's master switch is preserved —
    adding a device must not silently re-enable a channel they turned
    off."""
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"ok": False, "error": "user_not_found"}
    ep = body.endpoint.strip()
    if not ep.startswith("https://") or len(ep) > 1024:
        return {"ok": False, "error": "bad_endpoint"}
    if len(body.keys.p256dh) > 512 or len(body.keys.auth) > 512:
        return {"ok": False, "error": "bad_keys"}

    await db.add_push_subscription(
        db_user.account_id, db_user.id, endpoint=ep,
        p256dh=body.keys.p256dh, auth=body.keys.auth,
        device_label=_device_label(request))
    existing = await db.get_notification_channel(
        db_user.account_id, "user", db_user.id, "web_push")
    await db.upsert_notification_channel(
        db_user.account_id, "user", db_user.id, "web_push",
        address="devices", verified=True,
        enabled_master=existing.get("enabled_master", True) if existing else True)
    return {"ok": True}


class PushUnsubscribeRequest(BaseModel):
    endpoint: str


@router.post("/push/unsubscribe")
@limiter.limit("10/minute")
async def push_unsubscribe(
    request: Request, body: PushUnsubscribeRequest,
    user: dict = Depends(get_current_user),
):
    """Remove one device (scoped to the caller — you can only ever drop
    your own).  When the LAST device goes, the channel row un-verifies so
    the delivery gate stops selecting this user (nothing left to push to,
    and no digest items buffering for a device-less channel)."""
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"ok": False, "error": "user_not_found"}
    removed = await db.remove_push_subscription(
        db_user.account_id, db_user.id, body.endpoint.strip())
    remaining = await db.list_push_subscriptions(db_user.account_id, db_user.id)
    if not remaining:
        existing = await db.get_notification_channel(
            db_user.account_id, "user", db_user.id, "web_push")
        if existing:
            await db.upsert_notification_channel(
                db_user.account_id, "user", db_user.id, "web_push",
                address="devices", verified=False,
                enabled_master=existing.get("enabled_master", True))
    return {"ok": True, "removed": removed, "devices_left": len(remaining)}


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
