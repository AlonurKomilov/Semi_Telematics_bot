"""Web-push channel — OS notifications that reach a CLOSED dashboard.

The third personal channel.  Unlike email (one address) a push recipient
is a SET of devices (``push_subscriptions``): ``send`` fans out to every
device the user enabled and succeeds if at least one accepted.  A dead
endpoint (push service answers 404/410 — browser gone, permission
revoked) is pruned inline, so the device list is self-healing.

Render is the SHORT form: an OS notification is a glance surface, so
title + one clipped line + a click-URL (the service worker opens it).
Transport is ``pywebpush`` (VAPID + RFC 8291 encryption), blocking →
run in a thread.  Imports only infra + the transport lib, per the
delivery-core layer guard.
"""

from __future__ import annotations

import asyncio
import json
import logging

from .channels import DeliveryResult, NotificationContent, Payload, Recipient

logger = logging.getLogger("bot.notifications")

_BODY_MAX = 180          # OS toasts truncate around here anyway


class WebPushChannel:
    key = "web_push"
    personal = True
    respects_quiet_hours = True     # a push buzzes the phone/desktop

    def render(self, recipient: Recipient, content: NotificationContent) -> Payload:
        body = (content.body or "").split("\n", 1)[0].strip()
        if len(body) > _BODY_MAX:
            body = body[:_BODY_MAX - 1] + "…"
        # Same-origin only: the service worker navigates to this URL on
        # click, so an absolute URL from a future content source must
        # never become an off-site redirect.  Relative paths only —
        # and "//host" is protocol-relative (absolute), so it's excluded.
        url = content.url if (
            content.url.startswith("/") and not content.url.startswith("//")
        ) else "/alerts"
        return Payload(
            text=body, subject=content.title.strip(), parse_mode="",
            extra={
                "url": url,
                "severity": content.severity,
                # One tag per alert type → a newer notification of the same
                # type replaces the old one instead of stacking forever.
                "tag": f"notif-{content.alert_type or 'general'}",
            },
        )

    async def send(self, recipient: Recipient, payload: Payload) -> DeliveryResult:
        from infra.platform import get_platform_db

        from capabilities.notifications.vapid import ensure_vapid

        db = get_platform_db()
        if db is None:
            return DeliveryResult(ok=False, error="no_db")
        try:
            user_id = int(recipient.id)
        except (TypeError, ValueError):
            return DeliveryResult(ok=False, error="bad_recipient")

        subs = await db.list_push_subscriptions(recipient.account_id, user_id)
        if not subs:
            return DeliveryResult(ok=False, error="no_devices")

        vapid = await ensure_vapid(db)
        data = json.dumps({
            "title": payload.subject or "Notification",
            "body": payload.text,
            "url": payload.extra.get("url", "/alerts"),
            "severity": payload.extra.get("severity", "info"),
            "tag": payload.extra.get("tag", "notif"),
        })

        delivered = 0
        for sub in subs:
            ok, dead = await asyncio.to_thread(
                _push_one, sub, data, vapid["private_pem"])
            if ok:
                delivered += 1
                await db.touch_push_subscription(sub["endpoint"])
            elif dead:
                # Browser uninstalled / permission revoked — self-heal.
                await db.prune_push_subscription(sub["endpoint"])
                logger.info("web push: pruned dead endpoint for user %s", user_id)
        if delivered:
            return DeliveryResult(ok=True, provider_ref=str(delivered))
        return DeliveryResult(ok=False, error="all_failed")


def _push_one(sub: dict, data: str, private_pem: str) -> tuple[bool, bool]:
    """One blocking send.  Returns (delivered, endpoint_is_dead)."""
    import requests
    from pywebpush import WebPushException, webpush

    from capabilities.notifications.push_endpoint import validate_push_endpoint
    from capabilities.notifications.vapid import vapid_claims_sub

    # Re-validate at SEND time (already in a worker thread, so the DNS
    # lookup is fine here) — blunts DNS rebinding: an endpoint that
    # resolved public at subscribe time but private now is refused and
    # treated as dead so it gets pruned.
    if validate_push_endpoint(sub["endpoint"]) is not None:
        logger.warning("web push: endpoint failed send-time validation — pruning")
        return False, True

    # Anti-SSRF: the endpoint is a client-supplied URL, so the transport
    # must NOT follow redirects — otherwise a "public" endpoint could 302
    # the server-side POST to an internal host, sidestepping the is_global
    # gate that only ever sees the literal endpoint.  A fresh Session per
    # call (max_redirects=0 → any 3xx raises) keeps this thread-safe under
    # the fan-out's worker pool; the `with` closes its connection pool.
    with requests.Session() as session:
        session.max_redirects = 0
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=data,
                vapid_private_key=private_pem,
                # pywebpush mutates the claims dict (adds aud/exp) — fresh per call.
                vapid_claims={"sub": vapid_claims_sub()},
                # Bounded: an unresponsive push service must not pin a worker
                # thread from the shared pool indefinitely.
                timeout=10,
                # Queue for devices that are asleep/offline rather than the
                # default drop-if-not-connected (ttl=0) — an OS notification
                # an hour late still beats one that never arrives.
                ttl=3600,
                requests_session=session,
            )
            return True, False
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                return False, True
            logger.warning("web push send failed (%s): %s", status, e)
            return False, False
        except Exception:               # transport must never crash fan-out —
            # but keep the traceback: this branch would otherwise also
            # swallow genuine bugs (a bad refactor's AttributeError) silently.
            logger.exception("web push send failed")
            return False, False
