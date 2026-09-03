"""Sign-in security notices — the ``system.security`` source.

When a dashboard login arrives from a device this user has no active
session on, they get a notice on every personal channel they've
connected (Telegram DM, email, push) plus the in-app inbox: "New sign-in
— Chrome · Windows.  If this wasn't you, review your active sessions."

Lives in the interface layer deliberately: a LOGIN is an interface
event, and ``mint_session_token`` (interfaces/api/auth.py) is the single
choke point every login flow funnels through — the producer belongs
beside its event.  Same one-way seam as every source: imports the
notification service, never the reverse.

MANDATORY category: a compromised account is exactly the situation where
"the attacker muted my notifications" must not work — the toggle renders
locked-on and delivery ignores pref rows.  TARGETED: the notice is about
ONE user's account; each person only ever hears about their own
sign-ins, whatever their role.

``NOTIFICATIONS_SECURITY_EVENTS`` defaults ON — a silently-off security
control is worse than none; the env var is a kill switch for incident
rollback, not a rollout gate.  New-DEVICE only (matched against the
user's active sessions by device label), so a daily login from the same
browser never notifies.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from capabilities.notifications import NotificationContent, notify_user
from capabilities.notifications.categories import (
    TARGETED,
    NotificationCategory,
    register_category,
)

logger = logging.getLogger(__name__)

SECURITY_SIGNIN = "system.security"

register_category(NotificationCategory(
    key=SECURITY_SIGNIN,
    label="Security & sign-in",
    kind=TARGETED,
    mandatory=True,
))

_SECURITY_EVENTS_ENABLED = os.getenv(
    "NOTIFICATIONS_SECURITY_EVENTS", "1").strip().lower() not in ("0", "false")


async def is_new_device(db: Any, user_id: int, device_label: str) -> bool:
    """True when the user has NO active session with this device label.

    Coarse on purpose (label = "Chrome · Windows"): false negatives
    (same label, different machine) are acceptable; the alternative —
    fingerprinting — isn't worth it for a notice.  Fail-CLOSED to
    "not new" on any error so a storage blip can't spam sign-in notices.
    """
    try:
        sessions = await db.list_user_sessions(user_id)
        return not any(
            (s.get("device_label") or "") == device_label for s in sessions)
    except Exception:
        logger.warning("new-device check failed for user %s", user_id,
                       exc_info=True)
        return False


def signin_notice_action(session_id: int | None) -> dict[str, str]:
    """The notice's one button.  With the session known it is
    "Disconnect this session" → Active Sessions with THAT row focused,
    where the existing confirm → revoke runs; the URL is only a pointer,
    so a mail scanner prefetching the link revokes nothing.  Without it
    (the row failed to insert) the generic review link."""
    if session_id:
        return {"label": "Disconnect this session",
                "url": f"/profile?session={int(session_id)}"}
    return {"label": "Review sessions", "url": "/profile"}


async def announce_new_device_signin(
    db: Any, account_id: int, user_id: int, *,
    device_label: str, ip: str = "", session_id: int | None = None,
) -> None:
    """Tell the user their account just signed in from a new device.

    Fully non-fatal — a notification failure must never break a login.
    The new-device CHECK runs before the session row is inserted (else it
    matches itself); this announce runs after, so ``session_id`` is the
    row the notice can point at.
    """
    if not _SECURITY_EVENTS_ENABLED:
        return
    try:
        where = f" from {ip}" if ip else ""
        action = signin_notice_action(session_id)
        await notify_user(
            db, account_id, user_id,
            NotificationContent(
                title=f"New sign-in — {device_label or 'Unknown device'}",
                body=(
                    f"Your account just signed in on a new device{where}. "
                    "If this was you, no action is needed. If not, review "
                    "your active sessions and sign out anything you don't "
                    "recognize."
                ),
                category=SECURITY_SIGNIN,
                severity="info",
                url=action["url"],
                meta={"context": "Security", "action": action},
            ),
        )
    except Exception:
        logger.error("sign-in notice failed (acct=%d user=%d)",
                     account_id, user_id, exc_info=True)
