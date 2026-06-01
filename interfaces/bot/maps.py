"""Live Location Map — dashboard redirect.

The bot's ``/livemap`` used to render a static PNG of the fleet and
ship it as a Telegram photo.  That surface is obsolete: ``dash.4truck.us
/live-map`` is an interactive map with real pan/zoom/layers and
auto-refresh, so a flat PNG in a chat is strictly worse for any task
beyond a single-glance check.

The command stays registered so the menu button and any saved
shortcuts still work — it just nudges the user to the dashboard
instead of generating a degraded view.
"""

from telegram import Update
from telegram.ext import ContextTypes

from interfaces.bot.auth import _require_registered
from interfaces.bot.helpers import reply_dashboard_redirect


@_require_registered
async def cmd_livemap(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,  # noqa: ARG001
    company: str | None = None,  # noqa: ARG001  (kept for callback compat)
):
    """Redirect the user to the interactive live map on the dashboard."""
    await reply_dashboard_redirect(
        update,
        title="🗺 Live Map moved",
        body=(
            "The interactive map with pan/zoom, layers, and live "
            "vehicle status now lives on the dashboard."
        ),
        path="/live-map",
        label="Open Live Map",
    )
