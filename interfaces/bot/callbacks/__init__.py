"""Callback query router and text input handler.

Builds a :class:`CallbackRouter` with domain-specific handlers registered
from submodules, plus a route table for simple one-line delegations to
existing command functions.
"""

from telegram import Update
from telegram.ext import ContextTypes

from adapters.samsara.client import populate_company_display
from infra.context import get_company_display
from capabilities.formatting import format_help, format_welcome_unregistered, format_system_owner_welcome

from interfaces.bot.config import get_platform_db, get_tenant_db, SUPPORT_CONTACT
from interfaces.bot.keyboards import (
    main_menu_kb, back_kb, system_owner_kb, unregistered_kb,
    co_menu_kb, vehicle_company_picker_kb,
)
from interfaces.bot.helpers import _show, _render_audit_log, _safe_error
from interfaces.bot.auth import _get_user, _group_chat_guard
from capabilities.localization.i18n import t
from interfaces.bot.callback_router import CallbackRouter

# ── Import command functions for simple route delegation ─────────

from interfaces.bot.registration import cmd_start
from interfaces.bot.fleet import (
    cmd_faults, cmd_faults_pdf, cmd_faults_csv,
    cmd_vehicle, cmd_critical,
    cmd_fuel, cmd_fuel_pdf, cmd_fuel_csv,
    cmd_alerts, cmd_alert_toggle, cmd_ai_alert_toggle, cmd_alert_disable_all,
    cmd_alert_history, cmd_pending_alerts,
    cmd_vehicle_report,
    cmd_health, cmd_health_pdf, cmd_health_csv,
    cmd_efficiency, cmd_efficiency_pdf, cmd_efficiency_csv,
    cmd_weather, cmd_api_status,
    cmd_camera_check, cmd_camera_check_vehicle,
    cmd_camera_history, cmd_camera_check_pdf, cmd_camera_check_csv,
    cmd_cam_tool, cmd_cam_company_pick, cmd_cam_page,
)
from interfaces.bot.management import cmd_account, cmd_users
from interfaces.bot.admin import cmd_admin, cmd_accounts, cmd_sys_ai_stats, cmd_sys_server
from interfaces.bot.scorecards import cmd_scorecards, cmd_scorecards_pdf, cmd_scorecards_csv
from interfaces.bot.fuel_costs import cmd_fuelcost, cmd_fuelcost_add, cmd_fuelcost_summary
from interfaces.bot.costs import cmd_costmile, cmd_costmile_report
from interfaces.bot.maintenance import (
    cmd_maintenance, cmd_maint_add, cmd_maint_type, cmd_maint_view, cmd_maint_done,
    cmd_maint_select_vehicle, cmd_maint_skip_date, cmd_maint_skip_miles, cmd_maint_skip_desc,
    cmd_maint_detail, cmd_maint_edit, cmd_maint_delete, cmd_maint_delete_confirm,
    cmd_maint_edit_type, cmd_maint_edit_date, cmd_maint_edit_miles, cmd_maint_edit_desc,
    cmd_maint_set_type, cmd_maint_remove_field,
    cmd_maint_company_pick, cmd_maint_vehicle_page,
)
from interfaces.bot.maps import cmd_livemap
from interfaces.bot.routes import cmd_route, cmd_route_go
from interfaces.bot.geofences import cmd_geofences, cmd_add_zone, cmd_list_zones, cmd_delete_zone
from interfaces.bot.geofences import handle_delete_zone_callback, handle_add_zone_roles_callback
from interfaces.bot.events import cmd_events, cmd_events_text, cmd_events_csv
from interfaces.bot.work_hours import (
    cmd_work_hours, cmd_whours_add, cmd_whours_view,
    cmd_whours_rename, cmd_whours_hours, cmd_whours_start_hour,
    cmd_whours_end_hour, cmd_whours_changerole, cmd_whours_role,
    cmd_whours_delete,
)
from interfaces.bot.ai import (
    cmd_ai, cmd_ai_ask_prompt, cmd_ai_answer, cmd_ai_summary,
    cmd_ai_diagnose, cmd_ai_suggest, cmd_ai_newchat,
    cmd_ai_models, cmd_ai_set_model, cmd_ai_set_vision_model, cmd_ai_alerts,
    handle_ai_usage,
)
from interfaces.bot.auto_reports import (
    cmd_auto_reports, cmd_auto_reports_subscribe,
    cmd_auto_reports_unsubscribe, cmd_auto_reports_set_hour,
    cmd_auto_reports_set_tz, cmd_auto_reports_set_type,
)
from interfaces.bot.knowledge import (
    cmd_tips, cmd_kb_category, cmd_kb_pinned, cmd_kb_article,
    cmd_kb_search, handle_kb_search_input,
)
from capabilities.alerting import handle_alert_ack
from interfaces.bot.vehicles import show_vehicle_list

# ── Import domain handler submodules ─────────────────────────────

from interfaces.bot.callbacks import navigation, settings, company, users, groups, parking
from interfaces.bot.callbacks.text_handlers import handle_text  # re-exported

# ── Build the router ─────────────────────────────────────────────

_router = CallbackRouter()

# Register domain handlers
navigation.register(_router)
settings.register(_router)
company.register(_router)
users.register(_router)
groups.register(_router)
parking.register(_router)


# ── Simple fleet command routes ──────────────────────────────────

def _co(fn):
    """Create a prefix handler that extracts a company code suffix."""
    async def _handler(update, context):
        data = update.callback_query.data
        co = data.split("_", 1)[-1] if "_" in data else ""
        # For prefixed routes, strip the known prefix to get the company code
        await fn(update, context, company=co)
    return _handler


# Exact-match fleet routes
_router.exact("cmd_faults", cmd_faults)
_router.exact("faults_pdf", cmd_faults_pdf)
_router.exact("faults_csv", cmd_faults_csv)
_router.exact("cmd_critical", cmd_critical)
_router.exact("cmd_fuel", cmd_fuel)
_router.exact("fuel_pdf", cmd_fuel_pdf)
_router.exact("fuel_csv", cmd_fuel_csv)
_router.exact("cmd_alerts", cmd_alerts)
_router.exact("alert_disable_all", cmd_alert_disable_all)
_router.exact("cmd_myvehicle", cmd_vehicle)
_router.exact("cmd_health", cmd_health)
_router.exact("health_pdf", cmd_health_pdf)
_router.exact("health_csv", cmd_health_csv)
_router.exact("cmd_efficiency", cmd_efficiency)
_router.exact("eff_pdf", cmd_efficiency_pdf)
_router.exact("eff_csv", cmd_efficiency_csv)
_router.exact("cmd_weather", cmd_weather)
_router.exact("cmd_api_status", cmd_api_status)
_router.exact("cmd_camera_check", cmd_camera_check)
_router.exact("cmd_camera_report", cmd_camera_check)   # alias from reports menu
_router.exact("cmd_cam_tool", cmd_cam_tool)             # camera tool entry (per-truck flow)
_router.exact("cam_check_pdf", cmd_camera_check_pdf)
_router.exact("cam_check_csv", cmd_camera_check_csv)
_router.exact("cam_check_history", cmd_camera_history)
_router.exact("cmd_account", cmd_account)
_router.exact("cmd_users", cmd_users)
_router.exact("cmd_scorecards", cmd_scorecards)
_router.exact("scorecard_pdf", cmd_scorecards_pdf)
_router.exact("scorecard_csv", cmd_scorecards_csv)
_router.exact("cmd_livemap", cmd_livemap)
_router.exact("cmd_route", cmd_route)
_router.exact("cmd_geofences", cmd_geofences)
_router.exact("cmd_add_zone", cmd_add_zone)
_router.exact("cmd_list_zones", cmd_list_zones)
_router.exact("cmd_delete_zone", cmd_delete_zone)
_router.exact("cmd_events", cmd_events)
_router.exact("cmd_fuelcost", cmd_fuelcost)
_router.exact("fuelcost_add", cmd_fuelcost_add)
_router.exact("fuelcost_summary", cmd_fuelcost_summary)
_router.exact("cmd_costmile", cmd_costmile)
_router.exact("cmd_maintenance", cmd_maintenance)
_router.exact("maint_add", cmd_maint_add)
_router.exact("maint_view", cmd_maint_view)
_router.exact("maint_skip_date", cmd_maint_skip_date)
_router.exact("maint_skip_miles", cmd_maint_skip_miles)
_router.exact("maint_skip_desc", cmd_maint_skip_desc)
_router.exact("cmd_work_hours", cmd_work_hours)
_router.exact("whours_add", cmd_whours_add)
# Backward-compat aliases for cached Telegram buttons
_router.exact("cmd_work_hours", cmd_work_hours)
_router.exact("wsched_add", cmd_whours_add)
_router.exact("cmd_auto_reports", cmd_auto_reports)
_router.exact("ar_unsub", cmd_auto_reports_unsubscribe)
_router.exact("cmd_alert_history", cmd_alert_history)
_router.exact("cmd_pending_alerts", cmd_pending_alerts)
_router.exact("cmd_tips", cmd_tips)
_router.exact("kb_pinned", cmd_kb_pinned)
_router.exact("kb_search", cmd_kb_search)
_router.exact("noop", lambda u, c: u.callback_query.answer())

# AI routes
_router.exact("cmd_ai", cmd_ai)
_router.exact("cmd_ai_alerts", cmd_ai_alerts)
_router.exact("ai_newchat", cmd_ai_newchat)
_router.exact("ai_summary", cmd_ai_summary)
_router.exact("ai_models", lambda u, c: cmd_ai_models(u, c, mode="text"))
_router.exact("ai_models_text", lambda u, c: cmd_ai_models(u, c, mode="text"))
_router.exact("ai_models_vision", lambda u, c: cmd_ai_models(u, c, mode="vision"))


async def _ai_ask(update, context):
    await cmd_ai_ask_prompt(update, context)

_router.exact("ai_ask", _ai_ask)
_router.exact("ai_chat", _ai_ask)


# ── Prefix fleet routes ─────────────────────────────────────────

async def _faults_pdf_co(u, c):
    co = u.callback_query.data.replace("faults_pdf_", "")
    await cmd_faults_pdf(u, c, company=co)

async def _faults_csv_co(u, c):
    co = u.callback_query.data.replace("faults_csv_", "")
    await cmd_faults_csv(u, c, company=co)

async def _cmd_critical_co(u, c):
    co = u.callback_query.data.replace("cmd_critical_", "")
    await cmd_critical(u, c, company=co)

async def _fuel_pdf_co(u, c):
    co = u.callback_query.data.replace("fuel_pdf_", "")
    await cmd_fuel_pdf(u, c, company=co)

async def _fuel_csv_co(u, c):
    co = u.callback_query.data.replace("fuel_csv_", "")
    await cmd_fuel_csv(u, c, company=co)

async def _alert_toggle(u, c):
    alert_type = u.callback_query.data.replace("alert_toggle_", "")
    await cmd_alert_toggle(u, c, alert_type=alert_type)

async def _ai_toggle(u, c):
    ai_type = u.callback_query.data.replace("ai_toggle_", "")
    await cmd_ai_alert_toggle(u, c, ai_type=ai_type)

async def _health_pdf_co(u, c):
    co = u.callback_query.data.replace("health_pdf_", "")
    await cmd_health_pdf(u, c, company=co)

async def _health_csv_co(u, c):
    co = u.callback_query.data.replace("health_csv_", "")
    await cmd_health_csv(u, c, company=co)

async def _eff_pdf_co(u, c):
    co = u.callback_query.data.replace("eff_pdf_", "")
    await cmd_efficiency_pdf(u, c, company=co)

async def _eff_csv_co(u, c):
    co = u.callback_query.data.replace("eff_csv_", "")
    await cmd_efficiency_csv(u, c, company=co)

async def _cam_truck(u, c):
    truck = u.callback_query.data.replace("cam_vehicle_", "")
    await cmd_camera_check_vehicle(u, c, vehicle_name=truck)

async def _scorecard_pdf_co(u, c):
    co = u.callback_query.data.replace("scorecard_pdf_", "")
    await cmd_scorecards_pdf(u, c, company=co)

async def _scorecard_csv_co(u, c):
    co = u.callback_query.data.replace("scorecard_csv_", "")
    await cmd_scorecards_csv(u, c, company=co)

async def _livemap_co(u, c):
    co = u.callback_query.data.replace("cmd_livemap_", "")
    await cmd_livemap(u, c, company=co)

async def _events_text(u, c):
    days = int(u.callback_query.data.replace("events_text_", ""))
    await cmd_events_text(u, c, days=days)

async def _events_csv(u, c):
    days = int(u.callback_query.data.replace("events_csv_", ""))
    await cmd_events_csv(u, c, days=days)

async def _costmile_pdf_co(u, c):
    co = u.callback_query.data.replace("costmile_pdf_", "")
    await cmd_costmile_report(u, c, company=co, fmt="text")

async def _costmile_csv_co(u, c):
    co = u.callback_query.data.replace("costmile_csv_", "")
    await cmd_costmile_report(u, c, company=co, fmt="csv")

async def _maint_type(u, c):
    task_type = u.callback_query.data.replace("maint_type_", "")
    await cmd_maint_type(u, c, task_type=task_type)

async def _maint_done(u, c):
    task_id = int(u.callback_query.data.replace("maint_done_", ""))
    await cmd_maint_done(u, c, task_id=task_id)

# ── Additional maintenance prefix wrappers ───────────────────────

async def _maint_co(u, c):
    co = u.callback_query.data.replace("maint_co_", "")
    await cmd_maint_company_pick(u, c, company=co)

async def _maint_truck(u, c):
    rest = u.callback_query.data.replace("maint_vehicle_", "")
    parts = rest.split("_", 1)
    co = parts[0] if len(parts) >= 1 else ""
    truck = parts[1] if len(parts) >= 2 else ""
    await cmd_maint_select_vehicle(u, c, company=co, vehicle_name=truck)

async def _maint_pg(u, c):
    rest = u.callback_query.data.replace("maint_pg_", "")
    parts = rest.rsplit("_", 1)
    co = parts[0] if len(parts) >= 1 else "ALL"
    page = int(parts[1]) if len(parts) >= 2 else 0
    await cmd_maint_vehicle_page(u, c, company=co, page=page)

async def _maint_lpage(u, c):
    page = int(u.callback_query.data.replace("maint_lpage_", ""))
    await cmd_maint_view(u, c, page=page)

async def _maint_detail(u, c):
    task_id = int(u.callback_query.data.replace("maint_detail_", ""))
    await cmd_maint_detail(u, c, task_id=task_id)

async def _maint_edit(u, c):
    task_id = int(u.callback_query.data.replace("maint_edit_", ""))
    await cmd_maint_edit(u, c, task_id=task_id)

async def _maint_del(u, c):
    task_id = int(u.callback_query.data.replace("maint_del_", ""))
    await cmd_maint_delete(u, c, task_id=task_id)

async def _maint_delok(u, c):
    task_id = int(u.callback_query.data.replace("maint_delok_", ""))
    await cmd_maint_delete_confirm(u, c, task_id=task_id)

async def _maint_etype(u, c):
    task_id = int(u.callback_query.data.replace("maint_etype_", ""))
    await cmd_maint_edit_type(u, c, task_id=task_id)

async def _maint_edate(u, c):
    task_id = int(u.callback_query.data.replace("maint_edate_", ""))
    await cmd_maint_edit_date(u, c, task_id=task_id)

async def _maint_emiles(u, c):
    task_id = int(u.callback_query.data.replace("maint_emiles_", ""))
    await cmd_maint_edit_miles(u, c, task_id=task_id)

async def _maint_edesc(u, c):
    task_id = int(u.callback_query.data.replace("maint_edesc_", ""))
    await cmd_maint_edit_desc(u, c, task_id=task_id)

async def _maint_setype(u, c):
    rest = u.callback_query.data.replace("maint_setype_", "")
    parts = rest.split("_", 1)
    task_id = int(parts[0])
    new_type = parts[1] if len(parts) > 1 else ""
    await cmd_maint_set_type(u, c, task_id=task_id, new_type=new_type)

async def _maint_rmdate(u, c):
    task_id = int(u.callback_query.data.replace("maint_rmdate_", ""))
    await cmd_maint_remove_field(u, c, task_id=task_id, field="date")

async def _maint_rmmiles(u, c):
    task_id = int(u.callback_query.data.replace("maint_rmmiles_", ""))
    await cmd_maint_remove_field(u, c, task_id=task_id, field="miles")

async def _maint_rmdesc(u, c):
    task_id = int(u.callback_query.data.replace("maint_rmdesc_", ""))
    await cmd_maint_remove_field(u, c, task_id=task_id, field="desc")

# ── Camera tool prefix wrappers ──────────────────────────────────

async def _camco(u, c):
    co = u.callback_query.data.replace("camco_", "")
    await cmd_cam_company_pick(u, c, company=co)

async def _cam_page(u, c):
    rest = u.callback_query.data.replace("cam_page_", "")
    parts = rest.rsplit("_", 1)
    co = parts[0] if len(parts) >= 1 else "ALL"
    page = int(parts[1]) if len(parts) >= 2 else 0
    await cmd_cam_page(u, c, company=co, page=page)

# ── Work hours prefix wrappers ────────────────────────────────

async def _whours_view(u, c):
    sid = int(u.callback_query.data.replace("whours_view_", ""))
    await cmd_whours_view(u, c, schedule_id=sid)

async def _whours_rename(u, c):
    sid = int(u.callback_query.data.replace("whours_rename_", ""))
    await cmd_whours_rename(u, c, schedule_id=sid)

async def _whours_hours(u, c):
    sid = int(u.callback_query.data.replace("whours_hours_", ""))
    await cmd_whours_hours(u, c, schedule_id=sid)

async def _whours_start(u, c):
    rest = u.callback_query.data.replace("whours_start_", "")
    parts = rest.rsplit("_", 1)
    sid = int(parts[0]) if len(parts) >= 1 else 0
    hour = int(parts[1]) if len(parts) >= 2 else 0
    await cmd_whours_start_hour(u, c, schedule_id=sid, hour=hour)

async def _whours_end(u, c):
    rest = u.callback_query.data.replace("whours_end_", "")
    parts = rest.rsplit("_", 1)
    sid = int(parts[0]) if len(parts) >= 1 else 0
    hour = int(parts[1]) if len(parts) >= 2 else 0
    await cmd_whours_end_hour(u, c, schedule_id=sid, hour=hour)

async def _whours_changerole(u, c):
    sid = int(u.callback_query.data.replace("whours_changerole_", ""))
    await cmd_whours_changerole(u, c, schedule_id=sid)

async def _whours_role(u, c):
    role = u.callback_query.data.replace("whours_role_", "")
    await cmd_whours_role(u, c, role=role)

async def _whours_del(u, c):
    sid = int(u.callback_query.data.replace("whours_del_", ""))
    await cmd_whours_delete(u, c, schedule_id=sid)

# Backward-compat: old wsched_ buttons cached in Telegram chats still route correctly
async def _wsched_view(u, c):
    sid = int(u.callback_query.data.replace("wsched_view_", ""))
    await cmd_whours_view(u, c, schedule_id=sid)

async def _wsched_rename(u, c):
    sid = int(u.callback_query.data.replace("wsched_rename_", ""))
    await cmd_whours_rename(u, c, schedule_id=sid)

async def _wsched_hours(u, c):
    sid = int(u.callback_query.data.replace("wsched_hours_", ""))
    await cmd_whours_hours(u, c, schedule_id=sid)

async def _wsched_start(u, c):
    rest = u.callback_query.data.replace("wsched_start_", "")
    parts = rest.rsplit("_", 1)
    sid = int(parts[0]) if len(parts) >= 1 else 0
    hour = int(parts[1]) if len(parts) >= 2 else 0
    await cmd_whours_start_hour(u, c, schedule_id=sid, hour=hour)

async def _wsched_end(u, c):
    rest = u.callback_query.data.replace("wsched_end_", "")
    parts = rest.rsplit("_", 1)
    sid = int(parts[0]) if len(parts) >= 1 else 0
    hour = int(parts[1]) if len(parts) >= 2 else 0
    await cmd_whours_end_hour(u, c, schedule_id=sid, hour=hour)

async def _wsched_changerole(u, c):
    sid = int(u.callback_query.data.replace("wsched_changerole_", ""))
    await cmd_whours_changerole(u, c, schedule_id=sid)

async def _wsched_role(u, c):
    role = u.callback_query.data.replace("wsched_role_", "")
    await cmd_whours_role(u, c, role=role)

async def _wsched_del(u, c):
    sid = int(u.callback_query.data.replace("wsched_del_", ""))
    await cmd_whours_delete(u, c, schedule_id=sid)

async def _ar_freq(u, c):
    freq = u.callback_query.data.replace("ar_freq_", "")
    await cmd_auto_reports_subscribe(u, c, frequency=freq)

async def _ar_type(u, c):
    rtype = u.callback_query.data.replace("ar_type_", "")
    await cmd_auto_reports_set_type(u, c, report_type=rtype)

async def _ar_hour(u, c):
    hour = int(u.callback_query.data.replace("ar_hour_", ""))
    await cmd_auto_reports_set_hour(u, c, hour=hour)

async def _ar_tz(u, c):
    tz = u.callback_query.data.replace("ar_tz_", "")
    await cmd_auto_reports_set_tz(u, c, tz=tz)

async def _ai_setmodel(u, c):
    model = u.callback_query.data.replace("ai_setmodel_", "")
    await cmd_ai_set_model(u, c, model_name=model)

async def _ai_setvision(u, c):
    model = u.callback_query.data.replace("ai_setvision_", "")
    await cmd_ai_set_vision_model(u, c, model_name=model)

async def _ai_sug(u, c):
    try:
        idx = int(u.callback_query.data.replace("ai_sug_", ""))
    except ValueError:
        idx = -1
    await cmd_ai_suggest(u, c, index=idx)

async def _ai_diag(u, c):
    data = u.callback_query.data
    parts = data.replace("ai_diag_", "").split("_", 2)
    ack_id_arg = None
    if len(parts) == 3:
        truck_part = parts[2]
        if ":" in truck_part:
            truck_part, ack_str = truck_part.rsplit(":", 1)
            try:
                ack_id_arg = int(ack_str)
            except ValueError:
                pass
        await cmd_ai_diagnose(
            u, c,
            vehicle_name=truck_part, company=parts[1],
            alert_context=parts[0], ack_id=ack_id_arg,
        )
    elif len(parts) == 2:
        await cmd_ai_diagnose(u, c, vehicle_name=parts[1], company=parts[0])

async def _costmile_text(u, c):
    await cmd_costmile_report(u, c, fmt="text")

async def _costmile_csv(u, c):
    await cmd_costmile_report(u, c, fmt="csv")

# Register prefix routes (order matters: longer/more specific first)
_router.prefix("faults_pdf_", _faults_pdf_co)
_router.prefix("faults_csv_", _faults_csv_co)
_router.prefix("cmd_critical_", _cmd_critical_co)
_router.prefix("fuel_pdf_", _fuel_pdf_co)
_router.prefix("fuel_csv_", _fuel_csv_co)
_router.prefix("alert_toggle_", _alert_toggle)
_router.prefix("ai_toggle_", _ai_toggle)
_router.prefix("health_pdf_", _health_pdf_co)
_router.prefix("health_csv_", _health_csv_co)
_router.prefix("eff_pdf_", _eff_pdf_co)
_router.prefix("eff_csv_", _eff_csv_co)
_router.prefix("cam_vehicle_", _cam_truck)
_router.prefix("scorecard_pdf_", _scorecard_pdf_co)
_router.prefix("scorecard_csv_", _scorecard_csv_co)
_router.prefix("cmd_livemap_", _livemap_co)
_router.prefix("events_text_", _events_text)
_router.prefix("events_csv_", _events_csv)
_router.prefix("costmile_pdf_", _costmile_pdf_co)
_router.prefix("costmile_csv_", _costmile_csv_co)
_router.prefix("maint_type_", _maint_type)
_router.prefix("maint_done_", _maint_done)
_router.prefix("maint_setype_", _maint_setype)   # must be before maint_*
_router.prefix("maint_co_", _maint_co)
_router.prefix("maint_vehicle_", _maint_truck)
_router.prefix("maint_pg_", _maint_pg)
_router.prefix("maint_lpage_", _maint_lpage)
_router.prefix("maint_detail_", _maint_detail)
_router.prefix("maint_delok_", _maint_delok)      # must be before maint_del_
_router.prefix("maint_del_", _maint_del)
_router.prefix("maint_etype_", _maint_etype)
_router.prefix("maint_edate_", _maint_edate)
_router.prefix("maint_emiles_", _maint_emiles)
_router.prefix("maint_edesc_", _maint_edesc)
_router.prefix("maint_edit_", _maint_edit)
_router.prefix("maint_rmdate_", _maint_rmdate)
_router.prefix("maint_rmmiles_", _maint_rmmiles)
_router.prefix("maint_rmdesc_", _maint_rmdesc)
_router.prefix("camco_", _camco)
_router.prefix("cam_page_", _cam_page)
_router.prefix("whours_start_", _whours_start)   # must be before whours_*
_router.prefix("whours_end_", _whours_end)         # must be before whours_*
_router.prefix("whours_view_", _whours_view)
_router.prefix("whours_rename_", _whours_rename)
_router.prefix("whours_hours_", _whours_hours)
_router.prefix("whours_changerole_", _whours_changerole)
_router.prefix("whours_role_", _whours_role)
_router.prefix("whours_del_", _whours_del)
# Backward-compat aliases for old wsched_ buttons cached in Telegram chats
_router.prefix("wsched_start_", _wsched_start)     # must be before wsched_*
_router.prefix("wsched_end_", _wsched_end)         # must be before wsched_*
_router.prefix("wsched_view_", _wsched_view)
_router.prefix("wsched_rename_", _wsched_rename)
_router.prefix("wsched_hours_", _wsched_hours)
_router.prefix("wsched_changerole_", _wsched_changerole)
_router.prefix("wsched_role_", _wsched_role)
_router.prefix("wsched_del_", _wsched_del)
_router.prefix("ar_freq_", _ar_freq)
_router.prefix("ar_type_", _ar_type)
_router.prefix("ar_hour_", _ar_hour)
_router.prefix("ar_tz_", _ar_tz)
_router.prefix("ai_setmodel_", _ai_setmodel)
_router.prefix("ai_setvision_", _ai_setvision)
_router.prefix("ai_sug_", _ai_sug)
_router.prefix("ai_diag_", _ai_diag)
_router.prefix("kb_cat:", cmd_kb_category)
_router.prefix("kb_art:", cmd_kb_article)

_router.exact("costmile_pdf", _costmile_text)
_router.exact("costmile_csv", _costmile_csv)


# ── Per-truck fault report ───────────────────────────────────────

async def _truckfaults(u, c):
    await u.callback_query.answer()
    rest = u.callback_query.data[len("vehiclefaults_"):]
    parts = rest.split("_", 1)
    t_org = parts[0] if len(parts) >= 1 else ""
    t_name = parts[1] if len(parts) >= 2 else ""
    await cmd_vehicle_report(u, c, vehicle_name=t_name, company=t_org)

_router.prefix("vehiclefaults_", _truckfaults)


# ── Truck lookup / browser ───────────────────────────────────────

async def _vehicle_lookup(u, c):
    await cmd_vehicle(u, c)

_router.prefix("vehicle_", _vehicle_lookup)
_router.prefix("covehicle_", _vehicle_lookup)


async def _vehicle_prompt(update, context):
    query = update.callback_query
    await query.answer()
    user = context.user_data["_db_user"]
    from capabilities.iam.permissions import can
    if not can(user.role, "can_vehicle_all"):
        await query.answer(t("access.no_access"), show_alert=True)
        return
    companies = context.user_data.get("_companies", [])
    company_codes = [o.code for o in companies]
    if len(company_codes) == 1:
        await show_vehicle_list(update, context, user, company_codes[0])
    else:
        await _show(update, context, [
            f"{t('vehicle.browse_title')}\n\n"
            f"{t('vehicle.browse_prompt')}"
        ], keyboard=vehicle_company_picker_kb(company_codes))

_router.exact("cmd_vehicle_prompt", _vehicle_prompt)


async def _vehicles_browse(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    company_filter = data[14:]  # "ALL" or company code
    if company_filter == "ALL":
        company_filter = None
    user = context.user_data["_db_user"]
    await show_vehicle_list(update, context, user, company_filter)

_router.prefix("vehicles_browse_", _vehicles_browse)


async def _vehicles_page(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split("_")
    page = int(parts[-1])
    company_filter = "_".join(parts[2:-1])
    if company_filter == "ALL":
        company_filter = None
    user = context.user_data["_db_user"]
    await show_vehicle_list(update, context, user, company_filter, page=page)

_router.prefix("vehicles_page_", _vehicles_page)


# ── Company sub-menu (per-company reports) ───────────────────────

async def _co_submenu(update, context):
    """Show per-company report menu.  Matches 'co_<CODE>' but NOT any of the
    more specific co_ prefixes (those are registered separately and matched
    first by the router via earlier registration)."""
    query = update.callback_query
    data = query.data
    co = data.replace("co_", "")
    await query.answer()
    name = get_company_display().get(co, co)
    text = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  🏢  <b>{name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"  {t('company.select_report', code=co)}"
    )
    await _show(update, context, [text], keyboard=co_menu_kb(co))


# Per-company commands — more specific prefixes first
async def _cofaults(u, c):
    co = u.callback_query.data.replace("cofaults_", "")
    await cmd_faults(u, c, company=co)

async def _cofuel(u, c):
    co = u.callback_query.data.replace("cofuel_", "")
    await cmd_fuel(u, c, company=co)

async def _cohealth(u, c):
    co = u.callback_query.data.replace("cohealth_", "")
    await cmd_health(u, c, company=co)

async def _coeff_pdf(u, c):
    co = u.callback_query.data.replace("coeff_pdf_", "")
    await cmd_efficiency_pdf(u, c, company=co)

async def _coeff_csv(u, c):
    co = u.callback_query.data.replace("coeff_csv_", "")
    await cmd_efficiency_csv(u, c, company=co)

async def _coeff(u, c):
    co = u.callback_query.data.replace("coeff_", "")
    await cmd_efficiency(u, c, company=co)

async def _coweather(u, c):
    co = u.callback_query.data.replace("coweather_", "")
    await cmd_weather(u, c, company=co)

_router.prefix("cofaults_", _cofaults)
_router.prefix("cofuel_", _cofuel)
_router.prefix("cohealth_", _cohealth)
_router.prefix("coeff_pdf_", _coeff_pdf)
_router.prefix("coeff_csv_", _coeff_csv)
_router.prefix("coeff_", _coeff)
_router.prefix("coweather_", _coweather)

# Generic co_ submenu — MUST be after all specific co* prefixes
_router.prefix("co_", _co_submenu)


# ── Geofence detail & zone management ───────────────────────────

async def _gf_detail(u, c):
    await u.callback_query.answer(t('geofence.detail_coming_soon'), show_alert=False)

async def _del_zone(u, c):
    await handle_delete_zone_callback(u, c)

async def _zone_roles(u, c):
    await handle_add_zone_roles_callback(u, c)

_router.prefix("gf_detail_", _gf_detail)
_router.prefix("del_zone:", _del_zone)
_router.prefix("zone_roles:", _zone_roles)


# ── Route replay ────────────────────────────────────────────────

async def _route_go(u, c):
    data = u.callback_query.data
    parts = data[len("route_go_"):].rsplit("_", 1)
    if len(parts) == 2:
        co_vehicle = parts[0]
        days_ago = int(parts[1])
        cv_parts = co_vehicle.split("_", 1)
        co = cv_parts[0] if len(cv_parts) >= 1 else ""
        vname = cv_parts[1] if len(cv_parts) >= 2 else ""
        await cmd_route_go(u, c, company=co, vehicle_name=vname, days_ago=days_ago)

_router.prefix("route_go_", _route_go)


# ── Alert acknowledgment ────────────────────────────────────────

async def _ack_alert(u, c):
    try:
        ack_id = int(u.callback_query.data.replace("ack_alert_", ""))
    except (ValueError, IndexError):
        await u.callback_query.answer("Invalid alert", show_alert=True)
        return
    await handle_alert_ack(u, c, ack_id=ack_id)

_router.prefix("ack_alert_", _ack_alert)

async def _back_alert(u, c):
    try:
        ack_id = int(u.callback_query.data.replace("back_alert_", ""))
    except (ValueError, IndexError):
        await u.callback_query.answer("Invalid alert", show_alert=True)
        return
    from capabilities.alerting import handle_back_to_alert
    await handle_back_to_alert(u, c, ack_id=ack_id)

_router.prefix("back_alert_", _back_alert)


# ── Audit log ────────────────────────────────────────────────────

async def _cmd_audit(update, context):
    query = update.callback_query
    await query.answer()
    user = context.user_data["_db_user"]
    from capabilities.iam.permissions import can
    if not can(user.role, "can_manage_users"):
        await query.answer(t("access.no_access"), show_alert=True)
        return
    text = await _render_audit_log(user.account_id)
    await _show(update, context, [text], keyboard=back_kb())

_router.exact("cmd_audit", _cmd_audit)


# ── AI usage stats ───────────────────────────────────────────────

async def _ai_usage_handler(update, context):
    data = update.callback_query.data
    user = context.user_data["_db_user"]
    await handle_ai_usage(update, context, user, data)

_router.exact("cmd_ai_usage", _ai_usage_handler)
_router.prefix("ai_usage_", _ai_usage_handler)


# ═══════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    # Silently ignore unauthorized group chats
    if not await _group_chat_guard(update):
        await query.answer()
        return

    # Clear any pending text-input state when user taps a button
    context.user_data.pop("_pending", None)

    # ── Unregistered user flows ─────────────────────────────────
    if data == "cmd_register_help":
        await query.answer()
        context.user_data["_pending"] = "register"
        await _show(update, context, [
            f"{t('register.prompt_title')}\n\n"
            f"{t('register.prompt')}"
        ])
        return
    if data == "cmd_join_help":
        await query.answer()
        context.user_data["_pending"] = "join"
        await _show(update, context, [
            f"{t('join.prompt_title')}\n\n"
            f"{t('join.prompt')}\n"
            f"{t('join.prompt_format')}"
        ])
        return

    # ── System owner panel buttons ──────────────────────────────
    if data.startswith("sys_"):
        _, tid, sys_owner = await _get_user(update)
        if not sys_owner:
            await query.answer(t('access.sysadmin_only'), show_alert=True)
            return

        if data == "sys_dashboard":
            await query.answer()
            await cmd_admin(update, context)
            return
        elif data == "sys_accounts":
            await query.answer()
            await cmd_accounts(update, context)
            return
        elif data == "sys_ai_stats":
            await query.answer()
            await cmd_sys_ai_stats(update, context)
            return
        elif data.startswith("sys_ai_"):
            await query.answer()
            try:
                ai_days = int(data.replace("sys_ai_", ""))
            except ValueError:
                ai_days = 90
            await cmd_sys_ai_stats(update, context, days=ai_days)
            return
        elif data == "sys_server":
            await query.answer()
            await cmd_sys_server(update, context)
            return

        await query.answer(t('common.unknown_admin_action'))
        return

    # ── Look up user ────────────────────────────────────────────
    user, tid, sys_owner = await _get_user(update)
    if not user:
        await query.answer()
        if sys_owner:
            await _show(update, context,
                        [format_system_owner_welcome()],
                        keyboard=system_owner_kb())
        else:
            # Per-account bot: show organization-specific message
            bot_account_id = context.bot_data.get("account_id")
            if bot_account_id is not None:
                acct = await get_platform_db().get_account(bot_account_id)
                acct_name = acct.name if acct else "this organization"
                first = getattr(update.effective_user, "first_name", "") or ""
                await _show(update, context, [
                    f"👋 Hi{(' ' + first) if first else ''}!\n\n"
                    f"This bot belongs to <b>{acct_name}</b>.\n"
                    "You're not registered as a member.\n\n"
                    "Ask your admin to send you an invite link,\n"
                    "or contact us at @Allen_Klein\n\n"
                    "🌐 <a href=\"https://4truck.us\">4truck.us</a>"
                ], keyboard=unregistered_kb())
            else:
                await _show(update, context,
                            [format_welcome_unregistered(SUPPORT_CONTACT)],
                            keyboard=unregistered_kb())
        return

    # Per-account bot isolation: reject users from other accounts
    bot_account_id = context.bot_data.get("account_id")
    if bot_account_id is not None and user.account_id != bot_account_id:
        await query.answer()
        await _show(update, context, [
            "⛔ This bot belongs to a different organization.\n"
            "Please use your own organization's bot."
        ])
        return

    # Check if account is active
    account = await get_platform_db().get_account(user.account_id)
    if not account or not account.is_active:
        await query.answer()
        await _show(update, context, [
            f"{t('start.account_disabled')}\n"
            f"Contact support: {SUPPORT_CONTACT}" if SUPPORT_CONTACT else
            t('start.account_disabled')
        ])
        return

    context.user_data["_db_user"] = user

    # Set i18n language for this request
    from capabilities.localization.i18n import set_lang
    set_lang(getattr(user, "language", "en") or "en")

    # Populate COMPANY_DISPLAY for this user's account
    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)
    context.user_data["_companies"] = companies
    context.user_data["_sys_owner"] = sys_owner

    # ── Dispatch via router ─────────────────────────────────────
    handled = await _router.dispatch(data, update, context)
    if not handled:
        await query.answer(t('common.unknown_action'))
