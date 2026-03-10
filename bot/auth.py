"""Authentication helpers and decorators."""

from telegram import Update
from telegram.ext import ContextTypes

from permissions import is_system_owner, can

from bot.config import db, SUPPORT_CONTACT
from bot.helpers import _show
from bot.keyboards import system_owner_kb, unregistered_kb, back_kb
from formatters import format_system_owner_welcome, format_welcome_unregistered


async def _is_group_chat(update: Update) -> bool:
    """Return True if this update comes from a group or supergroup or channel."""
    chat = update.effective_chat
    if not chat:
        return False
    return chat.type in ("group", "supergroup", "channel")


async def _group_chat_guard(update: Update) -> bool:
    """Return True if the update should be processed, False if it should be silently ignored.

    Rules:
      - Private chats → always allowed
      - Group/supergroup/channel → only if the chat is authorized in the DB
    """
    if not await _is_group_chat(update):
        return True  # private chat — always OK

    chat_id = update.effective_chat.id
    return await db.is_chat_authorized(chat_id)


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
    Silently ignores unauthorized group chats.
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs):
        if not await _group_chat_guard(update):
            return  # silently ignore unauthorized group

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
            if not await _group_chat_guard(update):
                return  # silently ignore unauthorized group

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


def _require_system_owner(func):
    """Decorator: system owner only (checked via env SYSTEM_OWNER_IDS)."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs):
        if not await _group_chat_guard(update):
            return  # silently ignore unauthorized group

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
