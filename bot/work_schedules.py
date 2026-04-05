"""Work Schedules — CRUD for employee working-hour schedules."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from permissions import can
from bot.config import db, logger
from bot.keyboards import (
    work_schedules_kb, work_schedule_detail_kb,
    work_schedule_hour_picker_kb, work_schedule_role_picker_kb,
    back_kb,
)
from bot.helpers import _show, _safe_error
from bot.auth import _require_registered
from bot.i18n import t


@_require_registered
async def cmd_work_schedules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of work schedules."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_users"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return
    schedules = await db.get_work_schedules(user.account_id)
    await _show(update, context, [
        "━━━━━━━━━━━━━━━━━━━\n"
        "  🕐  <b>Working Hours</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n  Manage employee shift schedules.\n"
        "  Tap a schedule to view or edit."
    ], keyboard=work_schedules_kb(schedules))


@_require_registered
async def cmd_wsched_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding a new work schedule — ask for label."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_manage_users"):
        return
    context.user_data["_pending"] = "wsched_label"
    await _show(update, context, [
        "📝 Enter a label for this schedule\n"
        "(e.g. <code>Day Shift</code>):"
    ], keyboard=InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Cancel", callback_data="cmd_work_schedules")],
    ]))


@_require_registered
async def cmd_wsched_view(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          schedule_id: int = 0):
    """View a single work schedule."""
    user = context.user_data["_db_user"]
    sched = await db.get_work_schedule(schedule_id)
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
    ], keyboard=work_schedule_detail_kb(schedule_id))


@_require_registered
async def cmd_wsched_rename(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            schedule_id: int = 0):
    """Prompt for new schedule label."""
    context.user_data["_pending"] = "wsched_rename"
    context.user_data["_wsched_edit_id"] = schedule_id
    await _show(update, context, [
        "📝 Enter new label for this schedule:"
    ], keyboard=InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Cancel", callback_data=f"wsched_view_{schedule_id}")],
    ]))


@_require_registered
async def cmd_wsched_hours(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           schedule_id: int = 0):
    """Show start hour picker."""
    context.user_data["_wsched_edit_id"] = schedule_id
    await _show(update, context, [
        "⏰ Select <b>start</b> hour:"
    ], keyboard=work_schedule_hour_picker_kb(f"wsched_start_{schedule_id}"))


@_require_registered
async def cmd_wsched_start_hour(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                schedule_id: int = 0, hour: int = 0):
    """Start hour selected — show end hour picker."""
    context.user_data["_wsched_start"] = hour
    context.user_data["_wsched_edit_id"] = schedule_id
    await _show(update, context, [
        f"⏰ Start: <b>{hour:02d}:00</b>\nSelect <b>end</b> hour:"
    ], keyboard=work_schedule_hour_picker_kb(f"wsched_end_{schedule_id}"))


@_require_registered
async def cmd_wsched_end_hour(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              schedule_id: int = 0, hour: int = 0):
    """End hour selected — save hours."""
    start = context.user_data.pop("_wsched_start", 0)
    await db.update_work_schedule(schedule_id, start_hour=start, end_hour=hour)
    context.user_data.pop("_wsched_edit_id", None)
    await cmd_wsched_view(update, context, schedule_id=schedule_id)


@_require_registered
async def cmd_wsched_changerole(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                schedule_id: int = 0):
    """Show role picker for a schedule."""
    context.user_data["_wsched_edit_id"] = schedule_id
    await _show(update, context, [
        "👥 Select which role this schedule applies to:"
    ], keyboard=work_schedule_role_picker_kb())


@_require_registered
async def cmd_wsched_role(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          role: str = "all"):
    """Role selected — save it."""
    sched_id = context.user_data.get("_wsched_edit_id")
    if not sched_id:
        return
    await db.update_work_schedule(sched_id, target_role=role)
    context.user_data.pop("_wsched_edit_id", None)
    await cmd_wsched_view(update, context, schedule_id=sched_id)


@_require_registered
async def cmd_wsched_delete(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            schedule_id: int = 0):
    """Delete a work schedule."""
    user = context.user_data["_db_user"]
    sched = await db.get_work_schedule(schedule_id)
    if not sched or sched["account_id"] != user.account_id:
        return
    await db.delete_work_schedule(schedule_id)
    await cmd_work_schedules(update, context)


async def handle_wsched_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input for work schedule wizard. Returns True if handled."""
    pending = context.user_data.get("_pending")
    user = context.user_data.get("_db_user")
    if not user:
        return False
    text = update.message.text.strip()

    if pending == "wsched_label":
        context.user_data.pop("_pending", None)
        try:
            sched = await db.create_work_schedule(
                account_id=user.account_id,
                label=text,
                start_hour=6,
                end_hour=22,
                created_by=user.telegram_id,
            )
            await cmd_wsched_view(update, context, schedule_id=sched["id"])
        except Exception as e:
            logger.error(f"Work schedule create error: {e}")
            await _show(update, context, [_safe_error(e)], keyboard=back_kb())
        return True

    if pending == "wsched_rename":
        context.user_data.pop("_pending", None)
        sched_id = context.user_data.pop("_wsched_edit_id", None)
        if sched_id:
            await db.update_work_schedule(sched_id, label=text)
            await cmd_wsched_view(update, context, schedule_id=sched_id)
        return True

    return False
