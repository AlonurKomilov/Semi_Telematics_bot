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
from bot.keyboards import main_menu_kb, system_owner_kb, unregistered_kb, back_kb
from bot.helpers import _show
from bot.auth import _get_user


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
            new_user = await db.redeem_invite(code, tid)
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
    await _show(update, context,
                [format_welcome_unregistered(SUPPORT_CONTACT)],
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
        user = await db.create_user(
            telegram_id=tid,
            account_id=account.id,
            role=Role.OWNER,
            department="management",
        )
        logger.info(f"New account: '{company_name}' by TG user {tid}")

        text = format_register_success(company_name)
        await _show(update, context, [text], keyboard=back_kb())

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
    new_user = await db.redeem_invite(code, tid)

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
