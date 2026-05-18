"""Telegram handler registration for per-account bot Applications.

This module is the single place that wires all command, callback, and text
handlers to a ``telegram.ext.Application`` instance.  It is imported by
``interfaces/bot/`` (the bot delivery surface) and injected into
``core.bot_registry`` at startup so that the registry never needs to import
from the interface layer.

Usage (in run.py or startup code)::

    import infra.bot_registry as _registry
    from interfaces.bot.handler_setup import register_handlers
    _registry.set_handler_setup(register_handlers)
"""

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)


def register_handlers(app: Application) -> None:
    """Attach all command/callback/text handlers to a bot Application."""
    from interfaces.bot.app import cmd_chatid, cmd_settings, cmd_audit
    from interfaces.bot.registration import cmd_start, cmd_register, cmd_join, cmd_help
    from interfaces.bot.fleet import (
        cmd_faults, cmd_vehicle, cmd_fuel, cmd_alerts,
        cmd_health, cmd_efficiency,
    )
    from interfaces.bot.management import (
        cmd_account, cmd_invite, cmd_users, cmd_setrole,
        cmd_remove, cmd_addcompany, cmd_removecompany,
        cmd_addgroup, cmd_removegroup, cmd_groups,
        handle_chat_shared,
    )
    from interfaces.bot.admin import (
        cmd_admin, cmd_accounts, cmd_sysaccount,
        cmd_broadcast, cmd_sys_disable_account,
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

    # Events
    app.add_handler(CommandHandler("events", cmd_events))
    app.add_handler(CommandHandler("tips", cmd_tips))

    # Stakeholder Risk Summary (premium)
    from interfaces.bot.reports import cmd_risk_report
    app.add_handler(CommandHandler("risk_report", cmd_risk_report))

    # Pay-for-Performance — driver self-service paystub
    from interfaces.bot.payroll import cmd_my_pay
    app.add_handler(CommandHandler("my_pay", cmd_my_pay))

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

    # System owner admin
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("accounts", cmd_accounts))
    app.add_handler(CommandHandler("sysaccount", cmd_sysaccount))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("sysdisable", cmd_sys_disable_account))

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
