"""AI → the Notifications service: the ``ai.*`` source.

Third notification source (after alerts and team activity): when an AI
proposal the user approved finishes executing, a persistent record lands
in THEIR in-app inbox — "the AI did X under your name" as a durable,
glanceable trail beside the audit log.

Deliberately **inbox-only** (``channels=["in_app"]``): the approver was
on-screen when it ran, so an email or push about it would be noise — the
value is the record, not the interruption.  Same one-way seam as every
source: this module imports the notification service, never the reverse.

``NOTIFICATIONS_AI_EVENTS`` defaults ON (unlike the outward-delivering
team flag) because this writes one row to the acting user's own inbox and
nothing else — the env var is a kill switch, not a rollout gate.  Muting
``ai.action_executed`` in Account activity turns it off per user.
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

AI_ACTION_EXECUTED = "ai.action_executed"

register_category(NotificationCategory(
    key=AI_ACTION_EXECUTED,
    label="AI completes an approved action",
    kind=TARGETED,
))

_AI_EVENTS_ENABLED = os.getenv(
    "NOTIFICATIONS_AI_EVENTS", "1") not in ("0", "false", "False")


def _humanize(tool: str) -> str:
    return (tool or "action").replace("_", " ").strip().capitalize()


async def announce_ai_action_executed(
    db: Any, account_id: int, user_id: int, *, tool: str,
    result: dict | None = None,
) -> None:
    """Record one executed AI action in the approver's inbox.

    Fully non-fatal — the action is already executed and finalized; a
    notification failure must never surface as an execution error.
    """
    if not _AI_EVENTS_ENABLED:
        return
    try:
        summary = ""
        if isinstance(result, dict):
            for key in ("summary", "message"):
                val = result.get(key)
                if isinstance(val, str) and val.strip():
                    summary = val.strip()
                    break
        await notify_user(
            db, account_id, user_id,
            NotificationContent(
                title=f"AI completed: {_humanize(tool)}",
                body=summary or "Executed after your approval.",
                category=AI_ACTION_EXECUTED,
                severity="info",
                url="/ai/chat",
                meta={"context": "AI"},
            ),
            channels=["in_app"],
        )
    except Exception as exc:
        logger.error(
            "announce_ai_action_executed failed (acct=%d user=%d tool=%s): %s",
            account_id, user_id, tool, exc, exc_info=True,
        )
