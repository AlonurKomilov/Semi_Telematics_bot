"""User-settings callback handlers — quiet hours, timezone, language."""

from bot.config import db
from bot.keyboards import (
    user_settings_kb, quiet_hours_kb, quiet_hours_picker_kb,
    settings_tz_kb, language_kb,
)
from bot.helpers import _show
from bot.i18n import t


async def cmd_settings(update, context):
    query = update.callback_query
    await query.answer()
    user = context.user_data["_db_user"]
    await _show(update, context, [
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('user_settings.header')}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n{t('user_settings.configure')}"
    ], keyboard=user_settings_kb(user))


async def settings_quiet(update, context):
    query = update.callback_query
    await query.answer()
    user = context.user_data["_db_user"]
    await _show(update, context, [
        t('user_settings.quiet_title') + "\n\n"
        + t('user_settings.quiet_desc')
    ], keyboard=quiet_hours_kb(user))


async def settings_quiet_set(update, context):
    query = update.callback_query
    await query.answer()
    user = context.user_data["_db_user"]
    schedules = await db.get_work_schedules_for_role(
        user.account_id, user.role.value,
    )
    await _show(update, context, [
        t('user_settings.quiet_select_title') + "\n\n"
        + t('user_settings.quiet_select_prompt')
    ], keyboard=quiet_hours_picker_kb(schedules=schedules if schedules else None))


async def quiet_set(update, context):
    query = update.callback_query
    data = query.data
    parts = data.replace("quiet_set_", "").split("_")
    q_start, q_end = int(parts[0]), int(parts[1])
    user = context.user_data["_db_user"]
    await db.update_user(user.id, quiet_start=q_start, quiet_end=q_end)
    user = await db.get_user(user.id)
    context.user_data["_db_user"] = user
    await query.answer(t('user_settings.quiet_updated_toast'))
    await _show(update, context, [
        t('user_settings.quiet_set_confirm', start=f"{q_start:02d}", end=f"{q_end:02d}")
    ], keyboard=user_settings_kb(user))


async def settings_quiet_off(update, context):
    query = update.callback_query
    user = context.user_data["_db_user"]
    await db.update_user(user.id, quiet_start=None, quiet_end=None)
    user = await db.get_user(user.id)
    context.user_data["_db_user"] = user
    await query.answer(t('user_settings.quiet_disabled_toast'))
    await _show(update, context, [
        t('user_settings.quiet_disabled')
    ], keyboard=user_settings_kb(user))


async def settings_tz(update, context):
    query = update.callback_query
    await query.answer()
    await _show(update, context, [
        t('user_settings.tz_title') + "\n\n"
        + t('user_settings.tz_prompt')
    ], keyboard=settings_tz_kb())


async def set_tz(update, context):
    query = update.callback_query
    data = query.data
    tz = data.replace("set_tz_", "")
    user = context.user_data["_db_user"]
    await db.update_user(user.id, timezone=tz)
    user = await db.get_user(user.id)
    context.user_data["_db_user"] = user
    tz_short = tz.split("/")[-1].replace("_", " ")
    await query.answer(t('user_settings.tz_toast', tz=tz_short))
    await _show(update, context, [
        t('user_settings.tz_updated', tz=tz_short)
    ], keyboard=user_settings_kb(user))


async def settings_lang(update, context):
    query = update.callback_query
    await query.answer()
    user = context.user_data["_db_user"]
    lang_code = getattr(user, "language", "en") or "en"
    from bot.i18n import LANGUAGE_NAMES
    lang_name = LANGUAGE_NAMES.get(lang_code, lang_code)
    await _show(update, context, [
        t('user_settings.lang_title') + "\n\n"
        + t('language.current', lang=lang_name) + "\n\n"
        + t('language.select')
    ], keyboard=language_kb(lang_code))


async def lang_region(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    region = data.replace("lang_region_", "")
    user = context.user_data["_db_user"]
    lang_code = getattr(user, "language", "en") or "en"
    from bot.i18n import LANGUAGE_NAMES
    lang_name = LANGUAGE_NAMES.get(lang_code, lang_code)
    await _show(update, context, [
        t('user_settings.lang_title') + "\n\n"
        + t('language.current', lang=lang_name) + "\n\n"
        + t('language.select')
    ], keyboard=language_kb(lang_code, region=region))


async def set_lang(update, context):
    query = update.callback_query
    data = query.data
    lang = data.replace("set_lang_", "")
    from bot.i18n import SUPPORTED_LANGUAGES, LANGUAGE_NAMES
    if lang not in SUPPORTED_LANGUAGES:
        await query.answer(t('common.unknown_language'), show_alert=True)
        return
    user = context.user_data["_db_user"]
    await db.update_user(user.id, language=lang)
    user = await db.get_user(user.id)
    context.user_data["_db_user"] = user
    lang_name = LANGUAGE_NAMES.get(lang, lang)
    await query.answer(f"✅ {lang_name}")
    await _show(update, context, [
        t('language.changed', lang=lang_name)
    ], keyboard=user_settings_kb(user))


def register(router):
    """Register user-settings routes."""
    router.exact("cmd_settings", cmd_settings)
    router.exact("settings_quiet", settings_quiet)
    router.exact("settings_quiet_set", settings_quiet_set)
    router.prefix("quiet_set_", quiet_set)
    router.exact("settings_quiet_off", settings_quiet_off)
    router.exact("settings_tz", settings_tz)
    router.prefix("set_tz_", set_tz)
    router.exact("settings_lang", settings_lang)
    router.prefix("lang_region_", lang_region)
    router.prefix("set_lang_", set_lang)
