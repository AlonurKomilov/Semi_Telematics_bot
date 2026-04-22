"""Events — on-demand safety event dashboard and CSV export."""

import asyncio
import io
import csv
from datetime import datetime as _dt
from constants import TZ_ET as _TZ_ET
from capabilities.localization.i18n import t

from telegram import Update
from telegram.ext import ContextTypes

from adapters.storage import Role
from capabilities.iam.permissions import can
from adapters.samsara.client import populate_company_display
from capabilities.formatting import format_events_dashboard
from capabilities.events.service import filter_events_by_access as _filter_events_by_access, get_events as _svc_get_events

from interfaces.bot.config import logger, get_user_company_codes, get_tenant_db
from interfaces.bot.keyboards import back_kb, events_format_kb
from interfaces.bot.helpers import _show, _show_loading, _user_menu_kb, _safe_error
from interfaces.bot.auth import _require_registered


@_require_registered
async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show format/period picker for events dashboard."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_events_all") or can(user.role, "can_events_own")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    text = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  🚨  <b>{t('events.title')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('events.select_period')}"
    )
    kb = events_format_kb()
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_events_text(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          days: int = 7):
    """Generate events text dashboard."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_events_all") or can(user.role, "can_events_own")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)
    company_label = t("common.all_companies")

    await _show_loading(update, context, t("events.loading"))
    try:
        events = await _svc_get_events(user.account_id, days=days)

        # Driver role: filter to own vehicle events only
        if user.role == Role.DRIVER and not can(user.role, "can_events_all"):
            truck_nums = [user.truck_num] if user.truck_num else []
            events = _filter_events_by_access(events, truck_nums)

        if not events:
            await _show(update, context,
                        [t("events.empty").format(days=days)], keyboard=back_kb())
            return

        texts = format_events_dashboard(events, days, company_label)
        await _show(update, context, texts, keyboard=back_kb())
    except Exception as e:
        logger.error(f"Events error: {e}")
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


@_require_registered
async def cmd_events_csv(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         days: int = 7):
    """Generate events CSV export."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_events_all") or can(user.role, "can_events_own")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)
    company_label = t("common.all_companies")

    await _show_loading(update, context, t("events.loading"))
    try:
        events = await _svc_get_events(user.account_id, days=days)

        # Driver role: filter to own vehicle events only
        if user.role == Role.DRIVER and not can(user.role, "can_events_all"):
            truck_nums = [user.truck_num] if user.truck_num else []
            events = _filter_events_by_access(events, truck_nums)

        from capabilities.reporting import generate_events_csv
        csv_buf = await asyncio.to_thread(generate_events_csv, events, days)
        chat_id = update.effective_chat.id
        await context.bot.send_document(
            chat_id=chat_id, document=csv_buf,
            caption=t("events.csv_caption").format(company=company_label),
        )
        await _show(update, context, [t("events.csv_exported")], keyboard=back_kb())
    except Exception as e:
        logger.error(f"Events CSV error: {e}")
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())
