"""Scheduled Reports — recurring PDF report delivery.

Users opt in to receive PDF reports (Faults, Fuel, Health, Efficiency,
Camera Check) on a Daily, Weekly, or Monthly cadence at a chosen
local hour, delivered via Telegram and/or email.  The scheduler job
runs every hour, matches schedules by timezone-aware hour conversion,
generates the requested PDF, and dispatches it to the user's selected
delivery channel(s).

The underlying storage table is still named ``digest_subscriptions``
for backwards compatibility — renaming the table would require a
migration with no user-visible benefit.  Everything user-facing
(API endpoint, page name, sidebar label, i18n keys, function names,
this module) reads ``scheduled_reports``.
"""

import asyncio
from datetime import datetime as _dt, timezone as _tz_mod

from telegram import Update
from telegram.ext import ContextTypes, Application
from telegram.constants import ParseMode

from capabilities.permissions.roles import can
from adapters.samsara.client import populate_company_display
from capabilities.reporting import compute_stats
from features.vehicles.faults.report import generate_fault_report_pdf
from features.vehicles.fuel.report import generate_fuel_report_pdf
from features.vehicles.efficiency.report import generate_fleet_efficiency_pdf
from features.vehicles.cameras.report import generate_camera_check_pdf
from features.vehicles.health.report import generate_vehicle_health_pdf
from capabilities.reporting.registry import REPORTS as _REPORT_REGISTRY

from capabilities.localization.i18n import t
from interfaces.bot.config import logger
from interfaces.bot.state import get_platform_db, get_tenant_db
from infra.bot_registry import get_app_for_account
from infra.isolation import SCHEDULED_REPORTS_JOB_TIMEOUT, run_account_job
from infra.services import get_tenant_db as _get_tenant_db_rls
from features.vehicles.service import (
    prepare_companies,
    get_vehicles_overview as _svc_vehicles_overview,
)
from capabilities.telemetry.service import (
    get_vehicles_with_faults as _svc_vehicles_with_faults,
)

# NOTE: `db` singleton removed — use get_platform_db() / get_tenant_db() instead
from interfaces.bot.keyboards import scheduled_reports_menu_kb
from interfaces.bot.helpers import _show
from interfaces.bot.auth import _require_registered

_tz_utc = _tz_mod.utc

# Report type → labels (used in menus and messages).  Sourced from
# the canonical registry so this and the dashboard, the bot keyboards,
# and the API export endpoint can't drift.
REPORT_TYPES = {r.key: r.label_with_emoji for r in _REPORT_REGISTRY}


@_require_registered
async def cmd_scheduled_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the Scheduled Reports menu — list view with multi-schedule.

    Renders every active schedule the user has plus per-row Stop
    buttons and an "Add new schedule" entry.  Empty state shows the
    frequency picker inline so the first-time experience is one tap.
    """
    user = context.user_data["_db_user"]
    if not can(user.role, "can_digest"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    subs = await tenant.get_digest_subscriptions(user.id)
    sep = t("alert_format.separator")
    text = (
        f"{sep}\n"
        f"  {t('scheduled_reports.header')}\n"
        f"{sep}\n"
        f"\n  {t('scheduled_reports.description')}\n"
    )
    if subs:
        for sub in subs:
            rtype = sub.get("report_type", "faults")
            freq = sub.get("frequency", "daily")
            hour = sub.get("send_hour", 7)
            tz = sub.get("timezone", "UTC")
            tz_short = tz.split("/")[-1].replace("_", " ") if "/" in tz else tz
            type_label = REPORT_TYPES.get(rtype, rtype.title())
            text += f"\n  {t('scheduled_reports.subscribed').format(rtype=type_label, freq=freq.title(), time=f'{hour:02d}:00')}"
            text += f"\n  {t('scheduled_reports.timezone_label').format(tz=tz_short)}"
    else:
        text += f"\n  {t('scheduled_reports.choose_frequency')}"

    await _show(update, context, [text], keyboard=scheduled_reports_menu_kb(subs))


@_require_registered
async def cmd_scheduled_reports_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point from the "+ Add new schedule" button.

    Shows the frequency picker — same first step the empty-state menu
    uses for users with no schedules.  Kept as a separate handler so
    the multi-schedule menu can route here explicitly without faking a
    frequency value.
    """
    user = context.user_data["_db_user"]
    if not can(user.role, "can_digest"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return
    sep = t("alert_format.separator")
    text = (
        f"{sep}\n"
        f"  {t('scheduled_reports.header')}\n"
        f"{sep}\n"
        f"\n  {t('scheduled_reports.choose_frequency')}\n"
    )
    # Reuse the empty-state keyboard (Daily / Weekly / Monthly picker)
    # by passing an empty list — the menu builder renders the
    # frequency picker when ``subs_list`` is empty.
    await _show(update, context, [text], keyboard=scheduled_reports_menu_kb([]))


@_require_registered
async def cmd_scheduled_reports_stop(
    update: Update, context: ContextTypes.DEFAULT_TYPE, report_type: str,
):
    """Stop ONE schedule (per-row delete from the multi-schedule menu).

    The legacy ``cmd_scheduled_reports_unsubscribe`` handler still
    exists for the "Stop all" path (it's wired to the ``ar_unsub``
    callback baked into pre-2026-06 messages) — this new entrypoint
    targets a single (user, report_type) pair so a user with multiple
    schedules can prune one without losing the others.
    """
    user = context.user_data["_db_user"]
    if not can(user.role, "can_digest"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return
    tenant = await get_tenant_db(user.account_id)
    await tenant.unsubscribe_digest(user.id, report_type=report_type)
    if update.callback_query:
        type_label = REPORT_TYPES.get(report_type, report_type.title())
        await update.callback_query.answer(
            f"🔕 {type_label} schedule stopped",
        )
    # Re-render the menu so the just-stopped row disappears.
    await cmd_scheduled_reports(update, context)


@_require_registered
async def cmd_scheduled_reports_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                     frequency: str = "daily"):
    """Start subscription wizard — frequency chosen, now pick report type."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_digest"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    context.user_data["_ar_freq"] = frequency

    from interfaces.bot.keyboards import scheduled_reports_type_kb
    await _show(update, context, [
        f"{t('scheduled_reports.subscribe_title').format(freq=frequency.title())}\n\n"
        f"  {t('scheduled_reports.choose_type')}\n"
    ], keyboard=scheduled_reports_type_kb())


@_require_registered
async def cmd_scheduled_reports_set_type(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                    report_type: str = "faults"):
    """Report type chosen, now pick delivery hour."""
    user = context.user_data["_db_user"]
    context.user_data["_ar_type"] = report_type

    from interfaces.bot.keyboards import scheduled_reports_hour_kb
    type_label = REPORT_TYPES.get(report_type, report_type.title())
    await _show(update, context, [
        f"  {type_label}\n\n"
        f"  {t('scheduled_reports.time_prompt')}\n"
        f"  {t('scheduled_reports.time_local')}\n"
    ], keyboard=scheduled_reports_hour_kb())


@_require_registered
async def cmd_scheduled_reports_set_hour(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                    hour: int = 7):
    """Hour chosen, now pick timezone."""
    user = context.user_data["_db_user"]
    context.user_data["_ar_hour"] = hour

    from interfaces.bot.keyboards import scheduled_reports_tz_kb
    await _show(update, context, [
        f"{t('scheduled_reports.delivery_at').format(time=f'{hour:02d}:00')}\n\n"
        f"  {t('scheduled_reports.select_tz')}\n"
    ], keyboard=scheduled_reports_tz_kb())


@_require_registered
async def cmd_scheduled_reports_set_tz(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                  tz: str = "America/New_York"):
    """Finalize subscription with timezone — all wizard data collected."""
    user = context.user_data["_db_user"]
    freq = context.user_data.pop("_ar_freq", "daily")
    rtype = context.user_data.pop("_ar_type", "faults")
    hour = context.user_data.pop("_ar_hour", 7)

    tenant = await get_tenant_db(user.account_id)
    await tenant.subscribe_digest_ext(
        user.id, frequency=freq, send_hour=hour,
        timezone=tz, report_type=rtype,
    )
    sub = await tenant.get_digest_subscription(user.id)

    type_label = REPORT_TYPES.get(rtype, rtype.title())
    tz_short = tz.split("/")[-1].replace("_", " ") if "/" in tz else tz
    await _show(update, context, [
        f"{t('scheduled_reports.subscribed_confirm')}\n\n"
        f"  {t('scheduled_reports.delivery_info').format(rtype=type_label, freq=freq.title(), time=f'{hour:02d}:00')}\n"
        f"  {t('scheduled_reports.timezone_info').format(tz=tz_short)}\n"
        f"  {t('scheduled_reports.auto_summary')}"
    ], keyboard=scheduled_reports_menu_kb(sub))


@_require_registered
async def cmd_scheduled_reports_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unsubscribe from report subscriptions."""
    user = context.user_data["_db_user"]
    tenant = await get_tenant_db(user.account_id)
    await tenant.unsubscribe_digest(user.id)
    await _show(update, context, [
        f"{t('scheduled_reports.unsubscribed')}\n\n"
        f"{t('scheduled_reports.resubscribe')}"
    ], keyboard=scheduled_reports_menu_kb(None))


# ══════════════════════════════════════════════════════════════════
# PDF GENERATION HELPERS (for scheduled delivery)
# ══════════════════════════════════════════════════════════════════

async def _generate_report_pdf(account_id: int, report_type: str):
    """Generate a PDF report for the given type.

    Returns (pdf_buf, caption, filename_stem) tuple, or
    (None, error_msg, None) on failure.

    Delegates to ``capabilities.reporting.data_fetch.build_report_pdf``
    so this scheduled path uses the same upstream as the dashboard
    ``/api/reports/export`` endpoint — eliminating the historical drift
    where the two channels returned different vehicle lists for the
    same user at the same moment.
    """
    from capabilities.reporting.data_fetch import build_report_pdf
    try:
        # ``prepare_companies`` + populate-display preserved here as a
        # belt-and-braces pass; build_report_pdf also calls
        # prepare_companies but the display-name population is
        # bot-specific and downstream PDFs render nicer with it.
        await prepare_companies(account_id)
        tenant = await get_tenant_db(account_id)
        companies = await tenant.get_account_companies(account_id)
        populate_company_display(companies)

        return await build_report_pdf(account_id, report_type, prepare=False)

    except ValueError as e:
        # Unknown report_type — surfaced as a soft failure with the
        # explanation in the caption slot.
        return None, str(e), None
    except Exception as e:
        logger.error(f"Report subscription generation error ({report_type}): {e}", exc_info=True)
        return None, t('scheduled_reports.error_content'), None


# ══════════════════════════════════════════════════════════════════
# SCHEDULED JOB
# ══════════════════════════════════════════════════════════════════

async def send_scheduled_reports(app: Application):
    """Scheduled job: send PDF reports to subscribers at their chosen local hour.

    Runs every hour. Uses timezone-aware subscriber matching.
    Weekly reports only send on Monday. Monthly on the 1st.
    """
    now = _dt.now(_tz_utc)
    current_hour = now.hour
    is_monday = now.weekday() == 0
    is_first = now.day == 1

    try:
        accounts = await get_platform_db().list_accounts()
    except Exception as e:
        logger.error(f"Report subscriptions — cannot list accounts: {e}", exc_info=True)
        return

    from constants import TZ_ET as _TZ_ET

    for account in accounts:
        async def _run(acct=account):
            tenant = await get_tenant_db(acct.id)
            bot_app = get_app_for_account(acct.id)
            if not bot_app:
                logger.warning("No bot for account %d — skipping report subscriptions", acct.id)
                return
            subs = await tenant.get_digest_subscribers_by_local_hour(current_hour)
            if not subs:
                return

            for sub in subs:
                freq = sub.get("frequency", "daily")
                if freq == "weekly" and not is_monday:
                    continue
                if freq == "monthly" and not is_first:
                    continue

                try:
                    report_type = sub.get("report_type", "faults")
                    result = await _generate_report_pdf(sub["account_id"], report_type)

                    # Parse delivery channels.  Default to "telegram"
                    # for any legacy row pre-dating the 076 migration.
                    raw_channels = sub.get("delivery_channels") or "telegram"
                    channels = {c.strip() for c in raw_channels.split(",") if c.strip()}

                    if result[0] is None:
                        # Soft failure (e.g. camera with no snapshots).
                        # Telegram channel surfaces the explanation;
                        # email channel stays silent so users with
                        # email-only delivery don't get a useless empty
                        # email for every soft-fail.
                        if "telegram" in channels and sub.get("telegram_id"):
                            await bot_app.bot.send_message(
                                chat_id=sub["telegram_id"],
                                text=result[1],
                                parse_mode=ParseMode.HTML,
                            )
                        continue

                    pdf_buf, caption, filename_base = result
                    now_et = _dt.now(_TZ_ET)
                    ts = now_et.strftime("%Y-%m-%d_%H%M")
                    filename = f"{filename_base}_{ts}.pdf"

                    # ── Telegram channel ────────────────────────────
                    if "telegram" in channels and sub.get("telegram_id"):
                        # PTB consumes the BytesIO cursor — clone for
                        # re-use if email also wants it.
                        if "email" in channels:
                            pdf_buf.seek(0)
                            tg_payload = pdf_buf.read()
                            pdf_buf.seek(0)
                        await bot_app.bot.send_document(
                            chat_id=sub["telegram_id"],
                            document=pdf_buf,
                            filename=filename,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                        )
                    else:
                        tg_payload = None

                    # ── Email channel ───────────────────────────────
                    if "email" in channels:
                        recipient = sub.get("email")
                        verified = bool(sub.get("email_verified"))
                        if not recipient or not verified:
                            # Should be caught at the API layer; this
                            # guard handles rows that pre-date the
                            # gate (e.g. a verified email going stale).
                            logger.info(
                                "Email channel skipped for user %s: "
                                "recipient=%r verified=%s",
                                sub.get("telegram_id") or sub.get("user_id"),
                                recipient, verified,
                            )
                        else:
                            from capabilities.email.smtp import send_email
                            if tg_payload is None:
                                pdf_buf.seek(0)
                                tg_payload = pdf_buf.read()
                            # Strip HTML from the Telegram caption for
                            # the plaintext body — most inboxes render
                            # the HTML part, but bots / accessibility
                            # tools fall back to plain text.
                            import re as _re
                            plain = _re.sub(r"<[^>]+>", "", caption)
                            display = sub.get("display_name") or "there"
                            subject = f"{filename_base.replace('_', ' ')} — {now_et.strftime('%b %d %Y')}"
                            html = (
                                f"<p>Hi {display},</p>"
                                f"<p>Your scheduled <b>{filename_base.replace('_', ' ')}</b> "
                                f"is attached.</p><p>{caption}</p>"
                                f"<p style='color:#666;font-size:12px'>"
                                f"You're receiving this because you set up a Scheduled Report "
                                f"on your 4truck account. Change or stop delivery any time at "
                                f"<a href='https://dash.4truck.us/reports/scheduled-reports'>"
                                f"dash.4truck.us/reports/scheduled-reports</a>.</p>"
                            )
                            ok = send_email(
                                to=recipient,
                                subject=subject,
                                body=f"Hi {display},\n\n{plain}\n\n— 4truck",
                                html_body=html,
                                attachments=[(filename, tg_payload, "application/pdf")],
                            )
                            if not ok:
                                logger.warning(
                                    "Email channel send to %s failed",
                                    recipient,
                                )
                except Exception as e:
                    logger.warning(
                        f"Report subscription send to {sub.get('telegram_id')}: {e}",
                        exc_info=True,
                    )

        tenant_db_rls = await _get_tenant_db_rls(account.id)
        await run_account_job(
            _run(), account_id=account.id,
            job_name="scheduled_reports",
            timeout=SCHEDULED_REPORTS_JOB_TIMEOUT,
            tenant_db=tenant_db_rls,
        )
