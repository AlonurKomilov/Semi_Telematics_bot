"""Fuel Cost Tracker — manual fill-up logging and summary."""

from datetime import datetime as _dt
from constants import TZ_ET as _TZ_ET
from capabilities.localization.i18n import t

from telegram import Update
from telegram.ext import ContextTypes

from capabilities.permissions.roles import can

from interfaces.bot.config import logger
from interfaces.bot.state import get_tenant_db
from interfaces.bot.keyboards import back_kb, fuelcost_menu_kb
from interfaces.bot.helpers import _show, _show_loading, _safe_error
from interfaces.bot.auth import _require_registered


@_require_registered
async def cmd_fuelcost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show fuel cost menu."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_view_fuel_cost"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    sep = t("alert_format.separator")
    text = (
        f"{sep}\n"
        f"  {t('fuel_costs.header')}\n"
        f"{sep}\n"
        f"\n  {t('fuel_costs.description')}\n"
    )
    await _show(update, context, [text], keyboard=fuelcost_menu_kb())


@_require_registered
async def cmd_fuelcost_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start fuel logging wizard — step 1: truck name."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_view_fuel_cost"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    context.user_data["_pending"] = "fuelcost_vehicle"
    context.user_data["_fuelcost"] = {}
    await _show(update, context, [
        f"{t('fuel_costs.add_title')}\n\n"
        f"{t('fuel_costs.step1_vehicle')}"
    ], keyboard=back_kb())


async def handle_fuelcost_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle multi-step fuel cost wizard text inputs."""
    user = context.user_data.get("_db_user")
    if not user:
        return False

    pending = context.user_data.get("_pending", "")
    wiz = context.user_data.get("_fuelcost", {})
    text = update.message.text.strip()

    if pending == "fuelcost_vehicle":
        wiz["vehicle"] = text
        context.user_data["_pending"] = "fuelcost_gallons"
        await _show(update, context, [
            f"{t('fuel_costs.step2_confirm').format(truck=text)}\n\n"
            f"{t('fuel_costs.step2_gallons')}"
        ], keyboard=back_kb())
        return True

    elif pending == "fuelcost_gallons":
        try:
            gallons = float(text)
        except ValueError:
            await _show(update, context, [t('fuel_costs.error_number').format(example="150.5")], keyboard=back_kb())
            return True
        wiz["gallons"] = gallons
        context.user_data["_pending"] = "fuelcost_price"
        await _show(update, context, [
            f"{t('fuel_costs.step3_confirm').format(gallons=gallons)}\n\n"
            f"{t('fuel_costs.step3_price')}"
        ], keyboard=back_kb())
        return True

    elif pending == "fuelcost_price":
        try:
            price = float(text.replace("$", ""))
        except ValueError:
            await _show(update, context, [t('fuel_costs.error_number').format(example="3.89")], keyboard=back_kb())
            return True
        wiz["price"] = price
        context.user_data["_pending"] = "fuelcost_odo"
        await _show(update, context, [
            f"{t('fuel_costs.step4_confirm').format(price=f'{price:.2f}')}\n\n"
            f"{t('fuel_costs.step4_odo')}"
        ], keyboard=back_kb())
        return True

    elif pending == "fuelcost_odo":
        try:
            odo = float(text.replace(",", ""))
        except ValueError:
            await _show(update, context, [t('fuel_costs.error_number').format(example="125000")], keyboard=back_kb())
            return True

        # Save entry
        truck = wiz.get("vehicle", "Unknown")
        gallons = wiz.get("gallons", 0)
        price = wiz.get("price", 0)
        total = round(gallons * price, 2)
        # The ACCOUNT's calendar day — this string is PERSISTED as the
        # fuel entry's date, so a hardcoded Eastern day gave any
        # non-Eastern fleet tomorrow's date on evening entries.
        from zoneinfo import ZoneInfo as _ZI

        from capabilities.localization.tz import effective_tz_for_account
        try:
            _acct_tz = _ZI(await effective_tz_for_account(user.account_id))
        except Exception:
            _acct_tz = _TZ_ET
        today = _dt.now(_acct_tz).strftime("%Y-%m-%d")

        try:
            tenant = await get_tenant_db(user.account_id)
            await tenant.add_fuel_entry(
                account_id=user.account_id,
                company_code="",
                vehicle_name=truck,
                gallons=gallons,
                price_per_gallon=price,
                odometer_miles=odo,
                date=today,
                created_by=user.telegram_id,
            )
            context.user_data.pop("_pending", None)
            context.user_data.pop("_fuelcost", None)

            await _show(update, context, [
                f"{t('fuel_costs.logged_title')}\n\n"
                f"  🚛 {truck}\n"
                f"  ⛽ {gallons} gal × ${price:.2f} = <b>${total:.2f}</b>\n"
                f"  📏 Odometer: {odo:,.0f} mi\n"
                f"  📅 {today}"
            ], keyboard=fuelcost_menu_kb())
        except Exception as e:
            logger.error(f"Fuel entry save error: {e}")
            await _show(update, context, [_safe_error(e)], keyboard=back_kb())
        return True

    return False


@_require_registered
async def cmd_fuelcost_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show per-truck fuel cost summary."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_view_fuel_cost"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await _show_loading(update, context, t('fuel_costs.loading_summary'))
    try:
        tenant = await get_tenant_db(user.account_id)
        summary = await tenant.get_fuel_summary(user.account_id)
        if not summary:
            await _show(update, context, [
                t('fuel_costs.no_entries')
            ], keyboard=fuelcost_menu_kb())
            return

        sep = t("alert_format.separator")
        lines = [
            f"{sep}\n"
            f"  {t('fuel_costs.summary_header')}\n"
            f"{sep}\n"
        ]
        grand_cost = 0
        grand_gal = 0
        for s in summary:
            name = s["vehicle_name"]
            entries = s["entries"]
            total_gal = s["total_gallons"] or 0
            total_cost = s["total_cost"] or 0
            avg_price = s["avg_price"] or 0
            first_odo = s["first_odo"] or 0
            last_odo = s["last_odo"] or 0
            miles = last_odo - first_odo if last_odo > first_odo else 0
            mpg = miles / total_gal if total_gal > 0 and miles > 0 else 0
            grand_cost += total_cost
            grand_gal += total_gal

            lines.append(
                f"\n{'─' * 28}\n"
                f"  🚛 <b>{name}</b>  ({entries} entries)\n"
                f"  ⛽ {total_gal:.1f} gal  ·  💲 ${total_cost:,.2f}\n"
                f"  📊 Avg ${avg_price:.2f}/gal"
            )
            if mpg > 0:
                lines[-1] += f"  ·  {mpg:.1f} MPG"

        lines.append(
            f"\n{'━' * 28}\n"
            f"  {t('fuel_costs.grand_total').format(cost=f'${grand_cost:,.2f}', gallons=f'{grand_gal:,.1f}')}"
        )

        full = "\n".join(lines)
        await _show(update, context, [full], keyboard=fuelcost_menu_kb())
    except Exception as e:
        logger.error(f"Fuel summary error: {e}")
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())
