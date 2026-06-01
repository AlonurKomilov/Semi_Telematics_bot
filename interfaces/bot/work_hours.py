"""Working Hours — dashboard redirect.

The bot's ``/work_hours`` flow was a multi-step wizard (pick schedule
→ rename → adjust start hour → adjust end hour → change role → delete
…) entirely composed of per-prompt text input.  Editing weekly
schedules through Telegram messages was awkward enough that operators
asked for a real form; ``dash.4truck.us/admin/work-hours`` is that
form.

This module is now a single stub.  All the ``cmd_whours_*`` /
``handle_whours_text`` callbacks were deleted along with their
prefix routings — saved Telegram buttons cached in old chats will
fall through to the global noop handler.
"""

from telegram import Update
from telegram.ext import ContextTypes

from interfaces.bot.auth import _require_registered
from interfaces.bot.helpers import reply_dashboard_redirect


@_require_registered
async def cmd_work_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect to the dashboard Working Hours page."""
    await reply_dashboard_redirect(
        update,
        title="🕐 Working Hours moved",
        body=(
            "Configure weekly schedules, assign roles, and tune "
            "quiet hours on the dashboard — the bot wizard has "
            "been retired."
        ),
        path="/admin/work-hours",
        label="Open Working Hours",
    )
