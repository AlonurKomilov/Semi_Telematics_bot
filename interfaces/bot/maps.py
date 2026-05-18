"""Live Location Map — static PNG map with truck markers."""

import asyncio
from datetime import datetime as _dt
from constants import TZ_ET as _TZ_ET
from capabilities.localization.i18n import t

from telegram import Update
from telegram.ext import ContextTypes

from adapters.storage import Role
from capabilities.iam.permissions import can
from adapters.samsara.client import populate_company_display
from infra.context import get_company_display
from capabilities.location.service import classify_vehicle_status, get_fleet_for_map as _svc_fleet_for_map
from capabilities.reporting.map_renderer import render_fleet_map as _render_map

from interfaces.bot.config import logger
from interfaces.bot.state import get_tenant_db
from interfaces.bot.keyboards import back_kb, livemap_refresh_kb
from interfaces.bot.helpers import _show, _show_loading, _safe_error
from interfaces.bot.auth import _require_registered



@_require_registered
async def cmd_livemap(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      company: str | None = None):
    """Generate and send live fleet location map."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_location_map") or can(user.role, "can_location_own")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)
    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')

    await _show_loading(update, context, t('live_map.loading').format(company=company_label))

    try:
        vehicles = await _svc_fleet_for_map(user.account_id, company=company)

        # Driver: filter to own truck
        if user.role == Role.DRIVER and not can(user.role, "can_location_map"):
            if user.truck_num:
                vehicles = [v for v in vehicles if user.truck_num.lower() in v["name"].lower()]

        if not vehicles:
            await _show(update, context, [
                t('live_map.no_vehicles').format(company=company_label)
            ], keyboard=back_kb())
            return

        map_buf = await asyncio.to_thread(_render_map, vehicles)
        if map_buf is None:
            await _show(update, context, [
                t('live_map.no_gps_data')
            ], keyboard=back_kb())
            return

        now_et = _dt.now(_TZ_ET)
        moving = sum(1 for v in vehicles
                     if classify_vehicle_status(v) == "moving")
        caption = (
            f"{t('live_map.caption_title').format(company=company_label)}\n"
            f"{t('live_map.caption_stats').format(total=len(vehicles), moving=moving)}\n"
            f"{t('live_map.caption_date').format(date=now_et.strftime('%b %d, %I:%M %p ET'))}"
        )

        chat_id = update.effective_chat.id
        await context.bot.send_photo(
            chat_id=chat_id, photo=map_buf, caption=caption,
        )
        await _show(update, context, ["🔄 Tap below to refresh or go back."], keyboard=livemap_refresh_kb(company))

    except ImportError:
        await _show(update, context, [
            t('live_map.install_required')
        ], keyboard=back_kb())
    except Exception as e:
        logger.error(f"Live map error: {e}")
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())
