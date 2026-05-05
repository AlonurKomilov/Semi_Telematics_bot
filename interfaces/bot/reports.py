"""Fleet report commands — faults, fuel, efficiency, health, weather, API status."""

import asyncio
from datetime import datetime as _dt
from constants import TZ_ET as _TZ_ET
from capabilities.localization.i18n import t

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from capabilities.iam.permissions import can
from adapters.samsara.client import SamsaraPermissionError
from infra.context import get_company_display
from capabilities.vehicles.service import (
    prepare_companies,
    get_fleet_overview as _svc_fleet_overview,
    get_company_codes as _svc_get_company_codes,
)
from capabilities.telemetry.service import (
    get_vehicles_with_faults as _svc_vehicles_with_faults,
    get_fleet_efficiency as _svc_fleet_efficiency,
    get_vehicle_health as _svc_vehicle_health,
    get_fleet_weather as _svc_fleet_weather,
)
from capabilities.reporting import (
    generate_fault_report_pdf,
    generate_fleet_efficiency_pdf,
    generate_fuel_report_pdf,
    generate_vehicle_health_pdf,
    generate_weather_pdf,
    compute_stats,
    generate_efficiency_csv,
    generate_fuel_csv,
    generate_health_csv,
    generate_fault_csv,
)

from interfaces.bot.config import (
    logger,
    _active_messages, get_client,
)
from interfaces.bot.keyboards import (
    back_kb, faults_menu_kb,
    efficiency_format_kb, fuel_format_kb, health_format_kb, faults_format_kb,
)
from interfaces.bot.helpers import (
    _show, _show_loading, _delete_old_messages, _company_line, _user_menu_kb,
    _msg_key, _safe_error,
)
from interfaces.bot.auth import _require_registered


def _skipped_warning(skipped: list[str]) -> str:
    """Return a warning line for companies that failed API calls, or ''."""
    if not skipped:
        return ""
    codes = ", ".join(skipped)
    return f"\n  ⚠️ <i>Skipped ({codes}) — API error</i>"


async def _send_report_doc(update, context, doc_buf, filename_base: str,
                           ext: str, company: str | None, caption: str,
                           keyboard):
    """Send a generated report document (PDF or CSV) with standard boilerplate.

    Handles: delete old messages, build timestamped filename, send document,
    store message ID for single-window tracking.
    """
    query = update.callback_query
    chat_id = query.message.chat.id if query else update.effective_chat.id
    key = _msg_key(update, context.user_data["_db_user"].account_id if context.user_data.get("_db_user") else 0)
    await _delete_old_messages(key, context.bot)

    ts = _dt.now(_TZ_ET).strftime("%Y-%m-%d_%H%M")
    prefix = f"{company}_" if company else ""

    msg = await context.bot.send_document(
        chat_id=chat_id,
        document=doc_buf,
        filename=f"{prefix}{filename_base}_{ts}.{ext}",
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
    _active_messages[key] = [msg.message_id]


# ══════════════════════════════════════════════════════════════════
# FAULT REPORTS
# ══════════════════════════════════════════════════════════════════

@_require_registered
async def cmd_faults(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     company: str | None = None):
    """Show format picker (PDF or CSV) for the fault report."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
    text = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('reports.faults_title')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {company_label}\n"
        f"\n  {t('report_format.choose_format')}"
    )
    kb = faults_format_kb(company)
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_faults_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         company: str | None = None):
    """Generate fault report PDF."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await prepare_companies(user.account_id)

    samsara = await get_client(user.account_id)
    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
    await _show_loading(update, context,
                        t('reports.loading_faults_pdf').format(company=company_label))
    try:
        faulted, total, breakdown = await _svc_vehicles_with_faults(user.account_id, company=company)
        all_fleet = await _svc_fleet_overview(user.account_id, company=company)
        stats = compute_stats(faulted, total)

        pdf_buf = await asyncio.to_thread(
            generate_fault_report_pdf,
            faulted, total,
            company_breakdown=breakdown,
            company_filter=company,
            all_vehicles=all_fleet,
        )

        caption = _faults_caption(stats, breakdown, company, None,
                                  skipped=samsara.last_skipped)
        kb = faults_menu_kb(company)
        await _send_report_doc(update, context, pdf_buf, "Fault_Report",
                               "pdf", company, caption, kb)

    except Exception as e:
        logger.error(f"Error in /faults PDF: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


@_require_registered
async def cmd_faults_csv(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         company: str | None = None):
    """Generate fault report CSV."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await prepare_companies(user.account_id)

    samsara = await get_client(user.account_id)
    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
    await _show_loading(update, context,
                        t('reports.loading_faults_csv').format(company=company_label))
    try:
        faulted, total, breakdown = await _svc_vehicles_with_faults(user.account_id, company=company)
        stats = compute_stats(faulted, total)

        if not faulted:
            kb = await _user_menu_kb(user)
            await _show(update, context, [
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"  {t('reports.faults_none_title')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"\n  {t('reports.faults_none_msg').format(count=total)}"
            ], keyboard=kb)
            return

        csv_buf = await asyncio.to_thread(
            generate_fault_csv, faulted, total, company_filter=company,
        )

        caption = _faults_caption(stats, breakdown, company, None,
                                  skipped=samsara.last_skipped)
        kb = await _user_menu_kb(user)
        await _send_report_doc(update, context, csv_buf, "Fault_Report",
                               "csv", company, caption, kb)

    except Exception as e:
        logger.error(f"Error in /faults CSV: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


def _faults_caption(stats, breakdown, company, companies, skipped=None):
    """Build shared Telegram caption for fault reports."""
    company_info = ""
    if not company and len(breakdown) > 1:
        company_info = f"\n  🏢  {_company_line(breakdown)}"

    health_pct = round((1 - stats['faulted'] / stats['total']) * 100) if stats['total'] else 0

    return (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('reports.faults_title')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('report_captions.faults_vehicles_scanned').format(count=stats['total'])}"
        f"{f' ({len(breakdown)} companies)' if len(breakdown) > 1 and not company else ''}\n"
        f"  {t('report_captions.faults_with_faults').format(faulted=stats['faulted'], clean=stats['clean'])}\n"
        f"  {t('report_captions.faults_total_codes').format(count=stats['total_dtcs'])}\n"
        f"  {t('report_captions.faults_fleet_health').format(pct=f'{health_pct}%')}\n"
        f"\n  {t('report_captions.faults_severity').format(stop=stats['stop'], protect=stats['protect'], emissions=stats['emissions'], warning=stats['warning'], minor=stats['minor'])}"
        f"{company_info}"
        f"{_skipped_warning(skipped or [])}"
    )


# ══════════════════════════════════════════════════════════════════
# FUEL REPORTS
# ══════════════════════════════════════════════════════════════════

@_require_registered
async def cmd_fuel(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   company: str | None = None):
    """Show format picker for the fuel report."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_fuel"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
    text = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('reports.fuel_title')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {company_label}\n"
        f"\n  {t('report_format.choose_format')}"
    )
    kb = fuel_format_kb(company)
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_fuel_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       company: str | None = None):
    """Generate fuel report PDF."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_fuel"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await prepare_companies(user.account_id)

    samsara = await get_client(user.account_id)
    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
    await _show_loading(update, context,
                        t('reports.loading_fuel_pdf').format(company=company_label))
    try:
        all_fleet = await _svc_fleet_overview(user.account_id, company=company)
        if not all_fleet:
            await _show(update, context, [
                t('reports.no_vehicles')
            ], keyboard=back_kb())
            return

        pdf_buf = await asyncio.to_thread(
            generate_fuel_report_pdf, all_fleet, company_filter=company,
        )

        company_codes = await _svc_get_company_codes(user.account_id)
        caption = _fuel_caption(all_fleet, company, company_codes,
                                skipped=samsara.last_skipped)
        kb = back_kb()
        await _send_report_doc(update, context, pdf_buf, "Fuel_Report",
                               "pdf", company, caption, kb)

    except Exception as e:
        logger.error(f"Error in /fuel PDF: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


@_require_registered
async def cmd_fuel_csv(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       company: str | None = None):
    """Generate fuel report CSV."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_fuel"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await prepare_companies(user.account_id)

    samsara = await get_client(user.account_id)
    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
    await _show_loading(update, context,
                        t('reports.loading_fuel_csv').format(company=company_label))
    try:
        all_fleet = await _svc_fleet_overview(user.account_id, company=company)
        if not all_fleet:
            await _show(update, context, [
                t('reports.no_vehicles')
            ], keyboard=back_kb())
            return

        csv_buf = await asyncio.to_thread(
            generate_fuel_csv, all_fleet, company_filter=company,
        )

        company_codes = await _svc_get_company_codes(user.account_id)
        caption = _fuel_caption(all_fleet, company, company_codes,
                                skipped=samsara.last_skipped)
        kb = await _user_menu_kb(user)
        await _send_report_doc(update, context, csv_buf, "Fuel_Report",
                               "csv", company, caption, kb)

    except Exception as e:
        logger.error(f"Error in /fuel CSV: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


def _fuel_caption(all_fleet, company, company_codes, skipped=None):
    """Build shared Telegram caption for fuel reports."""
    known = [v for v in all_fleet if v.get("fuel", {}).get("value") is not None]
    avg_fuel = sum(v["fuel"]["value"] for v in known) / len(known) if known else 0
    crit = sum(1 for v in all_fleet if (v.get("fuel", {}).get("value") or 999) <= 15)
    low = sum(1 for v in all_fleet if 15 < (v.get("fuel", {}).get("value") or 999) <= 30)
    good = sum(1 for v in all_fleet if (v.get("fuel", {}).get("value") or 0) > 30)
    def_known = [v for v in all_fleet if v.get("def_level", {}).get("value") is not None]
    avg_def = sum(v["def_level"]["value"] for v in def_known) / len(def_known) if def_known else 0
    low_def = sum(1 for v in def_known if v["def_level"]["value"] < 15)

    company_info = ""
    if not company and len(company_codes) > 1:
        company_info = f"\n  {t('report_captions.companies_count').format(count=len(company_codes))}"

    return (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('reports.fuel_title')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('report_captions.fuel_vehicles_scanned').format(count=len(all_fleet))}\n"
        f"  {t('report_captions.fuel_average').format(pct=f'{avg_fuel:.0f}%')}\n"
        f"  {t('report_captions.fuel_breakdown').format(critical=crit, low=low, good=good)}\n"
        f"  {t('report_captions.fuel_def').format(pct=f'{avg_def:.0f}%', count=low_def)}"
        f"{company_info}"
        f"{_skipped_warning(skipped or [])}"
    )


# ══════════════════════════════════════════════════════════════════
# EFFICIENCY REPORTS
# ══════════════════════════════════════════════════════════════════

@_require_registered
async def cmd_efficiency(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         company: str | None = None):
    """Show format picker (PDF or CSV) for the efficiency report."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_efficiency"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
    text = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('reports.efficiency_title')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('reports.efficiency_subtitle').format(company=company_label)}\n"
        f"\n  {t('report_format.choose_format')}"
    )
    kb = efficiency_format_kb(company)
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_efficiency_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             company: str | None = None):
    """Generate merged efficiency report as PDF."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_efficiency"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await prepare_companies(user.account_id)
    company_codes = await _svc_get_company_codes(user.account_id)

    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
    await _show_loading(update, context,
                        t('reports.loading_efficiency_pdf').format(company=company_label))
    try:
        vehicles = await _svc_fleet_efficiency(user.account_id, days=7, company=company)

        if not vehicles:
            kb = await _user_menu_kb(user)
            await _show(update, context, [
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"  {t('reports.efficiency_none_title')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"\n"
                f"  {t('reports.efficiency_none_msg')}"
            ], keyboard=kb)
            return

        pdf_buf = await asyncio.to_thread(
            generate_fleet_efficiency_pdf, vehicles, days=7, company_filter=company,
        )

        caption = _efficiency_caption(vehicles, company, company_codes)
        kb = await _user_menu_kb(user)
        await _send_report_doc(update, context, pdf_buf, "Efficiency",
                               "pdf", company, caption, kb)

    except SamsaraPermissionError as e:
        kb = await _user_menu_kb(user)
        await _show(update, context, [
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"  {t('access.permission_required_title')}\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"  {e}\n\n"
            f"  {t('access.permission_samsara')}"
        ], keyboard=kb)
    except Exception as e:
        logger.error(f"Error in efficiency PDF: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


@_require_registered
async def cmd_efficiency_csv(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             company: str | None = None):
    """Generate merged efficiency report as CSV."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_efficiency"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await prepare_companies(user.account_id)
    company_codes = await _svc_get_company_codes(user.account_id)

    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
    await _show_loading(update, context,
                        t('reports.loading_efficiency_csv').format(company=company_label))
    try:
        vehicles = await _svc_fleet_efficiency(user.account_id, days=7, company=company)

        if not vehicles:
            kb = await _user_menu_kb(user)
            await _show(update, context, [
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"  {t('reports.efficiency_none_title')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"\n"
                f"  {t('reports.efficiency_none_msg')}"
            ], keyboard=kb)
            return

        csv_buf = await asyncio.to_thread(
            generate_efficiency_csv, vehicles, days=7, company_filter=company,
        )

        caption = _efficiency_caption(vehicles, company, company_codes)
        kb = await _user_menu_kb(user)
        await _send_report_doc(update, context, csv_buf, "Efficiency",
                               "csv", company, caption, kb)

    except SamsaraPermissionError as e:
        kb = await _user_menu_kb(user)
        await _show(update, context, [
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"  {t('access.permission_required_title')}\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"  {e}\n\n"
            f"  {t('access.permission_samsara')}"
        ], keyboard=kb)
    except Exception as e:
        logger.error(f"Error in efficiency CSV: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


def _efficiency_caption(vehicles, company, company_codes):
    """Build a shared Telegram caption for efficiency reports."""
    total_eng_s = sum(v.get("_engine_s", v["_engine_hours"] * 3600) for v in vehicles)
    total_drv_s = sum(v.get("_driving_s", v["_driving_hours"] * 3600) for v in vehicles)
    total_eng = total_eng_s / 3600
    total_drv = total_drv_s / 3600
    total_idle = (total_eng_s - total_drv_s) / 3600
    total_miles = sum(v.get("_miles", 0) for v in vehicles)
    avg_drv_pct = (total_drv_s / total_eng_s * 100) if total_eng_s > 0 else 0

    with_driver = [v for v in vehicles if v.get("_driver_name")]
    total_fuel = sum(v["_fuel_gal"] for v in with_driver if v.get("_fuel_gal"))
    fuel_miles = sum(v.get("_miles", 0) for v in with_driver)
    fleet_mpg = fuel_miles / total_fuel if total_fuel > 0 else 0

    company_info = ""
    if not company and len(company_codes) > 1:
        company_info = f"\n  {t('report_captions.companies_count').format(count=len(company_codes))}"

    now_str = _dt.now(_TZ_ET).strftime("%b %d, %Y  %I:%M %p EST")
    return (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('reports.efficiency_title')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('report_captions.efficiency_vehicles').format(vehicles=len(vehicles), drivers=len(with_driver))}\n"
        f"  {t('report_captions.efficiency_hours').format(engine=f'{total_eng:,.1f}h', drive=f'{total_drv:,.1f}h', idle=f'{total_idle:,.1f}h')}\n"
        f"  {t('report_captions.efficiency_miles').format(miles=f'{total_miles:,}', driving_pct=f'{avg_drv_pct:.0f}%', mpg=f'{fleet_mpg:.1f}')}"
        f"{company_info}\n"
        f"\n  🕐  <i>{now_str}</i>"
    )


# ══════════════════════════════════════════════════════════════════
# HEALTH REPORTS
# ══════════════════════════════════════════════════════════════════

@_require_registered
async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     company: str | None = None):
    """Show format picker (PDF or CSV) for the vehicle health report."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_health"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
    text = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('reports.health_title')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {company_label}\n"
        f"\n  {t('report_format.choose_format')}"
    )
    kb = health_format_kb(company)
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_health_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         company: str | None = None):
    """Generate vehicle health dashboard PDF."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_health"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await prepare_companies(user.account_id)
    company_codes = await _svc_get_company_codes(user.account_id)

    samsara = await get_client(user.account_id)
    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
    await _show_loading(update, context,
                        t('reports.loading_health_pdf').format(company=company_label))
    try:
        vehicles = await _svc_vehicle_health(user.account_id, company=company)

        if not vehicles:
            kb = await _user_menu_kb(user)
            await _show(update, context, [
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"  {t('reports.health_none_title')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"\n"
                f"  {t('reports.health_none_msg')}"
            ], keyboard=kb)
            return

        pdf_buf = await asyncio.to_thread(
            generate_vehicle_health_pdf, vehicles, company_filter=company,
        )

        caption = _health_caption(vehicles, company, company_codes,
                                  skipped=samsara.last_skipped)
        kb = await _user_menu_kb(user)
        await _send_report_doc(update, context, pdf_buf, "Vehicle_Health",
                               "pdf", company, caption, kb)

    except SamsaraPermissionError as e:
        kb = await _user_menu_kb(user)
        await _show(update, context, [
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"  {t('access.permission_required_title')}\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"  {e}\n\n"
            f"  {t('access.permission_samsara')}"
        ], keyboard=kb)
    except Exception as e:
        logger.error(f"Error in health PDF: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


@_require_registered
async def cmd_health_csv(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         company: str | None = None):
    """Generate vehicle health CSV report."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_health"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await prepare_companies(user.account_id)
    company_codes = await _svc_get_company_codes(user.account_id)

    samsara = await get_client(user.account_id)
    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
    await _show_loading(update, context,
                        t('reports.loading_health_csv').format(company=company_label))
    try:
        vehicles = await _svc_vehicle_health(user.account_id, company=company)

        if not vehicles:
            kb = await _user_menu_kb(user)
            await _show(update, context, [
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"  {t('reports.health_none_title')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"\n"
                f"  {t('reports.health_none_msg')}"
            ], keyboard=kb)
            return

        csv_buf = await asyncio.to_thread(
            generate_health_csv, vehicles, company_filter=company,
        )

        caption = _health_caption(vehicles, company, company_codes,
                                  skipped=samsara.last_skipped)
        kb = await _user_menu_kb(user)
        await _send_report_doc(update, context, csv_buf, "Vehicle_Health",
                               "csv", company, caption, kb)

    except SamsaraPermissionError as e:
        kb = await _user_menu_kb(user)
        await _show(update, context, [
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"  {t('access.permission_required_title')}\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"  {e}\n\n"
            f"  {t('access.permission_samsara')}"
        ], keyboard=kb)
    except Exception as e:
        logger.error(f"Error in health CSV: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


def _health_caption(vehicles, company, company_codes, skipped=None):
    """Build shared Telegram caption for health reports."""
    alerts_count = sum(len(v.get("_health_alerts", [])) for v in vehicles)
    alert_vehicles = sum(1 for v in vehicles if v.get("_health_alerts"))
    eng_on = sum(1 for v in vehicles if v.get("_health", {}).get("engine_on"))
    eng_off = len(vehicles) - eng_on

    company_info = ""
    if not company and len(company_codes) > 1:
        company_info = f"\n  {t('report_captions.companies_count').format(count=len(company_codes))}"

    now_str = _dt.now(_TZ_ET).strftime("%b %d, %Y  %I:%M %p EST")
    return (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('reports.health_title')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('report_captions.health_vehicles_scanned').format(count=len(vehicles))}\n"
        f"  {t('report_captions.health_engine_status').format(on=eng_on, off=eng_off)}\n"
        f"  {t('report_captions.health_alerts').format(vehicles=alert_vehicles, total=alerts_count)}"
        f"{company_info}\n"
        f"{_skipped_warning(skipped or [])}"
        f"\n  🕐  <i>{now_str}</i>"
    )


# ══════════════════════════════════════════════════════════════════
# WEATHER REPORT
# ══════════════════════════════════════════════════════════════════

@_require_registered
async def cmd_weather(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      company: str | None = None):
    """Generate fleet weather / ambient conditions PDF report."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_fuel"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await prepare_companies(user.account_id)
    company_codes = await _svc_get_company_codes(user.account_id)

    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
    await _show_loading(update, context,
                        t('reports.loading_weather').format(company=company_label))
    try:
        vehicles = await _svc_fleet_weather(user.account_id, company=company)

        if not vehicles:
            kb = await _user_menu_kb(user)
            await _show(update, context, [
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"  {t('reports.weather_none')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"\n"
                f"  {t('reports.weather_none_msg')}"
            ], keyboard=kb)
            return

        pdf_buf = await asyncio.to_thread(
            generate_weather_pdf, vehicles, company_filter=company,
        )

        query = update.callback_query
        chat_id = query.message.chat.id if query else update.effective_chat.id
        key = _msg_key(update, context.user_data["_db_user"].account_id)
        await _delete_old_messages(key, context.bot)

        ts = _dt.now(_TZ_ET).strftime("%Y-%m-%d_%H%M")
        prefix = f"{company}_" if company else ""

        # Compute caption stats
        temps = [v["_weather"]["temp_f"] for v in vehicles
                 if v.get("_weather", {}).get("temp_f") is not None]
        freezing = sum(1 for t in temps if t <= 32)
        hot = sum(1 for t in temps if t >= 100)
        avg_temp = sum(temps) / len(temps) if temps else 0
        min_temp = min(temps) if temps else 0
        max_temp = max(temps) if temps else 0

        company_info = ""
        if not company and len(company_codes) > 1:
            company_info = f"\n  {t('report_captions.companies_count').format(count=len(company_codes))}"

        now_str = _dt.now(_TZ_ET).strftime("%b %d, %Y  %I:%M %p EST")
        freeze_line = f"\n  {t('report_captions.weather_freezing').format(count=freezing)}" if freezing else ""
        heat_line = f"\n  {t('report_captions.weather_hot').format(count=hot)}" if hot else ""
        caption = (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"  {t('reports.weather_title')}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {t('report_captions.weather_vehicles').format(count=len(vehicles))}\n"
            f"  {t('report_captions.weather_temp').format(avg=f'{avg_temp:.0f}', low=f'{min_temp:.0f}', high=f'{max_temp:.0f}')}"
            f"{freeze_line}{heat_line}"
            f"{company_info}\n"
            f"\n  🕐  <i>{now_str}</i>"
        )

        kb = await _user_menu_kb(user)
        msg = await context.bot.send_document(
            chat_id=chat_id,
            document=pdf_buf,
            filename=f"{prefix}Weather_Report_{ts}.pdf",
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        _active_messages[key] = [msg.message_id]

    except SamsaraPermissionError as e:
        kb = await _user_menu_kb(user)
        await _show(update, context, [
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"  {t('access.permission_required_title')}\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"  {e}\n\n"
            f"  {t('access.permission_samsara')}"
        ], keyboard=kb)
    except Exception as e:
        logger.error(f"Error in weather: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


# ══════════════════════════════════════════════════════════════════
# API STATUS CHECK
# ══════════════════════════════════════════════════════════════════

@_require_registered
async def cmd_api_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check Samsara API connectivity for all companies."""
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
                # Truncate long error messages
                if len(detail) > 80:
                    detail = detail[:77] + "…"
            lines.append(f"  {icon}  <b>{code}</b>: {detail}")

        all_ok = all(s.startswith("ok") for s in results.values())
        if all_ok:
            lines.append(f"\n  {t('reports.api_all_ok').format(count=len(results))}")
        else:
            failed = sum(1 for s in results.values() if not s.startswith("ok"))
            lines.append(
                f"\n  {t('reports.api_issues').format(failed=failed, total=len(results))}"
            )

        # Cache performance stats
        try:
            samsara = await get_client(user.account_id)
            cs = samsara.cache_stats
            lines.append(
                f"\n  {t('reports.cache_stats').format(hits=cs['hits'], misses=cs['misses'], size=cs['size'])}"
            )
        except Exception as e:
            logger.debug("Cache stats unavailable: %s", e)

        kb = await _user_menu_kb(user)
        await _show(update, context, ["\n".join(lines)], keyboard=kb)

    except Exception as e:
        logger.error(f"Error in API status: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())
