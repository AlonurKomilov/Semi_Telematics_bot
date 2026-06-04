"""System / operator bot daemon.

Runs as a SEPARATE long-running PTB Application from the customer-
facing daemon at ``interfaces/bot/app.py``.  Token comes from
``TELEGRAM_SYSTEM_BOT_TOKEN`` (see ``infra/config.py`` for the full
split rationale).  Only the operator-only commands are wired here —
no /start handler for customers, no /register, no /faults, nothing
tenant-facing.

Why a separate daemon (rather than reusing the customer one):

  - Anyone chatting with the customer-facing bot is, by definition,
    a customer surface.  Mixing operator commands into it means a
    system owner's "let me check /admin" lands in the same chat the
    customer support thread is in.  Worse, an inline keyboard from
    the operator dashboard can be clicked by anyone the operator
    forwarded the message to.
  - Webhook / polling lifecycle for the two bots is independent —
    rotating the customer login bot's token doesn't take down the
    operator's /admin command, and vice versa.
  - The error reporter (which uses ``TELEGRAM_SYSTEM_BOT_TOKEN``)
    keeps working as a pure API client; this daemon just adds an
    inbound command surface to the same token.

Lifecycle:

    sys_app = build_system_app()      # None when token unset
    if sys_app:
        await run_bot(sys_app)        # non-blocking; same helper as the customer daemon

Skipping the daemon (``TELEGRAM_SYSTEM_BOT_TOKEN`` empty) is a
supported state — operators can still use the web console at
system.4truck.us with no Telegram side.
"""

from __future__ import annotations

import logging

from telegram.ext import Application

from infra.config import TELEGRAM_SYSTEM_BOT_TOKEN

logger = logging.getLogger(__name__)


def build_system_app() -> Application | None:
    """Build the system-bot PTB Application, or return None if no token.

    The optional return is intentional: an operator who's only using
    the web console may not want a Telegram daemon at all.  Callers
    should ``if sys_app:`` and skip startup rather than fail.
    """
    if not TELEGRAM_SYSTEM_BOT_TOKEN:
        logger.info(
            "System bot daemon skipped — TELEGRAM_BOT_TOKEN is unset.  "
            "Operator commands available only on system.4truck.us.",
        )
        return None

    app = (Application.builder()
           .token(TELEGRAM_SYSTEM_BOT_TOKEN)
           .concurrent_updates(True)
           .build())

    # Route Telegram-side exceptions through the same error reporter
    # the customer daemon uses (which also routes through this bot —
    # so a system-bot crash report lands in the operator chat where
    # it was raised).
    async def _err(_update, context):
        exc = getattr(context, "error", None)
        try:
            from infra.error_reporter import report_error
            await report_error(exc, source="system_bot")
        except Exception:
            pass
    app.add_error_handler(_err)

    from interfaces.bot.handler_setup import register_system_handlers
    register_system_handlers(app)

    logger.info("System bot daemon built — operator commands ready")
    return app
