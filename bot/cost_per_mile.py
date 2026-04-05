"""Cost-Per-Mile Dashboard — fuel cost / odometer miles per truck."""

import asyncio
import io
import csv
from datetime import datetime as _dt
from constants import TZ_ET as _TZ_ET
from bot.i18n import t

from telegram import Update
from telegram.ext import ContextTypes

from permissions import can
from samsara_client import COMPANY_DISPLAY, populate_company_display

from bot.config import db, logger, get_client
from bot.keyboards import back_kb, costmile_format_kb
from bot.helpers import _show, _show_loading, _safe_error
from bot.auth import _require_registered


@_require_registered
async def cmd_costmile(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       company: str | None = None):
    """Show format picker for cost-per-mile."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_cost_per_mile"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    company_label = COMPANY_DISPLAY.get(company, t('common.all_companies')) if company else t('common.all_companies')
    sep = t("alert_format.separator")
    text = (
        f"{sep}\n"
        f"  {t('cost_per_mile.header')}\n"
        f"{sep}\n"
        f"\n  {company_label}\n"
        f"\n  {t('cost_per_mile.choose_format')}"
    )
    await _show(update, context, [text], keyboard=costmile_format_kb(company))


@_require_registered
async def cmd_costmile_report(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              company: str | None = None, fmt: str = "text"):
    """Generate cost-per-mile report."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_cost_per_mile"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    companies = await db.get_account_companies(user.account_id)
    populate_company_display(companies)
    company_label = COMPANY_DISPLAY.get(company, t('common.all_companies')) if company else t('common.all_companies')

    await _show_loading(update, context, t('cost_per_mile.loading').format(company=company_label))
    try:
        fuel_summary = await db.get_fuel_summary(user.account_id)
        if not fuel_summary:
            await _show(update, context, [
                t('cost_per_mile.no_fuel_data')
            ], keyboard=back_kb())
            return

        # Build cost-per-mile data
        results = []
        for s in fuel_summary:
            name = s["vehicle_name"]
            total_cost = s["total_cost"] or 0
            first_odo = s["first_odo"] or 0
            last_odo = s["last_odo"] or 0
            total_gal = s["total_gallons"] or 0
            miles = last_odo - first_odo if last_odo > first_odo else 0
            cpm = total_cost / miles if miles > 0 else 0
            mpg = miles / total_gal if total_gal > 0 and miles > 0 else 0

            results.append({
                "truck": name,
                "company": s.get("company_code", ""),
                "miles": round(miles),
                "cost": round(total_cost, 2),
                "gallons": round(total_gal, 1),
                "cpm": round(cpm, 2),
                "mpg": round(mpg, 1),
            })

        # Sort cheapest first
        results.sort(key=lambda x: x["cpm"] if x["cpm"] > 0 else 999)

        if fmt == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["Truck", "Company", "Miles", "Total Cost", "Gallons", "$/Mile", "MPG"])
            for r in results:
                writer.writerow([r["truck"], r["company"], r["miles"],
                                r["cost"], r["gallons"], r["cpm"], r["mpg"]])
            out = io.BytesIO(buf.getvalue().encode("utf-8"))
            out.name = "cost_per_mile.csv"
            chat_id = update.effective_chat.id
            await context.bot.send_document(chat_id=chat_id, document=out,
                                            caption=t('cost_per_mile.csv_caption').format(company=company_label))
            await _show(update, context, [t('cost_per_mile.csv_exported')], keyboard=back_kb())
            return

        # Text format
        now_et = _dt.now(_TZ_ET)
        sep = t("alert_format.separator")
        lines = [
            f"{sep}\n"
            f"  {t('cost_per_mile.header')}\n"
            f"{sep}\n"
            f"\n  {company_label}\n"
            f"  {now_et:%b %d, %Y %I:%M %p ET}\n"
        ]

        for r in results:
            cpm_display = f"${r['cpm']:.2f}/mi" if r["cpm"] > 0 else "N/A"
            mpg_display = f"{r['mpg']} MPG" if r["mpg"] > 0 else "—"
            lines.append(
                f"\n{'─' * 28}\n"
                f"  🚛 <b>{r['truck']}</b>\n"
                f"  💲 <b>{cpm_display}</b>  ·  {mpg_display}\n"
                f"  📏 {r['miles']:,} mi  ·  ${r['cost']:,.2f} total"
            )

        grand_cost = sum(r["cost"] for r in results)
        grand_miles = sum(r["miles"] for r in results)
        avg_cpm = grand_cost / grand_miles if grand_miles > 0 else 0
        lines.append(
            f"\n{'━' * 28}\n"
            f"  {t('cost_per_mile.fleet_avg').format(avg=f'${avg_cpm:.2f}')}\n"
            f"  {t('cost_per_mile.total_summary').format(cost=f'${grand_cost:,.2f}', miles=f'{grand_miles:,}')}"
        )

        full = "\n".join(lines)
        await _show(update, context, [full], keyboard=back_kb())

    except Exception as e:
        logger.error(f"Cost/mile error: {e}")
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())
