"""Account, invite, user and company management commands."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from capabilities.localization.i18n import t

from adapters.storage import Role
from capabilities.permissions.roles import can, role_display
from features.settings.invites.service import invite_authorized
from capabilities.formatting import (
    format_account_info,
    format_invite_created,
)

from interfaces.bot.config import logger
from interfaces.bot.state import get_platform_db, get_tenant_db
from interfaces.bot.keyboards import back_kb, invite_kb
from interfaces.bot.helpers import (
    _show, _safe_error,
    make_invite_link, reply_dashboard_redirect,
)
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
    """Create an invite code: /invite <role> [truck_num]

    Transition kindness: the legacy syntax ``/invite <role> <department>
    [truck_num]`` is silently accepted — if the second positional arg
    matches a known legacy department literal (``general``,
    ``operations``, ``management``, ``dispatch``), we skip it and treat
    the third arg as the truck.  Operators with muscle memory for the
    old shape don't get a cryptic error.
    """
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
                InlineKeyboardButton("🛡️ Safety", callback_data="inv_safety"),
                InlineKeyboardButton("📡 Dispatcher", callback_data="inv_dispatcher"),
            ],
            [
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
    # Legacy positional-arg compatibility: ``/invite driver general 47``
    # used to mean role=driver, dept=general, truck=47.  Department was
    # removed in migration 098.  If the 2nd arg looks like a known
    # legacy department literal, skip it and treat arg-3 as the truck.
    _LEGACY_DEPT_LITERALS = {"general", "operations", "management", "dispatch"}
    rest = list(context.args[1:])
    if rest and rest[0].lower() in _LEGACY_DEPT_LITERALS:
        rest = rest[1:]
    truck = rest[0] if rest else None

    # Validate role
    try:
        invite_role = Role.from_str(role_str)
    except ValueError:
        await _show(update, context, [
            f"{t('invite.unknown_role').replace('{role}', role_str)}\n\n"
            "Valid roles: admin, fleet, safety, dispatcher, driver"
        ], keyboard=back_kb())
        return

    # Invite-target authorization — a manager's sub-team whitelist (rank-
    # independent) OR the standard rank gate.  Mirrors the dashboard invites
    # feature (features/settings/invites.service.invite_authorized).
    actor_role = user.role.value if hasattr(user.role, "value") else user.role
    ok, reason = invite_authorized(actor_role, bool(user.is_manager), invite_role.value)
    if not ok:
        if reason == "owner_via_invite":
            await _show(update, context,
                        [t('access.owner_via_invite')],
                        keyboard=back_kb())
        elif reason.startswith("manager_invite_restricted:"):
            allowed = reason.split(":", 1)[1].replace(",", ", ")
            await _show(update, context,
                        [f"Your manager role may only invite: {allowed}."],
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
            truck_num=truck,
        )
        link = make_invite_link(invite.code, context)
        text = format_invite_created(
            invite.code, role_display(invite_role), "",
            invite_link=link,
        )
        kb = invite_kb(link)
        await _show(update, context, [text], keyboard=kb)

    except Exception as e:
        logger.error(f"Invite error: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


@_require_registered
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect to the dashboard Users page.

    The bot's interactive per-user role/department/remove grid (one
    user at a time, navigated through inline buttons) was retired in
    favour of ``/admin/users`` on the dashboard, which gives sortable
    columns + bulk actions.
    """
    await reply_dashboard_redirect(
        update,
        title="👥 Users moved",
        body=(
            "Browse, search, and manage team members — including role "
            "and department changes — on the dashboard."
        ),
        path="/admin/users",
        label="Open Users",
    )


@_require_registered
async def cmd_setrole(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect to the dashboard Users page.

    The bot's ``/setrole <telegram_id> <role>`` shortcut was an
    operator power-tool that bypassed the user list.  Role changes
    now happen on ``/admin/users`` where the actor sees who they're
    editing and the rank-based guardrails are visualised.
    """
    await reply_dashboard_redirect(
        update,
        title="🔧 Role changes moved",
        body=(
            "Find the team member on the Users page and change "
            "their role from there."
        ),
        path="/admin/users",
        label="Open Users",
    )


@_require_registered
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect to the dashboard Users page.

    Removing a user via Telegram by typing their numeric id was both
    error-prone (wrong id → wrong removal) and a security smell (no
    confirmation step).  The dashboard's user list has a per-row
    Remove with a confirmation dialog.
    """
    await reply_dashboard_redirect(
        update,
        title="🗑 Remove user moved",
        body=(
            "Open the Users page, find the team member, and use the "
            "Remove action there.  A confirmation step prevents "
            "accidental removals."
        ),
        path="/admin/users",
        label="Open Users",
    )


@_require_registered
async def cmd_addcompany(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect to the dashboard Companies page.

    Connecting a company used to mean typing
    ``/addorg CODE:samsara_api_key`` directly into Telegram chat —
    which both lands a secret in the user's chat history and leaks
    it into Telegram's server logs.  The dashboard form sends the
    key over TLS to the API and stores it encrypted; it never
    appears in any chat surface.
    """
    await reply_dashboard_redirect(
        update,
        title="🏢 Add Company moved",
        body=(
            "Connect a Samsara company on the dashboard — the API "
            "key is sent securely instead of being typed into chat "
            "(which would leave it in your Telegram history)."
        ),
        path="/admin/companies",
        label="Open Companies",
    )


@_require_registered
async def cmd_removecompany(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect to the dashboard Companies page."""
    await reply_dashboard_redirect(
        update,
        title="🏢 Remove Company moved",
        body=(
            "Find the company on the dashboard's Companies page and "
            "remove it from there.  A confirmation step prevents "
            "accidental detach."
        ),
        path="/admin/companies",
        label="Open Companies",
    )


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
