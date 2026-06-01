"""Driver Scorecards — dashboard redirect.

The bot's ``/scorecards`` flow rendered driver safety scorecards as
text + PDF/CSV attachments.  It's a pure fleet-manager / safety
surface — never used by drivers in the cab — so a desk-shaped
dashboard table beats a phone-screen Telegram message in every
dimension (sortable columns, drill-down, charting).

The PDF and CSV generators (``cmd_scorecards_pdf`` /
``cmd_scorecards_csv``) and the ``scorecard_pdf_*`` /
``scorecard_csv_*`` callback prefixes were removed.
"""

from telegram import Update
from telegram.ext import ContextTypes

from interfaces.bot.auth import _require_registered
from interfaces.bot.helpers import reply_dashboard_redirect


@_require_registered
async def cmd_scorecards(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,  # noqa: ARG001
    company: str | None = None,  # noqa: ARG001  (callback compat)
):
    """Redirect to the dashboard Scorecards page."""
    await reply_dashboard_redirect(
        update,
        title="🏆 Driver Scorecards moved",
        body=(
            "Sortable safety scorecards with per-driver drill-down, "
            "trends, and proper exports live on the dashboard."
        ),
        path="/driver-scorecards",
        label="Open Scorecards",
    )
