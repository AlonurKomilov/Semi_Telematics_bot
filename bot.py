#!/usr/bin/env python3
"""
Semi Telematics Bot — multi-tenant, role-based, single-window Telegram UI.

v4 — Database-driven multi-tenant architecture:
  • Any company can /register and create an isolated account
  • Owners invite team members via /invite → /join codes
  • Role-based menus and feature gating (owner → driver)
  • Per-account Samsara org isolation
  • Future-proof for PostgreSQL migration (Option B)
"""

import os
import logging
from datetime import datetime as _dt, timezone as _tz
from dotenv import load_dotenv

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import Database, Role, seed_from_env
from permissions import (
    get_permissions,
    can,
    role_display,
    role_emoji,
    visible_main_buttons,
    can_access_org_submenu,
    is_system_owner,
    SYSTEM_OWNER_IDS,
)
from samsara_client import (
    SamsaraClient,
    MultiOrgClient,
    ORG_DISPLAY,
    populate_org_display,
    build_multi_org_client,
)
from formatters import (
    format_truck_detail,
    format_low_fuel,
    format_help,
    format_new_fault_alert,
    format_truck_picker,
    format_welcome_unregistered,
    format_register_success,
    format_join_success,
    format_account_info,
    format_invite_created,
    format_users_list,
    format_org_added,
    format_system_owner_welcome,
    format_admin_dashboard,
    format_admin_account_detail,
    format_admin_accounts_list,
)
from pdf_report import (
    generate_fault_report_pdf,
    generate_critical_report_pdf,
    compute_stats,
)

# ── Configuration ────────────────────────────────────────────────

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SAMSARA_BASE_URL = os.getenv("SAMSARA_BASE_URL", "https://api.samsara.com")
ALERT_INTERVAL = int(os.getenv("ALERT_CHECK_INTERVAL_MINUTES", "30"))
FUEL_THRESHOLD = int(os.getenv("FUEL_LOW_THRESHOLD_PERCENT", "20"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/bot.db")
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Globals ──────────────────────────────────────────────────────

db = Database(DATABASE_PATH)

# Cache: account_id → MultiOrgClient (rebuilt on org changes)
_client_cache: dict[int, MultiOrgClient] = {}

_known_faults: dict[str, set[str]] = {}   # "acct:ORG:vid" → set(codes)
_active_messages: dict[int, list[int]] = {}   # chat_id → [msg_ids]


# ═══════════════════════════════════════════════════════════════════
# CLIENT CACHE — per-account Samsara clients
# ═══════════════════════════════════════════════════════════════════

async def get_client(account_id: int) -> MultiOrgClient:
    """Get or build a MultiOrgClient for an account."""
    if account_id in _client_cache:
        return _client_cache[account_id]
    orgs = await db.get_account_orgs(account_id)
    client = build_multi_org_client(orgs, SAMSARA_BASE_URL)
    _client_cache[account_id] = client
    return client


async def invalidate_client(account_id: int):
    """Drop cached client — call after adding/removing orgs."""
    old = _client_cache.pop(account_id, None)
    if old:
        await old.close()


async def get_user_org_codes(account_id: int) -> list[str]:
    """Get sorted org codes for an account."""
    orgs = await db.get_account_orgs(account_id)
    return [o.code for o in orgs]


# ═══════════════════════════════════════════════════════════════════
# KEYBOARDS — role-aware
# ═══════════════════════════════════════════════════════════════════

def main_menu_kb(role: Role, org_codes: list[str] | None = None) -> InlineKeyboardMarkup:
    """Build role-appropriate main menu."""
    perms = get_permissions(role)
    row1 = []
    row2 = []

    if perms.can_faults:
        row1.append(InlineKeyboardButton("🔧 Faults (PDF)", callback_data="cmd_faults"))
    if perms.can_critical:
        row1.append(InlineKeyboardButton("🚨 Critical (PDF)", callback_data="cmd_critical"))
    if perms.can_fuel:
        row2.append(InlineKeyboardButton("⛽ Fuel", callback_data="cmd_fuel"))
    if perms.can_alerts_all or perms.can_alerts_own:
        row2.append(InlineKeyboardButton("🔔 Alerts", callback_data="cmd_alerts"))

    rows = []
    if row1:
        rows.append(row1)
    if row2:
        rows.append(row2)

    # Per-org buttons (only when >1 org and role can filter)
    if org_codes and len(org_codes) > 1 and can_access_org_submenu(role):
        org_row = [
            InlineKeyboardButton(code, callback_data=f"org_{code}")
            for code in org_codes
        ]
        rows.append(org_row)

    # Management buttons
    mgmt = []
    if perms.can_manage_account:
        mgmt.append(InlineKeyboardButton("⚙️ Account", callback_data="cmd_account"))
    if perms.can_manage_users:
        mgmt.append(InlineKeyboardButton("👥 Users", callback_data="cmd_users"))
    if mgmt:
        rows.append(mgmt)

    # Driver: show truck button
    if role == Role.DRIVER:
        rows.append([InlineKeyboardButton("🚛 My Truck", callback_data="cmd_mytruck")])

    return InlineKeyboardMarkup(rows)


def org_menu_kb(org: str) -> InlineKeyboardMarkup:
    """Sub-menu for a specific org."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🔧 {org} Faults", callback_data=f"orgfaults_{org}"),
            InlineKeyboardButton(f"🚨 {org} Critical", callback_data=f"orgcritical_{org}"),
        ],
        [
            InlineKeyboardButton(f"⛽ {org} Fuel", callback_data=f"orgfuel_{org}"),
        ],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def truck_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔧 All Faults", callback_data="cmd_faults"),
            InlineKeyboardButton("🚨 Critical", callback_data="cmd_critical"),
        ],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def truck_picker_kb(matches: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for v in matches:
        org = v.get("_org", "?")
        name = v["name"]
        label = f"#{name} — {ORG_DISPLAY.get(org, org)} ({org})"
        rows.append([InlineKeyboardButton(label, callback_data=f"orgtruck_{org}_{name}")])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def unregistered_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Register Company", callback_data="cmd_register_help")],
        [InlineKeyboardButton("🔑 Join with Code", callback_data="cmd_join_help")],
    ])


def system_owner_kb() -> InlineKeyboardMarkup:
    """System owner admin panel keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Dashboard", callback_data="sys_dashboard"),
            InlineKeyboardButton("📋 Accounts", callback_data="sys_accounts"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="sys_dashboard"),
        ],
    ])


# ═══════════════════════════════════════════════════════════════════
# AUTH — database-driven
# ═══════════════════════════════════════════════════════════════════

async def _get_user(update: Update):
    """Look up the calling user. Returns (user_or_None, telegram_id, is_sys_owner).

    Three outcomes:
      1. System owner (env-driven)  → (None, tid, True)
      2. Registered customer user   → (User, tid, False)
      3. Unknown / new user         → (None, tid, False)
    """
    if update.callback_query:
        tid = update.callback_query.from_user.id
    elif update.message:
        tid = update.message.from_user.id
    else:
        tid = update.effective_user.id if update.effective_user else 0

    sys_owner = is_system_owner(tid)
    user = await db.get_user_by_telegram_id(tid)
    return user, tid, sys_owner


def _require_registered(func):
    """Decorator: registered users only. Unregistered → welcome screen.
    System owners are NOT customers — they get redirected to /admin.
    Also checks that the user's account is still active.
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs):
        user, tid, sys_owner = await _get_user(update)

        # System owner trying to use customer features → hint to /admin
        if sys_owner and not user:
            await _show(update, context,
                        [format_system_owner_welcome()],
                        keyboard=system_owner_kb())
            return

        if not user:
            await _show(update, context,
                        [format_welcome_unregistered(SUPPORT_CONTACT)],
                        keyboard=unregistered_kb())
            return

        # Check account still active
        account = await db.get_account(user.account_id)
        if not account or not account.is_active:
            msg = "⛔ Your account has been disabled."
            if SUPPORT_CONTACT:
                msg += f"\nContact support: {SUPPORT_CONTACT}"
            await _show(update, context, [msg])
            return

        context.user_data["_db_user"] = user
        return await func(update, context, **kwargs)
    return wrapper


def _require_permission(feature: str):
    """Decorator factory: check a specific permission flag."""
    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs):
            user = context.user_data.get("_db_user")
            if not user:
                user, _, sys_owner = await _get_user(update)
                if sys_owner and not user:
                    await _show(update, context,
                                [format_system_owner_welcome()],
                                keyboard=system_owner_kb())
                    return
            if not user:
                await _show(update, context,
                            [format_welcome_unregistered()],
                            keyboard=unregistered_kb())
                return
            if not can(user.role, feature):
                if update.callback_query:
                    await update.callback_query.answer(
                        "⛔ You don't have access to this feature.",
                        show_alert=True,
                    )
                else:
                    await _show(update, context,
                                ["⛔ You don't have access to this feature."],
                                keyboard=back_kb())
                return
            context.user_data["_db_user"] = user
            return await func(update, context, **kwargs)
        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════════
# SINGLE-WINDOW ENGINE
# ═══════════════════════════════════════════════════════════════════

async def _delete_old_messages(chat_id: int, bot):
    msg_ids = _active_messages.pop(chat_id, [])
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except BadRequest:
            pass


async def _show(update: Update, context: ContextTypes.DEFAULT_TYPE,
                texts: list[str], keyboard=None):
    query = update.callback_query
    bot = context.bot
    chat_id = query.message.chat.id if query else update.effective_chat.id

    if query and len(texts) == 1:
        try:
            await query.edit_message_text(
                text=texts[0],
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            _active_messages[chat_id] = [query.message.message_id]
            return
        except BadRequest as e:
            logger.debug(f"Edit failed, falling back: {e}")

    await _delete_old_messages(chat_id, bot)
    sent_ids = []
    for i, text in enumerate(texts):
        kb = keyboard if i == len(texts) - 1 else None
        msg = await bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        sent_ids.append(msg.message_id)
    _active_messages[chat_id] = sent_ids


async def _show_loading(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    query = update.callback_query
    if query:
        try:
            await query.answer()
            await query.edit_message_text(text=text, parse_mode=ParseMode.HTML)
            return
        except BadRequest:
            pass
    if update.message:
        chat_id = update.effective_chat.id
        msg = await update.message.reply_text(text)
        _active_messages[chat_id] = [msg.message_id]


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _org_line(breakdown: dict) -> str:
    parts = []
    for code in sorted(breakdown.keys()):
        info = breakdown[code]
        parts.append(f"{code}: {info['total']} trucks")
    return "  ·  ".join(parts)


async def _user_menu_kb(user) -> InlineKeyboardMarkup:
    """Build the role-aware main menu for this user."""
    org_codes = await get_user_org_codes(user.account_id)
    return main_menu_kb(user.role, org_codes)


# ═══════════════════════════════════════════════════════════════════
# REGISTRATION / JOIN COMMANDS
# ═══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point — detect user type and route accordingly.

    Flow:
      1. System owner? → admin panel
      2. Existing customer user? → role-aware menu
      3. New user? → welcome + register/join options + support contact
    """
    user, tid, sys_owner = await _get_user(update)

    # ── 1. System owner (platform admin) ──────────────────────
    if sys_owner and not user:
        await _show(update, context,
                    [format_system_owner_welcome()],
                    keyboard=system_owner_kb())
        return

    # System owner who is ALSO a customer (both roles) — show customer menu
    # but with admin hint
    if sys_owner and user:
        # Show normal customer menu + hint
        account = await db.get_account(user.account_id)
        org_codes = await get_user_org_codes(user.account_id)
        orgs = await db.get_account_orgs(user.account_id)
        populate_org_display(orgs)
        text = format_help(org_codes, user=user, account=account)
        text += "\n\n  ⚙️ <i>System admin: /admin</i>"
        kb = main_menu_kb(user.role, org_codes)
        await _show(update, context, [text], keyboard=kb)
        return

    # ── 2. Existing registered user ────────────────────────────
    if user:
        account = await db.get_account(user.account_id)
        org_codes = await get_user_org_codes(user.account_id)
        orgs = await db.get_account_orgs(user.account_id)
        populate_org_display(orgs)
        text = format_help(org_codes, user=user, account=account)
        kb = main_menu_kb(user.role, org_codes)
        await _show(update, context, [text], keyboard=kb)
        return

    # ── 3. New / unknown user ──────────────────────────────────
    await _show(update, context,
                [format_welcome_unregistered(SUPPORT_CONTACT)],
                keyboard=unregistered_kb())


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Register a new company account: /register Company Name"""
    user, tid, sys_owner = await _get_user(update)
    if user:
        await _show(update, context,
                    ["⚠️ You're already registered. Use /start to see your menu."],
                    keyboard=back_kb())
        return

    # System owner can't register as a customer to keep data clean.
    # They observe the platform via /admin only.
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
    """Join a company with invite code: /join XXXX-XXXX"""
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

    org_codes = await get_user_org_codes(new_user.account_id)
    orgs = await db.get_account_orgs(new_user.account_id)
    populate_org_display(orgs)
    kb = main_menu_kb(new_user.role, org_codes)

    await _show(update, context, [text], keyboard=kb)
    logger.info(f"User {tid} joined '{account.name}' as {new_user.role.value}")


# ═══════════════════════════════════════════════════════════════════
# FLEET COMMANDS — role-gated
# ═══════════════════════════════════════════════════════════════════

@_require_registered
async def cmd_faults(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     org: str | None = None):
    """Generate fault report PDF."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    # Populate ORG_DISPLAY for this account
    orgs = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs)

    samsara = await get_client(user.account_id)
    org_label = ORG_DISPLAY.get(org, "All Companies") if org else "All Companies"
    await _show_loading(update, context,
                        f"⏳ Generating fault report ({org_label})…")
    try:
        faulted, total, breakdown = await samsara.get_vehicles_with_faults(org=org)
        stats = compute_stats(faulted, total)

        pdf_buf = generate_fault_report_pdf(
            faulted, total,
            org_breakdown=breakdown,
            org_filter=org,
        )

        query = update.callback_query
        chat_id = query.message.chat.id if query else update.effective_chat.id
        await _delete_old_messages(chat_id, context.bot)

        ts = _dt.now(_tz.utc).strftime("%Y-%m-%d_%H%M")
        prefix = f"{org}_" if org else ""

        org_info = ""
        if not org and len(breakdown) > 1:
            org_info = f"\n  🏢  {_org_line(breakdown)}"

        caption = (
            "┌─────────────────────────┐\n"
            "  🔧  <b>FAULT CODE REPORT</b>\n"
            "└─────────────────────────┘\n"
            f"\n  📚  <b>{stats['total']}</b> trucks scanned"
            f"{f' ({len(breakdown)} companies)' if len(breakdown) > 1 and not org else ''}\n"
            f"  🔴  <b>{stats['faulted']}</b> with faults  ·  "
            f"✅ {stats['clean']} clean\n"
            f"  📄  <b>{stats['total_dtcs']}</b> total fault codes\n"
            f"\n  🛑 {stats['stop']} STOP  ·  "
            f"♨️ {stats['emissions']} EMIS  ·  "
            f"⚠️ {stats['warning']} WARN  ·  "
            f"🔧 {stats['minor']} MINOR"
            f"{org_info}"
        )

        kb = await _user_menu_kb(user)
        msg = await context.bot.send_document(
            chat_id=chat_id,
            document=pdf_buf,
            filename=f"{prefix}Fault_Report_{ts}.pdf",
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        _active_messages[chat_id] = [msg.message_id]

    except Exception as e:
        logger.error(f"Error in /faults: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_truck(update: Update, context: ContextTypes.DEFAULT_TYPE,
                    truck_name: str | None = None, org: str | None = None):
    """Look up a single truck."""
    user = context.user_data["_db_user"]

    # Parse truck name from args or callback
    if truck_name is None:
        if context.args:
            truck_name = " ".join(context.args)
        elif update.callback_query:
            data = update.callback_query.data
            if data.startswith("truck_"):
                truck_name = data.replace("truck_", "")
            elif data.startswith("orgtruck_"):
                parts = data.split("_", 2)
                if len(parts) == 3:
                    org = parts[1]
                    truck_name = parts[2]

    # Driver role: force own truck
    if user.role == Role.DRIVER:
        if not user.truck_num:
            await _show(update, context,
                        ["⚠️ No truck assigned. Ask your admin to set your truck number."],
                        keyboard=back_kb())
            return
        truck_name = user.truck_num
    elif not can(user.role, "can_truck_all"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    if not truck_name:
        await _show(update, context,
                    ["ℹ️  Type  /truck <b>NUMBER</b>\n\nExample:  /truck 134"],
                    keyboard=back_kb())
        return

    # Populate ORG_DISPLAY
    orgs = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs)
    org_codes = [o.code for o in orgs]

    await _show_loading(update, context, f"⏳ Looking up #{truck_name}…")

    try:
        samsara = await get_client(user.account_id)
        matches = await samsara.get_vehicle_detail(truck_name, org=org)

        if not matches:
            await _show(update, context,
                        [f"❌  Truck <b>#{truck_name}</b> not found."],
                        keyboard=back_kb())
            return

        if len(matches) == 1:
            vehicle = matches[0]
            messages = format_truck_detail(vehicle, show_org=len(org_codes) > 1)
            await _show(update, context, messages, keyboard=truck_kb())
        else:
            text = format_truck_picker(truck_name, matches)
            await _show(update, context, [text],
                        keyboard=truck_picker_kb(matches))

    except Exception as e:
        logger.error(f"Error in /truck: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_critical(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       org: str | None = None):
    """Generate critical fault report PDF."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_critical"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    orgs = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs)

    samsara = await get_client(user.account_id)
    org_label = ORG_DISPLAY.get(org, "All Companies") if org else "All Companies"
    await _show_loading(update, context,
                        f"⏳ Checking critical faults ({org_label})…")
    try:
        critical = await samsara.get_critical_faults(org=org)
        _, total, breakdown = await samsara.get_vehicles_with_faults(org=org)

        if not critical:
            kb = await _user_menu_kb(user)
            await _show(update, context, [
                "┌─────────────────────────┐\n"
                "  ✅  <b>ALL CLEAR</b>\n"
                "└─────────────────────────┘\n"
                "\n"
                "  No STOP, PROTECT, or\n"
                "  EMISSIONS lights active.\n"
                "\n"
                "  All trucks running clean 👍"
            ], keyboard=kb)
            return

        pdf_buf = generate_critical_report_pdf(
            critical, total,
            org_breakdown=breakdown,
            org_filter=org,
        )

        query = update.callback_query
        chat_id = query.message.chat.id if query else update.effective_chat.id
        await _delete_old_messages(chat_id, context.bot)

        ts = _dt.now(_tz.utc).strftime("%Y-%m-%d_%H%M")
        prefix = f"{org}_" if org else ""

        stop = sum(1 for v in critical if v.get('_lights', {}).get('stopIsOn'))
        emis = sum(1 for v in critical if v.get('_lights', {}).get('emissionsIsOn'))
        total_dtcs = sum(len(v.get('_dtcs', [])) for v in critical)

        org_info = ""
        if not org and len(breakdown) > 1:
            org_info = f"\n  🏢  {_org_line(breakdown)}"

        caption = (
            "┌─────────────────────────┐\n"
            "  🚨  <b>CRITICAL FAULTS</b>\n"
            "└─────────────────────────┘\n"
            f"\n  🚛  <b>{len(critical)}</b> trucks need attention\n"
            f"  📄  <b>{total_dtcs}</b> fault codes\n"
            f"\n  🛑 {stop} STOP  ·  ♨️ {emis} EMISSIONS"
            f"{org_info}"
        )

        kb = await _user_menu_kb(user)
        msg = await context.bot.send_document(
            chat_id=chat_id,
            document=pdf_buf,
            filename=f"{prefix}Critical_Report_{ts}.pdf",
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        _active_messages[chat_id] = [msg.message_id]

    except Exception as e:
        logger.error(f"Error in /critical: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_fuel(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   org: str | None = None):
    """Show low-fuel vehicles."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_fuel"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    orgs = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs)
    org_codes = [o.code for o in orgs]

    samsara = await get_client(user.account_id)
    org_label = ORG_DISPLAY.get(org, "All Companies") if org else "All Companies"
    await _show_loading(update, context,
                        f"⏳ Checking fuel levels ({org_label})…")
    try:
        low = await samsara.get_low_fuel_vehicles(FUEL_THRESHOLD, org=org)
        text = format_low_fuel(low, FUEL_THRESHOLD,
                               show_org=len(org_codes) > 1 and not org)
        await _show(update, context, [text], keyboard=back_kb())

    except Exception as e:
        logger.error(f"Error in /fuel: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle fault alerts on/off."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_alerts_all") and not can(user.role, "can_alerts_own"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    new_state = await db.toggle_alerts(user.telegram_id)
    org_codes = await get_user_org_codes(user.account_id)

    if new_state:
        text = (
            "┌─────────────────────────┐\n"
            "  🔔  <b>ALERTS ON</b>\n"
            "└─────────────────────────┘\n"
            "\n"
            f"  Checking every {ALERT_INTERVAL} min\n"
            f"  across {len(org_codes)} organization{'s' if len(org_codes) != 1 else ''}.\n"
            "  You'll get notified of\n"
            "  new critical faults.\n"
            "\n"
            "  Tap 🔔 Alerts to disable."
        )
    else:
        text = (
            "┌─────────────────────────┐\n"
            "  🔕  <b>ALERTS OFF</b>\n"
            "└─────────────────────────┘\n"
            "\n"
            "  Auto-notifications disabled.\n"
            "  Tap 🔔 Alerts to re-enable."
        )

    kb = main_menu_kb(user.role, org_codes)
    await _show(update, context, [text], keyboard=kb)


# ═══════════════════════════════════════════════════════════════════
# MANAGEMENT COMMANDS
# ═══════════════════════════════════════════════════════════════════

@_require_registered
async def cmd_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show account overview."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_account") and not can(user.role, "can_manage_users"):
        # Allow fleet managers to at least see basic info
        pass

    account = await db.get_account(user.account_id)
    orgs = await db.get_account_orgs(user.account_id)
    users = await db.list_account_users(user.account_id)

    text = format_account_info(account, orgs, users, user)
    await _show(update, context, [text], keyboard=back_kb())


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
        text = format_invite_created(
            invite.code, role_display(invite_role), dept,
        )
        await _show(update, context, [text], keyboard=back_kb())

    except Exception as e:
        logger.error(f"Invite error: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List team members."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_users"):
        await _show(update, context,
                    ["⛔ Only owners and admins can manage users."],
                    keyboard=back_kb())
        return

    account = await db.get_account(user.account_id)
    users = await db.list_account_users(user.account_id)
    text = format_users_list(users, account.name)
    await _show(update, context, [text], keyboard=back_kb())


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
    """Connect a Samsara org: /addorg CODE:api_key [Display Name]"""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_orgs"):
        await _show(update, context,
                    ["⛔ Only owners can manage organizations."],
                    keyboard=back_kb())
        return

    if not context.args:
        await _show(update, context, [
            "ℹ️  <b>Add Organization:</b>\n\n"
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
                    [f"⚠️ Organization <b>{code}</b> already exists in your account."],
                    keyboard=back_kb())
        return

    try:
        # Test the API key by fetching vehicles
        test_client = SamsaraClient(api_key, SAMSARA_BASE_URL, active_days=30)
        await _show_loading(update, context, f"⏳ Testing connection to {code}…")
        try:
            vehicles = await test_client.get_vehicles()
            truck_count = len(vehicles)
        except Exception:
            truck_count = None
        finally:
            await test_client.close()

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

        text = format_org_added(code, display_name, truck_count)
        kb = await _user_menu_kb(user)
        await _show(update, context, [text], keyboard=kb)

        logger.info(f"Org {code} added to account {user.account_id}")

    except Exception as e:
        logger.error(f"Add org error: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_removeorg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a Samsara org: /removeorg CODE"""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_orgs"):
        await _show(update, context,
                    ["⛔ Only owners can manage organizations."],
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
                    [f"❌ Organization <b>{code}</b> not found."],
                    keyboard=back_kb())
        return

    await db.remove_organization(org.id)
    await invalidate_client(user.account_id)

    orgs = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs)

    await _show(update, context,
                [f"✅ Organization <b>{code}</b> removed."],
                keyboard=back_kb())


# ═══════════════════════════════════════════════════════════════════
# SYSTEM OWNER COMMANDS (env-driven, NOT customers)
# ═══════════════════════════════════════════════════════════════════

def _require_system_owner(func):
    """Decorator: system owner only (checked via env SYSTEM_OWNER_IDS)."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs):
        _, tid, sys_owner = await _get_user(update)
        if not sys_owner:
            if update.callback_query:
                await update.callback_query.answer("⛔ System admin only", show_alert=True)
            else:
                await _show(update, context,
                            ["⛔ This command is for system administrators only."],
                            keyboard=back_kb())
            return
        return await func(update, context, **kwargs)
    return wrapper


@_require_system_owner
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """System owner dashboard — bot-wide analytics."""
    await _show_loading(update, context, "⏳ Loading admin dashboard…")
    try:
        stats = await db.get_system_stats()
        text = format_admin_dashboard(stats)
        await _show(update, context, [text], keyboard=system_owner_kb())
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"])


@_require_system_owner
async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all accounts (system owner)."""
    try:
        accounts = await db.list_accounts(active_only=False)
        text = format_admin_accounts_list(accounts)
        await _show(update, context, [text], keyboard=system_owner_kb())
    except Exception as e:
        logger.error(f"Accounts list error: {e}", exc_info=True)
        await _show(update, context, [f"❌ Error: {e}"])


@_require_system_owner
async def cmd_sysaccount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View detailed info about a specific account: /sysaccount <id>"""
    if not context.args:
        await _show(update, context, [
            "ℹ️  Usage:  /sysaccount <b>ID</b>\n\n"
            "  Example:  /sysaccount 1"
        ], keyboard=system_owner_kb())
        return

    try:
        acct_id = int(context.args[0])
    except ValueError:
        await _show(update, context,
                    ["❌ Invalid account ID."],
                    keyboard=system_owner_kb())
        return

    account = await db.get_account(acct_id)
    if not account:
        await _show(update, context,
                    [f"❌ Account #{acct_id} not found."],
                    keyboard=system_owner_kb())
        return

    orgs = await db.get_account_orgs(acct_id, active_only=False)
    users = await db.list_account_users(acct_id)
    text = format_admin_account_detail(account, orgs, users)
    await _show(update, context, [text], keyboard=system_owner_kb())


@_require_system_owner
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast a message to all registered users: /broadcast <message>"""
    if not context.args:
        await _show(update, context, [
            "ℹ️  Usage:\n\n"
            "  /broadcast <b>Your message here</b>\n\n"
            "  This sends to ALL registered users\n"
            "  across ALL accounts."
        ], keyboard=system_owner_kb())
        return

    message_text = " ".join(context.args)
    broadcast = (
        "╔══════════════════════════╗\n"
        "  📢  <b>SYSTEM NOTICE</b>\n"
        "╚══════════════════════════╝\n"
        "\n"
        f"  {message_text}\n"
    )

    accounts = await db.list_accounts()
    sent, failed = 0, 0
    for account in accounts:
        users = await db.list_account_users(account.id)
        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user.telegram_id,
                    text=broadcast,
                    parse_mode=ParseMode.HTML,
                )
                sent += 1
            except Exception as e:
                logger.warning(f"Broadcast to {user.telegram_id}: {e}")
                failed += 1

    await _show(update, context, [
        f"✅ Broadcast sent.\n\n"
        f"  ✉️  Delivered: {sent}\n"
        f"  ❌  Failed: {failed}"
    ], keyboard=system_owner_kb())


@_require_system_owner
async def cmd_sys_disable_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable/enable an account: /sysdisable <account_id>"""
    if not context.args:
        await _show(update, context, [
            "ℹ️  Usage:  /sysdisable <b>account_id</b>"
        ], keyboard=system_owner_kb())
        return

    try:
        acct_id = int(context.args[0])
    except ValueError:
        await _show(update, context, ["❌ Invalid account ID."],
                    keyboard=system_owner_kb())
        return

    account = await db.get_account(acct_id)
    if not account:
        await _show(update, context, [f"❌ Account #{acct_id} not found."],
                    keyboard=system_owner_kb())
        return

    new_state = not account.is_active
    await db.update_account(acct_id, is_active=new_state)

    # Invalidate client cache if disabling
    if not new_state:
        await invalidate_client(acct_id)

    status = "✅ ENABLED" if new_state else "🔴 DISABLED"
    await _show(update, context, [
        f"{status} account <b>{account.name}</b> (#{acct_id})"
    ], keyboard=system_owner_kb())


# ═══════════════════════════════════════════════════════════════════
# ORG SUB-MENU
# ═══════════════════════════════════════════════════════════════════

@_require_registered
async def show_org_menu(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        org: str):
    name = ORG_DISPLAY.get(org, org)
    text = (
        f"┌─────────────────────────┐\n"
        f"  🏢  <b>{name}</b>\n"
        f"└─────────────────────────┘\n"
        f"\n"
        f"  Select a report for {org}:"
    )
    await _show(update, context, [text], keyboard=org_menu_kb(org))


# ═══════════════════════════════════════════════════════════════════
# CALLBACK ROUTER
# ═══════════════════════════════════════════════════════════════════

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    # Unregistered user helpers
    if data == "cmd_register_help":
        await query.answer()
        await _show(update, context, [
            "ℹ️  To register your company:\n\n"
            "  /register <b>Your Company Name</b>\n\n"
            "  Example:\n"
            "  /register Acme Logistics LLC"
        ])
        return
    if data == "cmd_join_help":
        await query.answer()
        await _show(update, context, [
            "ℹ️  To join an existing team:\n\n"
            "  /join <b>XXXX-XXXX</b>\n\n"
            "  Get the code from your admin."
        ])
        return

    # ── System owner panel buttons ──────────────────────────────
    if data.startswith("sys_"):
        _, tid, sys_owner = await _get_user(update)
        if not sys_owner:
            await query.answer("⛔ System admin only", show_alert=True)
            return

        if data == "sys_dashboard":
            await query.answer()
            await cmd_admin(update, context)
            return
        elif data == "sys_accounts":
            await query.answer()
            await cmd_accounts(update, context)
            return

        await query.answer("Unknown admin action")
        return

    # ── Look up user ────────────────────────────────────────────
    user, tid, sys_owner = await _get_user(update)
    if not user:
        await query.answer()
        # System owner who isn't a customer
        if sys_owner:
            await _show(update, context,
                        [format_system_owner_welcome()],
                        keyboard=system_owner_kb())
        else:
            await _show(update, context,
                        [format_welcome_unregistered(SUPPORT_CONTACT)],
                        keyboard=unregistered_kb())
        return

    # Check if account is active
    account = await db.get_account(user.account_id)
    if not account or not account.is_active:
        await query.answer()
        await _show(update, context, [
            "⛔ Your account has been disabled.\n"
            f"Contact support: {SUPPORT_CONTACT}" if SUPPORT_CONTACT else
            "⛔ Your account has been disabled."
        ])
        return

    context.user_data["_db_user"] = user

    # Populate ORG_DISPLAY for this user's account
    orgs = await db.get_account_orgs(user.account_id)
    populate_org_display(orgs)

    # Main menu
    if data == "cmd_menu":
        await query.answer()
        account = await db.get_account(user.account_id)
        org_codes = [o.code for o in orgs]
        text = format_help(org_codes, user=user, account=account)
        kb = main_menu_kb(user.role, org_codes)
        await _show(update, context, [text], keyboard=kb)

    # All-org commands
    elif data == "cmd_faults":
        await cmd_faults(update, context)
    elif data == "cmd_critical":
        await cmd_critical(update, context)
    elif data == "cmd_fuel":
        await cmd_fuel(update, context)
    elif data == "cmd_alerts":
        await cmd_alerts(update, context)

    # Driver: my truck
    elif data == "cmd_mytruck":
        await cmd_truck(update, context)

    # Account / Users
    elif data == "cmd_account":
        await cmd_account(update, context)
    elif data == "cmd_users":
        await cmd_users(update, context)

    # Truck lookup
    elif data.startswith("truck_") or data.startswith("orgtruck_"):
        await cmd_truck(update, context)

    # Org sub-menu
    elif data.startswith("org_") and not data.startswith("orgfaults_") \
            and not data.startswith("orgcritical_") \
            and not data.startswith("orgfuel_") \
            and not data.startswith("orgtruck_"):
        org = data.replace("org_", "")
        await query.answer()
        await show_org_menu(update, context, org)

    # Per-org commands
    elif data.startswith("orgfaults_"):
        org = data.replace("orgfaults_", "")
        await cmd_faults(update, context, org=org)
    elif data.startswith("orgcritical_"):
        org = data.replace("orgcritical_", "")
        await cmd_critical(update, context, org=org)
    elif data.startswith("orgfuel_"):
        org = data.replace("orgfuel_", "")
        await cmd_fuel(update, context, org=org)

    else:
        await query.answer("Unknown action")


# ═══════════════════════════════════════════════════════════════════
# SCHEDULED ALERTS — multi-tenant
# ═══════════════════════════════════════════════════════════════════

async def check_new_faults(app: Application):
    """Check all accounts with alert subscribers for new faults."""
    global _known_faults

    try:
        subscribers = await db.get_all_alert_subscribers()
        if not subscribers:
            return

        # Group subscribers by account
        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        for account_id, subs in acct_subs.items():
            try:
                samsara = await get_client(account_id)
                faulted, _, _ = await samsara.get_vehicles_with_faults()

                # Populate org display for this account
                acct_orgs = await db.get_account_orgs(account_id)
                populate_org_display(acct_orgs)
                org_codes = [o.code for o in acct_orgs]

                for v in faulted:
                    org = v.get("_org", "?")
                    vid = f"{account_id}:{org}:{v['id']}"
                    current_codes = set()
                    for dtc in v.get("_dtcs", []):
                        key = f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}"
                        current_codes.add(key)

                    previously_known = _known_faults.get(vid, set())
                    new_codes = current_codes - previously_known

                    if new_codes and previously_known:
                        new_dtcs = [
                            dtc for dtc in v.get("_dtcs", [])
                            if f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}" in new_codes
                        ]
                        if new_dtcs:
                            alert_text = format_new_fault_alert(
                                v, new_dtcs,
                                show_org=len(org_codes) > 1,
                            )
                            kb = InlineKeyboardMarkup([
                                [InlineKeyboardButton(
                                    f"📋 View Truck #{v['name']}",
                                    callback_data=f"orgtruck_{org}_{v['name']}"
                                )],
                                [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
                            ])
                            for sub in subs:
                                # Driver: only alert for their truck
                                if sub.role == Role.DRIVER and sub.truck_num:
                                    if v["name"].lower() != sub.truck_num.lower():
                                        continue
                                try:
                                    msg = await app.bot.send_message(
                                        chat_id=sub.telegram_id,
                                        text=alert_text,
                                        parse_mode=ParseMode.HTML,
                                        reply_markup=kb,
                                    )
                                    _active_messages.setdefault(
                                        sub.telegram_id, []
                                    ).append(msg.message_id)
                                except Exception as e:
                                    logger.error(f"Alert to {sub.telegram_id}: {e}")

                    _known_faults[vid] = current_codes

            except Exception as e:
                logger.error(f"Fault check for account {account_id}: {e}")

    except Exception as e:
        logger.error(f"Fault check error: {e}")


# ═══════════════════════════════════════════════════════════════════
# INIT
# ═══════════════════════════════════════════════════════════════════

async def initialize_known_faults():
    """Pre-populate known faults for all accounts with alert subscribers."""
    global _known_faults
    try:
        subscribers = await db.get_all_alert_subscribers()
        account_ids = set(s.account_id for s in subscribers)

        for account_id in account_ids:
            try:
                samsara = await get_client(account_id)
                faulted, _, _ = await samsara.get_vehicles_with_faults()
                for v in faulted:
                    org = v.get("_org", "?")
                    vid = f"{account_id}:{org}:{v['id']}"
                    codes = set()
                    for dtc in v.get("_dtcs", []):
                        key = f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}"
                        codes.add(key)
                    _known_faults[vid] = codes
            except Exception as e:
                logger.error(f"Init faults for account {account_id}: {e}")

        logger.info(f"Known faults loaded for {len(_known_faults)} vehicles")
    except Exception as e:
        logger.error(f"Init faults failed: {e}")


async def post_init(app: Application):
    # Initialize database
    await db.initialize()

    # Seed from .env if this is the first run
    await seed_from_env(db)

    # Set bot commands
    await app.bot.set_my_commands([
        BotCommand("start", "🏠 Main menu"),
        BotCommand("register", "📝 Register company"),
        BotCommand("join", "🔑 Join with invite code"),
        BotCommand("faults", "🔧 Fault report (PDF)"),
        BotCommand("truck", "🚛 Truck detail"),
        BotCommand("critical", "🚨 Critical faults (PDF)"),
        BotCommand("fuel", "⛽ Low fuel"),
        BotCommand("alerts", "🔔 Auto-alerts"),
        BotCommand("invite", "✉️ Invite team member"),
        BotCommand("account", "🏢 Account info"),
        BotCommand("users", "👥 Manage users"),
        BotCommand("addorg", "📡 Connect Samsara org"),
        BotCommand("admin", "⚙️ System admin panel"),
        BotCommand("help", "ℹ️ Help"),
    ])
    logger.info("Commands set")

    # Initialize known faults for alerts
    await initialize_known_faults()

    # Notify system owner(s) that bot is online
    sys_accounts = await db.list_accounts()
    sys_total_users = await db.count_all_users()
    for soid in SYSTEM_OWNER_IDS:
        try:
            sys_msg = (
                "╔══════════════════════════╗\n"
                "     ⚙️  <b>Bot is Online</b>\n"
                "╚══════════════════════════╝\n"
                "\n"
                f"  System Owner Dashboard\n"
                f"  🏢 {len(sys_accounts)} accounts\n"
                f"  👥 {sys_total_users} users\n"
                "  Use /admin for full stats"
            )
            msg = await app.bot.send_message(
                chat_id=soid,
                text=sys_msg,
                parse_mode=ParseMode.HTML,
                reply_markup=system_owner_kb(),
            )
            _active_messages[soid] = [msg.message_id]
        except Exception as e:
            logger.warning(f"Startup msg to system owner {soid}: {e}")

    # Send startup message to all registered customer users
    accounts = await db.list_accounts()
    for account in accounts:
        acct_orgs = await db.get_account_orgs(account.id)
        populate_org_display(acct_orgs)
        org_codes = [o.code for o in acct_orgs]
        org_text = ", ".join(org_codes) if org_codes else "No orgs yet"

        users = await db.list_account_users(account.id)
        for user in users:
            try:
                kb = main_menu_kb(user.role, org_codes)
                startup = (
                    "╔══════════════════════════╗\n"
                    "     🟢  <b>Bot is Online</b>\n"
                    "╚══════════════════════════╝\n"
                    "\n"
                    f"  {role_display(user.role)}\n"
                    f"  🏢 {account.name}\n"
                    f"  Monitoring: {org_text}\n"
                    "  Tap a button to begin ▾"
                )
                msg = await app.bot.send_message(
                    chat_id=user.telegram_id,
                    text=startup,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
                _active_messages[user.telegram_id] = [msg.message_id]
            except Exception as e:
                logger.warning(f"Startup msg to {user.telegram_id}: {e}")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    logger.info("Starting Semi Telematics Bot — multi-tenant mode")

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    # Registration
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("join", cmd_join))

    # Fleet commands
    app.add_handler(CommandHandler("faults", cmd_faults))
    app.add_handler(CommandHandler("truck", cmd_truck))
    app.add_handler(CommandHandler("critical", cmd_critical))
    app.add_handler(CommandHandler("fuel", cmd_fuel))
    app.add_handler(CommandHandler("alerts", cmd_alerts))

    # Management
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CommandHandler("account", cmd_account))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("setrole", cmd_setrole))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("addorg", cmd_addorg))
    app.add_handler(CommandHandler("removeorg", cmd_removeorg))

    # System owner admin
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("accounts", cmd_accounts))
    app.add_handler(CommandHandler("sysaccount", cmd_sysaccount))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("sysdisable", cmd_sys_disable_account))

    # Callback router
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Scheduled alerts
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_new_faults, "interval",
        minutes=ALERT_INTERVAL, args=[app], id="fault_check",
    )
    scheduler.start()

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
