"""Account, invite, user and org management commands."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import Role
from permissions import can, role_display, role_emoji
from samsara_client import SamsaraClient, populate_org_display
from formatters import (
    format_account_info,
    format_invite_created,
    format_users_list,
    format_org_added,
)

import bot.config as _cfg
from bot.config import (
    db, logger, SAMSARA_BASE_URL,
    get_client, invalidate_client, get_user_org_codes,
)
from bot.keyboards import back_kb, invite_kb
from bot.helpers import _show, _show_loading, _user_menu_kb
from bot.auth import _require_registered


@_require_registered
async def cmd_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show account overview — with org management buttons for owners."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_account") and not can(user.role, "can_manage_users"):
        pass

    account = await db.get_account(user.account_id)
    orgs = await db.get_account_orgs(user.account_id)
    users = await db.list_account_users(user.account_id)

    text = format_account_info(account, orgs, users, user)

    rows = []
    # Org management buttons for owners
    if can(user.role, "can_manage_orgs"):
        for org in orgs:
            rows.append([InlineKeyboardButton(
                f"🗑 Remove {org.code} ({org.display_name})",
                callback_data=f"rmorg_{org.code}",
            )])
        rows.append([InlineKeyboardButton("📡 Add Company", callback_data="cmd_addorg_prompt")])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])

    await _show(update, context, [text], keyboard=InlineKeyboardMarkup(rows))


@_require_registered
async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create an invite code: /invite <role> [department] [truck_num]"""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_invite"):
        await _show(update, context,
                    ["⛔ Only owners and admins can invite users."],
                    keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context, [
            "ℹ️  <b>Invite Usage:</b>\n\n"
            "  /invite <b>role</b> [dept] [truck#]\n\n"
            "  Roles:\n"
            "  • <code>admin</code>\n"
            "  • <code>fleet_manager</code>\n"
            "  • <code>dispatcher</code>\n"
            "  • <code>driver</code>\n\n"
            "  Examples:\n"
            "  /invite fleet_manager\n"
            "  /invite dispatcher operations\n"
            "  /invite driver maintenance 134\n"
        ], keyboard=back_kb())
        return

    role_str = context.args[0].lower()
    dept = context.args[1] if len(context.args) > 1 else "general"
    truck = context.args[2] if len(context.args) > 2 else None

    # Validate role
    try:
        invite_role = Role.from_str(role_str)
    except ValueError:
        await _show(update, context, [
            f"❌ Unknown role: <code>{role_str}</code>\n\n"
            "Valid roles: admin, fleet_manager, dispatcher, driver"
        ], keyboard=back_kb())
        return

    # Can't invite a role higher than your own
    role_order = [Role.OWNER, Role.ADMIN, Role.FLEET_MGR, Role.DISPATCHER, Role.DRIVER]
    if role_order.index(invite_role) < role_order.index(user.role):
        await _show(update, context,
                    ["⛔ You can't invite someone with a higher role than yours."],
                    keyboard=back_kb())
        return

    # Owner can only be set, not invited
    if invite_role == Role.OWNER:
        await _show(update, context,
                    ["⛔ Owner role can't be assigned via invite. Use /setrole instead."],
                    keyboard=back_kb())
        return

    try:
        invite = await db.create_invite(
            account_id=user.account_id,
            created_by=user.id,
            role=invite_role,
            department=dept,
            truck_num=truck,
        )
        link = f"https://t.me/{_cfg.bot_username}?start=join_{invite.code}" if _cfg.bot_username else None
        text = format_invite_created(
            invite.code, role_display(invite_role), dept,
            invite_link=link,
        )
        kb = invite_kb(link)
        await _show(update, context, [text], keyboard=kb)

    except Exception as e:
        logger.error(f"Invite error: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List team members — with interactive buttons for management."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_users"):
        await _show(update, context,
                    ["⛔ Only owners and admins can manage users."],
                    keyboard=back_kb())
        return

    account = await db.get_account(user.account_id)
    users = await db.list_account_users(user.account_id)
    text = format_users_list(users, account.name)

    # Build interactive user buttons for management
    rows = []
    for u in users:
        label = f"{role_emoji(u.role)} {u.telegram_id}"
        if u.department:
            label += f" — {u.department}"
        rows.append([InlineKeyboardButton(label, callback_data=f"usrmenu_{u.telegram_id}")])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])

    await _show(update, context, [text], keyboard=InlineKeyboardMarkup(rows))


@_require_registered
async def cmd_setrole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Change a user's role: /setrole <telegram_id> <new_role>"""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_users"):
        await _show(update, context,
                    ["⛔ Only owners and admins can change roles."],
                    keyboard=back_kb())
        return

    if len(context.args or []) < 2:
        await _show(update, context, [
            "ℹ️  Usage:\n"
            "  /setrole <b>telegram_id</b> <b>role</b>\n\n"
            "  Example:\n"
            "  /setrole 123456789 fleet_manager"
        ], keyboard=back_kb())
        return

    try:
        target_tid = int(context.args[0])
        new_role = Role.from_str(context.args[1])
    except (ValueError, IndexError):
        await _show(update, context,
                    ["❌ Invalid telegram ID or role."],
                    keyboard=back_kb())
        return

    target_user = await db.get_user_by_telegram_id(target_tid)
    if not target_user or target_user.account_id != user.account_id:
        await _show(update, context,
                    ["❌ User not found in your account."],
                    keyboard=back_kb())
        return

    # Only owner can promote to owner
    if new_role == Role.OWNER and user.role != Role.OWNER:
        await _show(update, context,
                    ["⛔ Only owners can promote to owner."],
                    keyboard=back_kb())
        return

    await db.update_user(target_user.id, role=new_role)
    await _show(update, context, [
        f"✅ Updated user {target_tid} → {role_display(new_role)}"
    ], keyboard=back_kb())


@_require_registered
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a user: /remove <telegram_id>"""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_users"):
        await _show(update, context,
                    ["⛔ Only owners and admins can remove users."],
                    keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context, [
            "ℹ️  Usage:  /remove <b>telegram_id</b>"
        ], keyboard=back_kb())
        return

    try:
        target_tid = int(context.args[0])
    except ValueError:
        await _show(update, context,
                    ["❌ Invalid telegram ID."],
                    keyboard=back_kb())
        return

    if target_tid == user.telegram_id:
        await _show(update, context,
                    ["⚠️ You can't remove yourself."],
                    keyboard=back_kb())
        return

    target_user = await db.get_user_by_telegram_id(target_tid)
    if not target_user or target_user.account_id != user.account_id:
        await _show(update, context,
                    ["❌ User not found in your account."],
                    keyboard=back_kb())
        return

    await db.remove_user(target_user.id)
    await _show(update, context, [
        f"✅ Removed user {target_tid} from your account."
    ], keyboard=back_kb())


@_require_registered
async def cmd_addorg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Connect a company: /addorg CODE:api_key [Display Name]"""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_orgs"):
        await _show(update, context,
                    ["⛔ Only owners can manage companies."],
                    keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context, [
            "ℹ️  <b>Add Company:</b>\n\n"
            "  /addorg <b>CODE:samsara_api_key</b>\n\n"
            "  Example:\n"
            "  /addorg PTG:samsara_api_Cuuvx5LCti...\n\n"
            "  Optional display name:\n"
            "  /addorg PTG:api_key Premier Trucking\n"
        ], keyboard=back_kb())
        return

    # Parse CODE:KEY
    first_arg = context.args[0]
    if ":" not in first_arg:
        await _show(update, context,
                    ["❌ Format: CODE:api_key\nExample: PTG:samsara_api_xxx"],
                    keyboard=back_kb())
        return

    code, api_key = first_arg.split(":", 1)
    code = code.strip().upper()
    display_name = " ".join(context.args[1:]) if len(context.args) > 1 else code

    # Check if code already exists
    existing = await db.get_org_by_code(user.account_id, code)
    if existing:
        await _show(update, context,
                    [f"⚠️ Company <b>{code}</b> already exists in your account."],
                    keyboard=back_kb())
        return

    try:
        # Test the API key — fetch total vehicles, then active (30-day filter)
        test_client = SamsaraClient(api_key, SAMSARA_BASE_URL, active_days=0)
        await _show_loading(update, context, f"⏳ Testing connection to {code}…")
        total_trucks = None
        active_trucks = None
        try:
            all_vehicles = await test_client.get_vehicles()
            total_trucks = len(all_vehicles)
        except Exception:
            pass
        finally:
            await test_client.close()

        # Count active trucks (last 30 days GPS)
        if total_trucks is not None:
            active_client = SamsaraClient(api_key, SAMSARA_BASE_URL, active_days=30)
            try:
                active_list = await active_client.get_fleet_overview()
                active_trucks = len(active_list)
            except Exception:
                pass
            finally:
                await active_client.close()

        org = await db.add_organization(
            account_id=user.account_id,
            code=code,
            samsara_api_key=api_key,
            display_name=display_name,
        )

        # Invalidate client cache
        await invalidate_client(user.account_id)

        # Refresh ORG_DISPLAY
        orgs = await db.get_account_orgs(user.account_id)
        populate_org_display(orgs)

        text = format_org_added(code, display_name, total_trucks, active_trucks)
        kb = await _user_menu_kb(user)
        await _show(update, context, [text], keyboard=kb)

        logger.info(f"Org {code} added to account {user.account_id}")

    except Exception as e:
        logger.error(f"Add org error: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_removeorg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a company: /removeorg CODE"""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_orgs"):
        await _show(update, context,
                    ["⛔ Only owners can manage companies."],
                    keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context,
                    ["ℹ️  Usage:  /removeorg <b>CODE</b>"],
                    keyboard=back_kb())
        return

    code = context.args[0].strip().upper()
    org = await db.get_org_by_code(user.account_id, code)
    if not org:
        await _show(update, context,
                    [f"❌ Company <b>{code}</b> not found."],
                    keyboard=back_kb())
        return

    await db.remove_organization(org.id)
    await invalidate_client(user.account_id)

    orgs = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs)

    await _show(update, context,
                [f"✅ Company <b>{code}</b> removed."],
                keyboard=back_kb())
