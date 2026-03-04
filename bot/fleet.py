"""Fleet commands — fault reports, truck lookup, fuel, alerts."""

from datetime import datetime as _dt, timezone as _tz
from zoneinfo import ZoneInfo as _ZI

_TZ_ET = _ZI("America/New_York")

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database import Role
from permissions import can
from samsara_client import ORG_DISPLAY, populate_org_display
from formatters import (
    format_truck_detail,
    format_low_fuel,
    format_truck_picker,
    format_engine_hours,
)
from pdf_report import (
    generate_fault_report_pdf,
    generate_critical_report_pdf,
    generate_truck_detail_pdf,
    generate_engine_hours_pdf,
    compute_stats,
)

from bot.config import (
    db, logger, FUEL_THRESHOLD, ALERT_INTERVAL,
    _active_messages, get_client, get_user_org_codes,
)
from bot.keyboards import main_menu_kb, back_kb, truck_kb, truck_picker_kb
from bot.helpers import (
    _show, _show_loading, _delete_old_messages, _org_line, _user_menu_kb,
)
from bot.auth import _require_registered


@_require_registered
async def cmd_faults(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     org: str | None = None):
    """Generate fault report PDF."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    # Populate ORG_DISPLAY for this account
    orgs = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs)

    samsara = await get_client(user.account_id)
    org_label = ORG_DISPLAY.get(org, "All Companies") if org else "All Companies"
    await _show_loading(update, context,
                        f"⏳ Generating fault report ({org_label})…")
    try:
        faulted, total, breakdown = await samsara.get_vehicles_with_faults(org=org)
        stats = compute_stats(faulted, total)

        pdf_buf = generate_fault_report_pdf(
            faulted, total,
            org_breakdown=breakdown,
            org_filter=org,
        )

        query = update.callback_query
        chat_id = query.message.chat.id if query else update.effective_chat.id
        await _delete_old_messages(chat_id, context.bot)

        ts = _dt.now(_TZ_ET).strftime("%Y-%m-%d_%H%M")
        prefix = f"{org}_" if org else ""

        org_info = ""
        if not org and len(breakdown) > 1:
            org_info = f"\n  🏢  {_org_line(breakdown)}"

        caption = (
            "┌─────────────────────────┐\n"
            "  🔧  <b>FAULT CODE REPORT</b>\n"
            "└─────────────────────────┘\n"
            f"\n  📚  <b>{stats['total']}</b> trucks scanned"
            f"{f' ({len(breakdown)} companies)' if len(breakdown) > 1 and not org else ''}\n"
            f"  🔴  <b>{stats['faulted']}</b> with faults  ·  "
            f"✅ {stats['clean']} clean\n"
            f"  📄  <b>{stats['total_dtcs']}</b> total fault codes\n"
            f"\n  🛑 {stats['stop']} STOP  ·  "
            f"♨️ {stats['emissions']} EMIS  ·  "
            f"⚠️ {stats['warning']} WARN  ·  "
            f"🔧 {stats['minor']} MINOR"
            f"{org_info}"
        )

        kb = await _user_menu_kb(user)
        msg = await context.bot.send_document(
            chat_id=chat_id,
            document=pdf_buf,
            filename=f"{prefix}Fault_Report_{ts}.pdf",
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        _active_messages[chat_id] = [msg.message_id]

    except Exception as e:
        logger.error(f"Error in /faults: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_truck(update: Update, context: ContextTypes.DEFAULT_TYPE,
                    truck_name: str | None = None, org: str | None = None):
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
            elif data.startswith("orgtruck_"):
                parts = data.split("_", 2)
                if len(parts) == 3:
                    org = parts[1]
                    truck_name = parts[2]

    # Driver role: force own truck
    if user.role == Role.DRIVER:
        if not user.truck_num:
            await _show(update, context,
                        ["⚠️ No truck assigned. Ask your admin to set your truck number."],
                        keyboard=back_kb())
            return
        truck_name = user.truck_num
    elif not can(user.role, "can_truck_all"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    if not truck_name:
        await _show(update, context,
                    ["ℹ️  Type  /truck <b>NUMBER</b>\n\nExample:  /truck 134"],
                    keyboard=back_kb())
        return

    # Populate ORG_DISPLAY
    orgs = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs)
    org_codes = [o.code for o in orgs]

    await _show_loading(update, context, f"⏳ Looking up #{truck_name}…")

    try:
        samsara = await get_client(user.account_id)
        matches = await samsara.get_vehicle_detail(truck_name, org=org)

        if not matches:
            await _show(update, context,
                        [f"❌  Truck <b>#{truck_name}</b> not found."],
                        keyboard=back_kb())
            return

        if len(matches) == 1:
            vehicle = matches[0]
            _show_faults = can(user.role, "can_faults")
            messages = format_truck_detail(
                vehicle,
                show_org=len(org_codes) > 1,
                show_faults=_show_faults,
            )
            v_org = vehicle.get("_org", org or "")
            await _show(update, context, messages,
                        keyboard=truck_kb(truck_name, v_org,
                                          show_faults=_show_faults))
        else:
            text = format_truck_picker(truck_name, matches)
            await _show(update, context, [text],
                        keyboard=truck_picker_kb(matches))

    except Exception as e:
        logger.error(f"Error in /truck: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_truck_report(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          truck_name: str = "", org: str = ""):
    """Generate a single-truck fault detail PDF."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    # Populate ORG_DISPLAY
    orgs = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs)

    await _show_loading(update, context,
                        f"⏳ Generating report for #{truck_name}…")
    try:
        samsara = await get_client(user.account_id)
        matches = await samsara.get_vehicle_detail(truck_name, org=org)
        if not matches:
            await _show(update, context,
                        [f"❌ Truck <b>#{truck_name}</b> not found."],
                        keyboard=back_kb())
            return

        vehicle = matches[0]

        pdf_buf = generate_truck_detail_pdf(vehicle)

        query = update.callback_query
        chat_id = query.message.chat.id if query else update.effective_chat.id
        await _delete_old_messages(chat_id, context.bot)

        ts = _dt.now(_TZ_ET).strftime("%Y-%m-%d_%H%M")

        fc = vehicle.get("fault_codes", {})
        j1939 = fc.get("j1939", {})
        dtcs = j1939.get("diagnosticTroubleCodes", [])
        lights = j1939.get("checkEngineLights", {})

        if dtcs:
            sev_parts = []
            if lights.get("stopIsOn"):    sev_parts.append("🛑 STOP")
            if lights.get("protectIsOn"): sev_parts.append("🛡 PROTECT")
            if lights.get("emissionsIsOn"): sev_parts.append("♨️ EMISSIONS")
            if lights.get("warningIsOn"): sev_parts.append("⚠️ WARNING")
            sev_line = "  ".join(sev_parts) if sev_parts else "🔧 MINOR"

            caption = (
                f"┌─────────────────────────┐\n"
                f"  🚛  <b>TRUCK #{truck_name}</b>\n"
                f"└─────────────────────────┘\n"
                f"\n  📄  <b>{len(dtcs)}</b> active fault code"
                f"{'s' if len(dtcs) != 1 else ''}\n"
                f"  {sev_line}"
            )
        else:
            caption = (
                f"┌─────────────────────────┐\n"
                f"  🚛  <b>TRUCK #{truck_name}</b>\n"
                f"└─────────────────────────┘\n"
                f"\n  ✅  No active fault codes\n"
                f"  Truck is running clean!"
            )

        kb = truck_kb(truck_name, org, show_faults=can(user.role, "can_faults"))
        msg = await context.bot.send_document(
            chat_id=chat_id,
            document=pdf_buf,
            filename=f"Truck_{truck_name}_{ts}.pdf",
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        _active_messages[chat_id] = [msg.message_id]

    except Exception as e:
        logger.error(f"Error in truck report: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_critical(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       org: str | None = None):
    """Generate critical fault report PDF."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_critical"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    orgs = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs)

    samsara = await get_client(user.account_id)
    org_label = ORG_DISPLAY.get(org, "All Companies") if org else "All Companies"
    await _show_loading(update, context,
                        f"⏳ Checking critical faults ({org_label})…")
    try:
        critical = await samsara.get_critical_faults(org=org)
        _, total, breakdown = await samsara.get_vehicles_with_faults(org=org)

        if not critical:
            kb = await _user_menu_kb(user)
            await _show(update, context, [
                "┌─────────────────────────┐\n"
                "  ✅  <b>ALL CLEAR</b>\n"
                "└─────────────────────────┘\n"
                "\n"
                "  No STOP, PROTECT, or\n"
                "  EMISSIONS lights active.\n"
                "\n"
                "  All trucks running clean 👍"
            ], keyboard=kb)
            return

        pdf_buf = generate_critical_report_pdf(
            critical, total,
            org_breakdown=breakdown,
            org_filter=org,
        )

        query = update.callback_query
        chat_id = query.message.chat.id if query else update.effective_chat.id
        await _delete_old_messages(chat_id, context.bot)

        ts = _dt.now(_TZ_ET).strftime("%Y-%m-%d_%H%M")
        prefix = f"{org}_" if org else ""

        stop = sum(1 for v in critical if v.get('_lights', {}).get('stopIsOn'))
        emis = sum(1 for v in critical if v.get('_lights', {}).get('emissionsIsOn'))
        total_dtcs = sum(len(v.get('_dtcs', [])) for v in critical)

        org_info = ""
        if not org and len(breakdown) > 1:
            org_info = f"\n  🏢  {_org_line(breakdown)}"

        caption = (
            "┌─────────────────────────┐\n"
            "  🚨  <b>CRITICAL FAULTS</b>\n"
            "└─────────────────────────┘\n"
            f"\n  🚛  <b>{len(critical)}</b> trucks need attention\n"
            f"  📄  <b>{total_dtcs}</b> fault codes\n"
            f"\n  🛑 {stop} STOP  ·  ♨️ {emis} EMISSIONS"
            f"{org_info}"
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
        _active_messages[chat_id] = [msg.message_id]

    except Exception as e:
        logger.error(f"Error in /critical: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_fuel(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   org: str | None = None):
    """Show low-fuel vehicles."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_fuel"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    orgs = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs)
    org_codes = [o.code for o in orgs]

    samsara = await get_client(user.account_id)
    org_label = ORG_DISPLAY.get(org, "All Companies") if org else "All Companies"
    await _show_loading(update, context,
                        f"⏳ Checking fuel levels ({org_label})…")
    try:
        low = await samsara.get_low_fuel_vehicles(FUEL_THRESHOLD, org=org)
        text = format_low_fuel(low, FUEL_THRESHOLD,
                               show_org=len(org_codes) > 1 and not org)
        await _show(update, context, [text], keyboard=back_kb())

    except Exception as e:
        logger.error(f"Error in /fuel: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle fault alerts on/off."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_alerts_all") and not can(user.role, "can_alerts_own"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    new_state = await db.toggle_alerts(user.telegram_id)
    org_codes = await get_user_org_codes(user.account_id)

    if new_state:
        text = (
            "┌─────────────────────────┐\n"
            "  🔔  <b>ALERTS ON</b>\n"
            "└─────────────────────────┘\n"
            "\n"
            f"  Checking every {ALERT_INTERVAL} min\n"
            f"  across {len(org_codes)} {'companies' if len(org_codes) != 1 else 'company'}.\n"
            "  You'll get notified of\n"
            "  new critical faults.\n"
            "\n"
            "  Tap 🔔 Alerts to disable."
        )
    else:
        text = (
            "┌─────────────────────────┐\n"
            "  🔕  <b>ALERTS OFF</b>\n"
            "└─────────────────────────┘\n"
            "\n"
            "  Auto-notifications disabled.\n"
            "  Tap 🔔 Alerts to re-enable."
        )

    kb = main_menu_kb(user.role, org_codes)
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_engine_hours(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           org: str | None = None):
    """Generate engine hours + driving/idle breakdown PDF."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    # Populate ORG_DISPLAY for this account
    orgs = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs)
    org_codes = [o.code for o in orgs]

    samsara = await get_client(user.account_id)
    org_label = ORG_DISPLAY.get(org, "All Companies") if org else "All Companies"
    await _show_loading(update, context,
                        f"⏳ Analyzing engine hours ({org_label})…")
    try:
        vehicles = await samsara.get_engine_hours(days=7, org=org)

        if not vehicles:
            kb = await _user_menu_kb(user)
            await _show(update, context, [
                "┌─────────────────────────┐\n"
                "  ℹ️  <b>NO ENGINE DATA</b>\n"
                "└─────────────────────────┘\n"
                "\n"
                "  No engine hour data available\n"
                "  for the past 7 days."
            ], keyboard=kb)
            return

        pdf_buf = generate_engine_hours_pdf(vehicles, days=7, org_filter=org)

        query = update.callback_query
        chat_id = query.message.chat.id if query else update.effective_chat.id
        await _delete_old_messages(chat_id, context.bot)

        ts = _dt.now(_TZ_ET).strftime("%Y-%m-%d_%H%M")
        prefix = f"{org}_" if org else ""

        total_eng = sum(v["_engine_hours"] for v in vehicles)
        total_drv = sum(v["_driving_hours"] for v in vehicles)
        total_idle = sum(v["_idle_hours"] for v in vehicles)
        total_miles = sum(v.get("_miles", 0) for v in vehicles)
        avg_drv_pct = (total_drv / total_eng * 100) if total_eng > 0 else 0

        org_info = ""
        if not org and len(org_codes) > 1:
            org_info = f"\n  🏢  {len(org_codes)} companies"

        caption = (
            "┌─────────────────────────┐\n"
            "  ⏱  <b>ENGINE HOURS REPORT</b>\n"
            "└─────────────────────────┘\n"
            f"\n  🚛  <b>{len(vehicles)}</b> trucks analyzed (7 days)\n"
            f"  ⏱  <b>{total_eng:,.1f}h</b> total engine time\n"
            f"  🛣  <b>{total_miles:,}</b> miles driven\n"
            f"  🚗 {total_drv:,.1f}h driving  ·  🅿️ {total_idle:,.1f}h idle\n"
            f"  📈 Fleet avg: <b>{avg_drv_pct:.0f}%</b> driving"
            f"{org_info}"
        )

        kb = await _user_menu_kb(user)
        msg = await context.bot.send_document(
            chat_id=chat_id,
            document=pdf_buf,
            filename=f"{prefix}Engine_Hours_{ts}.pdf",
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        _active_messages[chat_id] = [msg.message_id]

    except Exception as e:
        logger.error(f"Error in engine hours: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())
