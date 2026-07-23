"""Invites → the Notifications service: the first NON-alert source.

This is the worked example of the TARGETED path (``notify_user``): when
someone accepts a team invite, the person who invited them hears about it
on whatever personal channels they've connected — Telegram DM, email, web
push — UNLESS they muted it (opt-out).  Alerts are broadcast (fan out to a
role); this is the other recipient model — one specific user, the actor
who cares — and it proves the notification spine handles more than alerts.

The same one-way seam alerting uses: a feature imports the notification
service (never the reverse) and registers its own category.  Importing
this module performs the registration, so both the API process (the
preferences UI needs the category to render a toggle) and the bot process
(which actually redeems invites) share the same catalog.

Delivery is gated by ``NOTIFICATIONS_TEAM_EVENTS`` (default off) so this
brand-new message type switches on deliberately — the same controlled-
rollout discipline as the alert channels (``NOTIFICATIONS_LIVE_DISPATCH``).
Registration is unconditional: the category exists for the prefs UI even
while delivery is dark.
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

INVITE_ACCEPTED = "team.invite_accepted"

# Registered unconditionally at import — the preferences UI lists it as a
# toggle even while delivery is flag-dark.  TARGETED + non-mandatory: an
# inviter who doesn't care can turn it off.
register_category(NotificationCategory(
    key=INVITE_ACCEPTED,
    label="Someone accepts your invite",
    kind=TARGETED,
))

_TEAM_EVENTS_ENABLED = os.getenv(
    "NOTIFICATIONS_TEAM_EVENTS", "0") not in ("0", "false", "False")


async def announce_invite_accepted(
    db: Any,
    code: str,
    new_user: Any,
    *,
    role_display: str = "",
) -> None:
    """Tell whoever sent invite ``code`` that ``new_user`` just accepted it.

    Safe to call right after a successful ``redeem_invite`` — it is fully
    non-fatal (a notification failure must never undo a completed
    redemption; the user is already in the account) and self-gating:

      • no-op unless ``NOTIFICATIONS_TEAM_EVENTS`` is on,
      • no-op for a system/operator-minted invite (no human inviter),
      • no-op if the inviter would be notifying themselves.

    ``code`` is looked up (not passed as an id) because ``created_by`` is
    immutable on the invite row and ``get_invite`` still returns it after
    redemption — so the caller doesn't have to thread the inviter through.
    """
    if not _TEAM_EVENTS_ENABLED:
        return
    try:
        invite = await db.get_invite(code)
        inviter_id = getattr(invite, "created_by", None) if invite else None
        if not inviter_id or inviter_id < 0:
            return                       # operator/system invite — no one to tell
        if new_user is not None and inviter_id == getattr(new_user, "id", None):
            return                       # don't notify yourself

        name = (getattr(new_user, "display_name", "") or "Someone").strip()
        role = (role_display or "").strip()
        body = f"{name} joined as {role}." if role else f"{name} joined your team."

        await notify_user(
            db,
            getattr(new_user, "account_id"),
            inviter_id,
            NotificationContent(
                title="Invite accepted",
                body=body,
                category=INVITE_ACCEPTED,
                severity="info",
            ),
        )
    except Exception as exc:
        logger.error(
            "announce_invite_accepted failed (code=%s): %s",
            code, exc, exc_info=True,
        )
