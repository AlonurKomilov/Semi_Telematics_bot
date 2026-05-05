"""Cameras — dashcam position & obstruction analysis.

Features:
  • Full fleet camera analysis (all vehicles)
  • Single-vehicle camera analysis (from truck detail view)
  • Camera analysis history stored in DB for trend tracking
  • PDF / CSV export for camera status reports
  • Multi-camera support (forward + inward)
  • Concurrent AI analysis with rate-limit-aware Telegram output
"""

import asyncio
import io
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from capabilities.iam.permissions import can
from adapters.samsara.client import populate_company_display

from interfaces.bot.config import logger, get_tenant_db
from interfaces.bot.keyboards import back_kb, cam_company_picker_kb, cam_vehicle_list_kb
from interfaces.bot.helpers import _show, _show_loading, _safe_error
from interfaces.bot.auth import _require_registered
from capabilities.localization.i18n import t

# Re-export camera functions from their canonical home in capabilities/media
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
    """Truncate text to limit, adding ellipsis if cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _camera_check_kb(show_export: bool = False) -> InlineKeyboardMarkup:
    """Keyboard shown after camera analysis results."""
    rows = []
    if show_export:
        rows.append([
            InlineKeyboardButton("📄 PDF", callback_data="cam_check_pdf"),
            InlineKeyboardButton("📊 CSV", callback_data="cam_check_csv"),
        ])
    rows.append([InlineKeyboardButton("📷 History", callback_data="cam_check_history")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


# ── Camera Tool — per-truck check flow ───────────────────────────

async def _show_cam_vehicle_list(update, context, user, company_filter, page=0):
    """Show paginated vehicle list for camera analysis tool."""
    await _show_loading(update, context, "⏳ Loading vehicles…")
    try:
        from capabilities.vehicles.service import get_fleet_overview as _svc_fleet_overview
        fleet = await _svc_fleet_overview(user.account_id, company=company_filter)
    except Exception as e:
        logger.warning(f"Fleet fetch failed for cam truck list: {e}")
        await _show(update, context, ["❌ Could not load fleet data."],
                     keyboard=back_kb())
        return
    if not fleet:
        await _show(update, context, ["ℹ️ No active vehicles found."],
                     keyboard=back_kb())
        return
    await _show(update, context, [
        "━━━━━━━━━━━━━━━━━━━\n"
        "  📷 Cameras\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\nSelect truck ({len(fleet)} vehicles):"
    ], keyboard=cam_vehicle_list_kb(fleet, page=page, company_filter=company_filter))


@_require_registered
async def cmd_cam_tool(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cameras tool entry — show company picker or direct to truck list."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return
    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)
    codes = [o.code for o in companies]
    if len(codes) == 1:
        await _show_cam_vehicle_list(update, context, user, codes[0])
    else:
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📷 Cameras\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\nSelect company:"
        ], keyboard=cam_company_picker_kb(codes))


@_require_registered
async def cmd_cam_company_pick(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                company: str = ""):
    """Company picked — show vehicle list for camera analysis."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        return
    await _show_cam_vehicle_list(update, context, user, company)


@_require_registered
async def cmd_cam_page(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        company: str = "", page: int = 0):
    """Paginate camera truck list."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        return
    await _show_cam_vehicle_list(update, context, user, company, page=page)


# ── Build caption for a result ───────────────────────────────────

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
        caption_lines.append(
            f"\n  📐 Alignment: {r.get('alignment', '?')}"
        )
        caption_lines.append(
            f"  🔍 Obstruction: {r.get('obstruction', '?')}"
        )
        caption_lines.append(
            f"  💡 Quality: {r.get('quality', '?')}"
        )

    return "\n".join(caption_lines)


# ── Gather snapshots from all companies ──────────────────────────


# ── Send results to chat (rate-limited) ─────────────────────────

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

        # Send photo for problems/warnings, text-only for OK
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
# Commands
# ══════════════════════════════════════════════════════════════════

@_require_registered
async def cmd_camera_check(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           vehicle_name: str | None = None):
    """Run a dashcam position & obstruction check.

    If vehicle_name is provided, checks only that vehicle.
    Otherwise checks all vehicles across all companies.
    """
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("No access", show_alert=True)
        return

    scope = f" for #{vehicle_name}" if vehicle_name else ""
    loading_text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  📷 Cameras\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  Downloading dashcam videos{scope} and\n"
        "  analyzing camera positions...\n"
        "\n  ⏳ This may take a minute."
    )
    await _show_loading(update, context, loading_text)

    try:
        all_snapshots, show_co = await _gather_snapshots(
            user.account_id, vehicle_name=vehicle_name,
        )

        if not all_snapshots:
            no_data = (
                "━━━━━━━━━━━━━━━━━━━\n"
                "  📷 Cameras\n"
                "━━━━━━━━━━━━━━━━━━━\n"
            )
            if vehicle_name:
                no_data += (
                    f"\n  No dashcam footage found for\n"
                    f"  #{vehicle_name} in the last 7 days."
                )
            else:
                no_data += (
                    "\n  No dashcam footage found in the\n"
                    "  last 7 days. Cameras may not have\n"
                    "  triggered any safety events yet."
                )
            await _show(update, context, [no_data], keyboard=back_kb())
            return

        # Analyze all snapshots concurrently (bounded)
        sem = asyncio.Semaphore(_AI_CONCURRENCY)
        tasks = [
            _analyze_snapshot(snap, user.account_id, sem)
            for snap in all_snapshots
        ]
        results = await asyncio.gather(*tasks)

        # Sort: problems first, then warnings, then OK
        priority = {"PROBLEM": 0, "WARNING": 1, "ERROR": 2, "OK": 3}
        results = sorted(
            results,
            key=lambda r: (priority.get(r.get("status", "OK"), 9), r["vehicle"]),
        )

        # Save to DB for history
        await _save_camera_results(user.account_id, results)

        # Store in context for PDF/CSV export
        context.user_data["_camera_results"] = results

        # Count stats
        problems = sum(1 for r in results if r.get("status") == "PROBLEM")
        warnings = sum(1 for r in results if r.get("status") == "WARNING")
        ok_count = sum(1 for r in results if r.get("status") == "OK")
        errors = sum(1 for r in results if r.get("status") == "ERROR")

        # Build summary header
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

        kb = _camera_check_kb(show_export=len(results) > 0)
        await _show(update, context, [header], keyboard=kb)

        # Send individual results
        await _send_results(update, context, results, show_co)

    except Exception as e:
        logger.error(f"Cameras failed: {e}")
        await _safe_error(update, context, f"Cameras failed: {e}")


# ── Single-vehicle camera analysis (from truck detail) ─────────────

@_require_registered
async def cmd_camera_check_vehicle(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 vehicle_name: str):
    """Run camera analysis for a single truck."""
    await cmd_camera_check(update, context, vehicle_name=vehicle_name)


# ── Camera analysis history ────────────────────────────────────────

@_require_registered
async def cmd_camera_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent camera analysis history from DB."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("No access", show_alert=True)
        return

    try:
        tenant_hist = await get_tenant_db(user.account_id)
        history = await tenant_hist.get_camera_check_history(
            user.account_id, limit=30,
        )
    except Exception as e:
        logger.warning(f"Camera history fetch failed: {e}")
        history = []

    if not history:
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📷 Camera History\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n  No camera analysiss recorded yet.\n"
            "  Run a camera analysis first."
        )
        await _show(update, context, [text], keyboard=back_kb())
        return

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        "  📷 Camera History",
        "━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    # Group by vehicle — show latest status and trend
    by_vehicle: dict[str, list[dict]] = {}
    for h in history:
        key = h["vehicle_name"]
        by_vehicle.setdefault(key, []).append(h)

    for vname, checks in sorted(by_vehicle.items()):
        latest = checks[0]
        icon = _status_icon(latest["status"])
        cam_icon = _CAM_ICONS.get(latest.get("camera_type", "forward"), "📷")
        try:
            dt = datetime.fromisoformat(latest["checked_at"].replace("Z", "+00:00"))
            time_str = dt.strftime("%b %d, %H:%M")
        except (ValueError, AttributeError, KeyError):
            time_str = "?"

        # Show trend: count of problems in last N checks
        problem_count = sum(
            1 for c in checks if c["status"] in ("PROBLEM", "WARNING")
        )
        trend = ""
        if problem_count > 1:
            trend = f" ⚠️ {problem_count}× issues"
        elif problem_count == 1 and latest["status"] in ("PROBLEM", "WARNING"):
            trend = " (new)"

        lines.append(
            f"  {icon} <b>#{vname}</b> {cam_icon} — {time_str}{trend}"
        )
        if latest["status"] != "OK":
            lines.append(f"     {latest.get('summary', '')[:60]}")

    text = "\n".join(lines)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Run Check Now", callback_data="cmd_camera_check")],
        [InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")],
    ])
    await _show(update, context, [text], keyboard=kb)


# ── PDF Export ───────────────────────────────────────────────────

@_require_registered
async def cmd_camera_check_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export the latest camera analysis results as PDF."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("No access", show_alert=True)
        return

    results = context.user_data.get("_camera_results")
    if not results:
        if update.callback_query:
            await update.callback_query.answer(
                "No results — run a camera analysis first", show_alert=True
            )
        return

    await _show_loading(update, context, "📄  Generating PDF...")

    try:
        from capabilities.reporting import generate_camera_check_pdf
        pdf_buf = await asyncio.get_event_loop().run_in_executor(
            None, generate_camera_check_pdf, results,
        )
        from interfaces.bot.reports import _send_report_doc
        await _send_report_doc(
            update, context, pdf_buf, "Camera_Check", "pdf", None,
            f"📷 Cameras — {len(results)} vehicle(s)",
            back_kb(),
        )
    except Exception as e:
        logger.error(f"Camera PDF export failed: {e}")
        await _safe_error(update, context, f"Camera PDF failed: {e}")


# ── CSV Export ───────────────────────────────────────────────────

@_require_registered
async def cmd_camera_check_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export the latest camera analysis results as CSV."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("No access", show_alert=True)
        return

    results = context.user_data.get("_camera_results")
    if not results:
        if update.callback_query:
            await update.callback_query.answer(
                "No results — run a camera analysis first", show_alert=True
            )
        return

    await _show_loading(update, context, "📊  Generating CSV...")

    try:
        from capabilities.reporting import generate_camera_check_csv
        csv_buf = generate_camera_check_csv(results)
        from interfaces.bot.reports import _send_report_doc
        await _send_report_doc(
            update, context, csv_buf, "Camera_Check", "csv", None,
            f"📷 Cameras — {len(results)} vehicle(s)",
            back_kb(),
        )
    except Exception as e:
        logger.error(f"Camera CSV export failed: {e}")
        await _safe_error(update, context, f"Camera CSV failed: {e}")
