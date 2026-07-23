"""In-app channel — the inbox behind the bell, as a Channel.

The intrinsic channel: ``send()`` PERSISTS a ``notification_inbox`` row
instead of transmitting anything.  Making the inbox a channel (rather
than a side-write in the service) means every row has already passed the
exact gates a real delivery passes — category audience, the caller's
``recipient_filter`` scoping predicate, and per-user mutes — via the same
``dispatch()`` / ``notify_user()`` fan-out.  No parallel write path to
drift.

``intrinsic = True``: no address to connect or verify, always available,
so the service skips the connection check and resolves broadcast
recipients opt-OUT (see ``channels.Channel``).  Cadence is always
immediate — batching a persisted record is meaningless.

Alerts do NOT flow through here (the bell reads them from
``alert_history``); this channel records the non-alert sources
(team.*, system.*).
"""

from __future__ import annotations

import json
import logging

from capabilities.notifications.channels import (
    DeliveryResult,
    NotificationContent,
    Payload,
    Recipient,
)

logger = logging.getLogger("notifications.inapp")


class InAppChannel:
    key = "in_app"
    personal = True
    intrinsic = True

    def render(self, recipient: Recipient, content: NotificationContent) -> Payload:
        """No markup dialect — the inbox stores the RAW semantic fields and
        the UI escapes at render time like any React text.  The structured
        record rides in ``Payload.extra``."""
        return Payload(
            text=content.body, subject=content.title, parse_mode="",
            extra={
                "category": content.category,
                "severity": content.severity,
                "url": content.url,
                "meta": json.dumps(content.meta) if content.meta else "",
            },
        )

    async def send(self, recipient: Recipient, payload: Payload) -> DeliveryResult:
        if recipient.type != "user":
            # The inbox is per-user; a shared/topic recipient has no inbox.
            return DeliveryResult(ok=False, error="not_a_user")
        try:
            user_id = int(recipient.id)
        except (TypeError, ValueError):
            return DeliveryResult(ok=False, error="bad_user_id")
        from infra.platform import get_platform_db
        db = get_platform_db()
        try:
            await db.add_inbox_notice(
                recipient.account_id, user_id,
                category=payload.extra.get("category", ""),
                title=payload.subject or payload.text,
                body=payload.text,
                severity=payload.extra.get("severity", "info"),
                url=payload.extra.get("url", ""),
                meta=payload.extra.get("meta", ""),
            )
            return DeliveryResult(ok=True)
        except Exception as e:
            logger.error("inbox write failed (acct=%s user=%s): %s",
                         recipient.account_id, user_id, e)
            return DeliveryResult(ok=False, error="store_failed")
