"""Telegram handler registration for per-account bot Applications.

This module is the single place that wires all command, callback, and text
handlers to a ``telegram.ext.Application`` instance.  It is imported by
``interfaces/bot/`` (the bot delivery surface) and injected into
``core.bot_registry`` at startup so that the registry never needs to import
from the interface layer.

Usage (in run.py or startup code)::

    import core.bot_registry as _registry
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
        cmd_faults, cmd_truck, cmd_fuel, cmd_alerts,
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

    # Registration
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("join", cmd_join))

    # Fleet commands
    app.add_handler(CommandHandler("faults", cmd_faults))
    app.add_handler(CommandHandler("truck", cmd_truck))
    app.add_handler(CommandHandler("fuel", cmd_fuel))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("efficiency", cmd_efficiency))

    # Events
    app.add_handler(CommandHandler("events", cmd_events))
    app.add_handler(CommandHandler("tips", cmd_tips))

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

    # User settings & audit
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("audit", cmd_audit))

    # System owner admin
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("accounts", cmd_accounts))
    app.add_handler(CommandHandler("sysaccount", cmd_sysaccount))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("sysdisable", cmd_sys_disable_account))

    # Callback router
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Native chat picker (ChatShared)
    app.add_handler(MessageHandler(
        filters.StatusUpdate.CHAT_SHARED, handle_chat_shared,
    ))

    # Text input handler (for interactive prompts)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_text,
    ))
