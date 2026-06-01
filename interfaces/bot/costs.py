"""Cost-Per-Mile — dashboard redirect.

The bot's ``/costmile`` flow rendered a per-truck cost-per-mile table
as either a text block or a CSV attachment.  Both are dashboard
shapes — a Telegram chat truncates the table, and a CSV in
Telegram can't be sorted/filtered.  ``dash.4truck.us/costs`` does
the same data with sortable columns and proper export.

This module is now a single stub.  ``cmd_costmile_report`` (the
PDF/CSV/text generator) was removed along with the
``costmile_pdf_*`` / ``costmile_csv_*`` callback prefixes.
"""

from telegram import Update
from telegram.ext import ContextTypes

from interfaces.bot.auth import _require_registered
from interfaces.bot.helpers import reply_dashboard_redirect


@_require_registered
async def cmd_costmile(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,  # noqa: ARG001
    company: str | None = None,  # noqa: ARG001  (callback compat)
):
    """Redirect to the dashboard Cost-Per-Mile page."""
    await reply_dashboard_redirect(
        update,
        title="💲 Cost-Per-Mile moved",
        body=(
            "Browse per-truck cost-per-mile with sortable columns "
            "and proper CSV/PDF export on the dashboard."
        ),
        path="/costs/cost-per-mile",
        label="Open Cost-Per-Mile",
    )
