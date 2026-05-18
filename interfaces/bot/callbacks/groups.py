"""Group / channel management callback handlers."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from interfaces.bot.state import get_platform_db
from interfaces.bot.keyboards import back_kb, group_picker_kb
from interfaces.bot.helpers import _show
from capabilities.localization.i18n import t
from interfaces.bot.management import cmd_groups


async def rmgroup_handler(update, context):
    query = update.callback_query
    data = query.data
    chat_id_str = data[8:]
    user = context.user_data["_db_user"]
    await query.answer()
    if not user.is_admin_or_above:
        await query.answer(t("access.no_access"), show_alert=True)
        return
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t('user_mgmt.remove_yes'), callback_data=f"rmgroupconfirm_{chat_id_str}"),
            InlineKeyboardButton(t('common.cancel_back'), callback_data="cmd_groups"),
        ],
    ])
    await _show(update, context, [
        f"⚠️ <b>Remove group <code>{chat_id_str}</code>?</b>\n\n"
        f"{t('groups.remove_confirm_msg')}"
    ], keyboard=kb)


async def rmgroupconfirm_handler(update, context):
    query = update.callback_query
    data = query.data
    chat_id_str = data[15:]
    user = context.user_data["_db_user"]
    await query.answer()
    try:
        group_chat_id = int(chat_id_str)
        await get_platform_db().remove_authorized_chat(user.account_id, group_chat_id)
    except ValueError:
        pass
    await cmd_groups(update, context)


async def addgroup_pick_handler(update, context):
    query = update.callback_query
    user = context.user_data["_db_user"]
    if not user.is_admin_or_above:
        await query.answer(t("access.no_access"), show_alert=True)
        return
    await query.answer()
    context.user_data["_awaiting_chat_pick"] = True
    await update.effective_chat.send_message(
        f"{t('groups.pick_prompt')}\n\n"
        f"{t('groups.pick_cancel')}",
        parse_mode="HTML",
        reply_markup=group_picker_kb(),
    )


async def addgroup_confirm_handler(update, context):
    query = update.callback_query
    await query.answer()
    user = context.user_data["_db_user"]
    pending = context.user_data.pop("_pending_group", None)
    if not pending:
        await _show(update, context,
                    [t('common.nothing_to_confirm')],
                    keyboard=back_kb())
        return
    await get_platform_db().add_authorized_chat(
        account_id=user.account_id,
        chat_id=pending["chat_id"],
        chat_title=pending["title"],
        added_by=user.id,
    )
    await _show(update, context, [
        f"{t('groups.authorized_title')}\n\n"
        f"  💬 <b>{pending['title']}</b>\n"
        f"  🆔 <code>{pending['chat_id']}</code>\n\n"
        f"  {t('groups.bot_responds')}"
    ], keyboard=back_kb())


async def addgroup_cancel_handler(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("_pending_group", None)
    await cmd_groups(update, context)


def register(router):
    """Register group-management routes."""
    router.exact("cmd_groups", cmd_groups)
    router.prefix("rmgroupconfirm_", rmgroupconfirm_handler)
    router.prefix("rmgroup_", rmgroup_handler)
    router.exact("addgroup_pick", addgroup_pick_handler)
    router.exact("addgroup_confirm", addgroup_confirm_handler)
    router.exact("addgroup_cancel", addgroup_cancel_handler)
