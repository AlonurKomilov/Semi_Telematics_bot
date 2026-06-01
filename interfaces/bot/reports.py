"""Reports — dashboard redirects + lightweight API health check.

The bot used to expose ``/faults``, ``/fuel``, ``/efficiency``,
``/health``, ``/weather``, and ``/risk_report`` as PDF/CSV
generators that shipped the file back as a Telegram document.
Reading a fleet-wide PDF on a phone is awkward; a CSV in a chat
is uselessly unsorted/unfilterable.  ``dash.4truck.us/reports``
covers the same data with sortable tables and proper export, so
all six command surfaces are now deep-link stubs.

What's still here:

* ``_skipped_warning``   — pure helper, imported by vehicles.py
                           for the fault report caption.  Kept as
                           module-private so the import path stays
                           stable.
* ``cmd_api_status``     — quick Samsara API health check
                           (``🟢/🔴`` per company).  Small,
                           text-only, useful as an operator
                           one-tap diagnostic from the bot menu;
                           no PDF/CSV machinery involved.

All PDF/CSV generators, caption helpers, and ``_send_report_doc``
were removed.  The ``faults_pdf`` / ``faults_csv`` / ``fuel_pdf`` /
``fuel_csv`` / ``health_pdf`` / ``health_csv`` / ``eff_pdf`` /
``eff_csv`` callback prefixes are no longer routed; cached buttons
fall through to the "unknown action" handler.
"""

from capabilities.localization.i18n import t

from telegram import Update
from telegram.ext import ContextTypes

from capabilities.iam.permissions import can

from interfaces.bot.config import logger
from interfaces.bot.keyboards import back_kb
from interfaces.bot.helpers import (
    _show, _show_loading, _safe_error, reply_dashboard_redirect,
)
from interfaces.bot.auth import _require_registered


# ── Shared helper kept for vehicles.py ────────────────────────────


def _skipped_warning(skipped: list[str]) -> str:
    """Return a warning line for companies that failed API calls, or ''.

    Used by vehicles.py's fault report caption — preserved here so
    the existing import path stays stable.
    """
    if not skipped:
        return ""
    codes = ", ".join(skipped)
    return f"\n  ⚠️ <i>Skipped ({codes}) — API error</i>"


# ══════════════════════════════════════════════════════════════════
# DASHBOARD REDIRECTS (replace 6 PDF/CSV report commands)
# ══════════════════════════════════════════════════════════════════


@_require_registered
async def cmd_faults(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,  # noqa: ARG001
    company: str | None = None,  # noqa: ARG001  (callback compat)
):
    """Redirect to the dashboard Faults report."""
    await reply_dashboard_redirect(
        update,
        title="🛠 Faults Report moved",
        body=(
            "Browse fleet-wide fault codes with per-truck drill-down "
            "and CSV/PDF export on the dashboard."
        ),
        path="/reports/faults",
        label="Open Faults Report",
    )


@_require_registered
async def cmd_fuel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,  # noqa: ARG001
    company: str | None = None,  # noqa: ARG001
):
    """Redirect to the dashboard Fuel report."""
    await reply_dashboard_redirect(
        update,
        title="⛽ Fuel Report moved",
        body=(
            "Per-truck fuel levels, consumption trends, and low-fuel "
            "alerts now live on the dashboard."
        ),
        path="/reports/fuel",
        label="Open Fuel Report",
    )


@_require_registered
async def cmd_efficiency(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,  # noqa: ARG001
    company: str | None = None,  # noqa: ARG001
):
    """Redirect to the dashboard Efficiency report."""
    await reply_dashboard_redirect(
        update,
        title="📊 Efficiency Report moved",
        body=(
            "MPG, idle time, and per-driver efficiency rankings with "
            "drill-down + charts on the dashboard."
        ),
        path="/reports/efficiency",
        label="Open Efficiency Report",
    )


@_require_registered
async def cmd_health(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,  # noqa: ARG001
    company: str | None = None,  # noqa: ARG001
):
    """Redirect to the dashboard Vehicle Health report."""
    await reply_dashboard_redirect(
        update,
        title="🩺 Vehicle Health moved",
        body=(
            "Battery, oil pressure, coolant, DEF, and load per truck "
            "with historical trends on the dashboard."
        ),
        path="/reports/vehicle-health",
        label="Open Vehicle Health",
    )


@_require_registered
async def cmd_weather(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,  # noqa: ARG001
    company: str | None = None,  # noqa: ARG001
):
    """Redirect to the dashboard Weather Overlay (on live map)."""
    await reply_dashboard_redirect(
        update,
        title="🌦 Weather Overlay moved",
        body=(
            "Severe-weather overlay on the live map, with route "
            "intersection warnings, is on the dashboard."
        ),
        path="/live-map",
        label="Open Live Map",
    )


@_require_registered
async def cmd_risk_report(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect to the dashboard Risk Summary."""
    await reply_dashboard_redirect(
        update,
        title="⚠️ Risk Summary moved",
        body=(
            "Stakeholder-grade risk summary with driver / vehicle / "
            "carrier breakdowns is on the dashboard."
        ),
        path="/reports/risk-summary",
        label="Open Risk Summary",
    )


# ══════════════════════════════════════════════════════════════════
# QUICK API HEALTH CHECK — kept as a bot-shaped operator diagnostic
# ══════════════════════════════════════════════════════════════════


@_require_registered
async def cmd_api_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check Samsara API connectivity for all companies.

    Small, text-only, no PDF.  Kept on the bot because the "is my
    Samsara key reachable right now" question is a quick-glance
    operator moment — useful from a phone when a different surface
    is reporting stale data and you want a one-tap diagnostic.
    """
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_account"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await _show_loading(update, context, t('reports.loading_api'))
    try:
        from capabilities.alerting import check_api_health
        results = await check_api_health(user.account_id)

        lines = [
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"  {t('reports.api_status_title')}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
        ]
        for code, status in sorted(results.items()):
            if status.startswith("ok"):
                icon = "🟢"
                detail = status.replace("ok ", "")
            else:
                icon = "🔴"
                detail = status.replace("error: ", "")
                if len(detail) > 80:
                    detail = detail[:77] + "…"
            lines.append(f"\n  {icon}  <b>{code}</b>  {detail}")
        await _show(update, context, ["".join(lines)], keyboard=back_kb())
    except Exception as e:
        logger.error(f"API status check failed: {e}")
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())
