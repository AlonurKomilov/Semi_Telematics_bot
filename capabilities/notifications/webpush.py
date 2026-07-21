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

    def render(self, recipient: Recipient, content: NotificationContent) -> Payload:
        body = (content.body or "").split("\n", 1)[0].strip()
        if len(body) > _BODY_MAX:
            body = body[:_BODY_MAX - 1] + "…"
        return Payload(
            text=body, subject=content.title.strip(), parse_mode="",
            extra={
                "url": content.url or "/alerts",
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
    from pywebpush import WebPushException, webpush

    from capabilities.notifications.vapid import vapid_claims_sub

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
        )
        return True, False
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410):
            return False, True
        logger.warning("web push send failed (%s): %s", status, e)
        return False, False
    except Exception as e:              # transport must never crash fan-out
        logger.warning("web push send failed: %s", e)
        return False, False
