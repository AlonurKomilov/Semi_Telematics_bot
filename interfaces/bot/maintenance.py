"""Maintenance — notification cron jobs + "Done" inline ack.

The bot's maintenance surface used to be a full multi-step CRUD wizard
(company picker → vehicle picker → type → date → mileage → engine
hours → priority → recurrence → description, plus per-task edit /
delete / detail flows — 25+ ``cmd_maint_*`` handlers).  That whole
surface was retired in favour of ``dash.4truck.us/maintenance``,
which gives sortable columns, calendar view, templates, and bulk
actions instead of one-message-at-a-time text wizards.

What's still here:

* ``cmd_maintenance``     — single deep-link redirect to the
                            dashboard; nudges the user when they
                            type the old command or tap a stale
                            menu button.
* ``cmd_maint_done``      — the inline "✓ Mark done" handler.  When
                            a scheduler-posted overdue alert lands in
                            chat, the recipient can clear the task
                            with one tap without opening a browser.
                            This is the on-the-go moment the bot is
                            actually good at; everything else (add,
                            edit, delete, schedule) lives on the
                            dashboard.
* ``check_overdue_*`` /   — APScheduler hooks that the bot still
  ``check_upcoming_*``      owns because the *delivery surface* (per
                            -account bot, forum-topic routing,
                            admin DM fanout, email fallback) is
                            bot-shaped.  Pure system→human alert
                            dispatch; no user-facing UI.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, Application
from telegram.constants import ParseMode

from adapters.storage import Role
from infra.bot_registry import get_app_for_account

from interfaces.bot.config import logger
from interfaces.bot.state import get_platform_db, get_tenant_db
from infra.isolation import run_account_job
from infra.services import get_tenant_db as _get_tenant_db_rls
from features.maintenance.service import (
    mark_overdue_tasks_by_date,
    mark_overdue_tasks_by_mileage,
    mark_overdue_tasks_by_engine_hours,
    detect_upcoming_warnings,
    spawn_recurring_if_completed,
    TASK_TYPES,
)
from interfaces.bot.helpers import _show, reply_dashboard_redirect
from interfaces.bot.auth import _require_registered
from capabilities.alerting.registry import register_alert_source


def _task_label(task_type: str) -> str:
    """Pretty-print a task_type code (used by every alert formatter)."""
    return TASK_TYPES.get(task_type, task_type)


def _done_kb(task_id: int) -> InlineKeyboardMarkup:
    """One-button inline keyboard attached to scheduler-posted alerts.

    Lets the recipient (admin DM, group topic, or maintenance forum
    channel) clear the task with a single tap.  The callback routes
    to ``cmd_maint_done`` via the ``maint_done_`` prefix.
    """
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Mark Done", callback_data=f"maint_done_{task_id}"),
    ]])


# ══════════════════════════════════════════════════════════════════
# COMMAND ENTRY — dashboard redirect
# ══════════════════════════════════════════════════════════════════


@_require_registered
async def cmd_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect to the dashboard Maintenance page."""
    await reply_dashboard_redirect(
        update,
        title="🔧 Maintenance moved",
        body=(
            "Create, edit, and review maintenance tasks on the "
            "dashboard — sortable list, calendar view, templates, "
            "and bulk actions.  The bot still pings you when "
            "something is due."
        ),
        path="/maintenance/tasks",
        label="Open Maintenance",
    )


# ══════════════════════════════════════════════════════════════════
# INLINE "✓ MARK DONE" — only interaction kept on the bot side
# ══════════════════════════════════════════════════════════════════


@_require_registered
async def cmd_maint_done(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         task_id: int = 0):
    """Mark a maintenance task done from an inline button on a
    scheduler-posted alert.

    Driver attestation: stamps ``attested_by`` + ``attested_at`` on
    the row so the DOT binder can show "Driver Jane confirmed oil
    change on 2026-04-12 at 11:42".  Recurring tasks auto-spawn the
    next occurrence via ``spawn_recurring_if_completed``.

    No keyboard is rendered afterward — the original alert message
    already has the "✓ Done" button replaced by a confirmation; the
    user follows the link in the message body if they want to see
    the task list.
    """
    user = context.user_data["_db_user"]
    tenant = await get_tenant_db(user.account_id)
    task = await tenant.get_maintenance_task(task_id)
    if not task or task["account_id"] != user.account_id:
        if update.callback_query:
            await update.callback_query.answer("⛔ Task not found", show_alert=True)
        return

    await tenant.update_maintenance_status(task_id, "done", account_id=user.account_id)

    # Attestation — capture who pressed the button so the audit trail
    # records the human who confirmed completion (not the system).
    if getattr(user, "telegram_id", None):
        try:
            await tenant.record_task_attestation(
                task_id, account_id=user.account_id,
                attested_by=int(user.telegram_id),
            )
        except Exception as e:
            logger.debug("attestation stamp failed for task %d: %s", task_id, e)

    # SSOT recurring-task spawn — same function powers the API
    # completion path so bot + dashboard "mark done" produce
    # identical follow-up rows.
    next_info = ""
    spawned_id = await spawn_recurring_if_completed(
        task_id, user.account_id, "done", tenant,
    )
    if spawned_id:
        next_info = "\n\n🔄 Recurring task — next occurrence created automatically."

    type_label = _task_label(task["task_type"])
    await _show(update, context, [
        f"✅ <b>Task Completed!</b>\n\n"
        f"  🚛 #{task['vehicle_name']} — {type_label}"
        f"{next_info}"
    ], keyboard=None)


# ══════════════════════════════════════════════════════════════════
# ALERT FORMATTER — shared by every check_* job below
# ══════════════════════════════════════════════════════════════════


def _format_maintenance_alert(
    *,
    severity: str,
    title: str,
    vehicle_name: str,
    task_label: str,
    detail_lines: list[str],
    action: str,
) -> str:
    """Build a single maintenance alert in the unified Option A grammar.

    ``detail_lines`` are the rows that go under the body marker — one
    per due-condition (e.g. ``"Due at 50,000 mi"`` + ``"Current 51,200 mi"``).

    Every interpolated field is HTML-escaped — vehicle names and
    task labels come from operator input, ``detail_lines`` may
    embed Samsara values (mileage display strings are safe but a
    custom task label is not).
    """
    from capabilities.formatting.helpers import escape_html
    from capabilities.formatting.severity import badge, marker
    body_marker = marker(severity)

    lines: list[str] = [f"<b>{badge(severity)}</b> — {escape_html(title)}", ""]
    lines.append(f"🚛 <b>Vehicle #{escape_html(str(vehicle_name))}</b>")
    lines.append("")
    lines.append(f"{body_marker} <b>{escape_html(str(task_label))}</b>")
    for d in detail_lines:
        lines.append(f"      {escape_html(str(d))}")
    lines.append("")
    lines.append(f"💡 {escape_html(action)}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# SCHEDULED JOBS
# ══════════════════════════════════════════════════════════════════


@register_alert_source("maintenance_check", trigger="interval", hours=24)
async def check_overdue_maintenance(app: Application):
    """Scheduled job: check for overdue maintenance tasks (by date).

    Runs daily. Business logic (DB mutations) delegated to
    features.maintenance.service.mark_overdue_tasks_by_date.
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
                    notify_text = _format_maintenance_alert(
                        severity="warning",
                        title="Overdue Maintenance",
                        vehicle_name=task["vehicle_name"],
                        task_label=type_label,
                        detail_lines=[f"📅 Due {task.get('due_date', '?')}"],
                        action="Schedule shop · update task once complete",
                    )
                    # Forum routing: post once to the group's
                    # Maintenance topic when configured.  When that
                    # succeeds we skip the per-user DM fanout that
                    # follows — group members see the alert there.
                    from capabilities.alerting.pipeline import post_alert_to_topic
                    done_kb = _done_kb(task["id"])
                    posted = await post_alert_to_topic(
                        bot_app, account_id=acct.id,
                        alert_type="maintenance", text=notify_text,
                        severity="warning",
                        reply_markup=done_kb,
                        subject_id=str(task.get("vehicle_id")
                                       or task.get("vehicle_name") or ""),
                        subject_name=str(task.get("vehicle_name") or ""),
                        dedup_key=f"task:{task['id']}",
                    )
                    if posted:
                        continue
                    if task["created_by"]:
                        await bot_app.bot.send_message(
                            chat_id=task["created_by"],
                            text=notify_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=done_kb,
                        )
                    await _notify_account_admins(bot_app, acct.id, notify_text,
                                                 exclude=task["created_by"],
                                                 reply_markup=done_kb)
                except Exception as e:
                    logger.debug(f"Overdue notification failed: {e}")

        tenant_db_rls = await _get_tenant_db_rls(account.id)
        await run_account_job(_run(), account_id=account.id,
                              job_name="overdue_date_check",
                              tenant_db=tenant_db_rls)

    if total_marked:
        logger.info(f"Marked {total_marked} maintenance task(s) as overdue (date)")


@register_alert_source("maintenance_mileage_check", trigger="interval", hours=6)
async def check_overdue_by_mileage(app: Application):
    """Scheduled job: check for overdue maintenance by odometer reading.

    Runs every 6 hours. Business logic (DB mutations + odometer fetching)
    delegated to features.maintenance.service.mark_overdue_tasks_by_mileage.
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
                    notify_text = _format_maintenance_alert(
                        severity="warning",
                        title="Overdue Maintenance (Mileage)",
                        vehicle_name=task["vehicle_name"],
                        task_label=type_label,
                        detail_lines=[
                            f"🛣 Due at {due_miles:,.0f} mi",
                            f"📏 Current {current_miles:,.0f} mi",
                        ],
                        action="Schedule shop · update task once complete",
                    )
                    from capabilities.alerting.pipeline import post_alert_to_topic
                    done_kb = _done_kb(task["id"])
                    posted = await post_alert_to_topic(
                        bot_app, account_id=acct.id,
                        alert_type="maintenance", text=notify_text,
                        severity="warning",
                        reply_markup=done_kb,
                        subject_id=str(task.get("vehicle_id")
                                       or task.get("vehicle_name") or ""),
                        subject_name=str(task.get("vehicle_name") or ""),
                        dedup_key=f"task:{task['id']}",
                    )
                    if posted:
                        continue
                    if task["created_by"]:
                        await bot_app.bot.send_message(
                            chat_id=task["created_by"],
                            text=notify_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=done_kb,
                        )
                    await _notify_account_admins(
                        bot_app, acct.id, notify_text,
                        exclude=task["created_by"],
                        reply_markup=done_kb,
                    )
                except Exception as e:
                    logger.debug(f"Mileage overdue notification failed: {e}")

        tenant_db_rls = await _get_tenant_db_rls(account.id)
        await run_account_job(_run(), account_id=account.id,
                              job_name="overdue_mileage_check",
                              tenant_db=tenant_db_rls)

    if total_marked:
        logger.info(f"Marked {total_marked} maintenance task(s) as overdue (mileage)")


@register_alert_source("maintenance_engine_hours_check", trigger="interval", hours=6)
async def check_overdue_by_engine_hours(app: Application):
    """Scheduled job: engine-hours threshold crossings.

    Runs every 6 hours alongside the mileage check.  Business logic
    (warehouse read + status flip + alerted_at stamp) is in the service
    layer; this thin wrapper handles per-account fan-out and the
    Telegram notification format.
    """
    try:
        accounts = await get_platform_db().list_accounts()
    except Exception as e:
        logger.error(f"Engine-hours check — cannot list accounts: {e}", exc_info=True)
        return

    total_marked = 0
    for account in accounts:
        async def _run(acct=account):
            nonlocal total_marked
            bot_app = get_app_for_account(acct.id)
            if not bot_app:
                logger.warning("No bot for account %d — skipping engine-hours check", acct.id)
                return
            tenant = await get_tenant_db(acct.id)
            newly_overdue = await mark_overdue_tasks_by_engine_hours(acct.id, tenant)
            total_marked += len(newly_overdue)
            for task in newly_overdue:
                try:
                    type_label = _task_label(task["task_type"])
                    due = task["due_engine_hours"]
                    current = task.get("_current_engine_hours", due)
                    notify_text = _format_maintenance_alert(
                        severity="warning",
                        title="Overdue Maintenance (Engine Hours)",
                        vehicle_name=task["vehicle_name"],
                        task_label=type_label,
                        detail_lines=[
                            f"⏱ Due at {due:,.0f} hrs",
                            f"📏 Current {current:,.0f} hrs",
                        ],
                        action="Schedule shop · update task once complete",
                    )
                    from capabilities.alerting.pipeline import post_alert_to_topic
                    done_kb = _done_kb(task["id"])
                    posted = await post_alert_to_topic(
                        bot_app, account_id=acct.id,
                        alert_type="maintenance", text=notify_text,
                        severity="warning",
                        reply_markup=done_kb,
                        subject_id=str(task.get("vehicle_id")
                                       or task.get("vehicle_name") or ""),
                        subject_name=str(task.get("vehicle_name") or ""),
                        dedup_key=f"task:{task['id']}",
                    )
                    if posted:
                        continue
                    if task["created_by"]:
                        await bot_app.bot.send_message(
                            chat_id=task["created_by"],
                            text=notify_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=done_kb,
                        )
                    await _notify_account_admins(
                        bot_app, acct.id, notify_text,
                        exclude=task["created_by"],
                        reply_markup=done_kb,
                    )
                except Exception as e:
                    logger.debug(f"Engine-hours overdue notification failed: {e}")

        tenant_db_rls = await _get_tenant_db_rls(account.id)
        await run_account_job(_run(), account_id=account.id,
                              job_name="overdue_engine_hours_check",
                              tenant_db=tenant_db_rls)

    if total_marked:
        logger.info(f"Marked {total_marked} maintenance task(s) as overdue (engine hours)")


@register_alert_source("maintenance_warning_check", trigger="interval", hours=24)
async def check_upcoming_maintenance_warnings(app: Application):
    """Daily pre-overdue warning ("due in 7d / 500 mi / 50 hrs").

    Fires once per task per cycle: ``warning_sent_at`` is stamped after
    the notification so subsequent ticks skip warned tasks.  The actual
    overdue alert (``alerted_at``) is independent — a task can be warned
    today and still trigger the overdue notification 3 days later when
    it crosses.
    """
    try:
        accounts = await get_platform_db().list_accounts()
    except Exception as e:
        logger.error(f"Maintenance warning check — cannot list accounts: {e}", exc_info=True)
        return

    total_warned = 0
    for account in accounts:
        async def _run(acct=account):
            nonlocal total_warned
            bot_app = get_app_for_account(acct.id)
            if not bot_app:
                return
            tenant = await get_tenant_db(acct.id)
            upcoming = await detect_upcoming_warnings(acct.id, tenant)
            if not upcoming:
                return
            warned_ids: list[int] = []
            for task in upcoming:
                try:
                    type_label = _task_label(task["task_type"])
                    # Compose a short summary of which dimension(s) are
                    # approaching — date, miles, hours, or any combination.
                    bits: list[str] = []
                    if task.get("due_date"):
                        bits.append(f"📅 by {task['due_date'][:10]}")
                    if task.get("due_miles") and task.get("last_odometer"):
                        remaining = float(task["due_miles"]) - float(task["last_odometer"])
                        bits.append(f"🛣 {remaining:,.0f} mi to go")
                    if task.get("due_engine_hours") and task.get("last_engine_hours"):
                        remaining_h = float(task["due_engine_hours"]) - float(task["last_engine_hours"])
                        bits.append(f"⏱ {remaining_h:,.0f} hrs to go")
                    notify_text = _format_maintenance_alert(
                        severity="info",
                        title="Maintenance Due Soon",
                        vehicle_name=task["vehicle_name"],
                        task_label=type_label,
                        detail_lines=bits if bits else ["approaching"],
                        action="Plan shop visit · no immediate action needed",
                    )
                    from capabilities.alerting.pipeline import post_alert_to_topic
                    done_kb = _done_kb(task["id"])
                    posted = await post_alert_to_topic(
                        bot_app, account_id=acct.id,
                        alert_type="maintenance", text=notify_text,
                        severity="info",
                        reply_markup=done_kb,
                        subject_id=str(task.get("vehicle_id")
                                       or task.get("vehicle_name") or ""),
                        subject_name=str(task.get("vehicle_name") or ""),
                        dedup_key=f"task:{task['id']}",
                    )
                    if not posted:
                        if task["created_by"]:
                            await bot_app.bot.send_message(
                                chat_id=task["created_by"],
                                text=notify_text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=done_kb,
                            )
                        await _notify_account_admins(
                            bot_app, acct.id, notify_text,
                            exclude=task["created_by"],
                            reply_markup=done_kb,
                        )
                    warned_ids.append(int(task["id"]))
                except Exception as e:
                    logger.debug(f"Upcoming-warning notification failed: {e}")
            if warned_ids:
                await tenant.mark_tasks_warned_bulk(acct.id, warned_ids)
                total_warned += len(warned_ids)

        tenant_db_rls = await _get_tenant_db_rls(account.id)
        await run_account_job(_run(), account_id=account.id,
                              job_name="upcoming_warning_check",
                              tenant_db=tenant_db_rls)

    if total_warned:
        logger.info(f"Sent {total_warned} upcoming-maintenance warning(s)")


async def _notify_account_admins(app: Application, account_id: int, text: str,
                                  exclude: int = 0,
                                  reply_markup: InlineKeyboardMarkup | None = None):
    """Send a notification to all admins/owners of an account (except exclude).

    ``reply_markup`` attaches the inline "✓ Mark Done" button to every
    admin DM so any owner/admin can clear the task without opening a
    browser.

    Email fallback: when the Telegram send fails AND the user has an
    ``email`` on file AND SMTP is configured, the maintenance alert is
    also delivered via email so a muted/blocked bot doesn't suppress
    overdue evidence.  Email is best-effort and never blocks the rest
    of the fan-out.
    """
    from capabilities.email import is_email_configured, send_email
    smtp_on = is_email_configured()

    try:
        users = await get_platform_db().list_account_users(account_id)
        for u in users:
            if u.telegram_id == exclude:
                continue
            if u.role not in (Role.OWNER, Role.ADMIN):
                continue
            tg_ok = False
            try:
                await app.bot.send_message(
                    chat_id=u.telegram_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
                tg_ok = True
            except Exception as e:
                logger.debug("Could not notify admin %d: %s", u.telegram_id, e)
            # Fallback only when Telegram failed AND SMTP is set up AND
            # the user has an email.  Strips HTML for the text part
            # since the receiver might use a plain-text client.
            if not tg_ok and smtp_on and getattr(u, "email", None):
                import re
                plain = re.sub(r"<[^>]+>", "", text)
                send_email(
                    to=u.email,
                    subject="4truck maintenance alert",
                    body=plain,
                    html_body=text,
                )
    except Exception as e:
        logger.warning("_notify_account_admins failed for account %d: %s", account_id, e)
