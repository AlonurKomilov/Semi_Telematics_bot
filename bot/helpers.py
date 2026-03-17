"""Single-window engine and utility helpers."""

import html as _html
import re

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest

from bot.config import _active_messages, logger, get_user_company_codes
from bot.keyboards import main_menu_kb

MAX_TG_MSG = 4096  # Telegram message character limit

# Pre-compiled regex for allowed Telegram HTML tags (module-level for perf)
_ALLOWED_RE = re.compile(
    r'(</?(?:b|i|u|s|code|pre|a(?:\s[^>]*)?)>)', re.IGNORECASE
)


def escape_html(text: str) -> str:
    """Escape HTML special characters while preserving allowed tags.

    Preserves: <b>, </b>, <i>, </i>, <u>, </u>, <code>, </code>,
    <pre>, </pre>, <a href="...">, </a>, <s>, </s>.
    """
    parts = _ALLOWED_RE.split(text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # This is an allowed tag — keep as-is
            result.append(part)
        else:
            # Regular text — escape
            result.append(_html.escape(part))
    return "".join(result)


def _split_message(text: str, limit: int = MAX_TG_MSG) -> list[str]:
    """Split text into chunks that fit within Telegram's limit.

    Tries to split at newlines to keep formatting clean.
    """
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Find last newline within the limit
        cut = text.rfind('\n', 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip('\n')
    return chunks


def _msg_key(update: Update) -> tuple[int, int]:
    """Return (chat_id, user_id) key for _active_messages tracking."""
    if update.callback_query:
        chat_id = update.callback_query.message.chat.id
        user_id = update.callback_query.from_user.id
    elif update.message:
        chat_id = update.message.chat.id
        user_id = update.message.from_user.id
    else:
        chat_id = update.effective_chat.id if update.effective_chat else 0
        user_id = update.effective_user.id if update.effective_user else 0
    return (chat_id, user_id)


async def _delete_old_messages(key: tuple[int, int] | int, bot):
    # Support both tuple key and plain chat_id (for backward compat with fleet.py)
    msg_ids = _active_messages.pop(key, [])
    chat_id = key[0] if isinstance(key, tuple) else key
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except BadRequest:
            pass


async def _show(update: Update, context: ContextTypes.DEFAULT_TYPE,
                texts: list[str], keyboard=None):
    query = update.callback_query
    bot = context.bot
    chat_id = query.message.chat.id if query else update.effective_chat.id
    key = _msg_key(update)

    # Flatten: split any oversized text pages into Telegram-safe chunks
    pages: list[str] = []
    for t in texts:
        pages.extend(_split_message(t))

    if query and len(pages) == 1:
        try:
            await query.edit_message_text(
                text=pages[0],
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            _active_messages[key] = [query.message.message_id]
            return
        except BadRequest as e:
            logger.debug(f"Edit failed, falling back: {e}")

    await _delete_old_messages(key, bot)
    sent_ids = []
    for i, text in enumerate(pages):
        kb = keyboard if i == len(pages) - 1 else None
        msg = await bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        sent_ids.append(msg.message_id)
    _active_messages[key] = sent_ids


async def _show_loading(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    query = update.callback_query
    key = _msg_key(update)
    if query:
        try:
            await query.answer()
            await query.edit_message_text(text=text, parse_mode=ParseMode.HTML)
            return
        except BadRequest:
            pass
    if update.message:
        chat_id = update.effective_chat.id
        msg = await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        _active_messages[key] = [msg.message_id]


def _company_line(breakdown: dict) -> str:
    parts = []
    for code in sorted(breakdown.keys()):
        info = breakdown[code]
        parts.append(f"{code}: {info['total']} trucks")
    return "  ·  ".join(parts)


async def _user_menu_kb(user) -> InlineKeyboardMarkup:
    """Build the role-aware main menu for this user."""
    company_codes = await get_user_company_codes(user.account_id)
    return main_menu_kb(user.role, company_codes)
