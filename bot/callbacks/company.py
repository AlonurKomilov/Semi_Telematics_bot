"""Company-management callback handlers — company detail, API status, rename,
change key, remove, add-company wizard, and invite flows."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from permissions import can, role_display
from database import Role
from formatters import format_invite_created

from bot.config import get_platform_db, get_tenant_db, invalidate_client
from bot.keyboards import back_kb, skip_name_kb, invite_kb
from bot.helpers import _show, _safe_error, make_invite_link
from bot.i18n import t
from bot.management import cmd_addcompany


# ── Company detail ───────────────────────────────────────────────

async def _handle_company_menu(update, context, user, code):
    """Show detail view for a specific company with actions."""
    query = update.callback_query
    await query.answer()

    tenant = await get_tenant_db(user.account_id)
    company = await tenant.get_company_by_code(user.account_id, code)
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

    tenant = await get_tenant_db(user.account_id)
    company = await tenant.get_company_by_code(user.account_id, code)
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


# ── Invite ───────────────────────────────────────────────────────

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
            InlineKeyboardButton(t('user_mgmt.role_fleet'), callback_data="inv_fleet"),
        ],
        [
            InlineKeyboardButton(t('user_mgmt.role_safety'), callback_data="inv_safety"),
            InlineKeyboardButton(t('user_mgmt.role_dispatcher'), callback_data="inv_dispatcher"),
        ],
        [
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
    role_str = data[4:]  # admin, fleet, safety, dispatcher, driver
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
        invite = await get_platform_db().create_invite(
            account_id=user.account_id,
            created_by=user.id,
            role=invite_role,
            department="general",
        )
        link = make_invite_link(invite.code, context)
        text = format_invite_created(
            invite.code, role_display(invite_role), "general",
            invite_link=link,
        )
        kb = invite_kb(link)
        await _show(update, context, [text], keyboard=kb)
    except Exception as e:
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


# ── Callback entry points ───────────────────────────────────────

async def comenu_handler(update, context):
    query = update.callback_query
    data = query.data
    code = data[7:]  # comenu_CODE
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_account"):
        await query.answer(t("access.no_access"), show_alert=True)
        return
    await _handle_company_menu(update, context, user, code)


async def co_api_status_handler(update, context):
    query = update.callback_query
    data = query.data
    code = data[14:]  # co_api_status_CODE
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_account"):
        await query.answer(t("access.no_access"), show_alert=True)
        return
    await _handle_co_api_status(update, context, user, code)


async def co_chkey_handler(update, context):
    query = update.callback_query
    data = query.data
    code = data[9:]  # co_chkey_CODE
    user = context.user_data["_db_user"]
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


async def co_rename_handler(update, context):
    query = update.callback_query
    data = query.data
    code = data[10:]  # co_rename_CODE
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_companies"):
        await query.answer(t("access.no_access"), show_alert=True)
        return
    await query.answer()
    context.user_data["_pending"] = "rename_company"
    context.user_data["_rename_code"] = code
    tenant = await get_tenant_db(user.account_id)
    company = await tenant.get_company_by_code(user.account_id, code)
    current_name = company.display_name if company else code
    await _show(update, context, [
        t('company.rename_title', code=code) + "\n\n"
        + t('company.rename_current', name=current_name) + "\n\n"
        + t('company.rename_prompt')
    ], keyboard=InlineKeyboardMarkup([
        [InlineKeyboardButton(t('common.cancel_back'), callback_data=f"comenu_{code}")],
    ]))


async def rmco_handler(update, context):
    query = update.callback_query
    data = query.data
    code = data[5:]  # rmco_CODE
    user = context.user_data["_db_user"]
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


async def addcompany_prompt_handler(update, context):
    query = update.callback_query
    await query.answer()
    user = context.user_data["_db_user"]
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


async def addcompany_skip_name_handler(update, context):
    query = update.callback_query
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


async def integrate_guide_handler(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["_pending"] = "addcompany_code"
    context.user_data.pop("_addcompany", None)
    guide_text = t('samsara.connect_guide')
    await _show(update, context, [guide_text], keyboard=back_kb())


async def no_api_info_handler(update, context):
    query = update.callback_query
    await query.answer()
    await _show(update, context, [t('samsara.not_connected_info')], keyboard=back_kb())


async def invite_pick_handler(update, context):
    user = context.user_data["_db_user"]
    await _handle_invite_pick(update, context, user)


async def inv_handler(update, context):
    data = update.callback_query.data
    user = context.user_data["_db_user"]
    await _handle_invite_create(update, context, user, data)


def register(router):
    """Register company-management routes."""
    # Company detail & actions — more specific prefixes first
    router.prefix("co_api_status_", co_api_status_handler)
    router.prefix("co_chkey_", co_chkey_handler)
    router.prefix("co_rename_", co_rename_handler)
    router.prefix("comenu_", comenu_handler)
    router.prefix("rmco_", rmco_handler)

    # Add company wizard
    router.exact("cmd_addcompany_prompt", addcompany_prompt_handler)
    router.exact("addcompany_skip_name", addcompany_skip_name_handler)
    router.exact("cmd_integrate_guide", integrate_guide_handler)
    router.exact("cmd_no_api_info", no_api_info_handler)

    # Invite
    router.exact("cmd_invite_pick", invite_pick_handler)
    router.prefix("inv_", inv_handler)
