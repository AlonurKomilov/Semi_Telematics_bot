"""Routes — dashboard redirect.

The bot's ``/route`` flow used to ask the user to pick a truck, pick a
date, then ship a static PNG of the GPS trail.  ``dash.4truck.us
/routes`` does the same on an interactive map with date pickers,
multi-day playback, and KMZ export — no part of that experience is
better served by a static image in a chat.

The command + the legacy ``cmd_route_go`` / ``handle_route_text``
callbacks were removed.  ``cmd_route`` is preserved as a one-tap
dashboard redirect so the existing menu button + saved shortcuts
don't dead-end.
"""

from telegram import Update
from telegram.ext import ContextTypes

from interfaces.bot.auth import _require_registered
from interfaces.bot.helpers import reply_dashboard_redirect


@_require_registered
async def cmd_route(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect the user to the interactive route history page."""
    await reply_dashboard_redirect(
        update,
        title="🛣 Routes moved",
        body=(
            "Pick a vehicle, pick a date, and replay the GPS trail "
            "on an interactive map — all on the dashboard."
        ),
        path="/routes",
        label="Open Routes",
    )
