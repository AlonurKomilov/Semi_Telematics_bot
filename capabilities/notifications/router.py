"""Notification channel API — connect / verify / unsubscribe.

router.py is interface-layer code co-located with its capability
(docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two
may import ``interfaces.api.deps``; nothing else in the feature may.
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

from capabilities.notifications.categories import categories_for_source
from capabilities.notifications.channels import parse_action
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
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"relevant_types": [], channel: {}}

    # Role-tailored alert types come from the category REGISTRY (each
    # ``alert.*`` category carries its audience rule, registered by the
    # alerting side) — never from importing alerting: the notification
    # core stays source-blind (layer boundary, test-enforced).
    relevant = [c.key.split(".", 1)[1]
                for c in categories_for_source("alert", db_user.role)]
    rows = await db.list_recipient_notification_prefs(
        db_user.account_id, "user", db_user.id)
    ch_rows = [r for r in rows if r["channel"] == channel]
    # Storage keys categories namespaced (``alert.faults``); this endpoint
    # still speaks the UI's bare alert-type vocabulary, so strip the prefix.
    types = {r["category"].split(".", 1)[-1]: bool(r["enabled"])
             for r in ch_rows if r["category"].startswith("alert.")}
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
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"ok": False, "error": "user_not_found"}
    # Registry-derived role gate (see get_channel_prefs — same rule, no
    # alerting import).
    relevant = [c.key.split(".", 1)[1]
                for c in categories_for_source("alert", db_user.role)]
    if body.alert_type not in relevant:
        return {"ok": False, "error": "irrelevant_type"}
    rows = await db.list_recipient_notification_prefs(
        db_user.account_id, "user", db_user.id)
    cadence = next((r["cadence"] for r in rows if r["channel"] == channel),
                   _CHANNEL_DEFAULT_CADENCE[channel])
    # UI speaks bare alert types; the matrix stores the namespaced category.
    await db.set_notification_pref(
        db_user.account_id, "user", db_user.id, channel,
        f"alert.{body.alert_type}", enabled=body.enabled, cadence=cadence)
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


# ── Account activity — the TARGETED (opt-out) sources ────────────────
# Alerts are broadcast + opt-IN (the matrix above).  Targeted categories
# (team.invite_accepted, future account/system notices) are opt-OUT: they
# reach every personal channel the user has connected UNLESS muted.  These
# two endpoints drive the "Account activity" section of the preferences
# page — one row per category, a toggle per personal channel, defaulting
# ON.  Telegram is a personal channel here too (targeted notices honour
# the per-category mute in the matrix), so all three columns appear.

# Personal channels, including telegram_dm — unlike the alert matrix,
# which routes Telegram through the legacy per-user columns — and the
# intrinsic in_app inbox (muting it hides a category from the bell).
AccountActivityChannel = Literal["telegram_dm", "email", "web_push", "in_app"]


@router.get("/preferences/account-activity")
@limiter.limit("30/minute")
async def get_account_activity_prefs(
    request: Request, user: dict = Depends(get_current_user),
):
    """Targeted categories with each personal channel's on/off + connection
    state.  Opt-out: a category is ON for a channel unless a pref row
    explicitly disables it.  Mandatory categories report ``mandatory:true``
    so the UI can lock their toggles on."""
    from infra.platform import get_platform_db
    from capabilities.notifications.categories import list_categories, TARGETED
    from capabilities.notifications.channels import personal_channels

    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"categories": [], "channels": {}}

    chans = personal_channels()
    # Connection state + the explicit-pref map, fetched ONCE per channel.
    # ``ready`` = the channel can actually deliver right now, using each
    # channel's own readiness rule (so the UI can grey out a column that
    # would otherwise lie): email needs a verified address, web push needs
    # at least one enabled device, Telegram needs its master switch on.
    channels: dict = {}
    pref_by_channel: dict[str, dict[str, bool]] = {}
    for ch in chans:
        conn = await db.get_notification_channel(
            db_user.account_id, "user", db_user.id, ch.key)
        verified = bool(conn and conn.get("verified"))
        master = bool(conn.get("enabled_master")) if conn else True
        if getattr(ch, "intrinsic", False):
            ready = True                    # in-app inbox: nothing to connect
        elif ch.key == "web_push":
            devices = await db.list_push_subscriptions(
                db_user.account_id, db_user.id)
            ready = bool(devices) and master
        else:
            ready = verified and master
        channels[ch.key] = {
            "connected": bool(conn), "verified": verified,
            "enabled_master": master, "ready": ready,
        }
        pref_by_channel[ch.key] = await db.get_pref_categories(
            db_user.account_id, "user", db_user.id, ch.key)

    # TARGETED categories (opt-out, personal) + broadcast SYSTEM notices
    # whose audience the caller's role passes (billing → owner/admin only)
    # — so the System section shows exactly what this person can receive,
    # never a locked billing row a driver will never get.
    role_str = getattr(db_user.role, "value", None) or str(db_user.role)

    def _listed(c) -> bool:
        if c.kind == TARGETED:
            return True
        if c.source == "system":
            return c.audience is None or bool(c.audience(role_str))
        return False

    cats = [c for c in list_categories() if _listed(c)]
    cats.sort(key=lambda c: (c.source, c.label))
    out = []
    for c in cats:
        per_channel = {}
        for ch in chans:
            prefs = pref_by_channel[ch.key]
            specific = prefs.get(c.key)                      # None | True | False
            # Opt-out: ON by default; mandatory can't be turned off.  The
            # '*' blanket row decides where there is no specific one —
            # the SAME precedence notify_user applies (service.py), so a
            # toggle can never show ON for a category delivery mutes.
            if c.mandatory:
                per_channel[ch.key] = True
            elif specific is not None:
                per_channel[ch.key] = specific
            else:
                per_channel[ch.key] = prefs.get("*", True)
        out.append({
            "key": c.key, "label": c.label, "source": c.source,
            "mandatory": c.mandatory, "channels": per_channel,
        })
    return {"categories": out, "channels": channels}


class AccountActivityRequest(BaseModel):
    category: str
    channel: AccountActivityChannel
    enabled: bool


@router.put("/preferences/account-activity")
@limiter.limit("60/minute")
async def set_account_activity_pref(
    request: Request, body: AccountActivityRequest,
    user: dict = Depends(get_current_user),
):
    """Mute / un-mute one targeted category on one personal channel.

    Guards: the category must be a REGISTERED targeted one (defence in
    depth — a broadcast alert type belongs to the matrix, not here), and a
    mandatory category can't be muted."""
    from infra.platform import get_platform_db
    from capabilities.notifications.categories import get_category, TARGETED

    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"ok": False, "error": "user_not_found"}
    cat = get_category(body.category)
    if cat is None or cat.kind != TARGETED:
        return {"ok": False, "error": "unknown_category"}
    if cat.mandatory:
        return {"ok": False, "error": "mandatory"}
    # Targeted notices are immediate (no digest); keep the row's cadence
    # uniform with that so the delivery path never batches an FYI.
    await db.set_notification_pref(
        db_user.account_id, "user", db_user.id, body.channel,
        body.category, enabled=body.enabled, cadence="immediate")
    return {"ok": True}


# ── In-app inbox — the bell's persisted feed (authed) ────────────────
# Rows are written by the intrinsic in_app channel (send() persists), so
# everything here is read/mark-read only.  Alerts are NOT in this feed —
# the bell reads those from /alerts/pending (dual-source by design).


@router.get("/inbox")
@limiter.limit("60/minute")
async def get_inbox(
    request: Request,
    source: str = "",
    before_id: int | None = None,
    limit: int = 30,
    user: dict = Depends(get_current_user),
):
    """Newest-first page of the caller's notices + their unread count.
    ``source`` filters to one namespace tab, or a comma-separated bucket of
    them (``team,ai`` = Activity); ``before_id`` is keyset pagination.

    No permission check on ``source``: inbox rows are per-USER, so asking
    for a bucket you never receive returns your own empty page, never
    someone else's notices."""
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"notices": [], "unread": 0}
    notices = await db.list_inbox_notices(
        db_user.account_id, db_user.id,
        source=source, limit=limit, before_id=before_id)
    unread = await db.count_inbox_unread(db_user.account_id, db_user.id)

    def _meta(meta_raw: str) -> dict:
        """Parsed meta object, {} on ANY malformed value — one bad row
        must never 500 the whole feed."""
        if not meta_raw:
            return {}
        try:
            import json as _json
            parsed = _json.loads(meta_raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _action(meta: dict) -> dict | None:
        """The row's inline action button ({label, url}).  Validity is
        ``channels.parse_action`` — the ONE definition of the
        relative-only rule, shared with the email renderer, because a
        security rule enforced in two places drifts."""
        parsed = parse_action(meta)
        return None if parsed is None else {"label": parsed[0], "url": parsed[1]}

    out = []
    for n in notices:
        meta = _meta(n.get("meta", ""))
        out.append(
            {"id": n["id"], "category": n["category"], "source": n["source"],
             "severity": n["severity"], "title": n["title"], "body": n["body"],
             "url": n["url"], "created_at": n["created_at"],
             "context": str(meta.get("context", "") or ""),
             "action": _action(meta),
             "read": bool(n["read_at"])})
    return {"notices": out, "unread": unread}


class InboxReadRequest(BaseModel):
    ids: list[int]


@router.post("/inbox/read")
@limiter.limit("60/minute")
async def mark_inbox_read_route(
    request: Request, body: InboxReadRequest,
    user: dict = Depends(get_current_user),
):
    """Mark specific notices read.  Storage scopes the UPDATE to the
    caller's own rows, so a foreign id silently matches nothing."""
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"ok": False, "error": "user_not_found"}
    n = await db.mark_inbox_read(db_user.account_id, db_user.id, body.ids)
    unread = await db.count_inbox_unread(db_user.account_id, db_user.id)
    return {"ok": True, "marked": n, "unread": unread}


@router.post("/inbox/read-all")
@limiter.limit("30/minute")
async def mark_inbox_all_read_route(
    request: Request, user: dict = Depends(get_current_user),
):
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"ok": False, "error": "user_not_found"}
    n = await db.mark_inbox_all_read(db_user.account_id, db_user.id)
    return {"ok": True, "marked": n, "unread": 0}


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
    import asyncio
    from urllib.parse import urlsplit

    from infra.platform import get_platform_db

    from capabilities.notifications.push_endpoint import (
        MAX_DEVICES_PER_USER,
        validate_push_endpoint,
    )
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        return {"ok": False, "error": "user_not_found"}
    ep = body.endpoint.strip()
    # Anti-SSRF gate (https-only, no userinfo, every resolved IP must be
    # globally routable) — DNS is blocking, so run it in a thread.
    err = await asyncio.to_thread(validate_push_endpoint, ep)
    if err:
        return {"ok": False, "error": err}
    # Real keys are ~87 (p256dh) / ~22 (auth) chars — a missing key would
    # store a device that "counts" in the UI but can never deliver.
    if not (32 <= len(body.keys.p256dh) <= 512) or not (8 <= len(body.keys.auth) <= 512):
        return {"ok": False, "error": "bad_keys"}

    devices = await db.list_push_subscriptions(db_user.account_id, db_user.id)
    if (len(devices) >= MAX_DEVICES_PER_USER
            and ep not in {d["endpoint"] for d in devices}):
        return {"ok": False, "error": "device_limit"}

    # Distinct-host telemetry — the data for a vendor allowlist should
    # this ever show abuse.
    logger.info("push subscribe host=%s account=%s",
                urlsplit(ep).hostname, db_user.account_id)

    moved = await db.add_push_subscription(
        db_user.account_id, db_user.id, endpoint=ep,
        p256dh=body.keys.p256dh, auth=body.keys.auth,
        device_label=_device_label(request))
    if moved:
        # Shared-device handoff: the endpoint moved to this caller and the
        # previous owner's channel state was repaired in the same
        # transaction.  Cross-ACCOUNT moves get warning level — the
        # forensic trail for tenant-boundary questions.
        log = logger.warning if moved["account_id"] != db_user.account_id else logger.info
        log("push endpoint reassigned: account %s user %s -> account %s user %s",
            moved["account_id"], moved["user_id"],
            db_user.account_id, db_user.id)

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
