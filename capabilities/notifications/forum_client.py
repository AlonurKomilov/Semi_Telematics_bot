"""Thin async wrappers around python-telegram-bot's forum-topic API.

The Bot API exposes everything we need — these helpers exist so the
rest of the codebase can talk in domain terms (``create_topic_for``,
``check_setup_preconditions``) instead of re-deriving the call
shapes at every call site.  All functions raise on Telegram error
so callers can decide locally how to handle the failure (mark
setup_status='partial', skip and continue, surface to admin, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from telegram import Bot
from telegram.error import BadRequest, Forbidden, TelegramError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ForumPrecheck:
    """Result of ``check_setup_preconditions``.

    All four flags must be True before ``/setupforum`` can succeed.
    ``error_hint`` is a human-readable explanation pointing the admin
    at the specific missing setting (so the bot can reply with a
    concrete next step instead of "something is wrong").
    """
    is_supergroup:    bool
    is_forum_enabled: bool
    bot_is_admin:     bool
    can_manage_topics: bool
    chat_title:       str
    error_hint:       str

    @property
    def ok(self) -> bool:
        return all((
            self.is_supergroup,
            self.is_forum_enabled,
            self.bot_is_admin,
            self.can_manage_topics,
        ))


async def check_setup_preconditions(bot: Bot, chat_id: int) -> ForumPrecheck:
    """Verify the chat is a forum and the bot has topic-management rights.

    Probes Telegram for the four conditions ``/setupforum`` needs:
      1. Chat is a supergroup (not basic group or channel)
      2. Forum/Topics is enabled on it (group setting only the owner can flip)
      3. The bot is an administrator
      4. The bot's admin rights include ``can_manage_topics``

    Returns a ``ForumPrecheck`` with one specific ``error_hint`` so the
    caller can show the admin exactly which Telegram setting to fix.
    """
    try:
        chat = await bot.get_chat(chat_id)
    except Forbidden:
        return ForumPrecheck(False, False, False, False, "",
                             "Bot is not a member of this chat. Add @"
                             + (bot.username or "the bot") + " to the group first.")
    except TelegramError as e:
        return ForumPrecheck(False, False, False, False, "",
                             f"Could not read chat: {e}")

    is_supergroup    = chat.type == "supergroup"
    is_forum_enabled = bool(getattr(chat, "is_forum", False))
    chat_title       = chat.title or ""

    if not is_supergroup:
        return ForumPrecheck(False, is_forum_enabled, False, False, chat_title,
                             "Forum routing only works in Telegram supergroups. "
                             "Convert this chat or create a new group with Topics enabled.")

    if not is_forum_enabled:
        return ForumPrecheck(True, False, False, False, chat_title,
                             "Topics are not enabled on this group. "
                             "Open the group's settings → Topics → toggle Enable Topics.")

    # Check the bot's membership status + topic-management right.
    try:
        me = await bot.get_chat_member(chat_id, bot.id)
    except TelegramError as e:
        return ForumPrecheck(True, True, False, False, chat_title,
                             f"Could not read bot's membership: {e}")

    bot_is_admin = me.status in ("administrator", "creator")
    can_manage_topics = bool(getattr(me, "can_manage_topics", False)) or me.status == "creator"

    if not bot_is_admin:
        return ForumPrecheck(True, True, False, False, chat_title,
                             "Promote the bot to administrator in this group "
                             "(with the 'Manage Topics' permission checked).")

    if not can_manage_topics:
        return ForumPrecheck(True, True, True, False, chat_title,
                             "The bot is an admin but missing the 'Manage Topics' "
                             "permission. Edit the bot's admin rights and enable it.")

    return ForumPrecheck(True, True, True, True, chat_title, "")


async def create_topic(
    bot: Bot, chat_id: int, *, name: str, icon_color: int,
) -> int:
    """Create a forum topic and return its ``message_thread_id``.

    Uses a longer read-timeout than the default (15 s vs the library's
    5 s) because forum-topic creation under burst conditions has been
    observed to stall server-side long enough to TimedOut a default
    request.  When a TimedOut happens the topic is often still created
    on Telegram's side; without the longer timeout the caller can't
    capture the thread_id and ends up retrying — which creates a
    duplicate topic in the group.
    """
    topic = await bot.create_forum_topic(
        chat_id=chat_id, name=name, icon_color=icon_color,
        read_timeout=15, write_timeout=15, connect_timeout=10,
    )
    logger.info("Created forum topic '%s' (thread_id=%s) in chat %d",
                name, topic.message_thread_id, chat_id)
    return topic.message_thread_id


# Raised by edit_topic when Telegram says the rename is a no-op.
# Callers should treat this as success and update their snapshot
# anyway — the end state (Telegram side) already matches the spec.
class TopicAlreadyNamed(Exception):
    """Telegram returned ``Topic_not_modified``: the rename is a no-op."""


async def edit_topic(
    bot: Bot, chat_id: int, message_thread_id: int, *, name: str | None = None,
) -> bool:
    """Rename a topic.  ``None`` for name means "leave unchanged".

    Returns True on success.  Telegram only allows editing the name +
    icon emoji; the icon *colour* is fixed at creation time.

    When Telegram returns ``Topic_not_modified`` (i.e. the rename
    target equals the current name), raises ``TopicAlreadyNamed`` so
    callers can distinguish "topic state is fine, just sync your
    snapshot" from "rename truly failed".
    """
    try:
        await bot.edit_forum_topic(
            chat_id=chat_id, message_thread_id=message_thread_id, name=name,
            read_timeout=15, write_timeout=15, connect_timeout=10,
        )
    except BadRequest as e:
        msg = str(e).lower().replace(" ", "_")
        if "topic_not_modified" in msg or "not_modified" in msg:
            raise TopicAlreadyNamed(str(e)) from e
        raise
    return True


async def delete_topic(bot: Bot, chat_id: int, message_thread_id: int) -> bool:
    """Delete a forum topic.  Idempotent — Telegram returns
    ``Topic_DELETED`` if the topic is already gone, which we swallow.
    """
    try:
        await bot.delete_forum_topic(
            chat_id=chat_id, message_thread_id=message_thread_id,
        )
        return True
    except BadRequest as e:
        if "topic" in str(e).lower() and "delete" in str(e).lower():
            return True  # already deleted
        raise


async def close_topic(bot: Bot, chat_id: int, message_thread_id: int) -> bool:
    """Close a topic (no new replies).  Used when soft-disabling a
    route without deleting Telegram history."""
    await bot.close_forum_topic(
        chat_id=chat_id, message_thread_id=message_thread_id,
    )
    return True


async def reopen_topic(bot: Bot, chat_id: int, message_thread_id: int) -> bool:
    await bot.reopen_forum_topic(
        chat_id=chat_id, message_thread_id=message_thread_id,
    )
    return True


async def rename_general(bot: Bot, chat_id: int, name: str) -> bool:
    """Rename the auto-created General topic — useful so admins can
    repurpose it as a team chat after the bot has created the
    purpose-specific topics."""
    await bot.edit_general_forum_topic(chat_id=chat_id, name=name)
    return True


async def hide_general(bot: Bot, chat_id: int) -> bool:
    """Hide the General topic from the topic list.  Members can still
    reach it via the chat header but it stops being visible as a
    persistent entry."""
    await bot.hide_general_forum_topic(chat_id=chat_id)
    return True


async def unhide_general(bot: Bot, chat_id: int) -> bool:
    await bot.unhide_general_forum_topic(chat_id=chat_id)
    return True
