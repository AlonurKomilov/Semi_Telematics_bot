"""Send-only Telegram client for the system / operator bot.

The system bot (``TELEGRAM_SYSTEM_BOT_TOKEN``) does NOT run as a
long-running PTB daemon — it has no message handlers, no /start
handler, no inline-callback dispatcher.  It's purely an API client
the platform uses to:

  - Push ops alerts via infra.error_reporter
  - Fetch user profile photos for the /admin/users avatar endpoint
  - Notify system owners on customer-daemon startup
  - (Future) operator-facing notifications: comp expiry, billing
    failures across all accounts, etc.

This module is the single seam those callers go through.  Keeping it
small + send-only means we never accidentally couple the system bot
to a PTB Application instance (which would tie its lifetime to
something else's startup/shutdown sequence).
"""

from __future__ import annotations

import logging
from typing import Iterable

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from infra.config import TELEGRAM_SYSTEM_BOT_TOKEN

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    """Whether the system bot has a token set.

    Callers should branch on this — sending without a token is a
    no-op-with-warning rather than an exception, because most of
    these notifications are best-effort (a missing system bot
    shouldn't crash a startup hook).
    """
    return bool(TELEGRAM_SYSTEM_BOT_TOKEN)


async def send_to_owners(
    owner_ids: Iterable[int],
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = ParseMode.HTML,
) -> int:
    """Send a message to each system-owner Telegram id.

    Returns the count of successful sends.  Per-recipient failures
    (user blocked the bot, never started a chat with it, etc.) are
    logged but don't poison the rest of the batch.  Returns 0 and
    logs a warning when the system bot isn't configured.
    """
    if not is_configured():
        logger.warning(
            "system_bot.send_to_owners: TELEGRAM_BOT_TOKEN is unset; skipping",
        )
        return 0
    # New Bot instance per call is fine — Telegram API calls are
    # short-lived and we don't share state across them.  Avoids the
    # surprise of accidentally retaining a stale Bot reference past
    # a token rotation.
    bot = Bot(token=TELEGRAM_SYSTEM_BOT_TOKEN)
    sent = 0
    for chat_id in owner_ids:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception:
            logger.warning(
                "system_bot.send_to_owners: send failed for chat=%s",
                chat_id, exc_info=True,
            )
    return sent


def system_console_kb(base_url: str = "https://system.4truck.us") -> InlineKeyboardMarkup:
    """Inline keyboard with URL buttons to the operator console.

    Used in place of the old ``system_owner_kb`` callback-button
    keyboard from the customer daemon.  URL buttons require no
    handler — Telegram opens the link in the user's browser — so
    the system bot can offer them without running a daemon.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Dashboard", url=f"{base_url}/"),
            InlineKeyboardButton("📋 Accounts",  url=f"{base_url}/accounts"),
        ],
        [
            InlineKeyboardButton("🧾 Invoices",  url=f"{base_url}/invoices"),
            InlineKeyboardButton("📜 Audit",     url=f"{base_url}/audit"),
        ],
    ])
