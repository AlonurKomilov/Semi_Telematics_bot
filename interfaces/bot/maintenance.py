"""Maintenance Scheduler — task CRUD, truck picker, odometer checks."""

from datetime import datetime as _dt, timezone, timedelta
from constants import TZ_ET as _TZ_ET
from capabilities.localization.i18n import t

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, Application
from telegram.constants import ParseMode

from adapters.storage import Role
from capabilities.iam.permissions import can
from adapters.samsara.client import populate_company_display
from core.context import get_company_display
from core.bot_registry import get_app_for_account

from interfaces.bot.config import logger, get_user_company_codes, get_platform_db, get_tenant_db
from core.isolation import run_account_job
from capabilities.telemetry.service import get_vehicle_odometer
from capabilities.vehicle_catalog.service import get_fleet_overview as _svc_fleet_overview
from capabilities.maintenance.service import (
    mark_overdue_tasks_by_date,
    mark_overdue_tasks_by_mileage,
    TASK_TYPES,
    has_maintenance_access,
)
from interfaces.bot.keyboards import (
    back_kb, maintenance_menu_kb,
    maint_company_picker_kb, maint_vehicle_list_kb,
    maint_type_kb, maint_due_kb, maint_miles_kb, maint_desc_kb,
    maint_task_detail_kb, maint_edit_kb,
    maint_delete_confirm_kb, maint_task_list_kb,
)
from interfaces.bot.helpers import _show, _show_loading, _safe_error
from interfaces.bot.auth import _require_registered


def _task_label(task_type: str) -> str:
    return TASK_TYPES.get(task_type, task_type)


def _check_perm(user) -> bool:
    return has_maintenance_access(user.role)


# ── Main menu ─────────────────────────────────────────────────────

@_require_registered
async def cmd_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show maintenance menu."""
    user = context.user_data["_db_user"]
    if not _check_perm(user):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    # Clear any wizard state
    context.user_data.pop("_pending", None)
    context.user_data.pop("_maint", None)

    text = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  🔧  <b>{t('maintenance.title')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('maintenance.description')}\n"
    )
    await _show(update, context, [text], keyboard=maintenance_menu_kb())


# ══════════════════════════════════════════════════════════════════
# ADD TASK — Wizard Flow
# ══════════════════════════════════════════════════════════════════

@_require_registered
async def cmd_maint_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start add-task wizard — step 1: pick truck from Samsara fleet."""
    user = context.user_data["_db_user"]
    if not _check_perm(user):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    context.user_data["_maint"] = {}
    context.user_data.pop("_pending", None)

    # Driver with assigned truck — auto-fill and skip to type selection
    if user.role == Role.DRIVER and user.truck_num:
        context.user_data["_maint"]["truck"] = user.truck_num
        context.user_data["_maint"]["vehicle_id"] = ""
        context.user_data["_maint"]["company"] = ""
        await _show(update, context, [
            f"🚛 Truck: <b>#{user.truck_num}</b>\n\n"
            f"<b>Step 1/5</b> — Select task type:"
        ], keyboard=maint_type_kb())
        return

    # Get companies
    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    codes = [o.code for o in companies]
    populate_company_display(companies)

    if len(codes) == 1:
        # Single company — go straight to truck list
        await _show_maint_truck_list(update, context, user, codes[0])
    else:
        await _show(update, context, [
            f"{t('maintenance.add_title')}\n\n"
            f"<b>Step 1/5</b> — Select company:"
        ], keyboard=maint_company_picker_kb(codes))


async def _show_maint_truck_list(update, context, user, company_filter, page=0):
    """Show paginated truck list for maintenance task creation."""
    await _show_loading(update, context, "⏳ Loading trucks…")

    try:
        fleet = await _svc_fleet_overview(user.account_id, company=company_filter)
    except Exception as e:
        logger.warning(f"Fleet fetch failed for maint truck list: {e}")
        await _show(update, context, ["❌ Could not load fleet data."],
                     keyboard=maintenance_menu_kb())
        return

    if not fleet:
        await _show(update, context, ["ℹ️ No active vehicles found."],
                     keyboard=maintenance_menu_kb())
        return

    await _show(update, context, [
        f"{t('maintenance.add_title')}\n\n"
        f"<b>Step 1/5</b> — Select truck ({len(fleet)} vehicles):"
    ], keyboard=maint_vehicle_list_kb(fleet, page=page, company_filter=company_filter))


@_require_registered
async def cmd_maint_select_truck(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                  company: str = "", truck_name: str = ""):
    """Truck selected — store and show type picker."""
    wiz = context.user_data.setdefault("_maint", {})
    wiz["truck"] = truck_name
    wiz["company"] = company
    wiz["vehicle_id"] = ""  # will try to find it

    # Try to get vehicle_id from fleet
    if company:
        try:
            user = context.user_data["_db_user"]
            fleet = await _svc_fleet_overview(user.account_id, company=company)
            for v in fleet:
                if v["name"] == truck_name:
                    wiz["vehicle_id"] = v.get("id", "")
                    break
        except Exception as e:
            logger.debug(f"Vehicle ID lookup failed: {e}")

    await _show(update, context, [
        f"🚛 Truck: <b>#{truck_name}</b>"
        + (f" — {get_company_display().get(company, company)}" if company else "")
        + f"\n\n<b>Step 2/5</b> — Select task type:"
    ], keyboard=maint_type_kb())


@_require_registered
async def cmd_maint_type(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         task_type: str = ""):
    """Wizard step 2: task type selected → show due date input."""
    wiz = context.user_data.get("_maint", {})
    wiz["type"] = task_type
    wiz["type_label"] = _task_label(task_type)

    # Show current odometer if we can
    odo_text = ""
    company = wiz.get("company", "")
    truck = wiz.get("truck", "")
    if company and truck:
        try:
            user = context.user_data["_db_user"]
            odo = await _get_current_odometer(user.account_id, company, truck)
            if odo is not None:
                wiz["current_odo"] = odo
                odo_text = f"\n📏 Current odometer: <b>{odo:,.0f} mi</b>\n"
        except Exception as e:
             logger.debug("Odometer unavailable — non-fatal: %s", e)

    context.user_data["_pending"] = "maint_due"
    await _show(update, context, [
        f"🚛 #{wiz.get('truck', '?')} — {wiz['type_label']}\n"
        f"{odo_text}\n"
        f"<b>Step 3/5</b> — Due date\n"
        f"Enter date (<code>YYYY-MM-DD</code>) or tap Skip:"
    ], keyboard=maint_due_kb())


@_require_registered
async def cmd_maint_skip_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skip due date → show due mileage input."""
    wiz = context.user_data.get("_maint", {})
    wiz["due_date"] = None
    context.user_data["_pending"] = "maint_miles"
    await _show_miles_step(update, context, wiz)


@_require_registered
async def cmd_maint_skip_miles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skip due mileage → show description input."""
    wiz = context.user_data.get("_maint", {})
    wiz["due_miles"] = None
    context.user_data["_pending"] = "maint_desc"
    await _show_desc_step(update, context, wiz)


@_require_registered
async def cmd_maint_skip_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skip description → create task."""
    wiz = context.user_data.get("_maint", {})
    wiz["description"] = ""
    await _finalize_task(update, context)


async def _show_miles_step(update, context, wiz):
    """Show the due-mileage input step."""
    odo_text = ""
    if wiz.get("current_odo"):
        odo_text = f"\n📏 Current odometer: <b>{wiz['current_odo']:,.0f} mi</b>\n"
    await _show(update, context, [
        f"🚛 #{wiz.get('truck', '?')} — {wiz.get('type_label', '?')}\n"
        f"{odo_text}\n"
        f"<b>Step 4/5</b> — Due mileage\n"
        f"Enter odometer reading (e.g. <code>250000</code>) or tap Skip:"
    ], keyboard=maint_miles_kb())


async def _show_desc_step(update, context, wiz):
    """Show the description input step."""
    await _show(update, context, [
        f"🚛 #{wiz.get('truck', '?')} — {wiz.get('type_label', '?')}\n\n"
        f"<b>Step 5/5</b> — Description (optional)\n"
        f"Enter notes or tap Skip:"
    ], keyboard=maint_desc_kb())


async def _finalize_task(update, context):
    """Save the task to DB and show success."""
    user = context.user_data["_db_user"]
    wiz = context.user_data.get("_maint", {})

    try:
        tenant = await get_tenant_db(user.account_id)
        task_id = await tenant.add_maintenance_task(
            account_id=user.account_id,
            company_code=wiz.get("company", ""),
            vehicle_id=wiz.get("vehicle_id", ""),
            vehicle_name=wiz.get("truck", "Unknown"),
            task_type=wiz.get("type", "custom"),
            description=wiz.get("description", ""),
            due_date=wiz.get("due_date"),
            due_miles=wiz.get("due_miles"),
            created_by=user.telegram_id,
        )
        context.user_data.pop("_pending", None)
        context.user_data.pop("_maint", None)

        type_label = _task_label(wiz.get("type", ""))
        due_date = wiz.get("due_date") or "—"
        due_miles = wiz.get("due_miles")
        due_mi_str = f"{due_miles:,.0f} mi" if due_miles else "—"
        desc = wiz.get("description", "")

        await _show(update, context, [
            f"✅ <b>Task Created!</b>\n\n"
            f"  🚛 #{wiz.get('truck', '?')}\n"
            f"  📋 {type_label}\n"
            f"  📅 Due: {due_date}\n"
            f"  🛣 Due miles: {due_mi_str}\n"
            + (f"  📝 {desc}\n" if desc else "")
        ], keyboard=maintenance_menu_kb())
    except Exception as e:
        logger.error(f"Maintenance task save error: {e}")
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


# ══════════════════════════════════════════════════════════════════
# VIEW TASKS — Paginated List
# ══════════════════════════════════════════════════════════════════

@_require_registered
async def cmd_maint_view(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         page: int = 0):
    """View maintenance tasks with pagination."""
    user = context.user_data["_db_user"]
    if not _check_perm(user):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await _show_loading(update, context, t("maintenance.loading_tasks"))

    vehicle_filter = None
    if user.role == Role.DRIVER and not can(user.role, "can_maintenance_all"):
        vehicle_filter = user.truck_num

    tenant = await get_tenant_db(user.account_id)
    tasks = await tenant.get_maintenance_tasks(user.account_id, vehicle_name=vehicle_filter)

    if not tasks:
        await _show(update, context, [
            f"{t('maintenance.view_empty')}\n\n"
            f"{t('maintenance.view_empty_hint')}"
        ], keyboard=maintenance_menu_kb())
        return

    # Summary counts
    overdue = sum(1 for t_ in tasks if t_["status"] == "overdue")
    pending = sum(1 for t_ in tasks if t_["status"] == "pending")
    done = sum(1 for t_ in tasks if t_["status"] == "done")

    header = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  🔧  <b>{t('maintenance.view_tasks_title')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  📊 {len(tasks)} total"
    )
    if overdue:
        header += f" · 🔴 {overdue} overdue"
    if pending:
        header += f" · 🟡 {pending} pending"
    if done:
        header += f" · ✅ {done} done"
    header += "\n\n  Tap a task to view details:"

    await _show(update, context, [header],
                keyboard=maint_task_list_kb(tasks, page=page))


# ══════════════════════════════════════════════════════════════════
# TASK DETAIL — View / Done / Edit / Delete
# ══════════════════════════════════════════════════════════════════

@_require_registered
async def cmd_maint_detail(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            task_id: int = 0):
    """Show detailed view of a single task."""
    user = context.user_data["_db_user"]
    tenant = await get_tenant_db(user.account_id)
    task = await tenant.get_maintenance_task(task_id)
    if not task or task["account_id"] != user.account_id:
        if update.callback_query:
            await update.callback_query.answer("⛔ Task not found", show_alert=True)
        return

    status_emoji = {"overdue": "🔴", "pending": "🟡", "done": "✅"}
    emoji = status_emoji.get(task["status"], "⚪")
    type_label = _task_label(task["task_type"])

    due_date = task.get("due_date") or "—"
    due_miles = task.get("due_miles")
    due_mi_str = f"{due_miles:,.0f} mi" if due_miles else "—"
    company = task.get("company_code", "")
    company_display = get_company_display().get(company, company) if company else ""

    lines = [
        f"━━━━━━━━━━━━━━━━━━━",
        f"  {emoji}  <b>Task #{task['id']}</b>",
        f"━━━━━━━━━━━━━━━━━━━\n",
        f"  🚛 Truck: <b>#{task['vehicle_name']}</b>",
    ]
    if company_display:
        lines.append(f"  🏢 Company: {company_display}")
    lines.extend([
        f"  📋 Type: {type_label}",
        f"  📅 Due date: {due_date}",
        f"  🛣 Due miles: {due_mi_str}",
        f"  📊 Status: <b>{task['status'].upper()}</b>",
    ])
    if task.get("description"):
        lines.append(f"  📝 {task['description']}")
    if task.get("created_at"):
        lines.append(f"\n  🕐 Created: {task['created_at'][:10]}")
    if task.get("completed_at"):
        lines.append(f"  ✅ Completed: {task['completed_at'][:10]}")
    if task.get("completion_notes"):
        lines.append(f"  📝 Notes: {task['completion_notes']}")

    await _show(update, context, ["\n".join(lines)],
                keyboard=maint_task_detail_kb(task["id"], task["status"]))


@_require_registered
async def cmd_maint_done(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         task_id: int = 0):
    """Mark a task as done."""
    user = context.user_data["_db_user"]
    tenant = await get_tenant_db(user.account_id)
    task = await tenant.get_maintenance_task(task_id)
    if not task or task["account_id"] != user.account_id:
        if update.callback_query:
            await update.callback_query.answer("⛔ Task not found", show_alert=True)
        return

    await tenant.update_maintenance_status(task_id, "done")

    # Handle recurring tasks — auto-create next occurrence
    recur_days = task.get("recur_interval_days")
    recur_miles = task.get("recur_interval_miles")
    next_info = ""
    if recur_days or recur_miles:
        new_due_date = None
        new_due_miles = None
        if recur_days and task.get("due_date"):
            try:
                old = _dt.strptime(task["due_date"], "%Y-%m-%d")
                new_due_date = (old + timedelta(days=recur_days)).strftime("%Y-%m-%d")
            except ValueError:
                pass
        if recur_miles and task.get("due_miles"):
            new_due_miles = task["due_miles"] + recur_miles

        await tenant.add_maintenance_task(
            account_id=task["account_id"],
            company_code=task.get("company_code", ""),
            vehicle_id=task.get("vehicle_id", ""),
            vehicle_name=task["vehicle_name"],
            task_type=task["task_type"],
            description=task.get("description", ""),
            due_date=new_due_date,
            due_miles=new_due_miles,
            created_by=task["created_by"],
            recur_interval_days=recur_days,
            recur_interval_miles=recur_miles,
        )
        next_info = "\n\n🔄 Recurring task — next occurrence created automatically."

    type_label = _task_label(task["task_type"])
    await _show(update, context, [
        f"✅ <b>Task Completed!</b>\n\n"
        f"  🚛 #{task['vehicle_name']} — {type_label}"
        f"{next_info}"
    ], keyboard=maintenance_menu_kb())


# ── Delete ────────────────────────────────────────────────────────

@_require_registered
async def cmd_maint_delete(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            task_id: int = 0):
    """Show delete confirmation."""
    user = context.user_data["_db_user"]
    tenant = await get_tenant_db(user.account_id)
    task = await tenant.get_maintenance_task(task_id)
    if not task or task["account_id"] != user.account_id:
        if update.callback_query:
            await update.callback_query.answer("⛔ Task not found", show_alert=True)
        return

    type_label = _task_label(task["task_type"])
    await _show(update, context, [
        f"⚠️ <b>Delete Task?</b>\n\n"
        f"  🚛 #{task['vehicle_name']} — {type_label}\n\n"
        f"This action cannot be undone."
    ], keyboard=maint_delete_confirm_kb(task_id))


@_require_registered
async def cmd_maint_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                    task_id: int = 0):
    """Actually delete the task."""
    user = context.user_data["_db_user"]
    tenant = await get_tenant_db(user.account_id)
    task = await tenant.get_maintenance_task(task_id)
    if not task or task["account_id"] != user.account_id:
        if update.callback_query:
            await update.callback_query.answer("⛔ Task not found", show_alert=True)
        return

    await tenant.delete_maintenance_task(task_id)
    await _show(update, context, [
        "🗑 <b>Task deleted.</b>"
    ], keyboard=maintenance_menu_kb())


# ══════════════════════════════════════════════════════════════════
# EDIT TASK
# ══════════════════════════════════════════════════════════════════

@_require_registered
async def cmd_maint_edit(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          task_id: int = 0):
    """Show edit menu for a task."""
    user = context.user_data["_db_user"]
    tenant = await get_tenant_db(user.account_id)
    task = await tenant.get_maintenance_task(task_id)
    if not task or task["account_id"] != user.account_id:
        if update.callback_query:
            await update.callback_query.answer("⛔ Task not found", show_alert=True)
        return

    type_label = _task_label(task["task_type"])
    due_mi = task.get("due_miles")
    await _show(update, context, [
        f"✏️ <b>Edit Task #{task['id']}</b>\n\n"
        f"  🚛 #{task['vehicle_name']} — {type_label}\n"
        f"  📅 Due date: {task.get('due_date') or '—'}\n"
        f"  🛣 Due miles: {f'{due_mi:,.0f} mi' if due_mi else '—'}\n"
        f"  📝 {task.get('description') or '—'}\n\n"
        f"Select field to edit:"
    ], keyboard=maint_edit_kb(task_id))


@_require_registered
async def cmd_maint_edit_type(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               task_id: int = 0):
    """Start editing task type — show type picker."""
    context.user_data["_pending"] = "maint_edit_type"
    context.user_data["_maint_edit_id"] = task_id

    # Reuse the type keyboard but with edit callback prefix
    types = list(TASK_TYPES.items())
    rows = []
    for i in range(0, len(types), 2):
        row = []
        for key, label in types[i:i + 2]:
            row.append(InlineKeyboardButton(label, callback_data=f"maint_setype_{task_id}_{key}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("◀️ Cancel", callback_data=f"maint_edit_{task_id}")])

    await _show(update, context, [
        "📋 Select new task type:"
    ], keyboard=InlineKeyboardMarkup(rows))


@_require_registered
async def cmd_maint_set_type(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              task_id: int = 0, new_type: str = ""):
    """Apply type change."""
    user = context.user_data["_db_user"]
    tenant = await get_tenant_db(user.account_id)
    task = await tenant.get_maintenance_task(task_id)
    if not task or task["account_id"] != user.account_id:
        return
    await tenant.update_maintenance_task(task_id, task_type=new_type)
    context.user_data.pop("_pending", None)
    context.user_data.pop("_maint_edit_id", None)
    await cmd_maint_detail(update, context, task_id=task_id)


@_require_registered
async def cmd_maint_edit_date(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               task_id: int = 0):
    """Prompt for new due date."""
    context.user_data["_pending"] = "maint_edit_date"
    context.user_data["_maint_edit_id"] = task_id
    await _show(update, context, [
        "📅 Enter new due date (<code>YYYY-MM-DD</code>):"
    ], keyboard=InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Remove Date", callback_data=f"maint_rmdate_{task_id}")],
        [InlineKeyboardButton("◀️ Cancel", callback_data=f"maint_edit_{task_id}")],
    ]))


@_require_registered
async def cmd_maint_edit_miles(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                task_id: int = 0):
    """Prompt for new due mileage."""
    context.user_data["_pending"] = "maint_edit_miles"
    context.user_data["_maint_edit_id"] = task_id
    await _show(update, context, [
        "🛣 Enter new due mileage (e.g. <code>250000</code>):"
    ], keyboard=InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Remove Mileage", callback_data=f"maint_rmmiles_{task_id}")],
        [InlineKeyboardButton("◀️ Cancel", callback_data=f"maint_edit_{task_id}")],
    ]))


@_require_registered
async def cmd_maint_edit_desc(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               task_id: int = 0):
    """Prompt for new description."""
    context.user_data["_pending"] = "maint_edit_desc"
    context.user_data["_maint_edit_id"] = task_id
    await _show(update, context, [
        "📝 Enter new description:"
    ], keyboard=InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Remove Description", callback_data=f"maint_rmdesc_{task_id}")],
        [InlineKeyboardButton("◀️ Cancel", callback_data=f"maint_edit_{task_id}")],
    ]))


@_require_registered
async def cmd_maint_remove_field(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                  task_id: int = 0, field: str = ""):
    """Remove a field value (date, miles, or desc)."""
    user = context.user_data["_db_user"]
    tenant = await get_tenant_db(user.account_id)
    task = await tenant.get_maintenance_task(task_id)
    if not task or task["account_id"] != user.account_id:
        return
    field_map = {"date": "due_date", "miles": "due_miles", "desc": "description"}
    db_field = field_map.get(field)
    if db_field:
        await tenant.update_maintenance_task(task_id, **{db_field: None if db_field != "description" else ""})
    context.user_data.pop("_pending", None)
    context.user_data.pop("_maint_edit_id", None)
    await cmd_maint_detail(update, context, task_id=task_id)


# ── Public wrappers for callback router ──────────────────────────

@_require_registered
async def cmd_maint_company_pick(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                  company: str = ""):
    """Company picked in add-task flow — show truck list."""
    user = context.user_data["_db_user"]
    if not _check_perm(user):
        return
    await _show_maint_truck_list(update, context, user, company)


@_require_registered
async def cmd_maint_truck_page(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                company: str = "", page: int = 0):
    """Paginate the truck list in add-task flow."""
    user = context.user_data["_db_user"]
    if not _check_perm(user):
        return
    await _show_maint_truck_list(update, context, user, company, page=page)


# ══════════════════════════════════════════════════════════════════
# TEXT INPUT HANDLER
# ══════════════════════════════════════════════════════════════════

async def handle_maintenance_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle multi-step maintenance wizard text inputs."""
    user = context.user_data.get("_db_user")
    if not user:
        return False

    pending = context.user_data.get("_pending", "")
    wiz = context.user_data.get("_maint", {})
    text = update.message.text.strip()

    # ── Add-task wizard steps ──────────────────────────────

    if pending == "maint_due":
        try:
            _dt.strptime(text, "%Y-%m-%d")
            wiz["due_date"] = text
        except ValueError:
            await _show(update, context, [
                "❌ Invalid date format. Use <code>YYYY-MM-DD</code>\n"
                "Or tap ⏭ to skip:"
            ], keyboard=maint_due_kb())
            return True

        context.user_data["_pending"] = "maint_miles"
        await _show_miles_step(update, context, wiz)
        return True

    elif pending == "maint_miles":
        try:
            miles = float(text.replace(",", ""))
            if miles <= 0:
                raise ValueError
            wiz["due_miles"] = miles
        except ValueError:
            await _show(update, context, [
                "❌ Invalid mileage. Enter a positive number (e.g. <code>250000</code>)\n"
                "Or tap ⏭ to skip:"
            ], keyboard=maint_miles_kb())
            return True

        context.user_data["_pending"] = "maint_desc"
        await _show_desc_step(update, context, wiz)
        return True

    elif pending == "maint_desc":
        wiz["description"] = text
        await _finalize_task(update, context)
        return True

    # ── Edit steps ─────────────────────────────────────────

    elif pending == "maint_edit_date":
        task_id = context.user_data.get("_maint_edit_id", 0)
        try:
            _dt.strptime(text, "%Y-%m-%d")
        except ValueError:
            await _show(update, context, [
                "❌ Invalid date. Use <code>YYYY-MM-DD</code>"
            ], keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Cancel", callback_data=f"maint_edit_{task_id}")],
            ]))
            return True
        tenant = await get_tenant_db(user.account_id)
        await tenant.update_maintenance_task(task_id, due_date=text)
        context.user_data.pop("_pending", None)
        context.user_data.pop("_maint_edit_id", None)
        await cmd_maint_detail(update, context, task_id=task_id)
        return True

    elif pending == "maint_edit_miles":
        task_id = context.user_data.get("_maint_edit_id", 0)
        try:
            miles = float(text.replace(",", ""))
            if miles <= 0:
                raise ValueError
        except ValueError:
            await _show(update, context, [
                "❌ Invalid mileage. Enter a positive number."
            ], keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Cancel", callback_data=f"maint_edit_{task_id}")],
            ]))
            return True
        tenant = await get_tenant_db(user.account_id)
        await tenant.update_maintenance_task(task_id, due_miles=miles)
        context.user_data.pop("_pending", None)
        context.user_data.pop("_maint_edit_id", None)
        await cmd_maint_detail(update, context, task_id=task_id)
        return True

    elif pending == "maint_edit_desc":
        task_id = context.user_data.get("_maint_edit_id", 0)
        tenant = await get_tenant_db(user.account_id)
        await tenant.update_maintenance_task(task_id, description=text)
        context.user_data.pop("_pending", None)
        context.user_data.pop("_maint_edit_id", None)
        await cmd_maint_detail(update, context, task_id=task_id)
        return True

    return False


# ══════════════════════════════════════════════════════════════════
# ODOMETER HELPERS
# ══════════════════════════════════════════════════════════════════

# _get_current_odometer is the canonical get_vehicle_odometer in capabilities/telemetry/service.py
_get_current_odometer = get_vehicle_odometer


# ══════════════════════════════════════════════════════════════════
# SCHEDULED JOBS
# ══════════════════════════════════════════════════════════════════

async def check_overdue_maintenance(app: Application):
    """Scheduled job: check for overdue maintenance tasks (by date).

    Runs daily. Business logic (DB mutations) delegated to
    capabilities.maintenance.service.mark_overdue_tasks_by_date.
    """
    try:
        accounts = await get_platform_db().list_accounts()
    except Exception as e:
        logger.error(f"Overdue date check — cannot list accounts: {e}", exc_info=True)
        return

    total_marked = 0
    for account in accounts:
        async def _run(acct=account):
            nonlocal total_marked
            bot_app = get_app_for_account(acct.id)
            if not bot_app:
                logger.warning("No bot for account %d — skipping overdue date check", acct.id)
                return
            tenant = await get_tenant_db(acct.id)
            newly_overdue = await mark_overdue_tasks_by_date(acct.id, tenant)
            total_marked += len(newly_overdue)
            for task in newly_overdue:
                try:
                    type_label = _task_label(task["task_type"])
                    notify_text = (
                        f"🔴 <b>Overdue Maintenance</b>\n\n"
                        f"  🚛 #{task['vehicle_name']}\n"
                        f"  📋 {type_label}\n"
                        f"  📅 Due: {task.get('due_date', '?')}\n"
                    )
                    if task["created_by"]:
                        await bot_app.bot.send_message(
                            chat_id=task["created_by"],
                            text=notify_text,
                            parse_mode=ParseMode.HTML,
                        )
                    await _notify_account_admins(bot_app, acct.id, notify_text,
                                                 exclude=task["created_by"])
                except Exception as e:
                    logger.debug(f"Overdue notification failed: {e}")

        await run_account_job(_run(), account_id=account.id,
                              job_name="overdue_date_check")

    if total_marked:
        logger.info(f"Marked {total_marked} maintenance task(s) as overdue (date)")


async def check_overdue_by_mileage(app: Application):
    """Scheduled job: check for overdue maintenance by odometer reading.

    Runs every 6 hours. Business logic (DB mutations + odometer fetching)
    delegated to capabilities.maintenance.service.mark_overdue_tasks_by_mileage.
    """
    try:
        accounts = await get_platform_db().list_accounts()
    except Exception as e:
        logger.error(f"Odometer check — cannot list accounts: {e}", exc_info=True)
        return

    total_marked = 0

    for account in accounts:
        async def _run(acct=account):
            nonlocal total_marked
            bot_app = get_app_for_account(acct.id)
            if not bot_app:
                logger.warning("No bot for account %d — skipping mileage check", acct.id)
                return
            tenant = await get_tenant_db(acct.id)
            newly_overdue = await mark_overdue_tasks_by_mileage(acct.id, tenant)
            total_marked += len(newly_overdue)
            for task in newly_overdue:
                try:
                    type_label = _task_label(task["task_type"])
                    due_miles = task["due_miles"]
                    current_miles = task.get("_current_miles", due_miles)
                    notify_text = (
                        f"🔴 <b>Overdue Maintenance (Mileage)</b>\n\n"
                        f"  🚛 #{task['vehicle_name']}\n"
                        f"  📋 {type_label}\n"
                        f"  🛣 Due at: {due_miles:,.0f} mi\n"
                        f"  📏 Current: {current_miles:,.0f} mi\n"
                    )
                    if task["created_by"]:
                        await bot_app.bot.send_message(
                            chat_id=task["created_by"],
                            text=notify_text,
                            parse_mode=ParseMode.HTML,
                        )
                    await _notify_account_admins(
                        bot_app, acct.id, notify_text,
                        exclude=task["created_by"],
                    )
                except Exception as e:
                    logger.debug(f"Mileage overdue notification failed: {e}")

        await run_account_job(_run(), account_id=account.id,
                              job_name="overdue_mileage_check")

    if total_marked:
        logger.info(f"Marked {total_marked} maintenance task(s) as overdue (mileage)")


async def _notify_account_admins(app: Application, account_id: int, text: str,
                                  exclude: int = 0):
    """Send a notification to all admins/owners of an account (except exclude)."""
    try:
        users = await get_platform_db().list_account_users(account_id)
        for u in users:
            if u.telegram_id == exclude:
                continue
            if u.role in (Role.OWNER, Role.ADMIN):
                try:
                    await app.bot.send_message(
                        chat_id=u.telegram_id,
                        text=text,
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as e:
                    logger.debug("Could not notify admin %d: %s", u.telegram_id, e)
    except Exception as e:
        logger.warning("_notify_account_admins failed for account %d: %s", account_id, e)
