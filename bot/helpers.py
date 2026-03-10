"""Single-window engine and utility helpers."""

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest

from bot.config import _active_messages, logger, get_user_company_codes
from bot.keyboards import main_menu_kb


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

    if query and len(texts) == 1:
        try:
            await query.edit_message_text(
                text=texts[0],
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            _active_messages[key] = [query.message.message_id]
            return
        except BadRequest as e:
            logger.debug(f"Edit failed, falling back: {e}")

    await _delete_old_messages(key, bot)
    sent_ids = []
    for i, text in enumerate(texts):
        kb = keyboard if i == len(texts) - 1 else None
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
        msg = await update.message.reply_text(text)
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
