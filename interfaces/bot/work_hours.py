"""Work Hours — CRUD for employee working-hour presets."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from capabilities.iam.permissions import can
from interfaces.bot.config import logger, get_tenant_db
from interfaces.bot.keyboards import (
    work_hours_kb, work_hour_detail_kb,
    work_hour_picker_kb, work_hour_role_picker_kb,
    back_kb,
)
from interfaces.bot.helpers import _show, _safe_error
from interfaces.bot.auth import _require_registered
from capabilities.localization.i18n import t


@_require_registered
async def cmd_work_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of work schedules."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_users"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return
    tenant = await get_tenant_db(user.account_id)
    schedules = await tenant.get_work_hours(user.account_id)
    await _show(update, context, [
        "━━━━━━━━━━━━━━━━━━━\n"
        "  🕐  <b>Working Hours</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n  Manage employee shift schedules.\n"
        "  Tap a schedule to view or edit."
    ], keyboard=work_hours_kb(schedules))


@_require_registered
async def cmd_whours_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding a new work schedule — ask for label."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_users"):
        return
    context.user_data["_pending"] = "whours_label"
    await _show(update, context, [
        "📝 Enter a label for this schedule\n"
        "(e.g. <code>Day Shift</code>):"
    ], keyboard=InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Cancel", callback_data="cmd_work_hours")],
    ]))


@_require_registered
async def cmd_whours_view(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          schedule_id: int = 0):
    """View a single work schedule."""
    user = context.user_data["_db_user"]
    tenant = await get_tenant_db(user.account_id)
    sched = await tenant.get_work_hour(schedule_id)
    if not sched or sched["account_id"] != user.account_id:
        if update.callback_query:
            await update.callback_query.answer("Schedule not found", show_alert=True)
        return

    label = sched.get("label", "Unnamed")
    start = sched.get("start_hour", 0)
    end = sched.get("end_hour", 0)
    role = sched.get("target_role", "all")

    await _show(update, context, [
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  🕐  <b>{label}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  ⏰ Hours: <b>{start:02d}:00 – {end:02d}:00</b>"
        f"\n  👥 Role: <b>{role}</b>"
    ], keyboard=work_hour_detail_kb(schedule_id))


@_require_registered
async def cmd_whours_rename(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            schedule_id: int = 0):
    """Prompt for new schedule label."""
    context.user_data["_pending"] = "whours_rename"
    context.user_data["_whours_edit_id"] = schedule_id
    await _show(update, context, [
        "📝 Enter new label for this schedule:"
    ], keyboard=InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Cancel", callback_data=f"whours_view_{schedule_id}")],
    ]))


@_require_registered
async def cmd_whours_hours(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           schedule_id: int = 0):
    """Show start hour picker."""
    context.user_data["_whours_edit_id"] = schedule_id
    await _show(update, context, [
        "⏰ Select <b>start</b> hour:"
    ], keyboard=work_hour_picker_kb(f"whours_start_{schedule_id}"))


@_require_registered
async def cmd_whours_start_hour(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                schedule_id: int = 0, hour: int = 0):
    """Start hour selected — show end hour picker."""
    context.user_data["_wsched_start"] = hour
    context.user_data["_whours_edit_id"] = schedule_id
    await _show(update, context, [
        f"⏰ Start: <b>{hour:02d}:00</b>\nSelect <b>end</b> hour:"
    ], keyboard=work_hour_picker_kb(f"whours_end_{schedule_id}"))


@_require_registered
async def cmd_whours_end_hour(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              schedule_id: int = 0, hour: int = 0):
    """End hour selected — save hours."""
    user = context.user_data["_db_user"]
    start = context.user_data.pop("_wsched_start", 0)
    tenant = await get_tenant_db(user.account_id)
    await tenant.update_work_hour(schedule_id, start_hour=start, end_hour=hour)
    context.user_data.pop("_whours_edit_id", None)
    await cmd_whours_view(update, context, schedule_id=schedule_id)


@_require_registered
async def cmd_whours_changerole(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                schedule_id: int = 0):
    """Show role picker for a schedule."""
    context.user_data["_whours_edit_id"] = schedule_id
    await _show(update, context, [
        "👥 Select which role this schedule applies to:"
    ], keyboard=work_hour_role_picker_kb())


@_require_registered
async def cmd_whours_role(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          role: str = "all"):
    """Role selected — save it."""
    sched_id = context.user_data.get("_whours_edit_id")
    if not sched_id:
        return
    user = context.user_data["_db_user"]
    tenant = await get_tenant_db(user.account_id)
    await tenant.update_work_hour(sched_id, target_role=role)
    context.user_data.pop("_whours_edit_id", None)
    await cmd_whours_view(update, context, schedule_id=sched_id)


@_require_registered
async def cmd_whours_delete(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            schedule_id: int = 0):
    """Delete a work schedule."""
    user = context.user_data["_db_user"]
    tenant = await get_tenant_db(user.account_id)
    sched = await tenant.get_work_hour(schedule_id)
    if not sched or sched["account_id"] != user.account_id:
        return
    await tenant.delete_work_hour(schedule_id)
    await cmd_work_hours(update, context)


async def handle_whours_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input for work schedule wizard. Returns True if handled."""
    pending = context.user_data.get("_pending")
    user = context.user_data.get("_db_user")
    if not user:
        return False
    text = update.message.text.strip()

    if pending == "whours_label":
        context.user_data.pop("_pending", None)
        try:
            tenant = await get_tenant_db(user.account_id)
            sched = await tenant.create_work_hour(
                account_id=user.account_id,
                label=text,
                start_hour=6,
                end_hour=22,
                created_by=user.telegram_id,
            )
            await cmd_whours_view(update, context, schedule_id=sched["id"])
        except Exception as e:
            logger.error(f"Work schedule create error: {e}")
            await _show(update, context, [_safe_error(e)], keyboard=back_kb())
        return True

    if pending == "whours_rename":
        context.user_data.pop("_pending", None)
        sched_id = context.user_data.pop("_whours_edit_id", None)
        if sched_id:
            tenant = await get_tenant_db(user.account_id)
            await tenant.update_work_hour(sched_id, label=text)
            await cmd_whours_view(update, context, schedule_id=sched_id)
        return True

    return False
