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
)
from bot.helpers import _show, _show_loading, _user_menu_kb
from bot.auth import _get_user, _group_chat_guard
from bot.registration import cmd_start, cmd_register, cmd_join
from bot.fleet import (
    cmd_faults, cmd_faults_pdf, cmd_faults_csv,
    cmd_truck, cmd_critical,
    cmd_fuel, cmd_fuel_pdf, cmd_fuel_csv,
    cmd_alerts, cmd_alert_toggle, cmd_alert_disable_all,
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
from bot.digest import cmd_digest, cmd_digest_subscribe, cmd_digest_unsubscribe, cmd_digest_set_hour, cmd_digest_set_tz
from bot.maintenance import (
    cmd_maintenance, cmd_maint_add, cmd_maint_type,
    cmd_maint_view, cmd_maint_done, handle_maintenance_text,
)
from bot.maps import cmd_livemap
from bot.routes import cmd_route, cmd_route_go, handle_route_text
from bot.geofences import cmd_geofences
from bot.ai import (
    cmd_ai, cmd_ai_ask_prompt, cmd_ai_answer, cmd_ai_summary,
    cmd_ai_diagnose, cmd_ai_suggest, cmd_ai_newchat,
    cmd_ai_models, cmd_ai_set_model, cmd_ai_regions, cmd_ai_set_location,
)
from bot.alerts import handle_alert_ack


async def _show_truck_list(update, context, user, company_filter, page=0):
    """Fetch all trucks and show a paginated button list."""
    orgs_db = await db.get_account_companies(user.account_id)
    populate_company_display(orgs_db)
    company_codes = [o.code for o in orgs_db]
    show_org = len(company_codes) > 1

    await _show_loading(update, context, "⏳ Loading truck list…")
    try:
        samsara = await get_client(user.account_id)
        vehicles = await samsara.get_fleet_overview(company=company_filter)
        if not vehicles:
            label = company_filter or "all companies"
            await _show(update, context,
                        [f"ℹ️ No active trucks found for <b>{label}</b>."],
                        keyboard=back_kb())
            return

        total = len(vehicles)
        header = (
            f"🚛 <b>Trucks</b>  —  {total} active\n"
        )
        if company_filter:
            from samsara_client import COMPANY_DISPLAY
            header += f"  📡 {COMPANY_DISPLAY.get(company_filter, company_filter)}\n"

        kb = vehicle_list_kb(vehicles, page=page, company_filter=company_filter)
        await _show(update, context, [header + "\nTap a truck for details:"],
                    keyboard=kb)
    except Exception as e:
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


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
            "📝 <b>Register Your Company</b>\n\n"
            "Type your company name below:"
        ])
        return
    if data == "cmd_join_help":
        await query.answer()
        context.user_data["_pending"] = "join"
        await _show(update, context, [
            "🔑 <b>Join a Team</b>\n\n"
            "Type your invite code below:\n"
            "<i>(format: XXXX-XXXX)</i>"
        ])
        return

    # ── System owner panel buttons ──────────────────────────────
    if data.startswith("sys_"):
        _, tid, sys_owner = await _get_user(update)
        if not sys_owner:
            await query.answer("⛔ System admin only", show_alert=True)
            return

        if data == "sys_dashboard":
            await query.answer()
            await cmd_admin(update, context)
            return
        elif data == "sys_accounts":
            await query.answer()
            await cmd_accounts(update, context)
            return

        await query.answer("Unknown admin action")
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
            "⛔ Your account has been disabled.\n"
            f"Contact support: {SUPPORT_CONTACT}" if SUPPORT_CONTACT else
            "⛔ Your account has been disabled."
        ])
        return

    context.user_data["_db_user"] = user

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
            "  📊  <b>FLEET REPORTS</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n  Select a report type:"
        ], keyboard=submenu_reports_kb(user.role, company_codes))

    elif data == "submenu_tools":
        await query.answer()
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            "  🛠  <b>TOOLS</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n  Select a tool:"
        ], keyboard=submenu_tools_kb(user.role))

    elif data == "submenu_costs":
        await query.answer()
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            "  💰  <b>COST & MAINTENANCE</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n  Track costs and schedule maintenance:"
        ], keyboard=submenu_costs_kb(user.role))

    elif data == "submenu_mgmt":
        await query.answer()
        has_api = bool(companies)
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            "  👥  <b>TEAM & SETTINGS</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n  Manage your account and team:"
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
    elif data == "alert_disable_all":
        await cmd_alert_disable_all(update, context)

    # ── AI ────────────────────────────────────────────────────
    elif data == "cmd_ai":
        await cmd_ai(update, context)
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
        # ai_diag_{company}_{truck_name}
        parts = data.replace("ai_diag_", "").split("_", 1)
        if len(parts) == 2:
            await cmd_ai_diagnose(update, context, truck_name=parts[1], company=parts[0])
    elif data == "ai_models":
        await cmd_ai_models(update, context)
    elif data.startswith("ai_setmodel_"):
        model = data.replace("ai_setmodel_", "")
        await cmd_ai_set_model(update, context, model_name=model)
    elif data == "ai_regions":
        await cmd_ai_regions(update, context)
    elif data.startswith("ai_setloc_"):
        loc = data.replace("ai_setloc_", "")
        await cmd_ai_set_location(update, context, location=loc)

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
        guide_text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📡  <b>CONNECT SAMSARA API</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "  To unlock fleet monitoring you\n"
            "  need to connect your Samsara\n"
            "  account. Follow these steps:\n"
            "\n"
            "  <b>Step 1 — Get your API token</b>\n"
            "  ▸ Log in to <b>cloud.samsara.com</b>\n"
            "  ▸ Go to <b>Settings → API Tokens</b>\n"
            "  ▸ Click <b>Create API Token</b>\n"
            "  ▸ Name: <i>Semi Telematics Bot</i>\n"
            "  ▸ Copy the token (starts with\n"
            "     <code>samsara_api_...</code>)\n"
            "\n"
            "  <b>Step 2 — Type a company code</b>\n"
            "  A short code for your company,\n"
            "  e.g. <b>PTG</b>, <b>CFT</b>, <b>ACME</b>\n"
            "\n"
            "  👇 <b>Type your company code below:</b>"
        )
        await _show(update, context, [guide_text], keyboard=back_kb())

    elif data == "cmd_no_api_info":
        await query.answer()
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ⏳  <b>API NOT YET CONNECTED</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "  Your company owner hasn't\n"
            "  connected a Samsara API key yet.\n"
            "\n"
            "  Once they integrate the API,\n"
            "  fleet features will appear here\n"
            "  automatically:\n"
            "\n"
            "  • 🔧 Fault codes\n"
            "  • 🚨 Critical alerts\n"
            "  • ⛽ Fuel levels\n"
            "  • 🚛 Truck search\n"
            "\n"
            "  Ask your company owner or admin\n"
            "  to connect Samsara."
        ], keyboard=back_kb())

    # ── Account / Users ─────────────────────────────────────────
    elif data == "cmd_account":
        await cmd_account(update, context)
    elif data == "cmd_users":
        await cmd_users(update, context)

    # ── Group / Channel management ──────────────────────────────
    elif data == "cmd_groups":
        await cmd_groups(update, context)

    elif data.startswith("rmgroup_"):
        chat_id_str = data[8:]
        await query.answer()
        if not user.is_admin_or_above:
            await query.answer("⛔ No access", show_alert=True)
            return
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🗑 Yes, remove", callback_data=f"rmgroupconfirm_{chat_id_str}"),
                InlineKeyboardButton("◀️ Cancel", callback_data="cmd_groups"),
            ],
        ])
        await _show(update, context, [
            f"⚠️ <b>Remove group <code>{chat_id_str}</code>?</b>\n\n"
            "The bot will stop responding in this group."
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
            await query.answer("⛔ No access", show_alert=True)
            return
        await query.answer()
        context.user_data["_awaiting_chat_pick"] = True
        await update.effective_chat.send_message(
            "👇 <b>Tap a button below</b> to pick a\n"
            "group or channel from Telegram.\n\n"
            "Tap ❌ Cancel to go back.",
            parse_mode="HTML",
            reply_markup=group_picker_kb(),
        )

    elif data == "addgroup_confirm":
        await query.answer()
        pending = context.user_data.pop("_pending_group", None)
        if not pending:
            await _show(update, context,
                        ["⚠️ Nothing to confirm. Try again."],
                        keyboard=back_kb())
            return
        await db.add_authorized_chat(
            account_id=user.account_id,
            chat_id=pending["chat_id"],
            chat_title=pending["title"],
            added_by=user.id,
        )
        await _show(update, context, [
            f"✅ Authorized!\n\n"
            f"  💬 <b>{pending['title']}</b>\n"
            f"  🆔 <code>{pending['chat_id']}</code>\n\n"
            f"  The bot will now respond in this chat."
        ], keyboard=back_kb())

    elif data == "addgroup_cancel":
        await query.answer()
        context.user_data.pop("_pending_group", None)
        await cmd_groups(update, context)

    # ── Invite — role picker ────────────────────────────────────
    elif data == "cmd_invite_pick":
        await query.answer()
        if not can(user.role, "can_invite"):
            await query.answer("⛔ No access", show_alert=True)
            return
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔑 Admin", callback_data="inv_admin"),
                InlineKeyboardButton("🔧 Fleet", callback_data="inv_fleet_manager"),
            ],
            [
                InlineKeyboardButton("📡 Dispatcher", callback_data="inv_dispatcher"),
                InlineKeyboardButton("🚛 Driver", callback_data="inv_driver"),
            ],
            [InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")],
        ])
        await _show(update, context, [
            "✉️ <b>Invite Team Member</b>\n\n"
            "  <b>Step 1/2</b> — Select role\n\n"
            "Select the role for the new member:"
        ], keyboard=kb)

    elif data.startswith("inv_"):
        await query.answer()
        role_str = data[4:]  # admin, fleet_manager, dispatcher, driver
        try:
            invite_role = Role.from_str(role_str)
        except ValueError:
            await _show(update, context, ["❌ Invalid role."], keyboard=back_kb())
            return

        if invite_role == Role.DRIVER:
            # Ask for truck number
            context.user_data["_pending"] = "invite_driver"
            await _show(update, context, [
                f"🚛 <b>Invite Driver</b>\n\n"
                "  <b>Step 2/2</b> — Truck number\n\n"
                "Type the truck number for this driver\n"
                "(or type <b>skip</b> to leave blank):"
            ], keyboard=back_kb())
            return

        # Create invite immediately for non-driver roles
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
            await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())

    # ── Add Company wizard (step 1: code) ───────────────────────────
    elif data == "cmd_addcompany_prompt":
        await query.answer()
        if not can(user.role, "can_manage_companies"):
            await query.answer("⛔ No access", show_alert=True)
            return
        context.user_data["_pending"] = "addcompany_code"
        context.user_data.pop("_addcompany", None)
        await _show(update, context, [
            "📡 <b>Add Company</b>\n\n"
            "<b>Step 1/3</b> — Company code\n\n"
            "Type a short code (e.g. PTG, CFT):\n"
            "<i>This is your internal label for the company.</i>"
        ], keyboard=back_kb())

    # ── Remove Company (from account view) ──────────────────────────
    elif data.startswith("rmco_"):
        code = data[5:]
        if not can(user.role, "can_manage_companies"):
            await query.answer("⛔ No access", show_alert=True)
            return
        await query.answer()
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"🗑 Yes, remove {code}", callback_data=f"rmcoconfirm_{code}"),
                InlineKeyboardButton("◀️ Cancel", callback_data="cmd_account"),
            ],
        ])
        await _show(update, context, [
            f"⚠️ <b>Remove Company {code}?</b>\n\n"
            "This will disconnect this company from your account.\n"
            "All data stays in Samsara — only the bot link is removed."
        ], keyboard=kb)

    elif data.startswith("rmcoconfirm_"):
        code = data[12:]
        await query.answer()
        company = await db.get_company_by_code(user.account_id, code)
        if company:
            await db.remove_company(company.id)
            await invalidate_client(user.account_id)
            companies = await db.get_account_companies(user.account_id)
            populate_company_display(companies)
        kb = await _user_menu_kb(user)
        await _show(update, context, [f"✅ Company <b>{code}</b> removed."], keyboard=kb)

    # ── Add Company wizard: Skip display name ───────────────────────
    elif data == "addcompany_skip_name":
        await query.answer()
        wiz = context.user_data.pop("_addcompany", {})
        code = wiz.get("code", "")
        api_key = wiz.get("api_key", "")
        context.user_data.pop("_pending", None)
        if not code or not api_key:
            await _show(update, context, ["❌ Wizard state lost. Please start over."], keyboard=back_kb())
            return
        context.args = [f"{code}:{api_key}"]
        await cmd_addcompany(update, context)

    # ── Truck browser: company picker ───────────────────────────
    elif data == "cmd_truck_prompt":
        await query.answer()
        if not can(user.role, "can_truck_all"):
            await query.answer("⛔ No access", show_alert=True)
            return
        company_codes = [o.code for o in companies]
        if len(company_codes) == 1:
            # Skip picker, go straight to list
            await _show_truck_list(update, context, user, company_codes[0])
        else:
            await _show(update, context, [
                "🚛 <b>Search Truck</b>\n\n"
                "Select a company to browse trucks,\n"
                "or choose <b>All Companies</b>:"
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
        # Show actions for a specific user
        target_tid = int(data[8:])
        await query.answer()
        target_user = await db.get_user_by_telegram_id(target_tid)
        if not target_user or target_user.account_id != user.account_id:
            await _show(update, context, ["❌ User not found."], keyboard=back_kb())
            return

        rows = [
            [InlineKeyboardButton(
                f"🔄 Change Role ({role_display(target_user.role)})",
                callback_data=f"usrrole_{target_tid}",
            )],
        ]
        if target_tid != user.telegram_id:
            rows.append([InlineKeyboardButton(
                "🗑 Remove User",
                callback_data=f"usrremove_{target_tid}",
            )])
        rows.append([InlineKeyboardButton("◀️ Back to Team", callback_data="cmd_users")])

        await _show(update, context, [
            f"👤 <b>User <a href='tg://user?id={target_tid}'>{target_user.label}</a></b>\n"
            f"Role: {role_display(target_user.role)}\n"
            f"Dept: {target_user.department or '—'}\n"
            f"Truck: {target_user.truck_num or '—'}"
        ], keyboard=InlineKeyboardMarkup(rows))

    elif data.startswith("usrrole_"):
        # Show role selection for a user
        target_tid = int(data[8:])
        await query.answer()
        roles_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("👑 Owner", callback_data=f"setrole_{target_tid}_owner"),
                InlineKeyboardButton("🔑 Admin", callback_data=f"setrole_{target_tid}_admin"),
            ],
            [
                InlineKeyboardButton("🔧 Fleet", callback_data=f"setrole_{target_tid}_fleet_manager"),
                InlineKeyboardButton("📡 Dispatcher", callback_data=f"setrole_{target_tid}_dispatcher"),
            ],
            [
                InlineKeyboardButton("🚛 Driver", callback_data=f"setrole_{target_tid}_driver"),
            ],
            [InlineKeyboardButton("◀️ Cancel", callback_data=f"usrmenu_{target_tid}")],
        ])
        target_user = await db.get_user_by_telegram_id(target_tid)
        target_label = target_user.label if target_user else str(target_tid)
        await _show(update, context, [
            f"🔄 <b>Change Role for {target_label}</b>\n\n"
            "Select the new role:"
        ], keyboard=roles_kb)

    elif data.startswith("setrole_"):
        parts = data.split("_")  # setrole_{tid}_{role}
        target_tid = int(parts[1])
        new_role_str = "_".join(parts[2:])  # handles fleet_manager
        await query.answer()

        try:
            new_role = Role.from_str(new_role_str)
        except ValueError:
            await _show(update, context, ["❌ Invalid role."], keyboard=back_kb())
            return

        target_user = await db.get_user_by_telegram_id(target_tid)
        if not target_user or target_user.account_id != user.account_id:
            await _show(update, context, ["❌ User not found."], keyboard=back_kb())
            return

        if new_role == Role.OWNER and user.role != Role.OWNER:
            await _show(update, context, ["⛔ Only owners can promote to owner."], keyboard=back_kb())
            return

        await db.update_user(target_user.id, role=new_role)
        await _show(update, context, [
            f"✅ Updated {target_user.label} → {role_display(new_role)}"
        ], keyboard=back_kb())

    elif data.startswith("usrremove_"):
        target_tid = int(data[10:])
        await query.answer()
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🗑 Yes, remove", callback_data=f"usrremoveconfirm_{target_tid}"),
                InlineKeyboardButton("◀️ Cancel", callback_data="cmd_users"),
            ],
        ])
        target_user = await db.get_user_by_telegram_id(target_tid)
        target_label = target_user.label if target_user else str(target_tid)
        await _show(update, context, [
            f"⚠️ <b>Remove {target_label}?</b>\n\n"
            "They will lose access to this account."
        ], keyboard=kb)

    elif data.startswith("usrremoveconfirm_"):
        target_tid = int(data[17:])
        await query.answer()
        if target_tid == user.telegram_id:
            await _show(update, context, ["⚠️ You can't remove yourself."], keyboard=back_kb())
            return
        target_user = await db.get_user_by_telegram_id(target_tid)
        target_label = target_user.label if target_user else str(target_tid)
        if target_user and target_user.account_id == user.account_id:
            await db.remove_user(target_user.id)
        await _show(update, context, [f"✅ Removed {target_label}."], keyboard=back_kb())

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

    # ── Company sub-menu ────────────────────────────────────────────
    elif data.startswith("co_") and not data.startswith("cofaults_") \
            and not data.startswith("cofuel_") \
            and not data.startswith("cotruck_") \
            and not data.startswith("cohealth_") \
            and not data.startswith("coeff_") \
            and not data.startswith("coeff_pdf_") \
            and not data.startswith("coeff_csv_") \
            and not data.startswith("coweather_"):
        co = data.replace("co_", "")
        await query.answer()
        name = COMPANY_DISPLAY.get(co, co)
        text = (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"  🏢  <b>{name}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"\n"
            f"  Select a report for {co}:"
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
        await query.answer("Geofence details coming soon", show_alert=False)

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

    # ── Digest ──────────────────────────────────────────────────
    elif data == "cmd_digest":
        await cmd_digest(update, context)
    elif data == "digest_daily":
        await cmd_digest_subscribe(update, context, frequency="daily")
    elif data == "digest_weekly":
        await cmd_digest_subscribe(update, context, frequency="weekly")
    elif data == "digest_unsub":
        await cmd_digest_unsubscribe(update, context)
    elif data.startswith("digest_hour_"):
        hour = int(data.replace("digest_hour_", ""))
        await cmd_digest_set_hour(update, context, hour=hour)
    elif data.startswith("digest_tz_"):
        tz = data.replace("digest_tz_", "")
        await cmd_digest_set_tz(update, context, tz=tz)

    # ── Alert acknowledgment ────────────────────────────────────
    elif data.startswith("ack_alert_"):
        ack_id = int(data.replace("ack_alert_", ""))
        await handle_alert_ack(update, context, ack_id=ack_id)

    # ── User settings ───────────────────────────────────────────
    elif data == "cmd_settings":
        await query.answer()
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ⚙️  <b>SETTINGS</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\nConfigure your notification preferences:"
        ], keyboard=user_settings_kb(user))

    elif data == "settings_quiet":
        await query.answer()
        await _show(update, context, [
            "🌙 <b>Quiet Hours (DND)</b>\n\n"
            "During quiet hours, non-critical\nalerts are held until morning."
        ], keyboard=quiet_hours_kb(user))

    elif data == "settings_quiet_set":
        await query.answer()
        await _show(update, context, [
            "🌙 <b>Select Quiet Hours</b>\n\n"
            "Choose a preset, or alerts will be\nsilenced during these hours:"
        ], keyboard=quiet_hours_picker_kb())

    elif data.startswith("quiet_set_"):
        parts = data.replace("quiet_set_", "").split("_")
        q_start, q_end = int(parts[0]), int(parts[1])
        await db.update_user(user.id, quiet_start=q_start, quiet_end=q_end)
        user = await db.get_user(user.id)
        await query.answer("✅ Quiet hours updated")
        await _show(update, context, [
            f"✅ Quiet hours set: {q_start:02d}:00 — {q_end:02d}:00\n\n"
            "Non-critical alerts will be held\nduring these hours."
        ], keyboard=user_settings_kb(user))

    elif data == "settings_quiet_off":
        await db.update_user(user.id, quiet_start=None, quiet_end=None)
        user = await db.get_user(user.id)
        await query.answer("✅ Quiet hours disabled")
        await _show(update, context, [
            "✅ Quiet hours disabled.\n\n"
            "You'll receive all alerts at any time."
        ], keyboard=user_settings_kb(user))

    elif data == "settings_tz":
        await query.answer()
        await _show(update, context, [
            "🕐 <b>Select Timezone</b>\n\n"
            "This affects quiet hours and digest delivery:"
        ], keyboard=settings_tz_kb())

    elif data.startswith("set_tz_"):
        tz = data.replace("set_tz_", "")
        await db.update_user(user.id, timezone=tz)
        user = await db.get_user(user.id)
        tz_short = tz.split("/")[-1].replace("_", " ")
        await query.answer(f"✅ Timezone: {tz_short}")
        await _show(update, context, [
            f"✅ Timezone updated to <b>{tz_short}</b>"
        ], keyboard=user_settings_kb(user))

    # ── Audit log ───────────────────────────────────────────────
    elif data == "cmd_audit":
        await query.answer()
        if not can(user.role, "can_manage_users"):
            await query.answer("⛔ No access", show_alert=True)
            return
        entries = await db.get_audit_log(user.account_id, limit=15)
        if not entries:
            text = "📋 <b>Audit Log</b>\n\nNo recent activity."
        else:
            lines = ["📋 <b>Audit Log</b> (last 15)\n"]
            for e in entries:
                ts = e["created_at"][:16].replace("T", " ")
                lines.append(f"  • <code>{ts}</code> — {e['action']}")
                if e.get("details"):
                    lines.append(f"    {e['details'][:60]}")
            text = "\n".join(lines)
        await _show(update, context, [text], keyboard=back_kb())

    # ── AI usage stats ──────────────────────────────────────────
    elif data == "cmd_ai_usage" or data.startswith("ai_usage_"):
        if not can(user.role, "can_manage_account"):
            await query.answer("⛔ No access", show_alert=True)
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

        lines = [
            "━━━━━━━━━━━━━━━━━━━\n"
            "  🤖  <b>AI USAGE</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  📅 Last {days} days\n"
            f"\n  📊 Total requests: <b>{stats['total_requests']}</b>"
            f"\n  🔤 Total tokens: <b>{stats['total_tokens']:,}</b>\n"
        ]
        if stats["by_type"]:
            lines.append("\n  <b>By Type:</b>")
            for rt, info in stats["by_type"].items():
                lines.append(f"  • {rt}: {info['requests']} req ({info['tokens']:,} tok)")
        if stats["by_model"]:
            lines.append("\n  <b>By Model:</b>")
            for m, info in stats["by_model"].items():
                lines.append(f"  • {m}: {info['requests']} req ({info['tokens']:,} tok)")
        if daily:
            lines.append("\n  <b>Daily (last 7d):</b>")
            for d in daily:
                lines.append(f"  • {d['day']}: {d['requests']} req ({d['tokens'] or 0:,} tok)")

        text = "\n".join(lines)
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📅 7 days", callback_data="ai_usage_7"),
                InlineKeyboardButton("📅 30 days", callback_data="ai_usage_30"),
                InlineKeyboardButton("📅 90 days", callback_data="ai_usage_90"),
            ],
            [InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")],
        ])
        await _show(update, context, [text], keyboard=kb)

    else:
        await query.answer("Unknown action")


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
                "❌ Code must be 1–10 characters, no spaces.\n"
                "Example: <b>PTG</b>, <b>CFT</b>"
            ], keyboard=back_kb())
            return
        context.user_data["_addcompany"] = {"code": code}
        context.user_data["_pending"] = "addcompany_key"
        await _show(update, context, [
            f"📡 <b>Add Company — {code}</b>\n\n"
            "<b>Step 2/3</b> — Samsara API key\n\n"
            "Paste your Samsara API token below:\n"
            "<i>(starts with samsara_api_...)</i>"
        ], keyboard=back_kb())

    # ── Add Company wizard step 2: api key ──────────────────────────
    elif pending == "addcompany_key":
        api_key = text.strip()
        if not api_key or len(api_key) < 10:
            await _show(update, context, [
                "❌ That doesn't look like a valid API key.\n"
                "It should start with <code>samsara_api_</code>"
            ], keyboard=back_kb())
            return
        wiz = context.user_data.get("_addcompany", {})
        wiz["api_key"] = api_key
        context.user_data["_addcompany"] = wiz
        context.user_data["_pending"] = "addcompany_name"
        await _show(update, context, [
            f"📡 <b>Add Company — {wiz.get('code', '?')}</b>\n\n"
            "<b>Step 3/3</b> — Display name (optional)\n\n"
            "Type a friendly name for this company\n"
            "or tap <b>⏭ Skip</b> to use the code.\n\n"
            "Example: <i>Premier Trucking Group</i>"
        ], keyboard=skip_name_kb())

    # ── Add Company wizard step 3: display name → create ────────────
    elif pending == "addcompany_name":
        wiz = context.user_data.pop("_addcompany", {})
        code = wiz.get("code", "")
        api_key = wiz.get("api_key", "")
        display_name = code if text.lower().strip() == "skip" else text.strip()
        if not code or not api_key:
            await _show(update, context, ["❌ Wizard state lost. Please start over."], keyboard=back_kb())
            return
        # Delegate to cmd_addcompany with synthesized args
        context.args = [f"{code}:{api_key}"] + (display_name.split() if display_name != code else [])
        await cmd_addcompany(update, context)

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
            truck_label = truck_num or "not assigned"
            link = f"https://t.me/{_cfg.bot_username}?start=join_{invite.code}" if _cfg.bot_username else None
            invite_text = format_invite_created(
                invite.code, role_display(Role.DRIVER),
                "operations",
                invite_link=link,
            )
            if truck_num:
                invite_text += f"\n  🚛 Truck: #{truck_num}"
            kb = invite_kb(link)
            await _show(update, context, [invite_text], keyboard=kb)
        except Exception as e:
            await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())

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
