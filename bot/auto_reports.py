"""Auto Reports — scheduled report delivery (replaces old Digest).

Users subscribe to receive automated PDF reports (Faults, Fuel, Health,
Efficiency) on a Daily, Weekly, or Monthly schedule at a chosen local hour.
The scheduler job runs every hour, matches subscribers by timezone-aware
hour conversion, generates the requested PDF, and sends it via Telegram.
"""

import asyncio
from datetime import datetime as _dt, timezone as _tz_mod
from zoneinfo import ZoneInfo as _ZI

from telegram import Update
from telegram.ext import ContextTypes, Application
from telegram.constants import ParseMode

from database import Role
from permissions import can
from samsara_client import populate_company_display
from pdf_generator import (
    generate_fault_report_pdf,
    generate_fuel_report_pdf,
    generate_vehicle_health_pdf,
    generate_fleet_efficiency_pdf,
    compute_stats,
)

from bot.i18n import t
from bot.config import db, logger, get_client
from bot.keyboards import back_kb, auto_reports_menu_kb
from bot.helpers import _show
from bot.auth import _require_registered

_tz_utc = _tz_mod.utc

# Report type → labels (used in menus and messages)
REPORT_TYPES = {
    "faults": "🔧 Faults",
    "fuel": "⛽ Fuel & DEF",
    "health": "🏥 Vehicle Health",
    "efficiency": "📊 Efficiency",
}


@_require_registered
async def cmd_auto_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show auto reports subscription menu."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_digest"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    sub = await db.get_digest_subscription(user.id)
    sep = t("alert_format.separator")
    text = (
        f"{sep}\n"
        f"  {t('auto_reports.header')}\n"
        f"{sep}\n"
        f"\n  {t('auto_reports.description')}\n"
    )
    if sub:
        rtype = sub.get("report_type", "faults")
        freq = sub.get("frequency", "daily")
        hour = sub.get("send_hour", 7)
        tz = sub.get("timezone", "UTC")
        tz_short = tz.split("/")[-1].replace("_", " ") if "/" in tz else tz
        type_label = REPORT_TYPES.get(rtype, rtype.title())
        text += f"\n  {t('auto_reports.subscribed').format(rtype=type_label, freq=freq.title(), time=f'{hour:02d}:00')}"
        text += f"\n  {t('auto_reports.timezone_label').format(tz=tz_short)}"
    else:
        text += f"\n  {t('auto_reports.choose_frequency')}"

    await _show(update, context, [text], keyboard=auto_reports_menu_kb(sub))


@_require_registered
async def cmd_auto_reports_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                     frequency: str = "daily"):
    """Start subscription wizard — frequency chosen, now pick report type."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_digest"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    context.user_data["_ar_freq"] = frequency

    from bot.keyboards import auto_reports_type_kb
    await _show(update, context, [
        f"{t('auto_reports.subscribe_title').format(freq=frequency.title())}\n\n"
        f"  {t('auto_reports.choose_type')}\n"
    ], keyboard=auto_reports_type_kb())


@_require_registered
async def cmd_auto_reports_set_type(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                    report_type: str = "faults"):
    """Report type chosen, now pick delivery hour."""
    user = context.user_data["_db_user"]
    context.user_data["_ar_type"] = report_type

    from bot.keyboards import auto_reports_hour_kb
    type_label = REPORT_TYPES.get(report_type, report_type.title())
    await _show(update, context, [
        f"  {type_label}\n\n"
        f"  {t('auto_reports.time_prompt')}\n"
        f"  {t('auto_reports.time_local')}\n"
    ], keyboard=auto_reports_hour_kb())


@_require_registered
async def cmd_auto_reports_set_hour(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                    hour: int = 7):
    """Hour chosen, now pick timezone."""
    user = context.user_data["_db_user"]
    context.user_data["_ar_hour"] = hour

    from bot.keyboards import auto_reports_tz_kb
    await _show(update, context, [
        f"{t('auto_reports.delivery_at').format(time=f'{hour:02d}:00')}\n\n"
        f"  {t('auto_reports.select_tz')}\n"
    ], keyboard=auto_reports_tz_kb())


@_require_registered
async def cmd_auto_reports_set_tz(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                  tz: str = "America/New_York"):
    """Finalize subscription with timezone — all wizard data collected."""
    user = context.user_data["_db_user"]
    freq = context.user_data.pop("_ar_freq", "daily")
    rtype = context.user_data.pop("_ar_type", "faults")
    hour = context.user_data.pop("_ar_hour", 7)

    await db.subscribe_digest_ext(
        user.id, frequency=freq, send_hour=hour,
        timezone=tz, report_type=rtype,
    )
    sub = await db.get_digest_subscription(user.id)

    type_label = REPORT_TYPES.get(rtype, rtype.title())
    tz_short = tz.split("/")[-1].replace("_", " ") if "/" in tz else tz
    await _show(update, context, [
        f"{t('auto_reports.subscribed_confirm')}\n\n"
        f"  {t('auto_reports.delivery_info').format(rtype=type_label, freq=freq.title(), time=f'{hour:02d}:00')}\n"
        f"  {t('auto_reports.timezone_info').format(tz=tz_short)}\n"
        f"  {t('auto_reports.auto_summary')}"
    ], keyboard=auto_reports_menu_kb(sub))


@_require_registered
async def cmd_auto_reports_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unsubscribe from auto reports."""
    user = context.user_data["_db_user"]
    await db.unsubscribe_digest(user.id)
    await _show(update, context, [
        f"{t('auto_reports.unsubscribed')}\n\n"
        f"{t('auto_reports.resubscribe')}"
    ], keyboard=auto_reports_menu_kb(None))


# ══════════════════════════════════════════════════════════════════
# PDF GENERATION HELPERS (for scheduled delivery)
# ══════════════════════════════════════════════════════════════════

async def _generate_report_pdf(account_id: int, report_type: str):
    """Generate a PDF report for the given type.

    Returns (pdf_bytes, caption) tuple, or (None, error_msg) on failure.
    """
    try:
        samsara = await get_client(account_id)
        companies = await db.get_account_companies(account_id)
        populate_company_display(companies)

        if report_type == "faults":
            faulted, total, breakdown = await samsara.get_vehicles_with_faults()
            all_fleet = await samsara.get_fleet_overview()
            pdf_buf = await asyncio.to_thread(
                generate_fault_report_pdf,
                faulted, total,
                company_breakdown=breakdown,
                all_vehicles=all_fleet,
            )
            stats = compute_stats(faulted, total)
            caption = (
                f"🔧 <b>Fault Report</b>\n"
                f"📚 {stats['total']} trucks · 🔴 {stats['faulted']} faulted · "
                f"✅ {stats['clean']} clean"
            )
            return pdf_buf, caption, "Fault_Report"

        elif report_type == "fuel":
            vehicles = await samsara.get_fleet_overview()
            pdf_buf = await asyncio.to_thread(
                generate_fuel_report_pdf, vehicles,
            )
            caption = f"⛽ <b>Fuel & DEF Report</b>\n📚 {len(vehicles)} trucks"
            return pdf_buf, caption, "Fuel_DEF_Report"

        elif report_type == "health":
            faulted, total, breakdown = await samsara.get_vehicles_with_faults()
            all_fleet = await samsara.get_fleet_overview()
            pdf_buf = await asyncio.to_thread(
                generate_vehicle_health_pdf, all_fleet, faulted,
            )
            caption = f"🏥 <b>Vehicle Health Report</b>\n📚 {total} trucks"
            return pdf_buf, caption, "Vehicle_Health_Report"

        elif report_type == "efficiency":
            vehicles = await samsara.get_fleet_overview()
            pdf_buf = await asyncio.to_thread(
                generate_fleet_efficiency_pdf, vehicles,
            )
            caption = f"📊 <b>Efficiency Report</b>\n📚 {len(vehicles)} trucks"
            return pdf_buf, caption, "Efficiency_Report"

        else:
            return None, f"Unknown report type: {report_type}", None

    except Exception as e:
        logger.error(f"Auto report generation error ({report_type}): {e}", exc_info=True)
        return None, t('auto_reports.error_content'), None


# ══════════════════════════════════════════════════════════════════
# SCHEDULED JOB
# ══════════════════════════════════════════════════════════════════

async def send_auto_reports(app: Application):
    """Scheduled job: send PDF reports to subscribers at their chosen local hour.

    Runs every hour. Uses timezone-aware subscriber matching.
    Weekly reports only send on Monday. Monthly on the 1st.
    """
    now = _dt.now(_tz_utc)
    current_hour = now.hour
    is_monday = now.weekday() == 0
    is_first = now.day == 1

    subscribers = await db.get_digest_subscribers_by_local_hour(current_hour)
    if not subscribers:
        return

    from zoneinfo import ZoneInfo as _ZI
    _TZ_ET = _ZI("America/New_York")

    for sub in subscribers:
        freq = sub.get("frequency", "daily")
        if freq == "weekly" and not is_monday:
            continue
        if freq == "monthly" and not is_first:
            continue

        try:
            report_type = sub.get("report_type", "faults")
            result = await _generate_report_pdf(sub["account_id"], report_type)

            if result[0] is None:
                # Error — send text message
                await app.bot.send_message(
                    chat_id=sub["telegram_id"],
                    text=result[1],
                    parse_mode=ParseMode.HTML,
                )
                continue

            pdf_buf, caption, filename_base = result
            now_et = _dt.now(_TZ_ET)
            ts = now_et.strftime("%Y-%m-%d_%H%M")

            await app.bot.send_document(
                chat_id=sub["telegram_id"],
                document=pdf_buf,
                filename=f"{filename_base}_{ts}.pdf",
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.warning(f"Auto report send to {sub.get('telegram_id')}: {e}")
