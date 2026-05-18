"""Routes — GPS history plotted on a static map."""

import asyncio
from datetime import datetime as _dt, timedelta, timezone
from capabilities.localization.i18n import t

from telegram import Update
from telegram.ext import ContextTypes

from adapters.storage import Role
from capabilities.iam.permissions import can
from adapters.samsara.client import populate_company_display
from capabilities.routes.service import get_vehicle_gps_history
from capabilities.reporting.map_renderer import render_route_map as _render_route

from interfaces.bot.config import logger
from interfaces.bot.state import get_tenant_db
from interfaces.bot.keyboards import back_kb, route_date_kb
from interfaces.bot.helpers import _show, _show_loading, _safe_error
from interfaces.bot.auth import _require_registered



@_require_registered
async def cmd_route(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start routes — show truck picker or auto-select for drivers."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_route_all") or can(user.role, "can_route_own")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    # Driver with truck_num: auto-select
    if user.role == Role.DRIVER and user.truck_num:
        tenant = await get_tenant_db(user.account_id)
        companies = await tenant.get_account_companies(user.account_id)
        co_code = companies[0].code if companies else ""
        kb = route_date_kb(user.truck_num, co_code)
        await _show(update, context, [
            f"{t('route.title')}\n\n"
            f"  🚛 {user.truck_num}\n\n"
            f"  {t('route.select_date')}"
        ], keyboard=kb)
        return

    # Non-driver: ask for truck name
    context.user_data["_pending"] = "route_vehicle"
    await _show(update, context, [
        f"{t('route.title')}\n\n"
        f"{t('route.type_vehicle')}"
    ], keyboard=back_kb())


@_require_registered
async def cmd_route_go(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       company: str = "", vehicle_name: str = "", days_ago: int = 0):
    """Fetch GPS history and render route map."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_route_all") or can(user.role, "can_route_own")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)

    # Date range
    now = _dt.now(timezone.utc)
    end = now - timedelta(days=days_ago)
    start = end.replace(hour=0, minute=0, second=0) - timedelta(days=0)
    end = end.replace(hour=23, minute=59, second=59)

    date_label = (now - timedelta(days=days_ago)).strftime("%b %d, %Y")
    await _show_loading(update, context,
                        t('route.loading').format(vehicle=vehicle_name, date=date_label))

    try:
        # Get GPS history using shared service
        points = await get_vehicle_gps_history(
            user.account_id, vehicle_name, start, end,
        )

        if not points:
            await _show(update, context, [
                t('route.no_gps_data').format(vehicle=vehicle_name, date=date_label)
            ], keyboard=back_kb())
            return

        map_buf, miles = await asyncio.to_thread(_render_route, points)
        if map_buf is None:
            await _show(update, context, [
                t('route.not_enough_points')
            ], keyboard=back_kb())
            return

        caption = (
            f"{t('route.caption_title').format(vehicle=vehicle_name)}\n"
            f"{t('route.caption_date').format(date=date_label, miles=miles)}\n"
            f"{t('route.caption_markers')}"
        )

        chat_id = update.effective_chat.id
        await context.bot.send_photo(
            chat_id=chat_id, photo=map_buf, caption=caption,
        )
        await _show(update, context, [""], keyboard=back_kb())

    except ImportError:
        await _show(update, context, [
            t('route.map_unavailable')
        ], keyboard=back_kb())
    except Exception as e:
        logger.error(f"Routes error: {e}")
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


async def handle_route_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle truck name input for routes."""
    user = context.user_data.get("_db_user")
    if not user:
        return False

    pending = context.user_data.get("_pending", "")
    if pending != "route_vehicle":
        return False

    text = update.message.text.strip()
    context.user_data.pop("_pending", None)

    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    co_code = companies[0].code if companies else ""
    kb = route_date_kb(text, co_code)
    await _show(update, context, [
        f"{t('route.title')}\n\n"
        f"  🚛 {text}\n\n"
        f"  {t('route.select_date')}"
    ], keyboard=kb)
    return True
