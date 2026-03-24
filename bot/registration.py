"""Registration and join commands."""

from telegram import Update
from telegram.ext import ContextTypes
from bot.i18n import t

from database import Role
from permissions import role_display
from samsara_client import populate_company_display
from formatters import (
    format_help,
    format_welcome_unregistered,
    format_system_owner_welcome,
    format_register_success,
    format_join_success,
)

from bot.config import db, SUPPORT_CONTACT, logger, get_user_company_codes
from bot.keyboards import main_menu_kb, system_owner_kb, unregistered_kb, back_kb, onboarding_kb
from bot.helpers import _show
from bot.auth import _get_user


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show role-aware help — lists features the current user can access."""
    from permissions import get_permissions
    user, tid, sys_owner = await _get_user(update)

    if sys_owner and not user:
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  {t('help.sysadmin_title')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {t('help.cmd_admin')}\n"
            f"  {t('help.cmd_accounts')}\n"
            f"  {t('help.cmd_broadcast')}\n"
        ], keyboard=system_owner_kb())
        return

    if not user:
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  {t('help.unreg_title')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {t('help.unreg_register')}\n"
            f"\n  {t('help.unreg_join')}\n"
        ], keyboard=unregistered_kb())
        return

    perms = get_permissions(user.role)
    r_display = role_display(user.role)
    lines = [
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('help.user_title').replace('{account}', r_display)}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('help.features_label')}\n"
    ]

    # Fleet Reports
    report_items = []
    if perms.can_faults:
        report_items.append("🔧 Faults · 🚨 Critical · 🏥 Health · 📊 Efficiency · 🌡 Weather")
    if perms.can_fuel:
        report_items.append("⛽ Fuel & DEF levels")
    if perms.can_truck_all:
        report_items.append("🚛 Search any truck")
    elif perms.can_truck_own:
        report_items.append("🚛 View your truck")
    if report_items:
        lines.append(f"\n  {t('help.reports_label')}")
        for item in report_items:
            lines.append(f"  · {item}")

    # Tools
    tool_items = []
    if perms.can_scorecard_all or perms.can_scorecard_own:
        tool_items.append("🏆 Driver scorecards")
    if perms.can_location_map or perms.can_location_own:
        tool_items.append("🗺 Live fleet map")
    if perms.can_route_all or perms.can_route_own:
        tool_items.append("🛣 Route replay")
    if perms.can_geofence_all or perms.can_geofence_own:
        tool_items.append("📍 Geofences")
    if tool_items:
        lines.append(f"\n  {t('help.tools_label')}")
        for item in tool_items:
            lines.append(f"  · {item}")

    # Cost & Maintenance
    cost_items = []
    if perms.can_fuel_cost:
        cost_items.append("💰 Fuel cost tracker")
    if perms.can_cost_per_mile:
        cost_items.append("📊 Cost per mile")
    if perms.can_maintenance_all or perms.can_maintenance_own:
        cost_items.append("🔧 Maintenance scheduler")
    if cost_items:
        lines.append(f"\n  {t('help.costs_label')}")
        for item in cost_items:
            lines.append(f"  · {item}")

    # Alerts & Digest
    if perms.can_alerts_all or perms.can_alerts_own:
        lines.append("\n  · 🔔 Alerts (auto-notifications)")
    if perms.can_digest:
        lines.append("  · 📬 Daily/weekly digest")

    # Management
    mgmt_items = []
    if perms.can_invite:
        mgmt_items.append("✉️ Invite team members")
    if perms.can_manage_users:
        mgmt_items.append("👥 Manage team & roles")
    if perms.can_manage_companies:
        mgmt_items.append("📡 Manage companies")
    if perms.can_manage_account:
        mgmt_items.append("⚙️ Account settings")
    if mgmt_items:
        lines.append(f"\n  {t('help.mgmt_label')}")
        for item in mgmt_items:
            lines.append(f"  · {item}")

    lines.append(f"\n  {t('help.tap_or_start')}")

    company_codes = await get_user_company_codes(user.account_id)
    companies = await db.get_account_companies(user.account_id)
    populate_company_display(companies)
    kb = main_menu_kb(user.role, company_codes)
    await _show(update, context, ["\n".join(lines)], keyboard=kb)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point — detect user type and route accordingly.

    Flow:
      0. Deep-link ?start=join_CODE → auto-join
      1. System owner? → admin panel
      2. Existing customer user? → role-aware menu
      3. New user? → welcome + register/join options + support contact
    """
    user, tid, sys_owner = await _get_user(update)

    # ── 0. Deep-link auto-join: /start join_XXXX-XXXX ─────────
    if context.args and context.args[0].startswith("join_"):
        code = context.args[0][5:].strip().upper()
        if not user and code:
            tg_name = getattr(update.effective_user, "full_name", "") or ""
            new_user = await db.redeem_invite(code, tid, display_name=tg_name)
            if new_user:
                account = await db.get_account(new_user.account_id)
                r_display = role_display(new_user.role)
                text = format_join_success(account.name, r_display)
                company_codes = await get_user_company_codes(new_user.account_id)
                companies = await db.get_account_companies(new_user.account_id)
                populate_company_display(companies)
                kb = main_menu_kb(new_user.role, company_codes)
                await _show(update, context, [text], keyboard=kb)
                logger.info(f"Deep-link join: {tid} → '{account.name}' as {new_user.role.value}")
                return
            else:
                await _show(update, context, [
                    f"{t('join.invalid_link')}\n"
                    f"{t('join.ask_admin')}"
                ], keyboard=unregistered_kb())
                return
        elif user:
            # Already registered — just show menu
            pass  # fall through to normal flow

    # ── 1. System owner (platform admin) ──────────────────────
    if sys_owner and not user:
        await _show(update, context,
                    [format_system_owner_welcome()],
                    keyboard=system_owner_kb())
        return

    # System owner who is ALSO a customer (both roles) — show customer menu
    # but with admin hint
    if sys_owner and user:
        account = await db.get_account(user.account_id)
        company_codes = await get_user_company_codes(user.account_id)
        companies = await db.get_account_companies(user.account_id)
        populate_company_display(companies)
        text = format_help(company_codes, user=user, account=account)
        text += f"\n\n  {t('start.sysadmin_hint')}"
        kb = main_menu_kb(user.role, company_codes)
        await _show(update, context, [text], keyboard=kb)
        return

    # ── 2. Existing registered user ────────────────────────────
    if user:
        account = await db.get_account(user.account_id)
        company_codes = await get_user_company_codes(user.account_id)
        companies = await db.get_account_companies(user.account_id)
        populate_company_display(companies)
        text = format_help(company_codes, user=user, account=account)
        kb = main_menu_kb(user.role, company_codes)
        await _show(update, context, [text], keyboard=kb)
        return

    # ── 3. New / unknown user ──────────────────────────────────
    name = getattr(update.effective_user, "first_name", "") or ""
    await _show(update, context,
                [format_welcome_unregistered(SUPPORT_CONTACT, name)],
                keyboard=unregistered_kb())


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Register a new company account."""
    user, tid, sys_owner = await _get_user(update)
    if user:
        await _show(update, context,
                    [t('register.already_registered')],
                    keyboard=back_kb())
        return

    # System owner can't register as a customer to keep data clean.
    if sys_owner:
        await _show(update, context, [
            t('register.sysadmin_use_admin')
        ], keyboard=system_owner_kb())
        return

    if not context.args:
        await _show(update, context, [
            f"{t('register.usage')}\n\n"
            f"  {t('register.usage_example_cmd')}\n\n"
            f"  {t('register.usage_example_label')}\n"
            f"  {t('register.usage_example_value')}"
        ])
        return

    company_name = " ".join(context.args)
    if len(company_name) < 2 or len(company_name) > 100:
        await _show(update, context,
                    [t('register.name_invalid')])
        return

    try:
        account = await db.create_account(company_name)
        tg_name = getattr(update.effective_user, "full_name", "") or ""
        user = await db.create_user(
            telegram_id=tid,
            account_id=account.id,
            role=Role.OWNER,
            department="management",
            display_name=tg_name,
        )
        logger.info(f"New account: '{company_name}' by TG user {tid}")

        text = format_register_success(company_name)
        text += (
            "\n\n━━━━━━━━━━━━━━━━━━━\n"
            "  🚀  <b>QUICK START</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n  Complete these steps to get started:"
        )
        await _show(update, context, [text], keyboard=onboarding_kb())

    except Exception as e:
        logger.error(f"Registration error: {e}", exc_info=True)
        await _show(update, context, [f"{t('register.failed').replace('{error}', str(e))}"])


async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Join a company with invite code."""
    user, tid, sys_owner = await _get_user(update)
    if user:
        account = await db.get_account(user.account_id)
        await _show(update, context, [
            t('join.already_member').replace('{account}', account.name)
        ], keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context, [
            f"{t('join.usage')}\n\n"
            f"  {t('join.usage_cmd')}\n\n"
            f"  {t('join.usage_note')}"
        ])
        return

    code = context.args[0].strip().upper()
    tg_name = getattr(update.effective_user, "full_name", "") or ""
    new_user = await db.redeem_invite(code, tid, display_name=tg_name)

    if not new_user:
        await _show(update, context, [
            f"{t('join.invalid_code')}\n"
            f"{t('join.ask_admin')}"
        ])
        return

    account = await db.get_account(new_user.account_id)
    r_display = role_display(new_user.role)
    text = format_join_success(account.name, r_display)

    company_codes = await get_user_company_codes(new_user.account_id)
    companies = await db.get_account_companies(new_user.account_id)
    populate_company_display(companies)
    kb = main_menu_kb(new_user.role, company_codes)

    await _show(update, context, [text], keyboard=kb)
    logger.info(f"User {tid} joined '{account.name}' as {new_user.role.value}")
