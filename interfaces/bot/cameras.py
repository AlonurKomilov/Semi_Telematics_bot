"""Cameras — pure dashboard redirects (interactive camera UI retired).

The bot used to expose:

* A paginated "pick a company → pick a truck → run camera check"
  wizard.
* A fleet-wide "check every camera" report.
* A paginated history browser.
* A ``/cam <truck>`` ad-hoc one-shot lookup.
* Per-truck camera buttons on the vehicle-detail view.

Every interactive surface has moved to ``dash.4truck.us/cameras``
where snapshots + AI analysis render properly (mobile-cramped photos
in a Telegram chat were strictly worse than a real grid view).  This
module keeps the command stubs alive so legacy menu buttons and
typed commands gracefully redirect instead of dead-ending.

What's still here:

* Five command stubs (``cmd_camera_check`` / ``cmd_camera_check_vehicle``
  / ``cmd_cam`` / ``cmd_cam_tool`` / ``cmd_camera_history``) — all
  call ``reply_dashboard_redirect``.
* Re-exports of ``_gather_snapshots`` / ``_analyze_snapshot`` /
  ``_save_camera_results`` / ``_save_camera_image`` from
  ``features/cameras`` — the **camera alerts pipeline** (cameras.py
  scheduled job) and ``capabilities/reporting/data_fetch.py`` import
  these via this module.  Removing them would break the
  scheduler-driven alert path, which we explicitly want to keep
  (alerts about camera issues still ping the bot, even though the
  manual ``check now`` flow lives on the dashboard).
"""

from telegram import Update
from telegram.ext import ContextTypes

from interfaces.bot.helpers import reply_dashboard_redirect
from interfaces.bot.auth import _require_registered

# Re-export camera helpers from their canonical home in features/cameras.
# scheduled_reports.py / capabilities/reporting/data_fetch.py import these as
# ``interfaces.bot.cameras._gather_snapshots`` etc., so the names stay
# stable here even though every interactive command path has been cut.
from features.cameras.service import (  # noqa: F401
    gather_snapshots as _gather_snapshots,
    analyze_snapshot as _analyze_snapshot,
    save_camera_results as _save_camera_results,
    save_camera_image as _save_camera_image,
)


# ══════════════════════════════════════════════════════════════════
# COMMAND STUBS — all redirect to the dashboard
# ══════════════════════════════════════════════════════════════════


_REDIRECT_BODY = (
    "Camera analysis (single-truck and fleet-wide), AI dashcam "
    "checks, and camera-check history all live on the dashboard "
    "now.  Use the link below to open the Cameras page."
)


@_require_registered
async def cmd_camera_check(update: Update, context: ContextTypes.DEFAULT_TYPE,  # noqa: ARG001
                           vehicle_name: str | None = None):  # noqa: ARG001
    """Redirect to dashboard Cameras page (ad-hoc check moved out)."""
    await reply_dashboard_redirect(
        update,
        title="📷 Cameras moved",
        body=_REDIRECT_BODY,
        path="/cameras",
        label="Open Cameras",
    )


@_require_registered
async def cmd_camera_check_vehicle(update: Update, context: ContextTypes.DEFAULT_TYPE,  # noqa: ARG001
                                   vehicle_name: str = ""):  # noqa: ARG001
    """Redirect (was the per-truck wrapper for cam_vehicle_<truck>)."""
    await reply_dashboard_redirect(
        update,
        title="📷 Cameras moved",
        body=_REDIRECT_BODY,
        path="/cameras",
        label="Open Cameras",
    )


@_require_registered
async def cmd_cam(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect (was /cam <truck> ad-hoc one-shot)."""
    await reply_dashboard_redirect(
        update,
        title="📷 Cameras",
        body=_REDIRECT_BODY,
        path="/cameras",
        label="Open Cameras",
    )


@_require_registered
async def cmd_cam_tool(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect (paginated picker retired)."""
    await reply_dashboard_redirect(
        update,
        title="📷 Cameras moved",
        body=_REDIRECT_BODY,
        path="/cameras",
        label="Open Cameras",
    )


@_require_registered
async def cmd_camera_history(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect (history browser moved to dashboard)."""
    await reply_dashboard_redirect(
        update,
        title="📷 Camera History moved",
        body=(
            "Browse past camera-check results with sortable columns "
            "and per-truck trends on the dashboard."
        ),
        path="/cameras",
        label="Open Camera History",
    )
