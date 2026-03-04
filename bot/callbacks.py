"""Callback query router and text input handler."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import Role
from permissions import can, role_display, is_system_owner
from samsara_client import ORG_DISPLAY, populate_org_display
from formatters import (
    format_help,
    format_welcome_unregistered,
    format_system_owner_welcome,
    format_invite_created,
)

import bot.config as _cfg
from bot.config import db, SUPPORT_CONTACT, get_user_org_codes, invalidate_client, get_client
from bot.keyboards import (
    main_menu_kb, org_menu_kb, back_kb, system_owner_kb, unregistered_kb,
    invite_kb, skip_name_kb, truck_company_picker_kb, vehicle_list_kb,
)
from bot.helpers import _show, _show_loading, _user_menu_kb
from bot.auth import _get_user
from bot.registration import cmd_start, cmd_register, cmd_join
from bot.fleet import cmd_faults, cmd_truck, cmd_critical, cmd_fuel, cmd_alerts, cmd_truck_report, cmd_engine_hours
from bot.management import cmd_account, cmd_users, cmd_addorg
from bot.admin import cmd_admin, cmd_accounts


async def _show_truck_list(update, context, user, org_filter, page=0):
    """Fetch all trucks and show a paginated button list."""
    orgs_db = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs_db)
    org_codes = [o.code for o in orgs_db]
    show_org = len(org_codes) > 1

    await _show_loading(update, context, "⏳ Loading truck list…")
    try:
        samsara = await get_client(user.account_id)
        vehicles = await samsara.get_fleet_overview(org=org_filter)
        if not vehicles:
            label = org_filter or "all companies"
            await _show(update, context,
                        [f"ℹ️ No active trucks found for <b>{label}</b>."],
                        keyboard=back_kb())
            return

        total = len(vehicles)
        header = (
            f"🚛 <b>Trucks</b>  —  {total} active\n"
        )
        if org_filter:
            from samsara_client import ORG_DISPLAY
            header += f"  📡 {ORG_DISPLAY.get(org_filter, org_filter)}\n"

        kb = vehicle_list_kb(vehicles, page=page, org_filter=org_filter)
        await _show(update, context, [header + "\nTap a truck for details:"],
                    keyboard=kb)
    except Exception as e:
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

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

    # Populate ORG_DISPLAY for this user's account
    orgs = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs)

    # ── Main menu ───────────────────────────────────────────────
    if data == "cmd_menu":
        await query.answer()
        account = await db.get_account(user.account_id)
        org_codes = [o.code for o in orgs]
        text = format_help(org_codes, user=user, account=account)
        if sys_owner:
            text += "\n\n  ⚙️ <i>System admin: /admin</i>"
        kb = main_menu_kb(user.role, org_codes)
        await _show(update, context, [text], keyboard=kb)

    # ── Fleet commands ──────────────────────────────────────────
    elif data == "cmd_faults":
        await cmd_faults(update, context)
    elif data == "cmd_critical":
        await cmd_critical(update, context)
    elif data == "cmd_fuel":
        await cmd_fuel(update, context)
    elif data == "cmd_alerts":
        await cmd_alerts(update, context)
    elif data == "cmd_mytruck":
        await cmd_truck(update, context)
    elif data == "cmd_engine_hours":
        await cmd_engine_hours(update, context)

    # ── Samsara API integration guide (no orgs yet) ─────────────
    elif data == "cmd_integrate_guide":
        await query.answer()
        context.user_data["_pending"] = "addorg_code"
        context.user_data.pop("_addorg", None)
        guide_text = (
            "┌─────────────────────────┐\n"
            "  📡  <b>CONNECT SAMSARA API</b>\n"
            "└─────────────────────────┘\n"
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
            "┌─────────────────────────┐\n"
            "  ⏳  <b>API NOT YET CONNECTED</b>\n"
            "└─────────────────────────┘\n"
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

    # ── Add Org wizard (step 1: code) ───────────────────────────
    elif data == "cmd_addorg_prompt":
        await query.answer()
        if not can(user.role, "can_manage_orgs"):
            await query.answer("⛔ No access", show_alert=True)
            return
        context.user_data["_pending"] = "addorg_code"
        context.user_data.pop("_addorg", None)
        await _show(update, context, [
            "📡 <b>Add Company</b>\n\n"
            "<b>Step 1/3</b> — Company code\n\n"
            "Type a short code (e.g. PTG, CFT):\n"
            "<i>This is your internal label for the company.</i>"
        ], keyboard=back_kb())

    # ── Remove Org (from account view) ──────────────────────────
    elif data.startswith("rmorg_"):
        code = data[6:]
        await query.answer()
        if not can(user.role, "can_manage_orgs"):
            await query.answer("⛔ No access", show_alert=True)
            return
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"🗑 Yes, remove {code}", callback_data=f"rmorgconfirm_{code}"),
                InlineKeyboardButton("◀️ Cancel", callback_data="cmd_account"),
            ],
        ])
        await _show(update, context, [
            f"⚠️ <b>Remove Company {code}?</b>\n\n"
            "This will disconnect this company from your account.\n"
            "All data stays in Samsara — only the bot link is removed."
        ], keyboard=kb)

    elif data.startswith("rmorgconfirm_"):
        code = data[13:]
        await query.answer()
        org = await db.get_org_by_code(user.account_id, code)
        if org:
            await db.remove_organization(org.id)
            await invalidate_client(user.account_id)
            orgs = await db.get_account_orgs(user.account_id)
            populate_org_display(orgs)
        kb = await _user_menu_kb(user)
        await _show(update, context, [f"✅ Company <b>{code}</b> removed."], keyboard=kb)

    # ── Add Org wizard: Skip display name ───────────────────────
    elif data == "addorg_skip_name":
        await query.answer()
        wiz = context.user_data.pop("_addorg", {})
        code = wiz.get("code", "")
        api_key = wiz.get("api_key", "")
        context.user_data.pop("_pending", None)
        if not code or not api_key:
            await _show(update, context, ["❌ Wizard state lost. Please start over."], keyboard=back_kb())
            return
        context.args = [f"{code}:{api_key}"]
        await cmd_addorg(update, context)

    # ── Truck browser: company picker ───────────────────────────
    elif data == "cmd_truck_prompt":
        await query.answer()
        if not can(user.role, "can_truck_all"):
            await query.answer("⛔ No access", show_alert=True)
            return
        org_codes = [o.code for o in orgs]
        if len(org_codes) == 1:
            # Skip picker, go straight to list
            await _show_truck_list(update, context, user, org_codes[0])
        else:
            await _show(update, context, [
                "🚛 <b>Search Truck</b>\n\n"
                "Select a company to browse trucks,\n"
                "or choose <b>All Companies</b>:"
            ], keyboard=truck_company_picker_kb(org_codes))

    elif data.startswith("trucks_browse_"):
        await query.answer()
        org_filter = data[14:]  # "ALL" or org code
        if org_filter == "ALL":
            org_filter = None
        await _show_truck_list(update, context, user, org_filter)

    elif data.startswith("trucks_page_"):
        await query.answer()
        # trucks_page_ORG_PAGE  or  trucks_page_ALL_PAGE
        parts = data.split("_")
        page = int(parts[-1])
        org_filter = "_".join(parts[2:-1])  # reconstruct org code
        if org_filter == "ALL":
            org_filter = None
        await _show_truck_list(update, context, user, org_filter, page=page)

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
            f"👤 <b>User {target_tid}</b>\n"
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
        await _show(update, context, [
            f"🔄 <b>Change Role for {target_tid}</b>\n\n"
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
            f"✅ Updated user {target_tid} → {role_display(new_role)}"
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
        await _show(update, context, [
            f"⚠️ <b>Remove user {target_tid}?</b>\n\n"
            "They will lose access to this account."
        ], keyboard=kb)

    elif data.startswith("usrremoveconfirm_"):
        target_tid = int(data[17:])
        await query.answer()
        if target_tid == user.telegram_id:
            await _show(update, context, ["⚠️ You can't remove yourself."], keyboard=back_kb())
            return
        target_user = await db.get_user_by_telegram_id(target_tid)
        if target_user and target_user.account_id == user.account_id:
            await db.remove_user(target_user.id)
        await _show(update, context, [f"✅ Removed user {target_tid}."], keyboard=back_kb())

    # ── Per-truck fault reports ──────────────────────────────────
    elif data.startswith("truckfaults_") or data.startswith("truckcritical_"):
        await query.answer()
        # truckfaults_ORG_NAME  or  truckcritical_ORG_NAME
        prefix = "truckfaults_" if data.startswith("truckfaults_") else "truckcritical_"
        rest = data[len(prefix):]
        parts = rest.split("_", 1)
        t_org = parts[0] if len(parts) >= 1 else ""
        t_name = parts[1] if len(parts) >= 2 else ""
        await cmd_truck_report(update, context, truck_name=t_name, org=t_org)

    # ── Truck lookup ────────────────────────────────────────────
    elif data.startswith("truck_") or data.startswith("orgtruck_"):
        await cmd_truck(update, context)

    # ── Org sub-menu ────────────────────────────────────────────
    elif data.startswith("org_") and not data.startswith("orgfaults_") \
            and not data.startswith("orgcritical_") \
            and not data.startswith("orgfuel_") \
            and not data.startswith("orgtruck_") \
            and not data.startswith("orgenghrs_"):
        org = data.replace("org_", "")
        await query.answer()
        name = ORG_DISPLAY.get(org, org)
        text = (
            f"┌─────────────────────────┐\n"
            f"  🏢  <b>{name}</b>\n"
            f"└─────────────────────────┘\n"
            f"\n"
            f"  Select a report for {org}:"
        )
        await _show(update, context, [text], keyboard=org_menu_kb(org))

    # Per-org commands
    elif data.startswith("orgfaults_"):
        org = data.replace("orgfaults_", "")
        await cmd_faults(update, context, org=org)
    elif data.startswith("orgcritical_"):
        org = data.replace("orgcritical_", "")
        await cmd_critical(update, context, org=org)
    elif data.startswith("orgfuel_"):
        org = data.replace("orgfuel_", "")
        await cmd_fuel(update, context, org=org)
    elif data.startswith("orgenghrs_"):
        org = data.replace("orgenghrs_", "")
        await cmd_engine_hours(update, context, org=org)

    else:
        await query.answer("Unknown action")


# ═══════════════════════════════════════════════════════════════════
# TEXT INPUT HANDLER — for pending prompts
# ═══════════════════════════════════════════════════════════════════

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text replies for pending interactive prompts."""
    pending = context.user_data.pop("_pending", None)

    if not pending:
        # No pending prompt — just show /start
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

    # ── Add Org wizard step 1: org code ─────────────────────────
    elif pending == "addorg_code":
        code = text.strip().upper()
        if not code or len(code) > 10 or " " in code:
            await _show(update, context, [
                "❌ Code must be 1–10 characters, no spaces.\n"
                "Example: <b>PTG</b>, <b>CFT</b>"
            ], keyboard=back_kb())
            return
        context.user_data["_addorg"] = {"code": code}
        context.user_data["_pending"] = "addorg_key"
        await _show(update, context, [
            f"📡 <b>Add Company — {code}</b>\n\n"
            "<b>Step 2/3</b> — Samsara API key\n\n"
            "Paste your Samsara API token below:\n"
            "<i>(starts with samsara_api_...)</i>"
        ], keyboard=back_kb())

    # ── Add Org wizard step 2: api key ──────────────────────────
    elif pending == "addorg_key":
        api_key = text.strip()
        if not api_key or len(api_key) < 10:
            await _show(update, context, [
                "❌ That doesn't look like a valid API key.\n"
                "It should start with <code>samsara_api_</code>"
            ], keyboard=back_kb())
            return
        wiz = context.user_data.get("_addorg", {})
        wiz["api_key"] = api_key
        context.user_data["_addorg"] = wiz
        context.user_data["_pending"] = "addorg_name"
        await _show(update, context, [
            f"📡 <b>Add Company — {wiz.get('code', '?')}</b>\n\n"
            "<b>Step 3/3</b> — Display name (optional)\n\n"
            "Type a friendly name for this company\n"
            "or tap <b>⏭ Skip</b> to use the code.\n\n"
            "Example: <i>Premier Trucking Group</i>"
        ], keyboard=skip_name_kb())

    # ── Add Org wizard step 3: display name → create ────────────
    elif pending == "addorg_name":
        wiz = context.user_data.pop("_addorg", {})
        code = wiz.get("code", "")
        api_key = wiz.get("api_key", "")
        display_name = code if text.lower().strip() == "skip" else text.strip()
        if not code or not api_key:
            await _show(update, context, ["❌ Wizard state lost. Please start over."], keyboard=back_kb())
            return
        # Delegate to cmd_addorg with synthesized args
        context.args = [f"{code}:{api_key}"] + (display_name.split() if display_name != code else [])
        await cmd_addorg(update, context)

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

    else:
        await cmd_start(update, context)
