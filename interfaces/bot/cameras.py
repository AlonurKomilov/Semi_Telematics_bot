"""Cameras — single-truck AI dashcam check + dashboard redirect for the
fleet-wide flow.

The bot used to expose a paginated "pick a company → pick a truck →
run camera check" wizard, a fleet-wide "check every camera" report,
a paginated history browser, and PDF/CSV exports — five separate
command surfaces.  All of the multi-truck and export shapes are
dashboard work; only the **per-truck ad-hoc lookup** ("does Truck
237's forward dashcam look obstructed right now?") is a moment the
bot is genuinely good at.

What's still here:

* ``cmd_camera_check``           — gated on a vehicle name.  Called
                                   with a truck → runs the AI
                                   analysis and sends the result(s).
                                   Called without a truck → redirects
                                   to the dashboard (fleet-wide flow
                                   moved there).
* ``cmd_camera_check_vehicle``   — thin wrapper kept for the per-
                                   truck button on the vehicle-detail
                                   view (callback ``cam_vehicle_<id>``).
* ``cmd_cam``                    — top-level ``/cam <truck>`` command
                                   for an ad-hoc one-shot check.
* ``cmd_cam_tool`` / ``cmd_camera_history`` — redirect stubs;
                                   the paginated picker + history
                                   browser moved to the dashboard.
* re-exports of ``_gather_snapshots`` / ``_analyze_snapshot`` /
  ``_save_camera_results`` / ``_save_camera_image`` from
  ``capabilities/media`` — used by ``auto_reports.py`` to power the
  scheduled fleet camera-check job, which keeps running.

Removed: ``cmd_camera_check_pdf``, ``cmd_camera_check_csv``,
``cmd_cam_company_pick``, ``cmd_cam_page``, ``_show_cam_vehicle_list``,
and the ``cam_check_pdf`` / ``cam_check_csv`` / ``cam_check_history`` /
``camco_`` / ``cam_page_`` callback prefixes.
"""

import asyncio
import io
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from capabilities.iam.permissions import can

from interfaces.bot.config import logger
from interfaces.bot.keyboards import back_kb
from interfaces.bot.helpers import (
    _show, _show_loading, _safe_error, reply_dashboard_redirect,
)
from interfaces.bot.auth import _require_registered

# Re-export camera helpers from their canonical home in capabilities/media.
# auto_reports.py imports these as ``interfaces.bot.cameras._gather_snapshots``
# etc., so the names stay stable here even though every interactive
# command path has been cut.
from capabilities.media.service import (  # noqa: F401
    gather_snapshots as _gather_snapshots,
    analyze_snapshot as _analyze_snapshot,
    save_camera_results as _save_camera_results,
    save_camera_image as _save_camera_image,
)

# ── Constants ────────────────────────────────────────────────────

_MAX_CAPTION = 1024          # Telegram photo caption limit
_MAX_TEXT_MSG = 4096         # Telegram text message limit
_SEND_DELAY = 0.35           # seconds between sends (rate-limit safety)
_AI_CONCURRENCY = 5          # max parallel AI vision calls
_CAM_ICONS = {"forward": "🎥", "inward": "🪞"}


def _status_icon(status: str) -> str:
    """Map analysis status to emoji."""
    return {"OK": "✅", "WARNING": "⚠️", "PROBLEM": "🚨"}.get(status, "❓")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _build_caption(r: dict, show_co: bool) -> str:
    """Build Telegram caption for a camera analysis result."""
    icon = _status_icon(r.get("status", "OK"))
    cam_icon = _CAM_ICONS.get(r.get("camera_type", "forward"), "📷")
    co_label = f" ({r['company']})" if show_co else ""
    caption_lines = [
        f"{icon} <b>#{r['vehicle']}{co_label}</b> {cam_icon}",
    ]
    if r.get("driver"):
        caption_lines.append(f"  👤 {r['driver']}")
    if r.get("event_time"):
        try:
            et = datetime.fromisoformat(
                r["event_time"].replace("Z", "+00:00")
            )
            caption_lines.append(f"  🕐 {et.strftime('%b %d, %H:%M')}")
        except (ValueError, AttributeError):
            pass
    caption_lines.append(f"\n  {r.get('summary', 'No analysis')}")
    if r.get("status") in ("PROBLEM", "WARNING", "ERROR"):
        caption_lines.append(f"\n  📐 Alignment: {r.get('alignment', '?')}")
        caption_lines.append(f"  🔍 Obstruction: {r.get('obstruction', '?')}")
        caption_lines.append(f"  💡 Quality: {r.get('quality', '?')}")
    return "\n".join(caption_lines)


async def _send_results(update, context, results: list[dict],
                        show_co: bool):
    """Send individual vehicle results with rate-limit delays."""
    chat_id = (
        update.callback_query.message.chat.id
        if update.callback_query
        else update.effective_chat.id
    )

    for r in results:
        caption = _build_caption(r, show_co)
        if r.get("status") in ("PROBLEM", "WARNING") and r.get("image_bytes"):
            caption = _truncate(caption, _MAX_CAPTION)
            try:
                photo = io.BytesIO(r["image_bytes"])
                photo.name = f"cam_{r['vehicle']}.jpg"
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=_truncate(caption, _MAX_TEXT_MSG),
                    parse_mode=ParseMode.HTML,
                )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=_truncate(caption, _MAX_TEXT_MSG),
                parse_mode=ParseMode.HTML,
            )
        await asyncio.sleep(_SEND_DELAY)


# ══════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════


@_require_registered
async def cmd_camera_check(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           vehicle_name: str | None = None):
    """Run an AI dashcam analysis on a single truck.

    The fleet-wide path (``vehicle_name is None``) was retired in
    favour of ``dash.4truck.us/cameras`` — selecting a truck from a
    Telegram-side paginated picker is dashboard-shaped work.  Per-
    truck checks ("how does Truck 237's forward camera look right
    now?") stay on the bot because they're one-message-in
    one-message-out from a phone.
    """
    if not vehicle_name:
        await reply_dashboard_redirect(
            update,
            title="📷 Cameras moved",
            body=(
                "Fleet-wide camera analysis and history live on the "
                "dashboard.  For a one-truck check, use "
                "<code>/cam &lt;truck&gt;</code>."
            ),
            path="/cameras",
            label="Open Cameras",
        )
        return

    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("No access", show_alert=True)
        return

    loading_text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  📷 Cameras\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  Downloading dashcam videos for #{vehicle_name} and\n"
        "  analyzing camera positions...\n"
        "\n  ⏳ This may take a minute."
    )
    await _show_loading(update, context, loading_text)

    try:
        all_snapshots, show_co = await _gather_snapshots(
            user.account_id, vehicle_name=vehicle_name,
        )

        if not all_snapshots:
            await _show(update, context, [
                "━━━━━━━━━━━━━━━━━━━\n"
                "  📷 Cameras\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"\n  No dashcam footage found for\n"
                f"  #{vehicle_name} in the last 7 days."
            ], keyboard=back_kb())
            return

        sem = asyncio.Semaphore(_AI_CONCURRENCY)
        tasks = [
            _analyze_snapshot(snap, user.account_id, sem)
            for snap in all_snapshots
        ]
        results = await asyncio.gather(*tasks)

        priority = {"PROBLEM": 0, "WARNING": 1, "ERROR": 2, "OK": 3}
        results = sorted(
            results,
            key=lambda r: (priority.get(r.get("status", "OK"), 9), r["vehicle"]),
        )
        await _save_camera_results(user.account_id, results)

        problems = sum(1 for r in results if r.get("status") == "PROBLEM")
        warnings = sum(1 for r in results if r.get("status") == "WARNING")
        ok_count = sum(1 for r in results if r.get("status") == "OK")
        errors = sum(1 for r in results if r.get("status") == "ERROR")

        header = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📷 Cameras Results\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  Checked {len(results)} camera(s)\n"
        )
        if problems:
            header += f"  🚨 {problems} problem(s)\n"
        if warnings:
            header += f"  ⚠️ {warnings} warning(s)\n"
        if ok_count:
            header += f"  ✅ {ok_count} OK\n"
        if errors:
            header += f"  ❓ {errors} error(s)\n"

        # No export/history keyboard — both flows moved to the dashboard.
        # A plain "Back" returns to the main menu.
        back = InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")],
        ])
        await _show(update, context, [header], keyboard=back)
        await _send_results(update, context, results, show_co)

    except Exception as e:
        logger.error(f"Cameras failed: {e}")
        await _safe_error(update, context, f"Cameras failed: {e}")


@_require_registered
async def cmd_camera_check_vehicle(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                   vehicle_name: str):
    """Per-truck wrapper used by the vehicle detail page's button
    (callback ``cam_vehicle_<truck>``).  Same body as cmd_camera_check
    with a guaranteed vehicle name."""
    await cmd_camera_check(update, context, vehicle_name=vehicle_name)


@_require_registered
async def cmd_cam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Top-level ``/cam <truck>`` command — ad-hoc one-shot camera check.

    Without arguments, redirects to the dashboard Cameras page.
    With a truck name, runs the AI analysis inline.  This is the bot
    moment for cameras: type ``/cam 237`` from a phone, get a
    dashcam screenshot + verdict back.
    """
    args = context.args or []
    if not args:
        await reply_dashboard_redirect(
            update,
            title="📷 Cameras",
            body=(
                "Usage: <code>/cam &lt;truck&gt;</code> for a one-truck "
                "check.  Fleet-wide camera analysis lives on the "
                "dashboard."
            ),
            path="/cameras",
            label="Open Cameras",
        )
        return
    vehicle_name = args[0].strip()
    await cmd_camera_check(update, context, vehicle_name=vehicle_name)


@_require_registered
async def cmd_cam_tool(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect to the dashboard Cameras page (paginated picker retired)."""
    await reply_dashboard_redirect(
        update,
        title="📷 Cameras moved",
        body=(
            "Pick a truck and run AI dashcam analysis on the "
            "dashboard.  For a quick one-truck check from the bot, "
            "use <code>/cam &lt;truck&gt;</code>."
        ),
        path="/cameras",
        label="Open Cameras",
    )


@_require_registered
async def cmd_camera_history(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect to the dashboard Camera History page."""
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
