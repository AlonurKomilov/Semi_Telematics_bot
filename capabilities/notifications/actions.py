"""Notification actions — button presses routed back to the source.

Part of the alert-DM migration (capabilities/alerting/docs/alert-dm-migration.md):
the spine renders a content's ``actions`` as buttons and, when one is
pressed, dispatches the press to the SOURCE's registered handler.  The
spine never knows what "acknowledge" means — it only carries the press.

Contract:

  • callback data = ``notif_act:{correlation_key}:{action_id}``
    (Telegram caps callback data at 64 bytes — ``build_action_callback_
    data`` returns None when a key is too long, and the renderer then
    drops the buttons rather than shipping dead ones).
  • ``correlation_key`` is namespaced ``{source}:{rest}`` (mirrors
    category namespacing: ``alert:123``) — the namespace picks the
    handler: key ``{source}.{action_id}`` (e.g. ``alert.ack``).
  • a handler is ``async (ActionContext) -> str | None``; the returned
    string becomes the callback answer toast (None → silent ack).

Sources register FROM their side (same one-way seam as categories):

    from capabilities.notifications.actions import register_action_handler
    register_action_handler("alert.ack", _handle_ack)

This module imports only stdlib + the telegram lib (lazily) — never a
source; the layer-boundary test enforces it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "notif_act:"
# Telegram's hard limit for callback_data is 64 BYTES.
MAX_CALLBACK_DATA = 64


@dataclass(frozen=True)
class ActionContext:
    """Everything a source handler gets about one button press.

    ``message_handle`` is the pressed message's edit-address (same shape
    the Telegram channel's ``send`` returns), so a handler can edit THAT
    message directly — though most will call ``update_delivery()`` with
    the correlation_key to update every recorded copy at once.
    """
    account_id: int
    correlation_key: str
    action_id: str
    source: str                     # correlation_key namespace ("alert")
    presser_telegram_id: int
    presser_name: str = ""
    channel: str = "telegram_dm"
    message_handle: dict = field(default_factory=dict)
    message_text: str = ""          # pressed message's current plain text


ActionHandler = Callable[[ActionContext], Awaitable["str | None"]]

_HANDLERS: dict[str, ActionHandler] = {}


def register_action_handler(key: str, handler: ActionHandler) -> ActionHandler:
    """Register (last-wins) the handler for ``{source}.{action_id}``."""
    _HANDLERS[key] = handler
    return handler


def get_action_handler(key: str) -> ActionHandler | None:
    return _HANDLERS.get(key)


def build_action_callback_data(correlation_key: str, action_id: str) -> str | None:
    """The callback-data string for one button — or None when it would
    exceed Telegram's 64-byte cap (the caller must then drop the button;
    a dead button is worse than none)."""
    data = f"{CALLBACK_PREFIX}{correlation_key}:{action_id}"
    if len(data.encode("utf-8")) > MAX_CALLBACK_DATA:
        logger.warning(
            "action callback data too long (%d bytes) — correlation keys "
            "must stay short: %r", len(data.encode("utf-8")), data)
        return None
    return data


def parse_action_callback_data(data: str) -> tuple[str, str] | None:
    """``notif_act:{correlation_key}:{action_id}`` → (key, action) or None.

    The correlation_key itself may contain colons (``alert:123``), so the
    action id is split from the RIGHT."""
    if not data.startswith(CALLBACK_PREFIX):
        return None
    rest = data[len(CALLBACK_PREFIX):]
    correlation_key, sep, action_id = rest.rpartition(":")
    if not sep or not correlation_key or not action_id:
        return None
    return correlation_key, action_id


async def handle_action_callback(update, context) -> None:
    """PTB callback entry for ``notif_act:*`` presses (registered by the
    bot's callback router).  Resolves the account from the serving bot
    (``bot_data['account_id']``), routes to the source handler, and
    answers the callback with the handler's toast."""
    query = update.callback_query
    parsed = parse_action_callback_data(query.data or "")
    if parsed is None:
        await query.answer("Unknown action", show_alert=False)
        return
    correlation_key, action_id = parsed
    source = correlation_key.split(":", 1)[0]
    handler = get_action_handler(f"{source}.{action_id}")
    if handler is None:
        logger.warning("no handler for notification action %s.%s",
                       source, action_id)
        await query.answer("This action is no longer available",
                           show_alert=False)
        return

    account_id = context.bot_data.get("account_id")
    if account_id is None:
        await query.answer("Unavailable", show_alert=False)
        return

    msg = query.message
    handle: dict = {}
    if msg is not None:
        handle = {"chat_id": msg.chat_id, "message_id": msg.message_id,
                  "kind": "photo" if getattr(msg, "photo", None) else "text"}
        thread_id = getattr(msg, "message_thread_id", None)
        if thread_id is not None:
            handle["thread_id"] = thread_id

    user = query.from_user
    ctx = ActionContext(
        account_id=int(account_id),
        correlation_key=correlation_key,
        action_id=action_id,
        source=source,
        presser_telegram_id=int(user.id) if user else 0,
        presser_name=(getattr(user, "full_name", "") or
                      getattr(user, "first_name", "") or "") if user else "",
        message_handle=handle,
        message_text=(getattr(msg, "text", None)
                      or getattr(msg, "caption", None) or "") if msg else "",
    )
    try:
        toast = await handler(ctx)
    except Exception:
        logger.exception("notification action handler %s.%s failed",
                         source, action_id)
        await query.answer("Something went wrong — try again",
                           show_alert=False)
        return
    await query.answer(toast or "Done", show_alert=False)
