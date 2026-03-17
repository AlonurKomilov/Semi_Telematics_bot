"""Registration and join commands."""

from telegram import Update
from telegram.ext import ContextTypes

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
            "  ℹ️  <b>HELP — System Admin</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n  /admin — Dashboard & analytics\n"
            "  /accounts — List all accounts\n"
            "  /broadcast — Message all users\n"
        ], keyboard=system_owner_kb())
        return

    if not user:
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ℹ️  <b>HELP</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n  /register <b>Company Name</b>\n"
            "  Create a new account.\n"
            "\n  /join <b>XXXX-XXXX</b>\n"
            "  Join with an invite code.\n"
        ], keyboard=unregistered_kb())
        return

    perms = get_permissions(user.role)
    r_display = role_display(user.role)
    lines = [
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  ℹ️  <b>HELP</b> — {r_display}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n  <b>Your available features:</b>\n"
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
        lines.append("\n  <b>📊 Fleet Reports</b>")
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
        lines.append("\n  <b>🛠 Tools</b>")
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
        lines.append("\n  <b>💰 Costs & Maintenance</b>")
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
        lines.append("\n  <b>👥 Management</b>")
        for item in mgmt_items:
            lines.append(f"  · {item}")

    lines.append("\n  Tap a button below or use /start")

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
                    "❌  Invalid, expired, or already-used invite link.\n"
                    "Ask your admin for a new one."
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
        text += "\n\n  ⚙️ <i>System admin: /admin</i>"
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
                    ["⚠️ You're already registered. Use /start to see your menu."],
                    keyboard=back_kb())
        return

    # System owner can't register as a customer to keep data clean.
    if sys_owner:
        await _show(update, context, [
            "⚠️ System owners use /admin to manage the platform.\n"
            "Customer registration is for business accounts only."
        ], keyboard=system_owner_kb())
        return

    if not context.args:
        await _show(update, context, [
            "ℹ️  Usage:\n\n"
            "  /register <b>Your Company Name</b>\n\n"
            "  Example:\n"
            "  /register Acme Logistics LLC"
        ])
        return

    company_name = " ".join(context.args)
    if len(company_name) < 2 or len(company_name) > 100:
        await _show(update, context,
                    ["⚠️ Company name must be 2–100 characters."])
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
        await _show(update, context, [f"❌ Registration failed: {e}"])


async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Join a company with invite code."""
    user, tid, sys_owner = await _get_user(update)
    if user:
        account = await db.get_account(user.account_id)
        await _show(update, context, [
            f"⚠️ You're already a member of <b>{account.name}</b>.\n"
            f"Use /start to see your menu."
        ], keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context, [
            "ℹ️  Usage:\n\n"
            "  /join <b>XXXX-XXXX</b>\n\n"
            "  Get the code from your admin."
        ])
        return

    code = context.args[0].strip().upper()
    tg_name = getattr(update.effective_user, "full_name", "") or ""
    new_user = await db.redeem_invite(code, tid, display_name=tg_name)

    if not new_user:
        await _show(update, context, [
            "❌  Invalid, expired, or already-used invite code.\n"
            "Ask your admin for a new one."
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
