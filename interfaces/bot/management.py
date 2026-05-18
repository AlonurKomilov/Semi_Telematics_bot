"""Account, invite, user and company management commands."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from capabilities.localization.i18n import t

from adapters.storage import Role
from capabilities.iam.permissions import can, role_display, role_emoji, validate_invite_role, validate_role_change
from adapters.samsara.client import SamsaraClient, populate_company_display
from capabilities.formatting import (
    format_account_info,
    format_invite_created,
    format_users_list,
    format_org_added,
)

from interfaces.bot.config import logger, SAMSARA_BASE_URL
from interfaces.bot.state import invalidate_client, get_platform_db, get_tenant_db
from interfaces.bot.keyboards import back_kb, invite_kb
from interfaces.bot.helpers import _show, _show_loading, _user_menu_kb, _safe_error, make_invite_link
from interfaces.bot.auth import _require_registered


@_require_registered
async def cmd_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show companies overview — clickable company buttons for drill-down."""
    user = context.user_data["_db_user"]

    platform = get_platform_db()
    account = await platform.get_account(user.account_id)
    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)

    text = format_account_info(account, companies, user)

    rows = []
    # Clickable company buttons — each opens a detail view
    for co in companies:
        label = f"🏢 {co.code}"
        if co.display_name and co.display_name != co.code:
            label += f" — {co.display_name}"
        rows.append([InlineKeyboardButton(label, callback_data=f"comenu_{co.code}")])
    # General actions
    if can(user.role, "can_manage_companies"):
        rows.append([InlineKeyboardButton(t('company.add_btn'), callback_data="cmd_addcompany_prompt")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="submenu_mgmt")])

    await _show(update, context, [text], keyboard=InlineKeyboardMarkup(rows))


@_require_registered
async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create an invite code: /invite <role> [department] [truck_num]"""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_invite"):
        await _show(update, context,
                    [t('access.admin_only')],
                    keyboard=back_kb())
        return

    if not context.args:
        # Redirect to the interactive button-based invite flow
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔑 Admin", callback_data="inv_admin"),
                InlineKeyboardButton("🔧 Fleet", callback_data="inv_fleet"),
            ],
            [
                InlineKeyboardButton("📡 Dispatcher", callback_data="inv_dispatcher"),
                InlineKeyboardButton("🚛 Driver", callback_data="inv_driver"),
            ],
            [InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")],
        ])
        await _show(update, context, [
            f"{t('invite.title')}\n\n"
            f"{t('invite.select_role')}\n\n"
            f"{t('invite.or_use_cmd')}"
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
            f"{t('invite.unknown_role').replace('{role}', role_str)}\n\n"
            "Valid roles: admin, fleet, safety, dispatcher, driver"
        ], keyboard=back_kb())
        return

    # Validate role rank — actor cannot invite to a role >= their own
    ok, reason = validate_invite_role(user.role, invite_role)
    if not ok:
        if reason == "owner_via_invite":
            await _show(update, context,
                        [t('access.owner_via_invite')],
                        keyboard=back_kb())
        else:
            await _show(update, context,
                        [t('access.cant_invite_higher')],
                        keyboard=back_kb())
        return

    try:
        invite = await get_platform_db().create_invite(
            account_id=user.account_id,
            created_by=user.id,
            role=invite_role,
            department=dept,
            truck_num=truck,
        )
        link = make_invite_link(invite.code, context)
        text = format_invite_created(
            invite.code, role_display(invite_role), dept,
            invite_link=link,
        )
        kb = invite_kb(link)
        await _show(update, context, [text], keyboard=kb)

    except Exception as e:
        logger.error(f"Invite error: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


@_require_registered
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List team members — with interactive buttons for management."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_users"):
        await _show(update, context,
                    [t('access.admin_only')],
                    keyboard=back_kb())
        return

    platform = get_platform_db()
    account = await platform.get_account(user.account_id)
    users = await platform.list_account_users(user.account_id)
    text = format_users_list(users, account.name)

    # Build interactive user buttons for management
    rows = []
    for u in users:
        label = f"{role_emoji(u.role)} {u.label}"
        if u.department:
            label += f" — {u.department}"
        rows.append([InlineKeyboardButton(label, callback_data=f"usrmenu_{u.telegram_id}")])
    # Team actions — Invite and Groups
    actions = []
    if can(user.role, "can_invite"):
        actions.append(InlineKeyboardButton("✉️ Invite", callback_data="cmd_invite_pick"))
    if can(user.role, "can_manage_users"):
        actions.append(InlineKeyboardButton("💬 Groups", callback_data="cmd_groups"))
    if actions:
        rows.append(actions)
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="submenu_mgmt")])

    await _show(update, context, [text], keyboard=InlineKeyboardMarkup(rows))


@_require_registered
async def cmd_setrole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Change a user's role: /setrole <telegram_id> <new_role>"""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_users"):
        await _show(update, context,
                    [t('access.admin_only')],
                    keyboard=back_kb())
        return

    if len(context.args or []) < 2:
        await _show(update, context, [
            f"{t('user_mgmt.setrole_usage')}\n"
            "  /setrole <b>telegram_id</b> <b>role</b>\n\n"
            "  Example:\n"
            "  /setrole 123456789 fleet"
        ], keyboard=back_kb())
        return

    try:
        target_tid = int(context.args[0])
        new_role = Role.from_str(context.args[1])
    except (ValueError, IndexError):
        await _show(update, context,
                    [t('user_mgmt.role_invalid')],
                    keyboard=back_kb())
        return

    target_user = await get_platform_db().get_user_by_telegram_id(target_tid)
    if not target_user or target_user.account_id != user.account_id:
        await _show(update, context,
                    [t('user_mgmt.user_not_found')],
                    keyboard=back_kb())
        return

    # Validate rank-based permission (fixes OWNER-only check)
    ok, reason = validate_role_change(user.role, target_user.role, new_role)
    if not ok:
        if reason == "cant_promote_above_self":
            await _show(update, context,
                        [t('access.cant_promote_owner')],
                        keyboard=back_kb())
        else:
            await _show(update, context,
                        [t('access.admin_only')],
                        keyboard=back_kb())
        return

    await get_platform_db().update_user(target_user.id, role=new_role)
    await _show(update, context, [
        t('user_mgmt.role_updated').replace('{user}', target_user.label).replace('{role}', role_display(new_role))
    ], keyboard=back_kb())


@_require_registered
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a user: /remove <telegram_id>"""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_users"):
        await _show(update, context,
                    [t('access.admin_remove')],
                    keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context, [
            t('user_mgmt.remove_usage')
        ], keyboard=back_kb())
        return

    try:
        target_tid = int(context.args[0])
    except ValueError:
        await _show(update, context,
                    [t('user_mgmt.invalid_id')],
                    keyboard=back_kb())
        return

    if target_tid == user.telegram_id:
        await _show(update, context,
                    [t('access.cant_self_remove')],
                    keyboard=back_kb())
        return

    target_user = await get_platform_db().get_user_by_telegram_id(target_tid)
    if not target_user or target_user.account_id != user.account_id:
        await _show(update, context,
                    [t('user_mgmt.user_not_found')],
                    keyboard=back_kb())
        return

    await get_platform_db().remove_user(target_user.id)
    await _show(update, context, [
        t('user_mgmt.user_removed').replace('{user}', target_user.label)
    ], keyboard=back_kb())


@_require_registered
async def cmd_addcompany(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Connect a company: /addcompany CODE:api_key [Display Name]"""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_companies"):
        await _show(update, context,
                    [t('access.owner_only')],
                    keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context, [
            f"{t('company.add_prompt')}\n\n"
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
                    [t('company.format_error')],
                    keyboard=back_kb())
        return

    code, api_key = first_arg.split(":", 1)
    code = code.strip().upper()
    display_name = " ".join(context.args[1:]) if len(context.args) > 1 else code

    # Check if code already exists
    tenant = await get_tenant_db(user.account_id)
    existing = await tenant.get_company_by_code(user.account_id, code)
    if existing:
        await _show(update, context,
                    [t('company.already_exists').replace('{name}', code)],
                    keyboard=back_kb())
        return

    try:
        # Test the API key — fetch total vehicles, then active (30-day filter)
        test_client = SamsaraClient(api_key, SAMSARA_BASE_URL, active_days=0)
        await _show_loading(update, context, t('company.testing').replace('{code}', code))
        total_trucks = None
        active_trucks = None
        try:
            all_vehicles = await test_client.get_vehicles()
            total_trucks = len(all_vehicles)
        except Exception as e:
            logger.debug("Could not fetch vehicles for API key test: %s", e)
        finally:
            await test_client.close()

        # Count active trucks (last 30 days GPS)
        if total_trucks is not None:
            active_client = SamsaraClient(api_key, SAMSARA_BASE_URL, active_days=30)
            try:
                active_list = await active_client.get_fleet_overview()
                active_trucks = len(active_list)
            except Exception as e:
                logger.debug("Could not fetch active trucks for API key test: %s", e)
            finally:
                await active_client.close()

        new_company = await tenant.add_company(
            account_id=user.account_id,
            code=code,
            samsara_api_key=api_key,
            display_name=display_name,
        )

        # Invalidate client cache
        await invalidate_client(user.account_id)

        # Refresh COMPANY_DISPLAY
        companies = await tenant.get_account_companies(user.account_id)
        populate_company_display(companies)

        text = format_org_added(code, display_name, total_trucks, active_trucks)
        kb = await _user_menu_kb(user)
        await _show(update, context, [text], keyboard=kb)

        logger.info(f"Org {code} added to account {user.account_id}")

    except Exception as e:
        logger.error(f"Add company error: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


@_require_registered
async def cmd_removecompany(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a company: /removecompany CODE"""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_companies"):
        await _show(update, context,
                    [t('access.owner_only')],
                    keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context,
                    ["ℹ️  Usage:  /removecompany <b>CODE</b>"],
                    keyboard=back_kb())
        return

    code = context.args[0].strip().upper()
    tenant = await get_tenant_db(user.account_id)
    company = await tenant.get_company_by_code(user.account_id, code)
    if not company:
        await _show(update, context,
                    [t('company.not_found_code').replace('{code}', code)],
                    keyboard=back_kb())
        return

    await tenant.remove_company(company.id)
    await invalidate_client(user.account_id)

    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)

    await _show(update, context,
                [t('company.removed').replace('{name}', code)],
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
                    [t('access.admin_groups')],
                    keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context, [
            f"{t('groups.add_prompt')}\n\n"
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
                    [t('groups.invalid_id')],
                    keyboard=back_kb())
        return

    title = " ".join(context.args[1:]) if len(context.args) > 1 else f"Group {chat_id}"

    await get_platform_db().add_authorized_chat(
        account_id=user.account_id,
        chat_id=chat_id,
        chat_title=title,
        added_by=user.id,
    )

    await _show(update, context, [
        f"{t('groups.added')}\n\n"
        f"  💬  <b>{title}</b>\n"
        f"  🆔  <code>{chat_id}</code>\n\n"
        f"  {t('groups.bot_responds')}"
    ], keyboard=back_kb())

    logger.info(f"Group {chat_id} authorized for account {user.account_id}")


@_require_registered
async def cmd_removegroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a group/channel: /removegroup <chat_id>"""
    user = context.user_data["_db_user"]
    if not user.is_admin_or_above:
        await _show(update, context,
                    [t('access.admin_groups')],
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
                    [t('groups.invalid_id')],
                    keyboard=back_kb())
        return

    await get_platform_db().remove_authorized_chat(user.account_id, chat_id)
    await _show(update, context, [
        t('groups.removed').replace('{id}', str(chat_id))
    ], keyboard=back_kb())


@_require_registered
async def cmd_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List authorized groups/channels."""
    user = context.user_data["_db_user"]
    if not user.is_admin_or_above:
        await _show(update, context,
                    [t('access.admin_groups')],
                    keyboard=back_kb())
        return

    chats = await get_platform_db().get_authorized_chats(user.account_id)

    if not chats:
        await _show(update, context, [
            f"{t('groups.title')}\n\n"
            f"  {t('groups.empty')}\n\n"
            f"  Tap <b>➕ Add</b> below to authorize\n"
            f"  a group or channel where the bot\n"
            f"  should respond to commands."
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('groups.add_btn'), callback_data="addgroup_pick")],
            [InlineKeyboardButton("◀️ Back", callback_data="cmd_users")],
        ]))
        return

    lines = [f"{t('groups.title')}\n"]
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
    rows.append([InlineKeyboardButton(t('groups.add_btn'), callback_data="addgroup_pick")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_users")])

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
