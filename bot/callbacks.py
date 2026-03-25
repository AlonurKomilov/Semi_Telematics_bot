"""Callback query router and text input handler."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import Role
from permissions import can, role_display, is_system_owner
from samsara_client import COMPANY_DISPLAY, populate_company_display
from formatters import (
    format_help,
    format_welcome_unregistered,
    format_system_owner_welcome,
    format_invite_created,
)

import bot.config as _cfg
from bot.config import db, SUPPORT_CONTACT, get_user_company_codes, invalidate_client, get_client
from bot.keyboards import (
    main_menu_kb, co_menu_kb, back_kb, system_owner_kb, unregistered_kb,
    invite_kb, skip_name_kb, truck_company_picker_kb, vehicle_list_kb,
    group_picker_kb,
    submenu_reports_kb, submenu_tools_kb, submenu_costs_kb, submenu_mgmt_kb,
    user_settings_kb, quiet_hours_kb, quiet_hours_picker_kb, settings_tz_kb,
    language_kb,
)
from bot.helpers import _show, _show_loading, _user_menu_kb, _render_audit_log, _safe_error
from bot.auth import _get_user, _group_chat_guard
from bot.i18n import t
from bot.registration import cmd_start, cmd_register, cmd_join
from bot.fleet import (
    cmd_faults, cmd_faults_pdf, cmd_faults_csv,
    cmd_truck, cmd_critical,
    cmd_fuel, cmd_fuel_pdf, cmd_fuel_csv,
    cmd_alerts, cmd_alert_toggle, cmd_ai_alert_toggle, cmd_alert_disable_all,
    cmd_alert_history, cmd_pending_alerts,
    cmd_truck_report,
    cmd_health, cmd_health_pdf, cmd_health_csv,
    cmd_efficiency, cmd_efficiency_pdf, cmd_efficiency_csv,
    cmd_weather, cmd_api_status,
)
from bot.management import cmd_account, cmd_users, cmd_addcompany, cmd_groups
from bot.admin import cmd_admin, cmd_accounts
from bot.scorecards import cmd_scorecards, cmd_scorecards_pdf, cmd_scorecards_csv
from bot.fuel_costs import cmd_fuelcost, cmd_fuelcost_add, handle_fuelcost_text, cmd_fuelcost_summary
from bot.cost_per_mile import cmd_costmile, cmd_costmile_report
from bot.auto_reports import cmd_auto_reports, cmd_auto_reports_subscribe, cmd_auto_reports_unsubscribe, cmd_auto_reports_set_hour, cmd_auto_reports_set_tz, cmd_auto_reports_set_type
from bot.maintenance import (
    cmd_maintenance, cmd_maint_add, cmd_maint_type,
    cmd_maint_view, cmd_maint_done, handle_maintenance_text,
)
from bot.maps import cmd_livemap
from bot.routes import cmd_route, cmd_route_go, handle_route_text
from bot.geofences import cmd_geofences
from bot.events import cmd_events, cmd_events_text, cmd_events_csv
from bot.ai import (
    cmd_ai, cmd_ai_ask_prompt, cmd_ai_answer, cmd_ai_summary,
    cmd_ai_diagnose, cmd_ai_suggest, cmd_ai_newchat,
    cmd_ai_models, cmd_ai_set_model, cmd_ai_alerts,
)
from bot.alerts import handle_alert_ack, handle_alert_snooze, handle_snooze_pick


async def _show_truck_list(update, context, user, company_filter, page=0):
    """Fetch all trucks and show a paginated button list."""
    orgs_db = await db.get_account_companies(user.account_id)
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
            from samsara_client import COMPANY_DISPLAY
            header += f"  📡 {COMPANY_DISPLAY.get(company_filter, company_filter)}\n"

        kb = vehicle_list_kb(vehicles, page=page, company_filter=company_filter)
        await _show(update, context, [header + f"\n{t('truck.browse_tap')}"],
                    keyboard=kb)
    except Exception as e:
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


# ── Extracted callback handlers ──────────────────────────────────

async def _handle_ai_usage(update, context, user, data):
    """Handle AI usage statistics display (cmd_ai_usage, ai_usage_*)."""
    query = update.callback_query
    if not can(user.role, "can_manage_account"):
        await query.answer(t("access.no_access"), show_alert=True)
        return
    await query.answer()
    days = 30
    if data.startswith("ai_usage_"):
        try:
            days = int(data.replace("ai_usage_", ""))
        except ValueError:
            days = 30
    stats = await db.get_ai_usage_stats(user.account_id, days=days)
    daily = await db.get_ai_usage_daily(user.account_id, days=min(days, 7))

    import ai_client as _aic
    total_cost = 0.0
    model_costs: dict[str, float] = {}
    if stats["by_model"]:
        for m, minfo in stats["by_model"].items():
            prompt_tok = minfo.get("prompt_tokens", 0)
            reply_tok = minfo.get("reply_tokens", 0)
            c = _aic.estimate_cost(m, prompt_tok, reply_tok)
            model_costs[m] = c
            total_cost += c

    tok_str = f"{stats['total_tokens']:,}"
    cost_str = f"${total_cost:.4f}"
    lines = [
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('ai_usage.title')}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('ai_usage.period').format(days=days)}\n"
        f"\n  {t('ai_usage.total_requests').format(count=stats['total_requests'])}"
        f"\n  {t('ai_usage.total_tokens').format(count=tok_str)}"
        f"\n  {t('ai_usage.est_cost').format(cost=cost_str)}\n"
    ]
    if stats["by_type"]:
        lines.append(f"\n  {t('ai_usage.by_type')}")
        for rt, info in stats["by_type"].items():
            lines.append(f"  • {rt}: {info['requests']} req ({info['tokens']:,} tok)")
    if stats["by_model"]:
        lines.append(f"\n  {t('ai_usage.by_model')}")
        for m, info in stats["by_model"].items():
            disp = _aic.MODEL_REGISTRY.get(m, {}).get("display", m)
            mc = model_costs.get(m, 0)
            pt = info.get("prompt_tokens", 0)
            rt = info.get("reply_tokens", 0)
            lines.append(
                f"  • {disp}: {info['requests']} req\n"
                f"    {pt:,} in / {rt:,} out · ${mc:.4f}"
            )
    if daily:
        lines.append(f"\n  {t('ai_usage.daily')}")
        for d in daily:
            lines.append(f"  • {d['day']}: {d['requests']} req ({d['tokens'] or 0:,} tok)")

    text = "\n".join(lines)
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t('ai_usage.btn_7d'), callback_data="ai_usage_7"),
            InlineKeyboardButton(t('ai_usage.btn_30d'), callback_data="ai_usage_30"),
            InlineKeyboardButton(t('ai_usage.btn_90d'), callback_data="ai_usage_90"),
        ],
        [InlineKeyboardButton(t('ai.btn_models'), callback_data="ai_models")],
        [InlineKeyboardButton(t('menu.back'), callback_data="cmd_menu")],
    ])
    await _show(update, context, [text], keyboard=kb)


async def _handle_user_menu(update, context, user, target_tid):
    """Show action buttons for a specific team member."""
    query = update.callback_query
    await query.answer()
    target_user = await db.get_user_by_telegram_id(target_tid)
    if not target_user or target_user.account_id != user.account_id:
        await _show(update, context, [t('user_mgmt.user_not_found_short')], keyboard=back_kb())
        return

    alerts_icon = "🔔" if target_user.alerts_on else "🔕"
    alerts_label = t('common.on') if target_user.alerts_on else t('common.off')

    rows = [
        [InlineKeyboardButton(
            t('user_mgmt.change_role').format(role=role_display(target_user.role)),
            callback_data=f"usrrole_{target_tid}",
        )],
        [InlineKeyboardButton(
            t('user_mgmt.change_dept').format(dept=target_user.department or '—'),
            callback_data=f"usrdept_{target_tid}",
        )],
        [InlineKeyboardButton(
            f"{alerts_icon} {t('user_mgmt.alerts_toggle').format(status=alerts_label)}",
            callback_data=f"usralerts_{target_tid}",
        )],
    ]
    if target_tid != user.telegram_id:
        rows.append([InlineKeyboardButton(
            t('user_mgmt.remove_user'),
            callback_data=f"usrremove_{target_tid}",
        )])
    rows.append([InlineKeyboardButton(t('user_mgmt.back_team'), callback_data="cmd_users")])

    await _show(update, context, [
        f"👤 <b>User <a href='tg://user?id={target_tid}'>{target_user.label}</a></b>\n"
        f"{t('user_mgmt.role_label')} {role_display(target_user.role)}\n"
        f"{t('user_mgmt.dept_label')} {target_user.department or '—'}\n"
        f"{t('user_mgmt.truck_label_short')} {target_user.truck_num or '—'}\n"
        f"{t('user_mgmt.alerts_status_label')} {alerts_icon} {alerts_label}"
    ], keyboard=InlineKeyboardMarkup(rows))


async def _handle_user_role_picker(update, context, user, target_tid):
    """Show role selection grid for changing a user's role."""
    query = update.callback_query
    await query.answer()
    roles_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t('user_mgmt.role_owner'), callback_data=f"setrole_{target_tid}_owner"),
            InlineKeyboardButton(t('user_mgmt.role_admin'), callback_data=f"setrole_{target_tid}_admin"),
        ],
        [
            InlineKeyboardButton(t('user_mgmt.role_fleet'), callback_data=f"setrole_{target_tid}_fleet_manager"),
            InlineKeyboardButton(t('user_mgmt.role_dispatcher'), callback_data=f"setrole_{target_tid}_dispatcher"),
        ],
        [
            InlineKeyboardButton(t('user_mgmt.role_driver'), callback_data=f"setrole_{target_tid}_driver"),
        ],
        [InlineKeyboardButton(t('user_mgmt.cancel'), callback_data=f"usrmenu_{target_tid}")],
    ])
    target_user = await db.get_user_by_telegram_id(target_tid)
    target_label = target_user.label if target_user else str(target_tid)
    await _show(update, context, [
        f"{t('user_mgmt.role_picker_title').format(user=target_label)}\n\n"
        f"{t('user_mgmt.role_picker_prompt')}"
    ], keyboard=roles_kb)


async def _handle_set_role(update, context, user, data):
    """Apply a role change to a user."""
    query = update.callback_query
    parts = data.split("_")  # setrole_{tid}_{role}
    target_tid = int(parts[1])
    new_role_str = "_".join(parts[2:])  # handles fleet_manager
    await query.answer()

    try:
        new_role = Role.from_str(new_role_str)
    except ValueError:
        await _show(update, context, [t('user_mgmt.invalid_role')], keyboard=back_kb())
        return

    target_user = await db.get_user_by_telegram_id(target_tid)
    if not target_user or target_user.account_id != user.account_id:
        await _show(update, context, [t('user_mgmt.user_not_found_short')], keyboard=back_kb())
        return

    if new_role == Role.OWNER and user.role != Role.OWNER:
        await _show(update, context, [t('access.cant_promote_owner')], keyboard=back_kb())
        return

    old_role = target_user.role
    await db.update_user(target_user.id, role=new_role)
    await db.add_audit_log(
        account_id=user.account_id, user_id=user.id,
        action="role_changed", target_type="user",
        target_id=str(target_user.id),
        details=f"{user.label} changed {target_user.label}: {role_display(old_role)} → {role_display(new_role)}",
    )
    await _show(update, context, [
        t('user_mgmt.role_updated').format(user=target_user.label, role=role_display(new_role))
    ], keyboard=back_kb())


async def _handle_company_menu(update, context, user, code):
    """Show detail view for a specific company with actions."""
    query = update.callback_query
    await query.answer()

    company = await db.get_company_by_code(user.account_id, code)
    if not company:
        await _show(update, context, [t('company.not_found')], keyboard=back_kb())
        return

    lines = [
        f"🏢 <b>{company.display_name or company.code}</b>\n",
        f"  {t('company.code_label')} <b>{company.code}</b>",
        f"  {t('company.api_key_label')} <code>{'•' * 8}…{company.samsara_api_key[-4:]}</code>" if len(company.samsara_api_key) > 4 else f"  {t('company.api_key_label')} {t('company.api_key_configured')}",
        f"  {t('company.active_days_label')} <b>{company.active_days}</b>",
        f"  {t('company.added_label')} {company.created_at[:10] if company.created_at else '—'}",
    ]

    rows = []
    if can(user.role, "can_manage_account"):
        rows.append([InlineKeyboardButton(
            t('company.api_status'), callback_data=f"co_api_status_{code}",
        )])
    if can(user.role, "can_manage_companies"):
        rows.append([InlineKeyboardButton(
            t('company.change_key'), callback_data=f"co_chkey_{code}",
        )])
        rows.append([InlineKeyboardButton(
            t('company.rename'), callback_data=f"co_rename_{code}",
        )])
        rows.append([InlineKeyboardButton(
            t('company.remove'), callback_data=f"rmco_{code}",
        )])
    rows.append([InlineKeyboardButton(t('company.back_companies'), callback_data="cmd_account")])

    await _show(update, context, ["\n".join(lines)],
                keyboard=InlineKeyboardMarkup(rows))


async def _handle_co_api_status(update, context, user, code):
    """Check API connectivity for a single company."""
    query = update.callback_query
    await query.answer()

    company = await db.get_company_by_code(user.account_id, code)
    if not company:
        await _show(update, context, [t('company.not_found')], keyboard=back_kb())
        return

    from samsara_client import SamsaraClient
    client = SamsaraClient(
        api_key=company.samsara_api_key,
        base_url="https://api.samsara.com",
    )
    try:
        vehicles = await client.get_vehicles()
        status_line = f"  {t('company.api_ok').format(name=code, count=len(vehicles))}"
    except Exception as e:
        err = str(e)
        if len(err) > 80:
            err = err[:77] + "…"
        status_line = f"  {t('company.api_fail').format(name=code, error=err)}"
    finally:
        await client.close()

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('company.api_status_title').format(name=code)}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n{status_line}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t('menu.back'), callback_data=f"comenu_{code}")],
    ])
    await _show(update, context, [text], keyboard=kb)


async def _handle_invite_pick(update, context, user):
    """Show role picker for creating an invite."""
    query = update.callback_query
    await query.answer()
    if not can(user.role, "can_invite"):
        await query.answer(t("access.no_access"), show_alert=True)
        return
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t('user_mgmt.role_admin'), callback_data="inv_admin"),
            InlineKeyboardButton(t('user_mgmt.role_fleet'), callback_data="inv_fleet_manager"),
        ],
        [
            InlineKeyboardButton(t('user_mgmt.role_dispatcher'), callback_data="inv_dispatcher"),
            InlineKeyboardButton(t('user_mgmt.role_driver'), callback_data="inv_driver"),
        ],
        [InlineKeyboardButton(t('menu.back'), callback_data="cmd_users")],
    ])
    await _show(update, context, [
        f"{t('invite.title')}\n\n"
        f"  {t('invite.step1')}\n\n"
        f"{t('invite.select_role')}"
    ], keyboard=kb)


async def _handle_invite_create(update, context, user, data):
    """Create an invite for the selected role."""
    query = update.callback_query
    await query.answer()
    role_str = data[4:]  # admin, fleet_manager, dispatcher, driver
    try:
        invite_role = Role.from_str(role_str)
    except ValueError:
        await _show(update, context, [t('user_mgmt.invalid_role')], keyboard=back_kb())
        return

    if invite_role == Role.DRIVER:
        context.user_data["_pending"] = "invite_driver"
        await _show(update, context, [
            f"{t('invite.driver_title')}\n\n"
            f"  {t('invite.step2')}\n\n"
            f"{t('invite.driver_prompt')}"
        ], keyboard=back_kb())
        return

    try:
        invite = await db.create_invite(
            account_id=user.account_id,
            created_by=user.id,
            role=invite_role,
            department="general",
        )
        link = f"https://t.me/{_cfg.bot_username}?start=join_{invite.code}" if _cfg.bot_username else None
        text = format_invite_created(
            invite.code, role_display(invite_role), "general",
            invite_link=link,
        )
        kb = invite_kb(link)
        await _show(update, context, [text], keyboard=kb)
    except Exception as e:
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


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
            await _show(update, context,
                        [format_welcome_unregistered(SUPPORT_CONTACT)],
                        keyboard=unregistered_kb())
        return

    # Check if account is active
    account = await db.get_account(user.account_id)
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
    from bot.i18n import set_lang
    set_lang(getattr(user, "language", "en") or "en")

    # Populate COMPANY_DISPLAY for this user's account
    companies = await db.get_account_companies(user.account_id)
    populate_company_display(companies)

    # ── Main menu ───────────────────────────────────────────────
    if data == "cmd_menu":
        await query.answer()
        account = await db.get_account(user.account_id)
        company_codes = [o.code for o in companies]
        text = format_help(company_codes, user=user, account=account)
        if sys_owner:
            text += "\n\n  ⚙️ <i>System admin: /admin</i>"
        kb = main_menu_kb(user.role, company_codes)
        await _show(update, context, [text], keyboard=kb)

    # ── Sub-menus ───────────────────────────────────────────────
    elif data == "submenu_reports":
        await query.answer()
        company_codes = [o.code for o in companies]
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  {t('reports_menu.header')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {t('reports_menu.subtitle')}"
        ], keyboard=submenu_reports_kb(user.role, company_codes))

    elif data == "submenu_tools":
        await query.answer()
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  {t('tools_menu.header')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {t('tools_menu.subtitle')}"
        ], keyboard=submenu_tools_kb(user.role))

    elif data == "submenu_costs":
        await query.answer()
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  {t('costs_menu.header')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {t('costs_menu.subtitle')}"
        ], keyboard=submenu_costs_kb(user.role))

    elif data == "submenu_mgmt":
        await query.answer()
        has_api = bool(companies)
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  {t('mgmt_menu.header')}\n"
            "━━━━━━━━━━━━━━━━━━━"
        ], keyboard=submenu_mgmt_kb(user.role, has_api))

    # ── Fleet commands ──────────────────────────────────────────
    elif data == "cmd_faults":
        await cmd_faults(update, context)
    elif data == "faults_pdf":
        await cmd_faults_pdf(update, context)
    elif data == "faults_csv":
        await cmd_faults_csv(update, context)
    elif data.startswith("faults_pdf_"):
        co = data.replace("faults_pdf_", "")
        await cmd_faults_pdf(update, context, company=co)
    elif data.startswith("faults_csv_"):
        co = data.replace("faults_csv_", "")
        await cmd_faults_csv(update, context, company=co)
    elif data == "cmd_critical":
        await cmd_critical(update, context)
    elif data.startswith("cmd_critical_"):
        co = data.replace("cmd_critical_", "")
        await cmd_critical(update, context, company=co)
    elif data == "cmd_fuel":
        await cmd_fuel(update, context)
    elif data == "fuel_pdf":
        await cmd_fuel_pdf(update, context)
    elif data == "fuel_csv":
        await cmd_fuel_csv(update, context)
    elif data.startswith("fuel_pdf_"):
        co = data.replace("fuel_pdf_", "")
        await cmd_fuel_pdf(update, context, company=co)
    elif data.startswith("fuel_csv_"):
        co = data.replace("fuel_csv_", "")
        await cmd_fuel_csv(update, context, company=co)
    elif data == "cmd_alerts":
        await cmd_alerts(update, context)
    elif data.startswith("alert_toggle_"):
        alert_type = data.replace("alert_toggle_", "")
        await cmd_alert_toggle(update, context, alert_type=alert_type)
    elif data.startswith("ai_toggle_"):
        ai_type = data.replace("ai_toggle_", "")
        await cmd_ai_alert_toggle(update, context, ai_type=ai_type)
    elif data == "alert_disable_all":
        await cmd_alert_disable_all(update, context)

    # ── AI ────────────────────────────────────────────────────
    elif data == "cmd_ai":
        await cmd_ai(update, context)
    elif data == "cmd_ai_alerts":
        await cmd_ai_alerts(update, context)
    elif data in ("ai_ask", "ai_chat"):
        await cmd_ai_ask_prompt(update, context)
    elif data == "ai_newchat":
        await cmd_ai_newchat(update, context)
    elif data == "ai_summary":
        await cmd_ai_summary(update, context)
    elif data.startswith("ai_sug_"):
        try:
            idx = int(data.replace("ai_sug_", ""))
        except ValueError:
            idx = -1
        await cmd_ai_suggest(update, context, index=idx)
    elif data.startswith("ai_diag_"):
        # Format: ai_diag_{alert_type}_{company}_{truck_name}[:ack_id]
        # Old format: ai_diag_{company}_{truck_name} (backward compat)
        parts = data.replace("ai_diag_", "").split("_", 2)
        ack_id_arg = None
        if len(parts) == 3:
            truck_part = parts[2]
            # Extract optional ack_id after colon
            if ":" in truck_part:
                truck_part, ack_str = truck_part.rsplit(":", 1)
                try:
                    ack_id_arg = int(ack_str)
                except ValueError:
                    pass
            await cmd_ai_diagnose(
                update, context,
                truck_name=truck_part, company=parts[1],
                alert_context=parts[0], ack_id=ack_id_arg,
            )
        elif len(parts) == 2:
            await cmd_ai_diagnose(update, context, truck_name=parts[1], company=parts[0])
    elif data == "ai_models":
        await cmd_ai_models(update, context)
    elif data.startswith("ai_setmodel_"):
        model = data.replace("ai_setmodel_", "")
        await cmd_ai_set_model(update, context, model_name=model)

    elif data == "cmd_mytruck":
        await cmd_truck(update, context)
    elif data == "cmd_health":
        await cmd_health(update, context)
    elif data == "health_pdf":
        await cmd_health_pdf(update, context)
    elif data == "health_csv":
        await cmd_health_csv(update, context)
    elif data.startswith("health_pdf_"):
        co = data.replace("health_pdf_", "")
        await cmd_health_pdf(update, context, company=co)
    elif data.startswith("health_csv_"):
        co = data.replace("health_csv_", "")
        await cmd_health_csv(update, context, company=co)
    elif data == "cmd_efficiency":
        await cmd_efficiency(update, context)
    elif data == "eff_pdf":
        await cmd_efficiency_pdf(update, context)
    elif data == "eff_csv":
        await cmd_efficiency_csv(update, context)
    elif data.startswith("eff_pdf_"):
        co = data.replace("eff_pdf_", "")
        await cmd_efficiency_pdf(update, context, company=co)
    elif data.startswith("eff_csv_"):
        co = data.replace("eff_csv_", "")
        await cmd_efficiency_csv(update, context, company=co)
    elif data == "cmd_weather":
        await cmd_weather(update, context)
    elif data == "cmd_api_status":
        await cmd_api_status(update, context)

    # ── Samsara API integration guide (no companies yet) ─────────────
    elif data == "cmd_integrate_guide":
        await query.answer()
        context.user_data["_pending"] = "addcompany_code"
        context.user_data.pop("_addcompany", None)
        guide_text = t('samsara.connect_guide')
        await _show(update, context, [guide_text], keyboard=back_kb())

    elif data == "cmd_no_api_info":
        await query.answer()
        await _show(update, context, [t('samsara.not_connected_info')], keyboard=back_kb())

    # ── Account / Users ─────────────────────────────────────────
    elif data == "cmd_account":
        await cmd_account(update, context)

    # ── Company detail drill-down ───────────────────────────────
    elif data.startswith("comenu_"):
        code = data[7:]
        if not can(user.role, "can_manage_account"):
            await query.answer(t("access.no_access"), show_alert=True)
            return
        await _handle_company_menu(update, context, user, code)

    elif data.startswith("co_api_status_"):
        code = data[14:]
        if not can(user.role, "can_manage_account"):
            await query.answer(t("access.no_access"), show_alert=True)
            return
        await _handle_co_api_status(update, context, user, code)

    elif data.startswith("co_chkey_"):
        code = data[9:]
        if not can(user.role, "can_manage_companies"):
            await query.answer(t("access.no_access"), show_alert=True)
            return
        await query.answer()
        context.user_data["_pending"] = "change_api_key"
        context.user_data["_chkey_code"] = code
        await _show(update, context, [
            t('company.chkey_title', code=code) + "\n\n"
            + t('company.chkey_prompt') + "\n"
            + t('company.chkey_hint')
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('common.cancel_back'), callback_data=f"comenu_{code}")],
        ]))

    elif data.startswith("co_rename_"):
        code = data[10:]
        if not can(user.role, "can_manage_companies"):
            await query.answer(t("access.no_access"), show_alert=True)
            return
        await query.answer()
        context.user_data["_pending"] = "rename_company"
        context.user_data["_rename_code"] = code
        await _show(update, context, [
            t('company.rename_title', code=code) + "\n\n"
            + t('company.rename_prompt')
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('common.cancel_back'), callback_data=f"comenu_{code}")],
        ]))

    elif data == "cmd_users":
        await cmd_users(update, context)

    # ── Group / Channel management ──────────────────────────────
    elif data == "cmd_groups":
        await cmd_groups(update, context)

    elif data.startswith("rmgroup_"):
        chat_id_str = data[8:]
        await query.answer()
        if not user.is_admin_or_above:
            await query.answer(t("access.no_access"), show_alert=True)
            return
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(t('user_mgmt.remove_yes'), callback_data=f"rmgroupconfirm_{chat_id_str}"),
                InlineKeyboardButton(t('common.cancel_back'), callback_data="cmd_groups"),
            ],
        ])
        await _show(update, context, [
            f"⚠️ <b>Remove group <code>{chat_id_str}</code>?</b>\n\n"
            f"{t('groups.remove_confirm_msg')}"
        ], keyboard=kb)

    elif data.startswith("rmgroupconfirm_"):
        chat_id_str = data[15:]
        await query.answer()
        try:
            group_chat_id = int(chat_id_str)
            await db.remove_authorized_chat(user.account_id, group_chat_id)
        except ValueError:
            pass
        await cmd_groups(update, context)

    elif data == "addgroup_pick":
        if not user.is_admin_or_above:
            await query.answer(t("access.no_access"), show_alert=True)
            return
        await query.answer()
        context.user_data["_awaiting_chat_pick"] = True
        await update.effective_chat.send_message(
            f"{t('groups.pick_prompt')}\n\n"
            f"{t('groups.pick_cancel')}",
            parse_mode="HTML",
            reply_markup=group_picker_kb(),
        )

    elif data == "addgroup_confirm":
        await query.answer()
        pending = context.user_data.pop("_pending_group", None)
        if not pending:
            await _show(update, context,
                        [t('common.nothing_to_confirm')],
                        keyboard=back_kb())
            return
        await db.add_authorized_chat(
            account_id=user.account_id,
            chat_id=pending["chat_id"],
            chat_title=pending["title"],
            added_by=user.id,
        )
        await _show(update, context, [
            f"{t('groups.authorized_title')}\n\n"
            f"  💬 <b>{pending['title']}</b>\n"
            f"  🆔 <code>{pending['chat_id']}</code>\n\n"
            f"  {t('groups.bot_responds')}"
        ], keyboard=back_kb())

    elif data == "addgroup_cancel":
        await query.answer()
        context.user_data.pop("_pending_group", None)
        await cmd_groups(update, context)

    # ── Invite — role picker ────────────────────────────────────
    elif data == "cmd_invite_pick":
        await _handle_invite_pick(update, context, user)

    elif data.startswith("inv_"):
        await _handle_invite_create(update, context, user, data)

    # ── Add Company wizard (step 1: code) ───────────────────────────
    elif data == "cmd_addcompany_prompt":
        await query.answer()
        if not can(user.role, "can_manage_companies"):
            await query.answer(t("access.no_access"), show_alert=True)
            return
        context.user_data["_pending"] = "addcompany_code"
        context.user_data.pop("_addcompany", None)
        await _show(update, context, [
            f"{t('company.add_title')}\n\n"
            f"{t('company.add_step1')}\n\n"
            f"{t('company.add_step1_prompt')}\n"
            f"{t('company.add_step1_hint')}"
        ], keyboard=back_kb())

    # ── Remove Company (from account view) ──────────────────────────
    elif data.startswith("rmco_"):
        code = data[5:]
        if not can(user.role, "can_manage_companies"):
            await query.answer(t("access.no_access"), show_alert=True)
            return
        await query.answer()
        context.user_data["_pending"] = "confirm_remove_company"
        context.user_data["_rmco_code"] = code
        await _show(update, context, [
            f"{t('company.remove_title').format(code=code)}\n\n"
            f"{t('company.remove_msg')}\n\n"
            f"{t('company.remove_type_confirm').format(code=code)}"
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('common.cancel_back'), callback_data=f"comenu_{code}")],
        ]))

    # ── Add Company wizard: Skip display name ───────────────────────
    elif data == "addcompany_skip_name":
        await query.answer()
        wiz = context.user_data.pop("_addcompany", {})
        code = wiz.get("code", "")
        api_key = wiz.get("api_key", "")
        context.user_data.pop("_pending", None)
        if not code or not api_key:
            await _show(update, context, [t('company.wizard_lost')], keyboard=back_kb())
            return
        context.args = [f"{code}:{api_key}"]
        await cmd_addcompany(update, context)

    # ── Truck browser: company picker ───────────────────────────
    elif data == "cmd_truck_prompt":
        await query.answer()
        if not can(user.role, "can_truck_all"):
            await query.answer(t("access.no_access"), show_alert=True)
            return
        company_codes = [o.code for o in companies]
        if len(company_codes) == 1:
            # Skip picker, go straight to list
            await _show_truck_list(update, context, user, company_codes[0])
        else:
            await _show(update, context, [
                f"{t('truck.browse_title')}\n\n"
                f"{t('truck.browse_prompt')}"
            ], keyboard=truck_company_picker_kb(company_codes))

    elif data.startswith("trucks_browse_"):
        await query.answer()
        company_filter = data[14:]  # "ALL" or company code
        if company_filter == "ALL":
            company_filter = None
        await _show_truck_list(update, context, user, company_filter)

    elif data.startswith("trucks_page_"):
        await query.answer()
        # trucks_page_ORG_PAGE  or  trucks_page_ALL_PAGE
        parts = data.split("_")
        page = int(parts[-1])
        company_filter = "_".join(parts[2:-1])  # reconstruct company code
        if company_filter == "ALL":
            company_filter = None
        await _show_truck_list(update, context, user, company_filter, page=page)

    elif data == "noop":
        await query.answer()

    # ── User management (from users view) ───────────────────────
    elif data.startswith("usrmenu_"):
        target_tid = int(data[8:])
        await _handle_user_menu(update, context, user, target_tid)

    elif data.startswith("usrrole_"):
        target_tid = int(data[8:])
        await _handle_user_role_picker(update, context, user, target_tid)

    elif data.startswith("setrole_"):
        await _handle_set_role(update, context, user, data)

    elif data.startswith("usrdept_"):
        target_tid = int(data[8:])
        await query.answer()
        if not can(user.role, "can_manage_users"):
            await query.answer(t("access.no_access"), show_alert=True)
            return
        context.user_data["_pending"] = "change_dept"
        context.user_data["_dept_tid"] = target_tid
        target_user = await db.get_user_by_telegram_id(target_tid)
        target_label = target_user.label if target_user else str(target_tid)
        await _show(update, context, [
            f"{t('user_mgmt.dept_change_title').format(user=target_label)}\n\n"
            f"{t('user_mgmt.dept_current').format(dept=target_user.department if target_user else '—')}\n\n"
            f"{t('user_mgmt.dept_prompt')}"
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('common.cancel_back'), callback_data=f"usrmenu_{target_tid}")],
        ]))

    elif data.startswith("usralerts_"):
        target_tid = int(data[10:])
        await query.answer()
        if not can(user.role, "can_manage_users"):
            await query.answer(t("access.no_access"), show_alert=True)
            return
        target_user = await db.get_user_by_telegram_id(target_tid)
        if target_user and target_user.account_id == user.account_id:
            new_state = not target_user.alerts_on
            await db.update_user(target_user.id, alerts_on=new_state)
            icon = "🔔" if new_state else "🔕"
            label = t('common.on') if new_state else t('common.off')
            await _show(update, context, [
                t('user_mgmt.alerts_updated').format(user=target_user.label, icon=icon, status=label)
            ], keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton(t('menu.back'), callback_data=f"usrmenu_{target_tid}")],
            ]))
        else:
            await _show(update, context, [t('user_mgmt.user_not_found_short')], keyboard=back_kb())

    elif data.startswith("usrremove_"):
        target_tid = int(data[10:])
        await query.answer()
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(t('user_mgmt.remove_yes'), callback_data=f"usrremoveconfirm_{target_tid}"),
                InlineKeyboardButton(t('common.cancel_back'), callback_data="cmd_users"),
            ],
        ])
        target_user = await db.get_user_by_telegram_id(target_tid)
        target_label = target_user.label if target_user else str(target_tid)
        await _show(update, context, [
            f"{t('user_mgmt.remove_confirm_title').format(user=target_label)}\n\n"
            f"{t('user_mgmt.remove_confirm_msg')}"
        ], keyboard=kb)

    elif data.startswith("usrremoveconfirm_"):
        target_tid = int(data[17:])
        await query.answer()
        if target_tid == user.telegram_id:
            await _show(update, context, [t('access.cant_self_remove')], keyboard=back_kb())
            return
        target_user = await db.get_user_by_telegram_id(target_tid)
        target_label = target_user.label if target_user else str(target_tid)
        if target_user and target_user.account_id == user.account_id:
            await db.remove_user(target_user.id)
            await db.add_audit_log(
                account_id=user.account_id, user_id=user.id,
                action="user_removed", target_type="user",
                target_id=str(target_user.id),
                details=f"{user.label} removed user {target_label}",
            )
        await _show(update, context, [t('user_mgmt.user_removed').format(user=target_label)], keyboard=back_kb())

    # ── Per-truck fault reports ──────────────────────────────────
    elif data.startswith("truckfaults_"):
        await query.answer()
        # truckfaults_ORG_NAME
        rest = data[len("truckfaults_"):]
        parts = rest.split("_", 1)
        t_org = parts[0] if len(parts) >= 1 else ""
        t_name = parts[1] if len(parts) >= 2 else ""
        await cmd_truck_report(update, context, truck_name=t_name, company=t_org)

    # ── Truck lookup ────────────────────────────────────────────
    elif data.startswith("truck_") or data.startswith("cotruck_"):
        await cmd_truck(update, context)

    # ── Company detail drill-down ───────────────────────────────
    elif data.startswith("comenu_"):
        code = data[7:]
        await _handle_company_menu(update, context, user, code)

    elif data.startswith("co_api_status_"):
        code = data[14:]
        if not can(user.role, "can_manage_account"):
            await query.answer(t("access.no_access"), show_alert=True)
            return
        await _handle_co_api_status(update, context, user, code)

    elif data.startswith("co_chkey_"):
        code = data[9:]
        if not can(user.role, "can_manage_companies"):
            await query.answer(t("access.no_access"), show_alert=True)
            return
        await query.answer()
        context.user_data["_pending"] = "change_api_key"
        context.user_data["_chkey_code"] = code
        await _show(update, context, [
            t('company.chkey_title', code=code) + "\n\n"
            + t('company.chkey_prompt') + "\n"
            + t('company.chkey_hint')
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('common.cancel_back'), callback_data=f"comenu_{code}")],
        ]))

    elif data.startswith("co_rename_"):
        code = data[10:]
        if not can(user.role, "can_manage_companies"):
            await query.answer(t("access.no_access"), show_alert=True)
            return
        await query.answer()
        context.user_data["_pending"] = "rename_company"
        context.user_data["_rename_code"] = code
        company = await db.get_company_by_code(user.account_id, code)
        current_name = company.display_name if company else code
        await _show(update, context, [
            t('company.rename_title', code=code) + "\n\n"
            + t('company.rename_current', name=current_name) + "\n\n"
            + t('company.rename_prompt')
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('common.cancel_back'), callback_data=f"comenu_{code}")],
        ]))

    # ── Company sub-menu (per-company reports) ──────────────────────
    elif data.startswith("co_") and not data.startswith("cofaults_") \
            and not data.startswith("cofuel_") \
            and not data.startswith("cotruck_") \
            and not data.startswith("cohealth_") \
            and not data.startswith("coeff_") \
            and not data.startswith("coeff_pdf_") \
            and not data.startswith("coeff_csv_") \
            and not data.startswith("coweather_") \
            and not data.startswith("co_api_status_") \
            and not data.startswith("co_chkey_") \
            and not data.startswith("co_rename_"):
        co = data.replace("co_", "")
        await query.answer()
        name = COMPANY_DISPLAY.get(co, co)
        text = (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"  🏢  <b>{name}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"\n"
            f"  {t('company.select_report', code=co)}"
        )
        await _show(update, context, [text], keyboard=co_menu_kb(co))

    # Per-company commands — format pickers
    elif data.startswith("cofaults_"):
        co = data.replace("cofaults_", "")
        await cmd_faults(update, context, company=co)
    elif data.startswith("cofuel_"):
        co = data.replace("cofuel_", "")
        await cmd_fuel(update, context, company=co)
    elif data.startswith("cohealth_"):
        co = data.replace("cohealth_", "")
        await cmd_health(update, context, company=co)

    # Per-company commands — direct PDF/CSV
    elif data.startswith("coeff_pdf_"):
        co = data.replace("coeff_pdf_", "")
        await cmd_efficiency_pdf(update, context, company=co)
    elif data.startswith("coeff_csv_"):
        co = data.replace("coeff_csv_", "")
        await cmd_efficiency_csv(update, context, company=co)
    elif data.startswith("coeff_"):
        co = data.replace("coeff_", "")
        await cmd_efficiency(update, context, company=co)
    elif data.startswith("coweather_"):
        co = data.replace("coweather_", "")
        await cmd_weather(update, context, company=co)

    # ── Scorecards ──────────────────────────────────────────────
    elif data == "cmd_scorecards":
        await cmd_scorecards(update, context)
    elif data == "scorecard_pdf":
        await cmd_scorecards_pdf(update, context)
    elif data == "scorecard_csv":
        await cmd_scorecards_csv(update, context)
    elif data.startswith("scorecard_pdf_"):
        co = data.replace("scorecard_pdf_", "")
        await cmd_scorecards_pdf(update, context, company=co)
    elif data.startswith("scorecard_csv_"):
        co = data.replace("scorecard_csv_", "")
        await cmd_scorecards_csv(update, context, company=co)

    # ── Live Map ────────────────────────────────────────────────
    elif data == "cmd_livemap":
        await cmd_livemap(update, context)
    elif data.startswith("cmd_livemap_"):
        co = data.replace("cmd_livemap_", "")
        await cmd_livemap(update, context, company=co)

    # ── Route Replay ────────────────────────────────────────────
    elif data == "cmd_route":
        await cmd_route(update, context)
    elif data.startswith("route_go_"):
        parts = data[len("route_go_"):].rsplit("_", 1)
        if len(parts) == 2:
            co_vehicle = parts[0]
            days_ago = int(parts[1])
            # co_vehicle = "COMPANY_VEHICLENAME"
            cv_parts = co_vehicle.split("_", 1)
            co = cv_parts[0] if len(cv_parts) >= 1 else ""
            vname = cv_parts[1] if len(cv_parts) >= 2 else ""
            await cmd_route_go(update, context, company=co, vehicle_name=vname, days_ago=days_ago)

    # ── Geofences ───────────────────────────────────────────────
    elif data == "cmd_geofences":
        await cmd_geofences(update, context)
    elif data.startswith("gf_detail_"):
        # Individual geofence detail — just acknowledge for now
        await query.answer(t('geofence.detail_coming_soon'), show_alert=False)

    # ── Events ──────────────────────────────────────────────────
    elif data == "cmd_events":
        await cmd_events(update, context)
    elif data.startswith("events_text_"):
        days = int(data.replace("events_text_", ""))
        await cmd_events_text(update, context, days=days)
    elif data.startswith("events_csv_"):
        days = int(data.replace("events_csv_", ""))
        await cmd_events_csv(update, context, days=days)

    # ── Events ──────────────────────────────────────────────────
    elif data == "cmd_events":
        await cmd_events(update, context)
    elif data.startswith("events_text_"):
        days = int(data.replace("events_text_", ""))
        await cmd_events_text(update, context, days=days)
    elif data.startswith("events_csv_"):
        days = int(data.replace("events_csv_", ""))
        await cmd_events_csv(update, context, days=days)

    # ── Fuel Costs ──────────────────────────────────────────────
    elif data == "cmd_fuelcost":
        await cmd_fuelcost(update, context)
    elif data == "fuelcost_add":
        await cmd_fuelcost_add(update, context)
    elif data == "fuelcost_summary":
        await cmd_fuelcost_summary(update, context)

    # ── Cost Per Mile ───────────────────────────────────────────
    elif data == "cmd_costmile":
        await cmd_costmile(update, context)
    elif data == "costmile_pdf":
        await cmd_costmile_report(update, context, fmt="text")
    elif data == "costmile_csv":
        await cmd_costmile_report(update, context, fmt="csv")
    elif data.startswith("costmile_pdf_"):
        co = data.replace("costmile_pdf_", "")
        await cmd_costmile_report(update, context, company=co, fmt="text")
    elif data.startswith("costmile_csv_"):
        co = data.replace("costmile_csv_", "")
        await cmd_costmile_report(update, context, company=co, fmt="csv")

    # ── Maintenance ─────────────────────────────────────────────
    elif data == "cmd_maintenance":
        await cmd_maintenance(update, context)
    elif data == "maint_add":
        await cmd_maint_add(update, context)
    elif data.startswith("maint_type_"):
        task_type = data.replace("maint_type_", "")
        await cmd_maint_type(update, context, task_type=task_type)
    elif data == "maint_view":
        await cmd_maint_view(update, context)
    elif data.startswith("maint_done_"):
        task_id = int(data.replace("maint_done_", ""))
        await cmd_maint_done(update, context, task_id=task_id)

    # ── Auto Reports ────────────────────────────────────────────
    elif data == "cmd_auto_reports":
        await cmd_auto_reports(update, context)
    elif data.startswith("ar_freq_"):
        freq = data.replace("ar_freq_", "")
        await cmd_auto_reports_subscribe(update, context, frequency=freq)
    elif data.startswith("ar_type_"):
        rtype = data.replace("ar_type_", "")
        await cmd_auto_reports_set_type(update, context, report_type=rtype)
    elif data == "ar_unsub":
        await cmd_auto_reports_unsubscribe(update, context)
    elif data.startswith("ar_hour_"):
        hour = int(data.replace("ar_hour_", ""))
        await cmd_auto_reports_set_hour(update, context, hour=hour)
    elif data.startswith("ar_tz_"):
        tz = data.replace("ar_tz_", "")
        await cmd_auto_reports_set_tz(update, context, tz=tz)

    # ── Alert acknowledgment ────────────────────────────────────
    elif data.startswith("ack_alert_"):
        try:
            ack_id = int(data.replace("ack_alert_", ""))
        except (ValueError, IndexError):
            await query.answer("Invalid alert", show_alert=True)
            return
        await handle_alert_ack(update, context, ack_id=ack_id)

    elif data.startswith("snooze_pick_"):
        try:
            ack_id = int(data.replace("snooze_pick_", ""))
        except (ValueError, IndexError):
            await query.answer("Invalid alert", show_alert=True)
            return
        await handle_snooze_pick(update, context, ack_id=ack_id)

    elif data.startswith("snooze_set_"):
        # snooze_set_{ack_id}_{minutes}
        try:
            parts = data.replace("snooze_set_", "").rsplit("_", 1)
            ack_id = int(parts[0])
            minutes = int(parts[1])
        except (ValueError, IndexError):
            await query.answer("Invalid snooze", show_alert=True)
            return
        await handle_alert_snooze(update, context, ack_id=ack_id, minutes=minutes)

    elif data.startswith("snooze_back_"):
        # Restore original alert keyboard — re-fetch alert to rebuild
        try:
            ack_id = int(data.replace("snooze_back_", ""))
        except (ValueError, IndexError):
            await query.answer("Invalid alert", show_alert=True)
            return
        from bot.alerts import _restore_alert_keyboard
        await _restore_alert_keyboard(update, context, ack_id=ack_id)

    elif data.startswith("snooze_alert_"):
        try:
            ack_id = int(data.replace("snooze_alert_", ""))
        except (ValueError, IndexError):
            await query.answer("Invalid alert", show_alert=True)
            return
        await handle_alert_snooze(update, context, ack_id=ack_id)

    elif data == "cmd_alert_history":
        await cmd_alert_history(update, context)

    elif data == "cmd_pending_alerts":
        await cmd_pending_alerts(update, context)

    # ── User settings ───────────────────────────────────────────
    elif data == "cmd_settings":
        await query.answer()
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  {t('user_settings.header')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n{t('user_settings.configure')}"
        ], keyboard=user_settings_kb(user))

    elif data == "settings_quiet":
        await query.answer()
        await _show(update, context, [
            t('user_settings.quiet_title') + "\n\n"
            + t('user_settings.quiet_desc')
        ], keyboard=quiet_hours_kb(user))

    elif data == "settings_quiet_set":
        await query.answer()
        await _show(update, context, [
            t('user_settings.quiet_select_title') + "\n\n"
            + t('user_settings.quiet_select_prompt')
        ], keyboard=quiet_hours_picker_kb())

    elif data.startswith("quiet_set_"):
        parts = data.replace("quiet_set_", "").split("_")
        q_start, q_end = int(parts[0]), int(parts[1])
        await db.update_user(user.id, quiet_start=q_start, quiet_end=q_end)
        user = await db.get_user(user.id)
        await query.answer(t('user_settings.quiet_updated_toast'))
        await _show(update, context, [
            t('user_settings.quiet_set_confirm', start=f"{q_start:02d}", end=f"{q_end:02d}")
        ], keyboard=user_settings_kb(user))

    elif data == "settings_quiet_off":
        await db.update_user(user.id, quiet_start=None, quiet_end=None)
        user = await db.get_user(user.id)
        await query.answer(t('user_settings.quiet_disabled_toast'))
        await _show(update, context, [
            t('user_settings.quiet_disabled')
        ], keyboard=user_settings_kb(user))

    elif data == "settings_tz":
        await query.answer()
        await _show(update, context, [
            t('user_settings.tz_title') + "\n\n"
            + t('user_settings.tz_prompt')
        ], keyboard=settings_tz_kb())

    elif data.startswith("set_tz_"):
        tz = data.replace("set_tz_", "")
        await db.update_user(user.id, timezone=tz)
        user = await db.get_user(user.id)
        tz_short = tz.split("/")[-1].replace("_", " ")
        await query.answer(t('user_settings.tz_toast', tz=tz_short))
        await _show(update, context, [
            t('user_settings.tz_updated', tz=tz_short)
        ], keyboard=user_settings_kb(user))

    elif data == "settings_lang":
        await query.answer()
        lang_code = getattr(user, "language", "en") or "en"
        from bot.i18n import LANGUAGE_NAMES
        lang_name = LANGUAGE_NAMES.get(lang_code, lang_code)
        await _show(update, context, [
            t('user_settings.lang_title') + "\n\n"
            + t('language.current', lang=lang_name) + "\n\n"
            + t('language.select')
        ], keyboard=language_kb(lang_code))

    elif data.startswith("lang_region_"):
        await query.answer()
        region = data.replace("lang_region_", "")
        lang_code = getattr(user, "language", "en") or "en"
        from bot.i18n import LANGUAGE_NAMES
        lang_name = LANGUAGE_NAMES.get(lang_code, lang_code)
        await _show(update, context, [
            t('user_settings.lang_title') + "\n\n"
            + t('language.current', lang=lang_name) + "\n\n"
            + t('language.select')
        ], keyboard=language_kb(lang_code, region=region))

    elif data.startswith("set_lang_"):
        lang = data.replace("set_lang_", "")
        from bot.i18n import SUPPORTED_LANGUAGES, LANGUAGE_NAMES
        if lang not in SUPPORTED_LANGUAGES:
            await query.answer(t('common.unknown_language'), show_alert=True)
            return
        await db.update_user(user.id, language=lang)
        user = await db.get_user(user.id)
        lang_name = LANGUAGE_NAMES.get(lang, lang)
        await query.answer(f"✅ {lang_name}")
        await _show(update, context, [
            t('language.changed', lang=lang_name)
        ], keyboard=user_settings_kb(user))

    # ── Audit log ───────────────────────────────────────────────
    elif data == "cmd_audit":
        await query.answer()
        if not can(user.role, "can_manage_users"):
            await query.answer(t("access.no_access"), show_alert=True)
            return
        text = await _render_audit_log(user.account_id, db)
        await _show(update, context, [text], keyboard=back_kb())

    # ── AI usage stats ──────────────────────────────────────────
    elif data == "cmd_ai_usage" or data.startswith("ai_usage_"):
        await _handle_ai_usage(update, context, user, data)

    else:
        await query.answer(t('common.unknown_action'))


# ═══════════════════════════════════════════════════════════════════
# TEXT INPUT HANDLER — for pending prompts
# ═══════════════════════════════════════════════════════════════════

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text replies for pending interactive prompts."""
    # Silently ignore unauthorized group chats
    if not await _group_chat_guard(update):
        return

    # Handle cancel from the group picker reply keyboard
    text_raw = (update.message.text or "").strip()
    if text_raw == "❌ Cancel" and context.user_data.pop("_awaiting_chat_pick", None):
        from telegram import ReplyKeyboardRemove
        await update.message.reply_text(
            "Cancelled.", reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.pop("_pending_group", None)
        await cmd_groups(update, context)
        return

    pending = context.user_data.pop("_pending", None)

    if not pending:
        # No pending prompt — route to AI if user is registered & AI configured
        text_msg = (update.message.text or "").strip()
        if text_msg:
            import ai_client
            user = context.user_data.get("_db_user")
            if not user:
                user, _, _ = await _get_user(update)
            if user and ai_client.is_configured():
                context.user_data["_db_user"] = user
                await cmd_ai_answer(update, context, question=text_msg)
                return
        await cmd_start(update, context)
        return

    text = update.message.text.strip()
    if not text:
        await cmd_start(update, context)
        return

    # ── Register company ────────────────────────────────────────
    if pending == "register":
        context.args = text.split()
        await cmd_register(update, context)

    # ── Join with invite code ───────────────────────────────────
    elif pending == "join":
        context.args = [text.replace(" ", "")]
        await cmd_join(update, context)

    # ── Truck lookup ────────────────────────────────────────────
    elif pending == "truck":
        context.args = text.split()
        await cmd_truck(update, context)

    # ── Add Company wizard step 1: company code ─────────────────────────
    elif pending == "addcompany_code":
        code = text.strip().upper()
        if not code or len(code) > 10 or " " in code:
            await _show(update, context, [
                t('company.add_code_invalid') + "\n"
                + t('company.add_code_example')
            ], keyboard=back_kb())
            return
        context.user_data["_addcompany"] = {"code": code}
        context.user_data["_pending"] = "addcompany_key"
        await _show(update, context, [
            t('company.add_wizard_title', code=code) + "\n\n"
            + t('company.add_step2') + "\n\n"
            + t('company.add_step2_prompt') + "\n"
            + t('company.add_step2_hint')
        ], keyboard=back_kb())

    # ── Add Company wizard step 2: api key ──────────────────────────
    elif pending == "addcompany_key":
        api_key = text.strip()
        if not api_key or len(api_key) < 10:
            await _show(update, context, [
                t('company.chkey_invalid')
            ], keyboard=back_kb())
            return
        wiz = context.user_data.get("_addcompany", {})
        wiz["api_key"] = api_key
        context.user_data["_addcompany"] = wiz
        context.user_data["_pending"] = "addcompany_name"
        await _show(update, context, [
            t('company.add_wizard_title', code=wiz.get('code', '?')) + "\n\n"
            + t('company.add_step3') + "\n\n"
            + t('company.add_step3_prompt') + "\n\n"
            + t('company.add_step3_example')
        ], keyboard=skip_name_kb())

    # ── Add Company wizard step 3: display name → create ────────────
    elif pending == "addcompany_name":
        wiz = context.user_data.pop("_addcompany", {})
        code = wiz.get("code", "")
        api_key = wiz.get("api_key", "")
        display_name = code if text.lower().strip() == "skip" else text.strip()
        if not code or not api_key:
            await _show(update, context, [t('company.wizard_lost')], keyboard=back_kb())
            return
        # Delegate to cmd_addcompany with synthesized args
        context.args = [f"{code}:{api_key}"] + (display_name.split() if display_name != code else [])
        await cmd_addcompany(update, context)

    # ── Change department for a user ──────────────────────────────
    elif pending == "change_dept":
        user = context.user_data.get("_db_user")
        if not user:
            user, _, _ = await _get_user(update)
        if not user:
            await cmd_start(update, context)
            return
        target_tid = context.user_data.pop("_dept_tid", None)
        if not target_tid:
            await _show(update, context, [t('common.session_expired')], keyboard=back_kb())
            return

        new_dept = text.strip()
        if not new_dept or len(new_dept) > 50:
            await _show(update, context, [
                t('user_mgmt.dept_invalid')
            ], keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton(t('common.back'), callback_data=f"usrmenu_{target_tid}")],
            ]))
            return

        target_user = await db.get_user_by_telegram_id(target_tid)
        if not target_user or target_user.account_id != user.account_id:
            await _show(update, context, [t('user_mgmt.user_not_found')], keyboard=back_kb())
            return

        await db.update_user(target_user.id, department=new_dept)
        await _show(update, context, [
            t('user_mgmt.dept_updated', user=target_user.label, dept=new_dept)
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('common.back'), callback_data=f"usrmenu_{target_tid}")],
        ]))

    # ── Confirm remove company (type code to confirm) ───────────
    elif pending == "confirm_remove_company":
        user = context.user_data.get("_db_user")
        if not user:
            user, _, _ = await _get_user(update)
        if not user:
            await cmd_start(update, context)
            return
        code = context.user_data.pop("_rmco_code", None)
        if not code:
            await _show(update, context, [t('common.session_expired')], keyboard=back_kb())
            return

        typed = text.strip().upper()
        if typed != code:
            await _show(update, context, [
                t('company.remove_mismatch', typed=typed, expected=code)
            ], keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton(t('common.back'), callback_data=f"comenu_{code}")],
            ]))
            return

        company = await db.get_company_by_code(user.account_id, code)
        if company:
            await db.remove_company(company.id)
            await invalidate_client(user.account_id)
            companies = await db.get_account_companies(user.account_id)
            populate_company_display(companies)
            # Audit log
            await db.add_audit_log(
                account_id=user.account_id,
                user_id=user.id,
                action="company_removed",
                target_type="company",
                target_id=str(company.id),
                details=f"{user.label} removed company {code} ({company.display_name})",
            )
            await _show(update, context, [
                t('company.remove_confirmed', code=code)
            ], keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton(t('company.back_companies'), callback_data="cmd_account")],
            ]))
        else:
            await _show(update, context, [
                t('company.not_found_code', code=code)
            ], keyboard=back_kb())

    # ── Change API key for a company ──────────────────────────────
    elif pending == "change_api_key":
        user = context.user_data.get("_db_user")
        if not user:
            user, _, _ = await _get_user(update)
        if not user:
            await cmd_start(update, context)
            return
        code = context.user_data.pop("_chkey_code", None)
        if not code:
            await _show(update, context, [t('common.session_expired')], keyboard=back_kb())
            return

        api_key = text.strip()
        if not api_key or len(api_key) < 10:
            await _show(update, context, [
                t('company.chkey_invalid')
            ], keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton(t('common.back'), callback_data=f"comenu_{code}")],
            ]))
            return

        company = await db.get_company_by_code(user.account_id, code)
        if not company:
            await _show(update, context, [t('company.not_found')], keyboard=back_kb())
            return

        await db.update_company(company.id, samsara_api_key=api_key)
        await invalidate_client(user.account_id)
        await db.add_audit_log(
            account_id=user.account_id, user_id=user.id,
            action="api_key_changed", target_type="company",
            target_id=str(company.id),
            details=f"{user.label} changed API key for {code}",
        )
        await _show(update, context, [
            t('company.chkey_updated', code=code)
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('common.back'), callback_data=f"comenu_{code}")],
        ]))

    # ── Rename company display name ─────────────────────────────
    elif pending == "rename_company":
        user = context.user_data.get("_db_user")
        if not user:
            user, _, _ = await _get_user(update)
        if not user:
            await cmd_start(update, context)
            return
        code = context.user_data.pop("_rename_code", None)
        if not code:
            await _show(update, context, [t('common.session_expired')], keyboard=back_kb())
            return

        new_name = text.strip()
        if not new_name or len(new_name) > 100:
            await _show(update, context, [
                t('company.rename_invalid')
            ], keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton(t('common.back'), callback_data=f"comenu_{code}")],
            ]))
            return

        company = await db.get_company_by_code(user.account_id, code)
        if not company:
            await _show(update, context, [t('company.not_found')], keyboard=back_kb())
            return

        old_name = company.display_name
        await db.update_company(company.id, display_name=new_name)
        # Refresh display cache
        companies = await db.get_account_companies(user.account_id)
        populate_company_display(companies)
        await db.add_audit_log(
            account_id=user.account_id, user_id=user.id,
            action="company_renamed", target_type="company",
            target_id=str(company.id),
            details=f"{user.label} renamed {code}: {old_name} → {new_name}",
        )
        await _show(update, context, [
            t('company.renamed', code=code, name=new_name)
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('common.back'), callback_data=f"comenu_{code}")],
        ]))

    # ── Invite driver (truck number) ────────────────────────────
    elif pending == "invite_driver":
        user = context.user_data.get("_db_user")
        if not user:
            user, _, _ = await _get_user(update)
        if not user:
            await cmd_start(update, context)
            return
        context.user_data["_db_user"] = user

        truck_num = None if text.lower() == "skip" else text
        try:
            invite = await db.create_invite(
                account_id=user.account_id,
                created_by=user.id,
                role=Role.DRIVER,
                department="operations",
                truck_num=truck_num,
            )
            truck_label = truck_num or t('invite.truck_not_assigned')
            link = f"https://t.me/{_cfg.bot_username}?start=join_{invite.code}" if _cfg.bot_username else None
            invite_text = format_invite_created(
                invite.code, role_display(Role.DRIVER),
                "operations",
                invite_link=link,
            )
            if truck_num:
                invite_text += "\n  " + t('invite.truck_assigned', truck=truck_num)
            kb = invite_kb(link)
            await _show(update, context, [invite_text], keyboard=kb)
        except Exception as e:
            await _show(update, context, [_safe_error(e)], keyboard=back_kb())

    # ── Fuel cost wizard ────────────────────────────────────────
    elif pending.startswith("fuelcost_"):
        context.user_data["_pending"] = pending  # restore for handler
        await handle_fuelcost_text(update, context)

    # ── Maintenance wizard ──────────────────────────────────────
    elif pending.startswith("maint_"):
        context.user_data["_pending"] = pending  # restore for handler
        await handle_maintenance_text(update, context)

    # ── Route replay truck input ────────────────────────────────
    elif pending == "route_truck":
        context.user_data["_pending"] = pending  # restore for handler
        await handle_route_text(update, context)

    # ── AI question input ───────────────────────────────────────
    elif pending == "ai_question":
        await cmd_ai_answer(update, context, question=text)

    else:
        await cmd_start(update, context)
