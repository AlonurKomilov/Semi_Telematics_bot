"""Single-window engine and utility helpers."""

import re
from typing import Optional

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest

from interfaces.bot.config import _active_messages, logger, get_user_company_codes, get_tenant_db
from interfaces.bot.keyboards import main_menu_kb

# Re-export escape_html from its canonical home in capabilities/formatting
from capabilities.formatting.helpers import escape_html  # noqa: F401

MAX_TG_MSG = 4096  # Telegram message character limit

# Pre-compiled regex for allowed Telegram HTML tags (module-level for perf)
_ALLOWED_RE = re.compile(
    r'(</?(?:b|i|u|s|code|pre|a(?:\s[^>]*)?)>)', re.IGNORECASE
)

# Patterns that may leak sensitive info in error messages
_SENSITIVE_RE = re.compile(
    r'samsara_api_\S+|'          # Samsara API keys
    r'AIza\S+|'                  # Google API keys
    r'/home/\S+|/tmp/\S+|'      # filesystem paths
    r'https?://\S*token\S*',    # URLs with tokens
    re.IGNORECASE,
)


def _safe_error(e: Exception) -> str:
    """Return a user-safe error message with sensitive info redacted."""
    msg = str(e)
    msg = _SENSITIVE_RE.sub("[REDACTED]", msg)
    if len(msg) > 200:
        msg = msg[:197] + "…"
    return f"❌ Error: {msg}"


def get_bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Get the bot username for the current bot instance.

    Checks context.bot_data first (per-account bot), then falls back
    to the global bot_username in bot.config.
    """
    username = context.bot_data.get("bot_username", "")
    if username:
        return username
    import interfaces.bot.config as _cfg
    return _cfg.bot_username or ""


def make_invite_link(invite_code: str, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    """Build a Telegram deep-link for an invite code, or None if no bot username."""
    username = get_bot_username(context)
    if not username:
        return None
    return f"https://t.me/{username}?start=join_{invite_code}"


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


async def _render_audit_log(account_id: int) -> str:
    """Render the audit log text for an account (shared by command and callback)."""
    tenant = await get_tenant_db(account_id)
    entries = await tenant.get_audit_log(account_id, limit=15)
    if not entries:
        return "📋 <b>Audit Log</b>\n\nNo recent activity."
    lines = ["📋 <b>Audit Log</b> (last 15)\n"]
    for e in entries:
        ts = e["created_at"][:16].replace("T", " ")
        lines.append(f"  • <code>{ts}</code> — {e['action']}")
        if e.get("details"):
            lines.append(f"    {e['details'][:60]}")
    return "\n".join(lines)
