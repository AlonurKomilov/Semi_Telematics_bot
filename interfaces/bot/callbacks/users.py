"""User-management callback handlers — user menu, role change, department,
alerts toggle, user removal."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from adapters.storage import Role
from capabilities.iam.permissions import can, role_display

from interfaces.bot.config import get_platform_db, get_tenant_db
from interfaces.bot.keyboards import back_kb
from interfaces.bot.helpers import _show
from capabilities.localization.i18n import t


async def _handle_user_menu(update, context, user, target_tid):
    """Show action buttons for a specific team member."""
    query = update.callback_query
    await query.answer()
    target_user = await get_platform_db().get_user_by_telegram_id(target_tid)
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
            InlineKeyboardButton(t('user_mgmt.role_fleet'), callback_data=f"setrole_{target_tid}_fleet"),
            InlineKeyboardButton(t('user_mgmt.role_safety'), callback_data=f"setrole_{target_tid}_safety"),
        ],
        [
            InlineKeyboardButton(t('user_mgmt.role_dispatcher'), callback_data=f"setrole_{target_tid}_dispatcher"),
            InlineKeyboardButton(t('user_mgmt.role_driver'), callback_data=f"setrole_{target_tid}_driver"),
        ],
        [InlineKeyboardButton(t('user_mgmt.cancel'), callback_data=f"usrmenu_{target_tid}")],
    ])
    target_user = await get_platform_db().get_user_by_telegram_id(target_tid)
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
    new_role_str = "_".join(parts[2:])  # handles multi-word roles
    await query.answer()

    try:
        new_role = Role.from_str(new_role_str)
    except ValueError:
        await _show(update, context, [t('user_mgmt.invalid_role')], keyboard=back_kb())
        return

    target_user = await get_platform_db().get_user_by_telegram_id(target_tid)
    if not target_user or target_user.account_id != user.account_id:
        await _show(update, context, [t('user_mgmt.user_not_found_short')], keyboard=back_kb())
        return

    if new_role == Role.OWNER and user.role != Role.OWNER:
        await _show(update, context, [t('access.cant_promote_owner')], keyboard=back_kb())
        return

    old_role = target_user.role
    await get_platform_db().update_user(target_user.id, role=new_role)
    tenant = await get_tenant_db(user.account_id)
    await tenant.add_audit_log(
        account_id=user.account_id, user_id=user.id,
        action="role_changed", target_type="user",
        target_id=str(target_user.id),
        details=f"{user.label} changed {target_user.label}: {role_display(old_role)} → {role_display(new_role)}",
    )
    await _show(update, context, [
        t('user_mgmt.role_updated').format(user=target_user.label, role=role_display(new_role))
    ], keyboard=back_kb())


# ── Callback entry points ───────────────────────────────────────

async def usrmenu_handler(update, context):
    data = update.callback_query.data
    target_tid = int(data[8:])
    user = context.user_data["_db_user"]
    await _handle_user_menu(update, context, user, target_tid)


async def usrrole_handler(update, context):
    data = update.callback_query.data
    target_tid = int(data[8:])
    user = context.user_data["_db_user"]
    await _handle_user_role_picker(update, context, user, target_tid)


async def setrole_handler(update, context):
    data = update.callback_query.data
    user = context.user_data["_db_user"]
    await _handle_set_role(update, context, user, data)


async def usrdept_handler(update, context):
    query = update.callback_query
    data = query.data
    target_tid = int(data[8:])
    user = context.user_data["_db_user"]
    await query.answer()
    if not can(user.role, "can_manage_users"):
        await query.answer(t("access.no_access"), show_alert=True)
        return
    context.user_data["_pending"] = "change_dept"
    context.user_data["_dept_tid"] = target_tid
    target_user = await get_platform_db().get_user_by_telegram_id(target_tid)
    target_label = target_user.label if target_user else str(target_tid)
    await _show(update, context, [
        f"{t('user_mgmt.dept_change_title').format(user=target_label)}\n\n"
        f"{t('user_mgmt.dept_current').format(dept=target_user.department if target_user else '—')}\n\n"
        f"{t('user_mgmt.dept_prompt')}"
    ], keyboard=InlineKeyboardMarkup([
        [InlineKeyboardButton(t('common.cancel_back'), callback_data=f"usrmenu_{target_tid}")],
    ]))


async def usralerts_handler(update, context):
    query = update.callback_query
    data = query.data
    target_tid = int(data[10:])
    user = context.user_data["_db_user"]
    await query.answer()
    if not can(user.role, "can_manage_users"):
        await query.answer(t("access.no_access"), show_alert=True)
        return
    target_user = await get_platform_db().get_user_by_telegram_id(target_tid)
    if target_user and target_user.account_id == user.account_id:
        new_state = not target_user.alerts_on
        await get_platform_db().update_user(target_user.id, alerts_on=new_state)
        icon = "🔔" if new_state else "🔕"
        label = t('common.on') if new_state else t('common.off')
        await _show(update, context, [
            t('user_mgmt.alerts_updated').format(user=target_user.label, icon=icon, status=label)
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('menu.back'), callback_data=f"usrmenu_{target_tid}")],
        ]))
    else:
        await _show(update, context, [t('user_mgmt.user_not_found_short')], keyboard=back_kb())


async def usrremove_handler(update, context):
    query = update.callback_query
    data = query.data
    target_tid = int(data[10:])
    await query.answer()
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t('user_mgmt.remove_yes'), callback_data=f"usrremoveconfirm_{target_tid}"),
            InlineKeyboardButton(t('common.cancel_back'), callback_data="cmd_users"),
        ],
    ])
    target_user = await get_platform_db().get_user_by_telegram_id(target_tid)
    target_label = target_user.label if target_user else str(target_tid)
    await _show(update, context, [
        f"{t('user_mgmt.remove_confirm_title').format(user=target_label)}\n\n"
        f"{t('user_mgmt.remove_confirm_msg')}"
    ], keyboard=kb)


async def usrremoveconfirm_handler(update, context):
    query = update.callback_query
    data = query.data
    target_tid = int(data[17:])
    user = context.user_data["_db_user"]
    await query.answer()
    if target_tid == user.telegram_id:
        await _show(update, context, [t('access.cant_self_remove')], keyboard=back_kb())
        return
    target_user = await get_platform_db().get_user_by_telegram_id(target_tid)
    target_label = target_user.label if target_user else str(target_tid)
    if target_user and target_user.account_id == user.account_id:
        await get_platform_db().remove_user(target_user.id)
        tenant = await get_tenant_db(user.account_id)
        await tenant.add_audit_log(
            account_id=user.account_id, user_id=user.id,
            action="user_removed", target_type="user",
            target_id=str(target_user.id),
            details=f"{user.label} removed user {target_label}",
        )
    await _show(update, context, [t('user_mgmt.user_removed').format(user=target_label)], keyboard=back_kb())


def register(router):
    """Register user-management routes."""
    router.prefix("usrmenu_", usrmenu_handler)
    router.prefix("usrrole_", usrrole_handler)
    router.prefix("setrole_", setrole_handler)
    router.prefix("usrdept_", usrdept_handler)
    router.prefix("usralerts_", usralerts_handler)
    router.prefix("usrremoveconfirm_", usrremoveconfirm_handler)
    router.prefix("usrremove_", usrremove_handler)
