"""Telegram handler registration for per-account bot Applications.

This module wires command, callback, and text handlers to a
``telegram.ext.Application`` instance.  Two separate registration
functions because the customer-facing bots and the operator-only
system bot expose different surfaces:

  - ``register_handlers`` (the default, used by ``bot_registry``)
    binds all the CUSTOMER-facing commands (/start, /faults, /vehicle,
    etc.).  Per-account bots and the global LOGIN bot both get this
    set.  Does NOT include /admin or any other system-owner command —
    those belong on the system bot daemon.

  - ``register_system_handlers`` binds ONLY the operator-only commands
    (/admin, /accounts, /sysaccount, /broadcast, /sysdisable).  Used
    by ``interfaces/bot/system_app.py`` to build the system-bot
    daemon at startup.  Never called on a per-account or login bot.

Usage::

    # Customer / per-account bots (delegated through bot_registry)
    import infra.bot_registry as _registry
    from interfaces.bot.handler_setup import register_handlers
    _registry.set_handler_setup(register_handlers)

    # System bot daemon (run.py builds it directly)
    from interfaces.bot.system_app import build_system_app
    sys_app = build_system_app()  # already calls register_system_handlers
"""

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)


def register_handlers(app: Application) -> None:
    """Attach customer-facing command/callback/text handlers to a bot.

    Used for the global LOGIN bot and every per-account bot.  No
    operator-only commands here — those go through
    ``register_system_handlers`` on the system bot daemon.
    """
    from interfaces.bot.app import cmd_chatid, cmd_settings, cmd_audit
    from interfaces.bot.registration import cmd_start, cmd_register, cmd_join, cmd_help
    from interfaces.bot.fleet import (
        cmd_faults, cmd_vehicle, cmd_fuel, cmd_alerts,
        cmd_health, cmd_efficiency, cmd_cam,
    )
    from interfaces.bot.management import (
        cmd_account, cmd_invite, cmd_users, cmd_setrole,
        cmd_remove, cmd_addcompany, cmd_removecompany,
        cmd_addgroup, cmd_removegroup, cmd_groups,
        handle_chat_shared,
    )
    from interfaces.bot.callbacks import handle_callback, handle_text
    from interfaces.bot.events import cmd_events
    from interfaces.bot.knowledge import cmd_tips
    from interfaces.bot.work_orders import cmd_invoice, handle_invoice_message

    # Registration
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("join", cmd_join))

    # Fleet commands
    app.add_handler(CommandHandler("faults", cmd_faults))
    app.add_handler(CommandHandler("vehicle", cmd_vehicle))
    app.add_handler(CommandHandler("fuel", cmd_fuel))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("efficiency", cmd_efficiency))
    # /cam <truck> — ad-hoc AI dashcam check for a single truck.
    # Fleet-wide camera analysis lives on the dashboard; this
    # command is for one-message-in one-message-out from a phone.
    app.add_handler(CommandHandler("cam", cmd_cam))

    # Events
    app.add_handler(CommandHandler("events", cmd_events))
    app.add_handler(CommandHandler("tips", cmd_tips))

    # Stakeholder Risk Summary (premium)
    from interfaces.bot.reports import cmd_risk_report
    app.add_handler(CommandHandler("risk_report", cmd_risk_report))

    # Pay-for-Performance — driver self-service paystub
    from interfaces.bot.payroll import cmd_my_pay
    app.add_handler(CommandHandler("my_pay", cmd_my_pay))

    # PTI — driver self-service deep-link to the Mini App + fleet
    # link to the dashboard review queue.  Notification helpers in
    # the same module are called by the cron jobs (capabilities/pti
    # /jobs.py) via lazy imports.
    from interfaces.bot.pti import cmd_pti
    app.add_handler(CommandHandler("pti", cmd_pti))

    # Auto Coaching — driver self-service assignments + ack callback
    from interfaces.bot.coaching import (
        ACK_CALLBACK_PREFIX,
        cb_coaching_ack,
        cmd_my_coaching,
    )
    app.add_handler(CommandHandler("my_coaching", cmd_my_coaching))
    app.add_handler(CallbackQueryHandler(
        cb_coaching_ack, pattern=f"^{ACK_CALLBACK_PREFIX}",
    ))

    # Management
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CommandHandler("account", cmd_account))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("setrole", cmd_setrole))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("addorg", cmd_addcompany))
    app.add_handler(CommandHandler("removeorg", cmd_removecompany))

    # Group / channel authorization
    app.add_handler(CommandHandler("addgroup", cmd_addgroup))
    app.add_handler(CommandHandler("removegroup", cmd_removegroup))
    app.add_handler(CommandHandler("groups", cmd_groups))
    app.add_handler(CommandHandler("chatid", cmd_chatid))

    # Forum-routing setup (Option C topic-based alert delivery)
    from interfaces.bot.forum_setup import (
        cmd_setup_forum, cmd_topics_status, cmd_repair_forum,
        cmd_reset_forum, cmd_connect_forum, cmd_test_forum,
        handle_forum_service_message,
    )
    app.add_handler(CommandHandler("setupforum", cmd_setup_forum))
    app.add_handler(CommandHandler("topicsstatus", cmd_topics_status))
    app.add_handler(CommandHandler("repairforum", cmd_repair_forum))
    app.add_handler(CommandHandler("resetforum", cmd_reset_forum))
    app.add_handler(CommandHandler("connectforum", cmd_connect_forum))
    app.add_handler(CommandHandler("testforum", cmd_test_forum))

    # Forum-topic discovery index: catches Telegram's service messages
    # (forum_topic_created / forum_topic_edited) so subsequent
    # /setupforum and /repairforum runs can adopt existing topics by
    # name instead of creating duplicates.  Telegram has no
    # getForumTopics API, so passive indexing is the only mechanism
    # available to a bot.
    app.add_handler(MessageHandler(
        filters.StatusUpdate.FORUM_TOPIC_CREATED
        | filters.StatusUpdate.FORUM_TOPIC_EDITED,
        handle_forum_service_message,
    ))

    # User settings & audit
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("audit", cmd_audit))

    # NOTE: operator-only commands (/admin, /accounts, /sysaccount,
    # /broadcast, /sysdisable) are NOT registered here — they're bound
    # to the system bot daemon via ``register_system_handlers`` so
    # customers chatting with the login bot or a per-account bot can't
    # accidentally trigger them.

    # Work orders — /invoice command + file-receive handler for the
    # photo/PDF upload step.  Registered BEFORE the generic text
    # handler so the wizard state machine sees photos and documents
    # before they hit the generic handler.
    #
    # ``filters.ChatType.PRIVATE`` restricts this to direct messages —
    # photos posted inside a forum-routing group topic must not
    # trigger the invoice wizard or any other private workflow.
    app.add_handler(CommandHandler("invoice", cmd_invoice))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & (filters.PHOTO | filters.Document.ALL),
        handle_invoice_message,
    ))

    # Callback router
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Native chat picker (ChatShared)
    app.add_handler(MessageHandler(
        filters.StatusUpdate.CHAT_SHARED, handle_chat_shared,
    ))

    # Text input handler (for interactive prompts + AI free-text).
    # ``filters.ChatType.PRIVATE`` keeps the AI + interactive prompts
    # in 1:1 DM only — group topics are alert-delivery surfaces, not
    # conversation surfaces.  Without this, every reply in a forum
    # topic was being routed to the AI ("Thinking…") which is both a
    # token-cost leak and a confusing UX.
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        _maybe_invoice_or_text(handle_invoice_message, handle_text),
    ))


def _maybe_invoice_or_text(invoice_handler, text_handler):
    """Wrap two MessageHandlers so the invoice wizard gets first crack
    at text input.  Telegram-bot doesn't support handler-priority for
    the same filter, so we chain manually: if the user has an
    ``_invoice`` wizard state pending, deliver to that; otherwise
    fall through to the generic text handler.
    """
    async def _dispatch(update, context):
        if context.user_data.get("_invoice"):
            await invoice_handler(update, context)
            return
        await text_handler(update, context)
    return _dispatch


# ── Front-door login bot handler set ─────────────────────────────


def register_login_handlers(app: Application) -> None:
    """Attach the minimal signup / registration command set.

    Used by the GLOBAL login bot only (``TELEGRAM_LOGIN_BOT_TOKEN``).
    The login bot is a front door — its job is to walk a new visitor
    through registration, then point them at their account's
    per-account bot for day-to-day commands.  It does NOT carry
    tenant traffic (``/faults``, ``/vehicle``, ``/fuel``, etc.) — at
    1000+ customers, funnelling tenant commands through a single PTB
    Application would be a serious bottleneck.  Tenant commands live
    exclusively on the per-account bots managed by
    ``infra.bot_registry`` (which calls ``register_handlers`` above).

    Commands exposed here:

      /start      welcome + signup
      /register   manual registration
      /join       invite-code join
      /help       what this bot is for
      /chatid     show the user's Telegram chat id (for support)

    The generic callback + text handlers are also registered so the
    registration wizard's inline buttons + free-text prompts work.
    They dispatch based on per-user state; an unregistered visitor
    has no tenant state, so tenant-side callbacks won't fire even
    if a stray callback_data slips through.
    """
    from interfaces.bot.registration import (
        cmd_start, cmd_register, cmd_join, cmd_help,
    )
    from interfaces.bot.app import cmd_chatid
    from interfaces.bot.callbacks import handle_callback, handle_text

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("join",     cmd_join))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("chatid",   cmd_chatid))

    # Callback + text routers — needed for the registration wizard
    # state machine.  Same dispatchers per-account bots use; unsafe
    # branches would self-gate on "no user / no account_id" anyway.
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        handle_text,
    ))


# ── System / operator bot handler set ────────────────────────────


def register_system_handlers(app: Application) -> None:
    """Attach operator-only commands to the system bot Application.

    Called by ``interfaces/bot/system_app.build_system_app`` for the
    daemon that runs on ``TELEGRAM_SYSTEM_BOT_TOKEN``.  This bot has
    no customer commands at all — anyone who chats with it gets a
    "for operators only" reply unless they're on SYSTEM_OWNER_IDS.
    """
    from interfaces.bot.admin import (
        cmd_admin, cmd_accounts, cmd_sysaccount,
        cmd_broadcast, cmd_sys_disable_account,
    )
    from interfaces.bot.callbacks import handle_callback

    # /start gives the operator a quick reference of what's available
    # here; non-operators get a short pointer to the customer surface.
    app.add_handler(CommandHandler("start", _cmd_system_start))

    # Operator-only commands.  The ``_require_system_owner`` decorator
    # on each command body already gates by SYSTEM_OWNER_IDS, so a
    # stranger who somehow lands on this bot can type /admin and still
    # gets refused.
    app.add_handler(CommandHandler("admin",      cmd_admin))
    app.add_handler(CommandHandler("accounts",   cmd_accounts))
    app.add_handler(CommandHandler("sysaccount", cmd_sysaccount))
    app.add_handler(CommandHandler("broadcast",  cmd_broadcast))
    app.add_handler(CommandHandler("sysdisable", cmd_sys_disable_account))

    # Callback router — handles the inline-button callbacks from
    # ``system_owner_kb`` (sys_dashboard, sys_accounts, etc.).
    app.add_handler(CallbackQueryHandler(handle_callback))


async def _cmd_system_start(update, _context):
    """``/start`` greeting for the system bot.

    Operators see the menu of available commands; everyone else gets
    a short "this isn't for you" pointing at the customer surface.
    Kept terse so the bot isn't a discovery vector for the operator
    surface — anyone who reaches it should already know why they're
    here.
    """
    from capabilities.iam.permissions import is_system_owner
    tid = update.effective_user.id if update.effective_user else 0
    if is_system_owner(tid):
        await update.message.reply_text(
            "⚙️ <b>4truck operator console</b>\n\n"
            "Commands on this bot:\n"
            "  /admin       — system dashboard\n"
            "  /accounts    — list all accounts\n"
            "  /sysaccount &lt;id&gt; — account detail\n"
            "  /broadcast &lt;msg&gt; — message all owners\n"
            "  /sysdisable &lt;id&gt; — disable an account\n\n"
            "Full operator UI: <code>system.4truck.us</code>",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await update.message.reply_text(
            "This bot is for 4truck platform operators only.  "
            "Customer access: https://4truck.us",
            disable_web_page_preview=True,
        )
