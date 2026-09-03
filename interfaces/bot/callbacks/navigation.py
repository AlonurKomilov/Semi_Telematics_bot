"""Navigation callback handlers — main menu and sub-menus."""

from interfaces.bot.state import get_platform_db
from interfaces.bot.keyboards import (
    main_menu_kb, submenu_reports_kb, submenu_tools_kb,
    submenu_costs_kb, submenu_mgmt_kb,
)
from interfaces.bot.helpers import _show
from capabilities.localization.i18n import t
from capabilities.permissions.scope import unit_width
from capabilities.formatting import format_help


async def cmd_menu(update, context):
    query = update.callback_query
    await query.answer()
    user = context.user_data["_db_user"]
    companies = context.user_data.get("_companies", [])
    sys_owner = context.user_data.get("_sys_owner", False)
    account = await get_platform_db().get_account(user.account_id)
    company_codes = [o.code for o in companies]
    text = format_help(company_codes, user=user, account=account)
    if sys_owner:
        text += "\n\n  ⚙️ <i>System admin: /admin</i>"
    kb = main_menu_kb(user.role, company_codes,
                      wide=(await unit_width(user.account_id, user.role, user, "vehicles")) == "all")
    await _show(update, context, [text], keyboard=kb)


async def submenu_reports(update, context):
    query = update.callback_query
    await query.answer()
    user = context.user_data["_db_user"]
    companies = context.user_data.get("_companies", [])
    company_codes = [o.code for o in companies]
    await _show(update, context, [
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('reports_menu.header')}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('reports_menu.subtitle')}"
    ], keyboard=submenu_reports_kb(
        user.role, company_codes,
        wide=(await unit_width(user.account_id, user.role, user, "vehicles")) == "all"))


async def submenu_tools(update, context):
    query = update.callback_query
    await query.answer()
    user = context.user_data["_db_user"]
    await _show(update, context, [
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('tools_menu.header')}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('tools_menu.subtitle')}"
    ], keyboard=submenu_tools_kb(user.role))


async def submenu_costs(update, context):
    query = update.callback_query
    await query.answer()
    user = context.user_data["_db_user"]
    await _show(update, context, [
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('costs_menu.header')}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('costs_menu.subtitle')}"
    ], keyboard=submenu_costs_kb(user.role))


async def submenu_mgmt(update, context):
    query = update.callback_query
    await query.answer()
    user = context.user_data["_db_user"]
    companies = context.user_data.get("_companies", [])
    has_api = bool(companies)
    await _show(update, context, [
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('mgmt_menu.header')}\n"
        "━━━━━━━━━━━━━━━━━━━"
    ], keyboard=submenu_mgmt_kb(user.role, has_api))


def register(router):
    """Register navigation routes."""
    router.exact("cmd_menu", cmd_menu)
    router.exact("submenu_reports", submenu_reports)
    router.exact("submenu_tools", submenu_tools)
    router.exact("submenu_costs", submenu_costs)
    router.exact("submenu_mgmt", submenu_mgmt)
