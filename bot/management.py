"""Account, invite, user and company management commands."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import Role
from permissions import can, role_display, role_emoji
from samsara_client import SamsaraClient, populate_company_display
from formatters import (
    format_account_info,
    format_invite_created,
    format_users_list,
    format_org_added,
)

import bot.config as _cfg
from bot.config import (
    db, logger, SAMSARA_BASE_URL,
    get_client, invalidate_client, get_user_company_codes,
)
from bot.keyboards import back_kb, invite_kb
from bot.helpers import _show, _show_loading, _user_menu_kb
from bot.auth import _require_registered


@_require_registered
async def cmd_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show account overview — with company management buttons for owners."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_account") and not can(user.role, "can_manage_users"):
        pass

    account = await db.get_account(user.account_id)
    companies = await db.get_account_companies(user.account_id)
    users = await db.list_account_users(user.account_id)

    text = format_account_info(account, companies, users, user)

    rows = []
    # Company management buttons for owners
    if can(user.role, "can_manage_companies"):
        for co in companies:
            rows.append([InlineKeyboardButton(
                f"🗑 Remove {co.code} ({co.display_name})",
                callback_data=f"rmco_{co.code}",
            )])
        rows.append([InlineKeyboardButton("📡 Add Company", callback_data="cmd_addcompany_prompt")])
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
        # Redirect to the interactive button-based invite flow
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
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
            "Select the role for the new member:\n\n"
            "<i>Or use: /invite role [dept] [truck#]</i>"
        ], keyboard=kb)
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
        label = f"{role_emoji(u.role)} {u.label}"
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
        f"✅ Updated {target_user.label} → {role_display(new_role)}"
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
        f"✅ Removed {target_user.label} from your account."
    ], keyboard=back_kb())


@_require_registered
async def cmd_addcompany(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Connect a company: /addcompany CODE:api_key [Display Name]"""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_companies"):
        await _show(update, context,
                    ["⛔ Only owners can manage companies."],
                    keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context, [
            "ℹ️  <b>Add Company:</b>\n\n"
            "  /addcompany <b>CODE:samsara_api_key</b>\n\n"
            "  Example:\n"
            "  /addcompany PTG:samsara_api_Cuuvx5LCti...\n\n"
            "  Optional display name:\n"
            "  /addcompany PTG:api_key Premier Trucking\n"
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
    existing = await db.get_company_by_code(user.account_id, code)
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

        new_company = await db.add_company(
            account_id=user.account_id,
            code=code,
            samsara_api_key=api_key,
            display_name=display_name,
        )

        # Invalidate client cache
        await invalidate_client(user.account_id)

        # Refresh COMPANY_DISPLAY
        companies = await db.get_account_companies(user.account_id)
        populate_company_display(companies)

        text = format_org_added(code, display_name, total_trucks, active_trucks)
        kb = await _user_menu_kb(user)
        await _show(update, context, [text], keyboard=kb)

        logger.info(f"Org {code} added to account {user.account_id}")

    except Exception as e:
        logger.error(f"Add company error: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_removecompany(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a company: /removecompany CODE"""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_companies"):
        await _show(update, context,
                    ["⛔ Only owners can manage companies."],
                    keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context,
                    ["ℹ️  Usage:  /removecompany <b>CODE</b>"],
                    keyboard=back_kb())
        return

    code = context.args[0].strip().upper()
    company = await db.get_company_by_code(user.account_id, code)
    if not company:
        await _show(update, context,
                    [f"❌ Company <b>{code}</b> not found."],
                    keyboard=back_kb())
        return

    await db.remove_company(company.id)
    await invalidate_client(user.account_id)

    companies = await db.get_account_companies(user.account_id)
    populate_company_display(companies)

    await _show(update, context,
                [f"✅ Company <b>{code}</b> removed."],
                keyboard=back_kb())


# ══════════════════════════════════════════════════════════════════
# GROUP / CHANNEL AUTHORIZATION
# ══════════════════════════════════════════════════════════════════

@_require_registered
async def cmd_addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Authorize a group/channel: /addgroup <chat_id> [title]"""
    user = context.user_data["_db_user"]
    if not user.is_admin_or_above:
        await _show(update, context,
                    ["⛔ Only owners and admins can manage group access."],
                    keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context, [
            "ℹ️  <b>Authorize Group / Channel</b>\n\n"
            "  /addgroup <b>chat_id</b> [title]\n\n"
            "  <b>How to get the chat ID:</b>\n"
            "  1. Add the bot to the group\n"
            "  2. Send /chatid in the group\n"
            "  3. The bot will show the chat ID\n\n"
            "  Example:\n"
            "  /addgroup -1001234567890 Fleet Team"
        ], keyboard=back_kb())
        return

    try:
        chat_id = int(context.args[0])
    except ValueError:
        await _show(update, context,
                    ["❌ Invalid chat ID. Must be a number (usually negative for groups)."],
                    keyboard=back_kb())
        return

    title = " ".join(context.args[1:]) if len(context.args) > 1 else f"Group {chat_id}"

    await db.add_authorized_chat(
        account_id=user.account_id,
        chat_id=chat_id,
        chat_title=title,
        added_by=user.id,
    )

    await _show(update, context, [
        f"✅ Group/channel authorized!\n\n"
        f"  💬  <b>{title}</b>\n"
        f"  🆔  <code>{chat_id}</code>\n\n"
        f"  The bot will now respond in this chat."
    ], keyboard=back_kb())

    logger.info(f"Group {chat_id} authorized for account {user.account_id}")


@_require_registered
async def cmd_removegroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a group/channel: /removegroup <chat_id>"""
    user = context.user_data["_db_user"]
    if not user.is_admin_or_above:
        await _show(update, context,
                    ["⛔ Only owners and admins can manage group access."],
                    keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context,
                    ["ℹ️  Usage:  /removegroup <b>chat_id</b>"],
                    keyboard=back_kb())
        return

    try:
        chat_id = int(context.args[0])
    except ValueError:
        await _show(update, context,
                    ["❌ Invalid chat ID."],
                    keyboard=back_kb())
        return

    await db.remove_authorized_chat(user.account_id, chat_id)
    await _show(update, context, [
        f"✅ Group/channel <code>{chat_id}</code> removed.\n"
        "  The bot will no longer respond there."
    ], keyboard=back_kb())


@_require_registered
async def cmd_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List authorized groups/channels."""
    user = context.user_data["_db_user"]
    if not user.is_admin_or_above:
        await _show(update, context,
                    ["⛔ Only owners and admins can manage group access."],
                    keyboard=back_kb())
        return

    chats = await db.get_authorized_chats(user.account_id)

    if not chats:
        await _show(update, context, [
            "💬 <b>Authorized Groups</b>\n\n"
            "  No groups authorized yet.\n\n"
            "  Tap <b>➕ Add</b> below to authorize\n"
            "  a group or channel where the bot\n"
            "  should respond to commands."
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Group / Channel", callback_data="addgroup_pick")],
            [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
        ]))
        return

    lines = ["💬 <b>Authorized Groups</b>\n"]
    for c in chats:
        lines.append(f"  • <b>{c.chat_title}</b>")
        lines.append(f"    🆔 <code>{c.chat_id}</code>")
    lines.append(f"\n  Total: {len(chats)} group(s)")

    # Build remove buttons + add button
    rows = []
    for c in chats:
        rows.append([InlineKeyboardButton(
            f"🗑 Remove {c.chat_title}",
            callback_data=f"rmgroup_{c.chat_id}",
        )])
    rows.append([InlineKeyboardButton("➕ Add Group / Channel", callback_data="addgroup_pick")])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])

    await _show(update, context, ["\n".join(lines)],
                keyboard=InlineKeyboardMarkup(rows))


@_require_registered
async def handle_chat_shared(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the native Telegram chat picker result (ChatShared)."""
    user = context.user_data["_db_user"]
    if not user.is_admin_or_above:
        return

    shared = update.effective_message.chat_shared
    if not shared:
        return

    chat_id = shared.chat_id
    title = shared.title or f"Chat {chat_id}"

    # Remove the reply keyboard
    from telegram import ReplyKeyboardRemove
    await update.effective_message.reply_text(
        "✅ Got it!", reply_markup=ReplyKeyboardRemove()
    )

    # Store pending data for confirm/cancel
    context.user_data["_pending_group"] = {
        "chat_id": chat_id,
        "title": title,
    }

    kind = "Channel" if shared.request_id == 2 else "Group"
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data="addgroup_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="addgroup_cancel"),
        ],
    ])
    await _show(update, context, [
        f"💬 <b>Add {kind}?</b>\n\n"
        f"  📝 <b>{title}</b>\n"
        f"  🆔 <code>{chat_id}</code>\n\n"
        "  Confirm to authorize this chat."
    ], keyboard=kb)
