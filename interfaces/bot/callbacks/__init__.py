"""Callback query router and text input handler.

Builds a :class:`CallbackRouter` with domain-specific handlers registered
from submodules, plus a route table for simple one-line delegations to
existing command functions.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from adapters.samsara.client import populate_company_display
from infra.context import get_company_display
from capabilities.formatting import (
    format_welcome_unregistered,
    format_unregistered_member,
    format_system_owner_welcome,
    format_invite_created,
)

from adapters.storage import Role
from capabilities.permissions.roles import role_display
from interfaces.bot.config import SUPPORT_CONTACT
from interfaces.bot.state import get_platform_db, get_tenant_db
from interfaces.bot.keyboards import (
    back_kb, system_owner_kb, unregistered_kb,
    co_menu_kb, vehicle_company_picker_kb,
)
from interfaces.bot.helpers import make_invite_link, _safe_error
from interfaces.bot.helpers import _show, _render_audit_log
from interfaces.bot.auth import _get_user, _group_chat_guard
from capabilities.localization.i18n import t
from interfaces.bot.callback_router import CallbackRouter

# ── Import command functions for simple route delegation ─────────

from interfaces.bot.fleet import (
    cmd_faults,
    cmd_vehicle, cmd_critical,
    cmd_fuel,
    cmd_alerts, cmd_alert_toggle, cmd_ai_alert_toggle, cmd_alert_disable_all,
    cmd_alert_history, cmd_pending_alerts,
    cmd_vehicle_report,
    cmd_health,
    cmd_efficiency,
    cmd_weather, cmd_api_status,
)
# Camera interactive surface (cmd_camera_check / cmd_camera_check_vehicle
# / cmd_cam_tool / cmd_camera_history / cmd_cam) is fully retired from
# the bot.  Old callbacks fall through to the unknown-action handler.
# Camera *alerts* still ping via capabilities/alerting/cameras.py.
from interfaces.bot.management import cmd_account, cmd_users, cmd_invite
from interfaces.bot.admin import cmd_admin, cmd_accounts, cmd_sys_ai_stats, cmd_sys_server
from interfaces.bot.scorecards import cmd_scorecards
from interfaces.bot.fuel_costs import cmd_fuelcost, cmd_fuelcost_add, cmd_fuelcost_summary
from interfaces.bot.costs import cmd_costmile
# Maintenance: CRUD wizard retired; the dashboard at
# /maintenance/tasks owns task management.  Only two bot entry
# points remain — ``cmd_maintenance`` deep-links to the dashboard,
# and ``cmd_maint_done`` handles the inline "✓ Done" tap on
# scheduler-posted overdue alerts so a recipient can clear a task
# without opening a browser.
from interfaces.bot.maintenance import cmd_maintenance, cmd_maint_done
# Deprecated dashboard-redirect stubs — kept so saved menu buttons
# still dead-end into a "moved to dashboard" message instead of
# silently failing.  See interfaces/bot/{maps,routes,work_hours}.py.
from interfaces.bot.maps import cmd_livemap
from interfaces.bot.routes import cmd_route
from interfaces.bot.work_hours import cmd_work_hours
from interfaces.bot.geofences import cmd_geofences
# Geofence CRUD wizard (cmd_add_zone / cmd_list_zones / cmd_delete_zone +
# the handle_*_callback helpers) is retired from the bot — adding a zone
# requires lat/lng + role-list selection, which is dashboard-shaped work.
# The read-only listing (cmd_geofences) stays for quick "what zones do
# we have?" lookups; the scheduled geofence-event poller in
# capabilities/geofencing/* keeps firing entry/exit alerts.
from interfaces.bot.events import cmd_events, cmd_events_text, cmd_events_csv
from interfaces.bot.ai import (
    cmd_ai, cmd_ai_ask_prompt, cmd_ai_summary,
    cmd_ai_diagnose, cmd_ai_suggest, cmd_ai_newchat,
    cmd_ai_models, cmd_ai_set_model, cmd_ai_set_vision_model, cmd_ai_alerts,
    handle_ai_usage,
)
from interfaces.bot.scheduled_reports import (
    cmd_scheduled_reports, cmd_scheduled_reports_subscribe,
    cmd_scheduled_reports_unsubscribe, cmd_scheduled_reports_set_hour,
    cmd_scheduled_reports_set_tz, cmd_scheduled_reports_set_type,
    cmd_scheduled_reports_add, cmd_scheduled_reports_stop,
)
from interfaces.bot.knowledge import (
    cmd_tips, cmd_kb_category, cmd_kb_pinned, cmd_kb_article,
    cmd_kb_search,
)
from capabilities.alerting import handle_alert_ack
from interfaces.bot.vehicles import show_vehicle_list

# ── Import domain handler submodules ─────────────────────────────

# Per-user role/dept grid (callbacks/users.py) and per-company action
# grid (callbacks/company.py) were retired when /users and /accounts
# moved to the dashboard.  The modules + their register() calls were
# removed below; cached buttons fall through to the unknown-action
# fallback.
from interfaces.bot.callbacks import navigation, settings, groups, parking
# Explicit-alias re-export so handler_setup.py can keep importing
# ``handle_text`` from this package (and ruff's F401 doesn't flag it).
from interfaces.bot.callbacks.text_handlers import handle_text as handle_text  # noqa: PLC0414

# ── Build the router ─────────────────────────────────────────────

logger = logging.getLogger(__name__)

_router = CallbackRouter()

# Register domain handlers
navigation.register(_router)
settings.register(_router)
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


# Exact-match fleet routes.
#
# Report PDF/CSV exports (faults_pdf, faults_csv, fuel_pdf, fuel_csv,
# health_pdf, health_csv, eff_pdf, eff_csv, scorecard_pdf,
# scorecard_csv, cam_check_pdf, cam_check_csv, cam_check_history)
# were retired in Tier 3 — they all moved to the dashboard.  Cached
# buttons fall through to the "unknown action" handler.
_router.exact("cmd_faults", cmd_faults)
_router.exact("cmd_critical", cmd_critical)
_router.exact("cmd_fuel", cmd_fuel)
_router.exact("cmd_alerts", cmd_alerts)
_router.exact("alert_disable_all", cmd_alert_disable_all)
_router.exact("cmd_myvehicle", cmd_vehicle)
_router.exact("cmd_health", cmd_health)
_router.exact("cmd_efficiency", cmd_efficiency)
_router.exact("cmd_weather", cmd_weather)
_router.exact("cmd_api_status", cmd_api_status)
# cmd_camera_check / cmd_camera_report / cmd_cam_tool routes removed
# with the camera-UI retirement; cached buttons fall through to the
# unknown-action handler.
_router.exact("cmd_account", cmd_account)
_router.exact("cmd_users", cmd_users)
_router.exact("cmd_invite", cmd_invite)
_router.exact("cmd_scorecards", cmd_scorecards)
_router.exact("cmd_livemap", cmd_livemap)
_router.exact("cmd_route", cmd_route)
_router.exact("cmd_geofences", cmd_geofences)
# cmd_add_zone / cmd_list_zones / cmd_delete_zone routes removed
# with the geofence-CRUD retirement; cached buttons fall through.
_router.exact("cmd_events", cmd_events)
_router.exact("cmd_fuelcost", cmd_fuelcost)
_router.exact("fuelcost_add", cmd_fuelcost_add)
_router.exact("fuelcost_summary", cmd_fuelcost_summary)
_router.exact("cmd_costmile", cmd_costmile)
_router.exact("cmd_maintenance", cmd_maintenance)
# Maintenance wizard sub-routes (maint_add / maint_view / maint_skip_*)
# moved to the dashboard.  Cached buttons fall through to the
# "unknown action" handler; the inline ``maint_done_`` prefix is
# the only maintenance interaction kept on the bot — see the
# prefix registration block further below.
_router.exact("cmd_work_hours", cmd_work_hours)
# The wizard sub-routes (whours_add / wsched_add / per-schedule
# edit prefixes) were removed when /work_hours moved to the
# dashboard; cached buttons fall through to "unknown action".
# Callback strings kept as ``cmd_auto_reports`` / ``ar_*`` even though
# the underlying handlers + file are renamed to ``scheduled_reports``
# — these strings are baked into InlineKeyboardButton.callback_data on
# every Scheduled-Reports message the bot has already sent.  Renaming
# would break the buttons cached in users' Telegram chats.  Drop these
# legacy strings only after one full release cycle has passed; until
# then keep the routing stable and let the new code own the function
# names.
_router.exact("cmd_auto_reports", cmd_scheduled_reports)
_router.exact("ar_unsub", cmd_scheduled_reports_unsubscribe)
# Multi-schedule (2026-06): per-row Stop + Add-new entrypoints.  The
# Stop handler is registered as a prefix so ``ar_stop_faults``,
# ``ar_stop_fuel``, etc. all route through.
_router.exact("ar_add", cmd_scheduled_reports_add)
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
#
# Tier 3 retired every ``*_pdf_<company>`` / ``*_csv_<company>``
# prefix wrapper — those PDF/CSV exports moved to the dashboard
# where sortable tables + proper export beat a Telegram attachment.

async def _cmd_critical_co(u, c):
    co = u.callback_query.data.replace("cmd_critical_", "")
    await cmd_critical(u, c, company=co)

async def _alert_toggle(u, c):
    alert_type = u.callback_query.data.replace("alert_toggle_", "")
    await cmd_alert_toggle(u, c, alert_type=alert_type)

async def _ai_toggle(u, c):
    ai_type = u.callback_query.data.replace("ai_toggle_", "")
    await cmd_ai_alert_toggle(u, c, ai_type=ai_type)

async def _events_text(u, c):
    days = int(u.callback_query.data.replace("events_text_", ""))
    await cmd_events_text(u, c, days=days)

async def _events_csv(u, c):
    days = int(u.callback_query.data.replace("events_csv_", ""))
    await cmd_events_csv(u, c, days=days)

# ── Maintenance: inline "✓ Done" from scheduler-posted alerts ─────
#
# Every other ``cmd_maint_*`` handler was retired when the CRUD
# surface moved to the dashboard.  ``_maint_done`` is the only one
# still wired because the dashboard's "schedule completed" workflow
# can't replicate the on-the-go moment when a dispatcher (or driver
# whose vehicle the alert names) wants to clear an overdue ping
# without opening a browser.

async def _maint_done(u, c):
    task_id = int(u.callback_query.data.replace("maint_done_", ""))
    await cmd_maint_done(u, c, task_id=task_id)

# ── Camera tool prefix wrappers ──────────────────────────────────
#
# The entire camera surface (paginated picker ``camco_<company>`` /
# ``cam_page_<co>_<page>``, per-truck ``cam_vehicle_<truck>`` button,
# fleet-wide and per-truck AI checks, history browser) moved to the
# dashboard.  Cached buttons fall through to the unknown-action
# handler.  Camera *alerts* still ping via the scheduler.

# NOTE: the entire ``_whours_*`` / ``_wsched_*`` callback grid was
# removed when Working Hours moved to the dashboard.  Old buttons
# cached in Telegram chats fall through to the "unknown action"
# fallback; new entries land on the dashboard via cmd_work_hours.

async def _ar_stop_per_type(u, c):
    """Per-row Stop button on the multi-schedule menu (2026-06)."""
    rtype = u.callback_query.data.replace("ar_stop_", "")
    await cmd_scheduled_reports_stop(u, c, report_type=rtype)


async def _ar_freq(u, c):
    freq = u.callback_query.data.replace("ar_freq_", "")
    await cmd_scheduled_reports_subscribe(u, c, frequency=freq)

async def _ar_type(u, c):
    rtype = u.callback_query.data.replace("ar_type_", "")
    await cmd_scheduled_reports_set_type(u, c, report_type=rtype)

async def _ar_hour(u, c):
    hour = int(u.callback_query.data.replace("ar_hour_", ""))
    await cmd_scheduled_reports_set_hour(u, c, hour=hour)

async def _ar_tz(u, c):
    tz = u.callback_query.data.replace("ar_tz_", "")
    await cmd_scheduled_reports_set_tz(u, c, tz=tz)

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

# costmile_pdf / costmile_csv exact-match handlers were retired with
# cmd_costmile_report — the dashboard owns CSV/PDF generation now.

# Register prefix routes (order matters: longer/more specific first)
_router.prefix("cmd_critical_", _cmd_critical_co)
_router.prefix("alert_toggle_", _alert_toggle)
_router.prefix("ai_toggle_", _ai_toggle)
_router.prefix("events_text_", _events_text)
_router.prefix("events_csv_", _events_csv)
# Retired in Tier 3 (moved to dashboard):
# faults_pdf_, faults_csv_, fuel_pdf_, fuel_csv_, health_pdf_,
# health_csv_, eff_pdf_, eff_csv_, scorecard_pdf_, scorecard_csv_,
# costmile_pdf_, costmile_csv_
# Only the inline "✓ Done" prefix survives — every other
# ``maint_*`` callback path moved to the dashboard.  Cached buttons
# in old chats fall through to the global "unknown action" handler.
_router.prefix("maint_done_", _maint_done)
# Camera UI (``camco_``, ``cam_page_``, ``cam_vehicle_``) and
# geofence-CRUD wizard prefixes (``del_zone:``, ``zone_roles:``,
# ``gf_detail_``) all moved to the dashboard.  Working-hours
# wizard prefixes (whours_* / wsched_*) too.
# Order matters: ``ar_stop_`` must register BEFORE any prefix that
# could also match (none today, but the rule keeps future overlaps
# safe — longer-specific prefixes first).
_router.prefix("ar_stop_", _ar_stop_per_type)
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
    from capabilities.permissions.roles import can
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


# Per-company report shortcuts (cofaults_ / cofuel_ / cohealth_ /
# coeff_ / coweather_) were retired with their underlying commands.
# The dashboard's company picker covers the same scope natively.
# Only the generic ``co_<CODE>`` submenu landing card remains.
_router.prefix("co_", _co_submenu)


# ── Geofence CRUD (retired) ─────────────────────────────────────
#
# The add-zone wizard (gf_detail_*, del_zone:*, zone_roles:*) was
# retired from the bot — entering lat/lng coordinates and toggling
# role-checkboxes is dashboard-shaped work.  Cached buttons fall
# through to the unknown-action handler.  The read-only
# ``/geofences`` listing stays for quick "what zones do we have?"
# lookups, and the entry/exit scheduler keeps firing alerts.


# ── Alert acknowledgment ────────────────────────────────────────

async def _ack_alert(u, c):
    try:
        ack_id = int(u.callback_query.data.replace("ack_alert_", ""))
    except (ValueError, IndexError):
        await u.callback_query.answer("Invalid alert", show_alert=True)
        return
    await handle_alert_ack(u, c, ack_id=ack_id)

_router.prefix("ack_alert_", _ack_alert)

# Spine-delivered notification actions (``notif_act:{corr_key}:{action}``)
# — the notifications capability routes the press to whichever source
# registered the handler (alert.ack, later work_order.approve, …).
# docs/architecture/alert-dm-migration.md, Phase 2.

async def _notif_action(u, c):
    from capabilities.notifications.actions import handle_action_callback
    await handle_action_callback(u, c)

_router.prefix("notif_act:", _notif_action)

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
    from capabilities.permissions.roles import can
    if not can(user.role, "can_manage_users"):
        await query.answer(t("access.no_access"), show_alert=True)
        return
    text = await _render_audit_log(user.account_id)
    await _show(update, context, [text], keyboard=back_kb())

_router.exact("cmd_audit", _cmd_audit)


# ── Invite flow (button-driven) ─────────────────────────────────
#
# The slash-command ``/invite`` (handler at interfaces/bot/management.py)
# shows a four-button role picker when called with no args.  These
# callbacks complete the flow on the Telegram side — bot invites are
# Telegram-channel only.  URL + Email channels stay dashboard-only
# (richer UX: typeahead vehicle picker, debounced duplicate-recipient
# check, bounce-tracked Resend transport, multi-locale i18n).  An
# operator who wants those clicks the "📨 Invite team member" button
# in the Management submenu, which deep-links to /admin/invites.

async def _cmd_invite_role(update, context):
    """Handle a role-pick from the ``/invite`` inline keyboard.

    Non-driver roles → create the Telegram-link invite immediately,
    return the deep-link with a Copy button.  Driver role → set a
    pending text-input state so the operator's next message is the
    vehicle number; the orphaned-since-refactor branch at
    ``text_handlers.py:invite_driver`` already handles the rest.
    """
    query = update.callback_query
    await query.answer()
    user = context.user_data.get("_db_user")
    if not user:
        from interfaces.bot.auth import _get_user
        user, _, _ = await _get_user(update)
    if not user:
        await query.answer(t("access.no_access"), show_alert=True)
        return
    context.user_data["_db_user"] = user

    from capabilities.permissions.roles import can
    if not can(user.role, "can_invite"):
        await query.answer(t("access.no_access"), show_alert=True)
        return

    role_name = query.data.replace("inv_", "")
    try:
        invite_role = Role.from_str(role_name)
    except ValueError:
        await query.answer(t("common.unknown_action"), show_alert=True)
        return

    # Rank check mirrors validate_invite_role from the slash-command
    # path.  HR-tier (can_invite + low rank) can't invite Admin or
    # peer-tier; OWNER-via-invite is forbidden outright.
    from capabilities.permissions.roles import validate_invite_role
    ok, reason = validate_invite_role(user.role, invite_role)
    if not ok:
        msg = (
            t('access.owner_via_invite')
            if reason == "owner_via_invite"
            else t('access.cant_invite_higher')
        )
        await _show(update, context, [msg], keyboard=back_kb())
        return

    # Driver flow needs a vehicle.  Stash a pending state and prompt
    # for the vehicle number — text_handlers.py picks up the next
    # message and finishes the invite create with truck_num set.
    # No paginated vehicle-picker on Telegram: typing the truck
    # number is faster than scrolling 100 inline buttons, and the
    # dashboard has the typeahead VehiclePicker for the rare case
    # where the operator doesn't know the number off-hand.
    if invite_role == Role.DRIVER:
        context.user_data["_pending"] = "invite_driver"
        await _show(update, context, [
            t('invite.driver_vehicle_prompt'),
        ], keyboard=back_kb())
        return

    # Non-driver: create the invite immediately + show the link.
    try:
        invite = await get_platform_db().create_invite(
            account_id=user.account_id,
            created_by=user.id,
            role=invite_role,
        )
        link = make_invite_link(invite.code, context)
        text = format_invite_created(
            invite.code, role_display(invite_role), "",
            invite_link=link,
        )
        from interfaces.bot.keyboards import invite_kb
        kb = invite_kb(link)
        await _show(update, context, [text], keyboard=kb)
    except Exception as e:
        logger.error("Invite (button) error: %s", e, exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


# Register one route per role — the existing keyboard at
# management.py:cmd_invite emits callback_data="inv_admin" etc.
for _role_key in ("admin", "fleet", "safety", "dispatcher", "driver"):
    _router.exact(f"inv_{_role_key}", _cmd_invite_role)


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
                    format_unregistered_member(acct_name, name=first, support_contact=SUPPORT_CONTACT),
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
