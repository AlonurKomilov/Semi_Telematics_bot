"""Registration and join commands."""

from telegram import Update
from telegram.ext import ContextTypes
from capabilities.localization.i18n import t

from adapters.storage import Role
from capabilities.permissions.roles import role_display
from adapters.samsara.client import populate_company_display
from capabilities.formatting import (
    format_help,
    format_welcome_unregistered,
    format_unregistered_member,
    format_system_owner_welcome,
    format_register_success,
    format_join_success,
)

from interfaces.bot.config import SUPPORT_CONTACT, logger
from interfaces.bot.state import get_user_company_codes, get_platform_db, get_tenant_db
from interfaces.bot.keyboards import main_menu_kb, system_owner_kb, unregistered_kb, back_kb, onboarding_kb
from interfaces.bot.helpers import _show
from interfaces.bot.auth import _get_user
from features.settings.invites.notifications import announce_invite_accepted


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show role-aware help — lists features the current user can access."""
    from capabilities.permissions.roles import get_permissions
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
    # Width claim, legacy pair on purpose — the vehicles pair is
    # view/view, so can_view_vehicles cannot say "wide only".
    # Moves in the width pass.
    if perms.can_vehicle_all:
        report_items.append("🚛 Search any vehicle")
    elif perms.can_vehicle_vehicle:
        report_items.append("🚛 View your vehicle")
    if report_items:
        lines.append(f"\n  {t('help.reports_label')}")
        for item in report_items:
            lines.append(f"  · {item}")

    # Tools
    tool_items = []
    if perms.can_view_scorecards:
        tool_items.append("🏆 Scorecards")
    if perms.can_view_location:
        tool_items.append("🗺 Live fleet map")
    if perms.can_view_routes:
        tool_items.append("🛣 Routes")
    if perms.can_view_geofence:
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
    if perms.can_view_maintenance:
        cost_items.append("🔧 Maintenance scheduler")
    if cost_items:
        lines.append(f"\n  {t('help.costs_label')}")
        for item in cost_items:
            lines.append(f"  · {item}")

    # Alerts & Digest
    if perms.can_alerts_all or perms.can_alerts_vehicle:
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
    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)
    kb = main_menu_kb(user.role, company_codes)
    await _show(update, context, ["\n".join(lines)], keyboard=kb)


async def _front_door_redirect(update, context, user) -> bool:
    """The GLOBAL login bot is a front door, not a control surface.

    A registered user whose account runs its OWN bot gets a pointer
    card to that bot instead of the tenant menu — one control surface
    per account, on the account's bot.  Renders nothing (returns
    False) on per-account bots, and for accounts with no bot of their
    own the login bot keeps serving the full menu (the documented
    fallback for customers who never connected one).

    Deep-link flows (login_TOKEN, link_TOKEN, join_CODE) are handled
    BEFORE this gate in cmd_start — logging in via the dashboard's
    bot-login link must keep working here regardless.
    """
    if context.bot_data.get("account_id") is not None:
        return False          # per-account bot — full surface is correct
    try:
        platform = get_platform_db()
        account = await platform.get_account(user.account_id)
    except Exception:
        return False          # storage hiccup → keep legacy behavior
    bot_username = str(getattr(account, "bot_username", "") or "")
    if not bot_username:
        return False          # no account bot — login bot IS their bot
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            t('front_door.open_button', username=bot_username),
            url=f"https://t.me/{bot_username}",
        ),
    ]])
    await _show(update, context, [
        f"{t('front_door.title')}\n\n"
        + t('front_door.body',
            username=bot_username,
            account=str(getattr(account, "name", "") or "")),
    ], keyboard=kb)
    return True


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
            platform = get_platform_db()
            # RuntimeError comes from redeem_invite's race-lost path:
            # an operator revoke (or a parallel redeem) won the race
            # between our get_invite snapshot and the guarded UPDATE.
            # Treat it identically to a None return so the bot UX is
            # uniform — invitee sees "invalid code", no operator
            # action is leaked via timing or message divergence.
            try:
                new_user = await platform.redeem_invite(code, tid, display_name=tg_name)
            except RuntimeError as e:
                logger.info("Deep-link join race lost: %s", e)
                new_user = None
            if new_user:
                account = await platform.get_account(new_user.account_id)
                r_display = role_display(new_user.role)
                text = format_join_success(account.name, r_display)
                company_codes = await get_user_company_codes(new_user.account_id)
                tenant = await get_tenant_db(new_user.account_id)
                companies = await tenant.get_account_companies(new_user.account_id)
                populate_company_display(companies)
                kb = main_menu_kb(new_user.role, company_codes)
                await _show(update, context, [text], keyboard=kb)
                logger.info(f"Deep-link join: {tid} → '{account.name}' as {new_user.role.value}")
                # Tell the inviter their invite was accepted (targeted,
                # opt-out, flag-gated, non-fatal — never blocks the join).
                await announce_invite_accepted(
                    platform, code, new_user, role_display=r_display)
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

    # ── 0b. Deep-link dashboard login: /start login_TOKEN ──────
    if context.args and context.args[0].startswith("login_"):
        await _handle_bot_login(update, context, context.args[0][6:], user, tid)
        return

    # ── 0c. Deep-link Telegram-link: /start link_TOKEN ─────────
    if context.args and context.args[0].startswith("link_"):
        await _handle_telegram_link(update, context, context.args[0][5:], user, tid)
        return

    # ── 1. System owner (platform admin) ──────────────────────
    if sys_owner and not user:
        await _show(update, context,
                    [format_system_owner_welcome()],
                    keyboard=system_owner_kb())
        return

    # ── 1b/2. Existing registered user ─────────────────────────
    # Front door first: on the GLOBAL login bot, a user whose account
    # runs its own bot belongs there — the tenant menu never renders
    # here (see _front_door_redirect).  NOTE the old sys-owner branch
    # appended a "System admin: /admin" hint to the tenant menu; that
    # was a leftover from the single-bot era — /admin is only handled
    # by the SYSTEM bot daemon now, so the hint advertised a command
    # that does nothing on this bot.  Operator tools live on the
    # system bot; the tenant menu stays tenant-only.
    if user:
        if await _front_door_redirect(update, context, user):
            return
        platform = get_platform_db()
        account = await platform.get_account(user.account_id)
        company_codes = await get_user_company_codes(user.account_id)
        tenant = await get_tenant_db(user.account_id)
        companies = await tenant.get_account_companies(user.account_id)
        populate_company_display(companies)
        text = format_help(company_codes, user=user, account=account)
        kb = main_menu_kb(user.role, company_codes)
        await _show(update, context, [text], keyboard=kb)
        return

    # ── 3. New / unknown user ──────────────────────────────────
    name = getattr(update.effective_user, "first_name", "") or ""

    # Per-account bot: this person is not in the organization
    bot_account_id = context.bot_data.get("account_id")
    if bot_account_id is not None:
        platform = get_platform_db()
        account = await platform.get_account(bot_account_id)
        account_name = account.name if account else "this organization"
        await _show(update, context, [
            format_unregistered_member(account_name, name=name, support_contact=SUPPORT_CONTACT),
        ], keyboard=unregistered_kb())
        return

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

    # Throttle account creation per Telegram id — 1/hour.  Without
    # this any unregistered TG user could script /register and mint
    # hundreds of tenant accounts (each seeding permissions + PTI
    # templates).  Redis-backed, fails open on Redis outage; the
    # web-signup path has Turnstile for the same job.
    from infra.cache import rate_limit_check
    if not await rate_limit_check(f"bot_register:{tid}", 3600, 1):
        await _show(update, context, [
            "⏳ You just created an account.  Please wait an hour "
            "before registering another company.",
        ])
        logger.info(f"Bot /register throttled for TG user {tid}")
        return

    try:
        platform = get_platform_db()
        account = await platform.create_account(company_name)
        tg_name = getattr(update.effective_user, "full_name", "") or ""
        user = await platform.create_user(
            telegram_id=tid,
            account_id=account.id,
            role=Role.OWNER,
            display_name=tg_name,
        )
        logger.info(f"New account: '{company_name}' by TG user {tid}")

        try:
            await platform.add_platform_audit(
                "account_created",
                account_id=account.id,
                actor=f"bot:{tid}",
                details=f"name={company_name!r}",
            )
        except Exception:
            logger.exception("platform audit write failed for account %s", account.id)

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
    platform = get_platform_db()
    if user:
        account = await platform.get_account(user.account_id)
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
    # See comment in cmd_start above — race-lost RuntimeError maps to
    # the same UX as a None return (invalid code).
    try:
        new_user = await platform.redeem_invite(code, tid, display_name=tg_name)
    except RuntimeError as e:
        logger.info("/join race lost: %s", e)
        new_user = None

    if not new_user:
        await _show(update, context, [
            f"{t('join.invalid_code')}\n"
            f"{t('join.ask_admin')}"
        ])
        return

    account = await platform.get_account(new_user.account_id)
    r_display = role_display(new_user.role)
    text = format_join_success(account.name, r_display)

    company_codes = await get_user_company_codes(new_user.account_id)
    tenant = await get_tenant_db(new_user.account_id)
    companies = await tenant.get_account_companies(new_user.account_id)
    populate_company_display(companies)
    kb = main_menu_kb(new_user.role, company_codes)

    await _show(update, context, [text], keyboard=kb)
    logger.info(f"User {tid} joined '{account.name}' as {new_user.role.value}")
    # Tell the inviter their invite was accepted (targeted, opt-out,
    # flag-gated, non-fatal — never blocks the join).
    await announce_invite_accepted(platform, code, new_user, role_display=r_display)


# ── Bot-login: approve/reject dashboard login via deep link ───

async def _handle_bot_login(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    token: str,
    user,
    tid: int,
):
    """Process /start login_TOKEN — approve or reject a dashboard login request.

    The token was generated by POST /api/auth/bot-login/init and stored in Redis.
    If the Telegram user is a registered employee, we write an approved JWT into
    Redis so the frontend polling loop can pick it up.
    """
    from infra.cache import get as redis_get, cache_set as redis_set
    from interfaces.api.auth import BOT_LOGIN_PREFIX, BOT_LOGIN_TTL, create_jwt

    # Validate token exists and is pending
    data = await redis_get(f"{BOT_LOGIN_PREFIX}{token}")
    if data is None or data.get("status") != "pending":
        await _show(update, context, [
            "⚠️ This login link has expired or was already used.\n"
            "Please request a new one from the dashboard."
        ])
        return

    name = getattr(update.effective_user, "first_name", "") or ""

    if not user:
        # Not registered — reject
        await redis_set(
            f"{BOT_LOGIN_PREFIX}{token}",
            {"status": "rejected", "reason": "User not registered"},
            ttl=60,
        )
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"  ❌ Login Denied\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"Hi{(' ' + name) if name else ''}, you are not registered\n"
            "in the system. Ask your company admin for\n"
            "an invite link to join your company first."
        ])
        logger.info(f"Bot-login rejected: TG user {tid} not registered")
        return

    # Registered user — approve, generate JWT
    platform = get_platform_db()
    account = await platform.get_account(user.account_id)
    account_name = account.name if account else "your company"
    # Bot login is a deliberate, identity-confirming action (user
    # tapped Approve in the bot), so issue the long-lived "remembered"
    # session — same default we give Telegram Mini App / Login Widget.
    jwt_token = create_jwt(
        user.telegram_id, user.account_id, user.role.value,
        remember_me=True,
    )

    await redis_set(
        f"{BOT_LOGIN_PREFIX}{token}",
        {
            "status": "approved",
            "access_token": jwt_token,
            "token_type": "bearer",
            "user": {
                "telegram_id": user.telegram_id,
                "name": name,
                "role": user.role.value,
                "account_id": user.account_id,
            },
        },
        ttl=BOT_LOGIN_TTL,
    )

    await _show(update, context, [
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"  ✅ Login Approved\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"Welcome, <b>{name}</b>!\n"
        f"Company: <b>{account_name}</b>\n"
        "\n"
        "You can now close this chat and return\n"
        "to the dashboard — you'll be logged in\n"
        "automatically."
    ])
    logger.info(f"Bot-login approved: TG user {tid} ({name}) for '{account_name}'")


# ── Telegram link: connect a Telegram identity to an email account ──

async def _handle_telegram_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    token: str,
    user,
    tid: int,
):
    """Process /start link_TOKEN — bind this Telegram chat to the email
    account that initiated the flow from the dashboard.

    Refuses if:
      - the token is unknown / expired,
      - the current Telegram ID is already attached to another user
        (avoids silently moving the link across accounts).
    """
    from infra.cache import get as redis_get, cache_set as redis_set
    from interfaces.bot.config import TELEGRAM_LINK_PREFIX, TELEGRAM_LINK_TTL

    key = f"{TELEGRAM_LINK_PREFIX}{token}"
    data = await redis_get(key)
    if data is None or data.get("status") != "pending":
        await _show(update, context, [
            "⚠️ This link has expired or was already used.\n"
            "Open your dashboard profile and start the link again."
        ])
        return

    target_user_id = int(data.get("user_id") or 0)
    platform = get_platform_db()
    # Refuse if this Telegram ID already belongs to someone else.
    existing = await platform.get_user_by_telegram_id(tid)
    if existing and existing.id != target_user_id:
        await redis_set(
            key,
            {
                "status": "rejected",
                "user_id": target_user_id,
                "reason": "This Telegram account is already linked to a different dashboard user.",
            },
            ttl=60,
        )
        await _show(update, context, [
            "❌ This Telegram account is already linked to a different\n"
            "dashboard user.  Sign in with that account, or ask an\n"
            "admin to clear the old link before retrying."
        ])
        logger.info(
            f"Telegram-link rejected: tid={tid} already on user_id={existing.id}, "
            f"requested user_id={target_user_id}"
        )
        return

    if existing and existing.id == target_user_id:
        # Idempotent: the link is already in place.  Mark success so
        # the dashboard poll resolves.
        await redis_set(
            key,
            {"status": "linked", "user_id": target_user_id, "telegram_id": tid},
            ttl=TELEGRAM_LINK_TTL,
        )
        await _show(update, context, [
            "✅ Already linked.\n"
            "You're all set — close this chat and return to the dashboard."
        ])
        return

    try:
        await platform.link_telegram_to_user(target_user_id, tid)
    except Exception as e:
        logger.error(f"Telegram-link DB error: {e}", exc_info=True)
        await redis_set(
            key,
            {"status": "rejected", "user_id": target_user_id, "reason": "Storage error"},
            ttl=60,
        )
        await _show(update, context, [
            "⚠️ Something went wrong while saving the link.\n"
            "Try again from the dashboard."
        ])
        return

    await redis_set(
        key,
        {"status": "linked", "user_id": target_user_id, "telegram_id": tid},
        ttl=TELEGRAM_LINK_TTL,
    )
    await _show(update, context, [
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "  ✅ Telegram linked\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Your Telegram chat is now connected to your\n"
        "dashboard account.  You can close this chat\n"
        "and return to the dashboard."
    ])
    logger.info(f"Telegram-link approved: tid={tid} → user_id={target_user_id}")
