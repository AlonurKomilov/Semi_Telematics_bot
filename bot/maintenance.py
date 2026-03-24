"""Maintenance Scheduler — task CRUD and overdue checking."""

from datetime import datetime as _dt, timezone
from zoneinfo import ZoneInfo as _ZI
from bot.i18n import t

_TZ_ET = _ZI("America/New_York")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, Application
from telegram.constants import ParseMode

from database import Role
from permissions import can
from samsara_client import populate_company_display

from bot.config import db, logger, get_client
from bot.keyboards import back_kb, maintenance_menu_kb, maintenance_task_kb
from bot.helpers import _show, _show_loading, _safe_error
from bot.auth import _require_registered

def _task_types():
    return {
        "oil": t("maintenance.task_types.oil"),
        "tires": t("maintenance.task_types.tires"),
        "brakes": t("maintenance.task_types.brakes"),
        "inspection": t("maintenance.task_types.inspection"),
        "custom": t("maintenance.task_types.custom"),
    }


@_require_registered
async def cmd_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show maintenance menu."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_maintenance_all") or can(user.role, "can_maintenance_own")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    text = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  🔧  <b>{t('maintenance.title')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('maintenance.description')}\n"
    )
    await _show(update, context, [text], keyboard=maintenance_menu_kb())


@_require_registered
async def cmd_maint_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start add-task wizard — step 1: truck name."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_maintenance_all") or can(user.role, "can_maintenance_own")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    context.user_data["_pending"] = "maint_truck"
    context.user_data["_maint"] = {}

    if user.role == Role.DRIVER and user.truck_num:
        # Auto-fill truck for driver
        context.user_data["_maint"]["truck"] = user.truck_num
        context.user_data["_pending"] = "maint_type"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(label, callback_data=f"maint_type_{key}")]
            for key, label in _task_types().items()
        ] + [[InlineKeyboardButton(t("maintenance.cancel"), callback_data="cmd_maintenance")]])
        await _show(update, context, [
            f"{t('maintenance.truck_label', name=user.truck_num)}\n\n"
            f"{t('maintenance.step2_type')}\n"
            f"{t('maintenance.step2_prompt')}"
        ], keyboard=kb)
        return

    await _show(update, context, [
        f"{t('maintenance.add_title')}\n\n"
        f"{t('maintenance.step1_truck')}\n"
        f"{t('maintenance.step1_prompt')}"
    ], keyboard=back_kb())


@_require_registered
async def cmd_maint_type(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         task_type: str = ""):
    """Wizard step 2: select task type via callback."""
    wiz = context.user_data.get("_maint", {})
    wiz["type"] = task_type
    wiz["type_label"] = _task_types().get(task_type, task_type)
    context.user_data["_pending"] = "maint_due"
    await _show(update, context, [
        f"  {wiz['type_label']}\n\n"
        f"{t('maintenance.step3_date')}\n"
        f"{t('maintenance.step3_prompt')}\n"
        f"{t('maintenance.step3_skip')}"
    ], keyboard=back_kb())


@_require_registered
async def cmd_maint_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View maintenance tasks."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_maintenance_all") or can(user.role, "can_maintenance_own")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await _show_loading(update, context, t("maintenance.loading_tasks"))

    vehicle_filter = None
    if user.role == Role.DRIVER and not can(user.role, "can_maintenance_all"):
        vehicle_filter = user.truck_num

    tasks = await db.get_maintenance_tasks(user.account_id, vehicle_name=vehicle_filter)

    if not tasks:
        await _show(update, context, [
            f"{t('maintenance.view_empty')}\n\n"
            f"{t('maintenance.view_empty_hint')}"
        ], keyboard=maintenance_menu_kb())
        return

    lines = [
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  🔧  <b>{t('maintenance.view_tasks_title')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
    ]

    total = len(tasks)
    shown = min(total, 20)
    lines.append(f"\n  {t('maintenance.view_showing', shown=shown, total=total)}\n")

    status_emoji = {"overdue": "🔴", "pending": "🟡", "done": "✅"}

    task_types = _task_types()
    for task in tasks[:20]:
        emoji = status_emoji.get(task["status"], "⚪")
        type_label = task_types.get(task["task_type"], task["task_type"])
        due = task.get("due_date", "—") or "—"
        due_mi = task.get("due_miles")
        due_str = due
        if due_mi:
            due_str += f" / {due_mi:,.0f} mi"
        lines.append(
            f"\n{emoji} <b>{task['vehicle_name']}</b> — {type_label}\n"
            f"   {t('maintenance.due_label')} {due_str}\n"
            f"   {t('maintenance.status_label')} {task['status'].upper()}"
        )
        if task.get("description"):
            lines.append(f"   📝 {task['description']}")

    full = "\n".join(lines)

    # Build action buttons for pending/overdue tasks
    rows = []
    for task in tasks[:10]:
        if task["status"] in ("pending", "overdue"):
            rows.append([InlineKeyboardButton(
                t("maintenance.done_btn", vehicle=task["vehicle_name"], type=task_types.get(task["task_type"], task["task_type"])),
                callback_data=f"maint_done_{task['id']}",
            )])
    rows.append([InlineKeyboardButton(t("maintenance.back"), callback_data="cmd_maintenance")])

    await _show(update, context, [full], keyboard=InlineKeyboardMarkup(rows))


@_require_registered
async def cmd_maint_done(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         task_id: int = 0):
    """Mark a task as done."""
    user = context.user_data["_db_user"]
    # Verify the task belongs to this user's account
    tasks = await db.get_maintenance_tasks(user.account_id)
    if not any(t["id"] == task_id for t in tasks):
        if update.callback_query:
            await update.callback_query.answer(t("maintenance.task_not_found"), show_alert=True)
        return
    await db.update_maintenance_status(task_id, "done")
    # Auto-refresh the task list so user sees immediate update
    await cmd_maint_view(update, context)


async def handle_maintenance_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle multi-step maintenance wizard text inputs."""
    user = context.user_data.get("_db_user")
    if not user:
        return False

    pending = context.user_data.get("_pending", "")
    wiz = context.user_data.get("_maint", {})
    text = update.message.text.strip()

    if pending == "maint_truck":
        wiz["truck"] = text
        context.user_data["_pending"] = "maint_type"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(label, callback_data=f"maint_type_{key}")]
            for key, label in _task_types().items()
        ] + [[InlineKeyboardButton(t("maintenance.cancel"), callback_data="cmd_maintenance")]])
        await _show(update, context, [
            f"{t('maintenance.truck_label', name=text)}\n\n"
            f"{t('maintenance.step2_type')}\n"
            f"{t('maintenance.step2_prompt')}"
        ], keyboard=kb)
        return True

    elif pending == "maint_due":
        if text.lower() == "skip":
            wiz["due_date"] = None
        else:
            try:
                _dt.strptime(text, "%Y-%m-%d")
                wiz["due_date"] = text
            except ValueError:
                await _show(update, context, [
                    f"{t('maintenance.invalid_date')}\n{t('maintenance.invalid_date_skip')}"
                ], keyboard=back_kb())
                return True

        context.user_data["_pending"] = "maint_desc"
        await _show(update, context, [
            f"{t('maintenance.step4_desc')}\n"
            f"{t('maintenance.step4_prompt')}"
        ], keyboard=back_kb())
        return True

    elif pending == "maint_desc":
        desc = "" if text.lower() == "skip" else text

        try:
            await db.add_maintenance_task(
                account_id=user.account_id,
                company_code="",
                vehicle_name=wiz.get("truck", "Unknown"),
                task_type=wiz.get("type", "custom"),
                description=desc,
                due_date=wiz.get("due_date"),
                created_by=user.telegram_id,
            )
            context.user_data.pop("_pending", None)
            context.user_data.pop("_maint", None)

            type_label = _task_types().get(wiz.get("type", ""), wiz.get("type", ""))
            due = wiz.get("due_date") or t("maintenance.no_date_set")
            await _show(update, context, [
                f"{t('maintenance.task_created')}\n\n"
                f"  🚛 {wiz.get('truck', '?')}\n"
                f"  📋 {type_label}\n"
                f"  📅 {t('maintenance.due_label')} {due}\n"
                + (f"  📝 {desc}" if desc else "")
            ], keyboard=maintenance_menu_kb())
        except Exception as e:
            logger.error(f"Maintenance task save error: {e}")
            await _show(update, context, [_safe_error(e)], keyboard=back_kb())
        return True

    return False


async def check_overdue_maintenance(app: Application):
    """Scheduled job: check for overdue maintenance tasks.

    Runs daily. Marks tasks as overdue when due_date has passed.
    """
    try:
        overdue = await db.get_pending_tasks_by_date()
        for task in overdue:
            await db.update_maintenance_status(task["id"], "overdue")

            # Notify the task creator
            try:
                type_label = _task_types().get(task["task_type"], task["task_type"])
                await app.bot.send_message(
                    chat_id=task["created_by"],
                    text=(
                        f"{t('maintenance.overdue_notify')}\n\n"
                        f"  🚛 {task['vehicle_name']}\n"
                        f"  📋 {type_label}\n"
                        f"  📅 {t('maintenance.due_label')} {task.get('due_date', '?')}\n"
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.debug(f"Overdue notification failed: {e}")

        if overdue:
            logger.info(f"Marked {len(overdue)} maintenance task(s) as overdue")
    except Exception as e:
        logger.error(f"Overdue check error: {e}")
