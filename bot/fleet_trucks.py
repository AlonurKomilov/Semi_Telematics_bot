"""Fleet truck commands — truck lookup, truck detail, truck report, critical faults."""

import asyncio
from datetime import datetime as _dt
from constants import TZ_ET as _TZ_ET
from bot.i18n import t

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database import Role
from permissions import can
from samsara_client import populate_company_display
from core.context import get_company_display
from formatters import format_truck_detail, format_truck_picker
from reports import generate_critical_report_pdf, generate_truck_detail_pdf

from bot.config import logger, _active_messages, get_client, get_tenant_db
from bot.keyboards import back_kb, truck_kb, truck_picker_kb, vehicle_list_kb
from bot.helpers import (
    _show, _show_loading, _delete_old_messages, _company_line, _user_menu_kb,
    _msg_key, _safe_error,
)
from bot.auth import _require_registered
from bot.fleet_reports import _skipped_warning


@_require_registered
async def cmd_truck(update: Update, context: ContextTypes.DEFAULT_TYPE,
                    truck_name: str | None = None, company: str | None = None,
                    ack_id: int | None = None):
    """Look up a single truck."""
    user = context.user_data["_db_user"]

    # Parse truck name from args or callback
    if truck_name is None:
        if context.args:
            truck_name = " ".join(context.args)
        elif update.callback_query:
            data = update.callback_query.data
            if data.startswith("truck_"):
                truck_name = data.replace("truck_", "")
            elif data.startswith("cotruck_"):
                parts = data.split("_", 2)
                if len(parts) == 3:
                    company = parts[1]
                    truck_name = parts[2]
                    # Check for :ack_id suffix
                    if ":" in truck_name:
                        truck_name, ack_str = truck_name.rsplit(":", 1)
                        try:
                            ack_id = int(ack_str)
                        except ValueError:
                            pass

    # Driver role: force own truck
    if user.role == Role.DRIVER:
        if not user.truck_num:
            await _show(update, context,
                        [t('truck.no_truck_assigned')],
                        keyboard=back_kb())
            return
        truck_name = user.truck_num
    elif not can(user.role, "can_truck_all"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    if not truck_name:
        await _show(update, context,
                    [t('truck.usage_prompt')],
                    keyboard=back_kb())
        return

    # Populate COMPANY_DISPLAY
    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)
    company_codes = [o.code for o in companies]

    await _show_loading(update, context, t('truck.loading_lookup').format(name=truck_name))

    try:
        samsara = await get_client(user.account_id)
        matches = await samsara.get_vehicle_detail(truck_name, company=company)

        if not matches:
            await _show(update, context,
                        [t('truck.not_found').format(name=truck_name)],
                        keyboard=back_kb())
            return

        if len(matches) == 1:
            vehicle = matches[0]
            _show_faults = can(user.role, "can_faults")
            messages = format_truck_detail(
                vehicle,
                show_company=len(company_codes) > 1,
                show_faults=_show_faults,
            )
            v_org = vehicle.get("_org", company or "")
            await _show(update, context, messages,
                        keyboard=truck_kb(truck_name, v_org,
                                          show_faults=_show_faults,
                                          ack_id=ack_id))
        else:
            text = format_truck_picker(truck_name, matches)
            await _show(update, context, [text],
                        keyboard=truck_picker_kb(matches))

    except Exception as e:
        logger.error(f"Error in /truck: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


@_require_registered
async def cmd_truck_report(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          truck_name: str = "", company: str = ""):
    """Generate a single-truck fault detail PDF."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    # Populate COMPANY_DISPLAY
    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)

    await _show_loading(update, context,
                        t('truck.loading_report').format(name=truck_name))
    try:
        samsara = await get_client(user.account_id)
        matches = await samsara.get_vehicle_detail(truck_name, company=company)
        if not matches:
            await _show(update, context,
                        [t('truck.not_found').format(name=truck_name)],
                        keyboard=back_kb())
            return

        vehicle = matches[0]

        pdf_buf = await asyncio.to_thread(generate_truck_detail_pdf, vehicle)

        query = update.callback_query
        chat_id = query.message.chat.id if query else update.effective_chat.id
        key = _msg_key(update)
        await _delete_old_messages(key, context.bot)

        ts = _dt.now(_TZ_ET).strftime("%Y-%m-%d_%H%M")

        fc = vehicle.get("fault_codes", {})
        j1939 = fc.get("j1939", {})
        dtcs = j1939.get("diagnosticTroubleCodes", [])
        lights = j1939.get("checkEngineLights", {})

        if dtcs:
            sev_parts = []
            if lights.get("stopIsOn"):    sev_parts.append(t('truck.severity_stop'))
            if lights.get("protectIsOn"): sev_parts.append(t('truck.severity_protect'))
            if lights.get("emissionsIsOn"): sev_parts.append(t('truck.severity_emissions'))
            if lights.get("warningIsOn"): sev_parts.append(t('truck.severity_warning'))
            sev_line = "  ".join(sev_parts) if sev_parts else t('truck.severity_minor')

            caption = (
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"  {t('truck.detail_header').format(name=truck_name)}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"\n  {t('truck.active_fault_count').format(count=len(dtcs))}\n"
                f"  {sev_line}"
            )
        else:
            caption = (
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"  {t('truck.detail_header').format(name=truck_name)}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"\n  {t('truck.no_active_faults_caption')}"
            )

        kb = truck_kb(truck_name, company, show_faults=can(user.role, "can_faults"))
        msg = await context.bot.send_document(
            chat_id=chat_id,
            document=pdf_buf,
            filename=f"Truck_{truck_name}_{ts}.pdf",
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        _active_messages[key] = [msg.message_id]

    except Exception as e:
        logger.error(f"Error in truck report: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


@_require_registered
async def cmd_critical(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       company: str | None = None):
    """Generate critical fault report PDF."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)

    samsara = await get_client(user.account_id)
    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
    await _show_loading(update, context,
                        t('truck.loading_critical').format(company=company_label))
    try:
        critical = await samsara.get_critical_faults(company=company)
        _, total, breakdown = await samsara.get_vehicles_with_faults(company=company)

        if not critical:
            kb = await _user_menu_kb(user)
            await _show(update, context, [
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"  ✅  <b>{t('truck.all_clear_title')}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"\n"
                f"  {t('truck.all_clear_msg')}"
            ], keyboard=kb)
            return

        pdf_buf = await asyncio.to_thread(
            generate_critical_report_pdf,
            critical, total,
            company_breakdown=breakdown,
            company_filter=company,
        )

        query = update.callback_query
        chat_id = query.message.chat.id if query else update.effective_chat.id
        key = _msg_key(update)
        await _delete_old_messages(key, context.bot)

        ts = _dt.now(_TZ_ET).strftime("%Y-%m-%d_%H%M")
        prefix = f"{company}_" if company else ""

        stop = sum(1 for v in critical if v.get('_lights', {}).get('stopIsOn'))
        protect = sum(1 for v in critical if v.get('_lights', {}).get('protectIsOn'))
        emis = sum(1 for v in critical if v.get('_lights', {}).get('emissionsIsOn'))
        total_dtcs = sum(len(v.get('_dtcs', [])) for v in critical)
        health_pct = round((1 - len(critical) / total) * 100) if total else 0

        company_info = ""
        if not company and len(breakdown) > 1:
            company_info = f"\n  🏢  {_company_line(breakdown)}"

        caption = (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"  🚨  <b>{t('truck.critical_title')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {t('truck.critical_trucks').format(affected=len(critical), total=total)}\n"
            f"  {t('truck.critical_codes').format(count=total_dtcs)}\n"
            f"  {t('truck.critical_health').format(pct=health_pct)}\n"
            f"\n  {t('truck.critical_severity').format(stop=stop, protect=protect, emis=emis)}"
            f"{company_info}"
            f"{_skipped_warning(samsara.last_skipped)}"
        )

        kb = await _user_menu_kb(user)
        msg = await context.bot.send_document(
            chat_id=chat_id,
            document=pdf_buf,
            filename=f"{prefix}Critical_Report_{ts}.pdf",
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        _active_messages[key] = [msg.message_id]

    except Exception as e:
        logger.error(f"Error in /critical: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


# ── Truck browser (used by callbacks) ────────────────────────────

async def show_truck_list(update, context, user, company_filter, page=0):
    """Fetch all trucks and show a paginated button list."""
    tenant = await get_tenant_db(user.account_id)
    orgs_db = await tenant.get_account_companies(user.account_id)
    populate_company_display(orgs_db)
    company_codes = [o.code for o in orgs_db]
    show_org = len(company_codes) > 1

    await _show_loading(update, context, t('truck.loading_list'))
    try:
        samsara = await get_client(user.account_id)
        vehicles = await samsara.get_fleet_overview(company=company_filter)
        if not vehicles:
            label = company_filter or t('common.all_companies')
            await _show(update, context,
                        [t('truck.no_trucks_for').format(label=label)],
                        keyboard=back_kb())
            return

        total = len(vehicles)
        header = (
            f"{t('truck.browse_header').format(count=total)}\n"
        )
        if company_filter:
            header += f"  📡 {get_company_display().get(company_filter, company_filter)}\n"

        kb = vehicle_list_kb(vehicles, page=page, company_filter=company_filter)
        await _show(update, context, [header + f"\n{t('truck.browse_tap')}"],
                    keyboard=kb)
    except Exception as e:
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())
