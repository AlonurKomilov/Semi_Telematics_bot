"""Cost-Per-Mile Dashboard — fuel cost / odometer miles per truck."""

import io
import csv
from datetime import datetime as _dt
from constants import TZ_ET as _TZ_ET
from capabilities.localization.i18n import t

from telegram import Update
from telegram.ext import ContextTypes

from capabilities.iam.permissions import can
from capabilities.costs.service import compute_fleet_cpm
from adapters.samsara.client import populate_company_display
from infra.context import get_company_display

from interfaces.bot.config import logger
from interfaces.bot.state import get_tenant_db
from interfaces.bot.keyboards import back_kb, costmile_format_kb
from interfaces.bot.helpers import _show, _show_loading, _safe_error
from interfaces.bot.auth import _require_registered


@_require_registered
async def cmd_costmile(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       company: str | None = None):
    """Show format picker for cost-per-mile."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_cost_per_mile"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')
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

    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)
    company_label = get_company_display().get(company, t('common.all_companies')) if company else t('common.all_companies')

    await _show_loading(update, context, t('cost_per_mile.loading').format(company=company_label))
    try:
        fuel_summary = await tenant.get_fuel_summary(user.account_id)
        if not fuel_summary:
            await _show(update, context, [
                t('cost_per_mile.no_fuel_data')
            ], keyboard=back_kb())
            return

        # Build cost-per-mile data
        results, fleet = compute_fleet_cpm(fuel_summary)

        # Sort cheapest first
        results.sort(key=lambda x: x["cpm"] if x["cpm"] > 0 else 999)

        if fmt == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["Truck", "Company", "Miles", "Total Cost", "Gallons", "$/Mile", "MPG"])
            for r in results:
                writer.writerow([r["vehicle"], r["company"], r["miles"],
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

        avg_cpm = fleet["fleet_cpm"]
        total_cost = fleet["fleet_cost"]
        total_miles = fleet["fleet_miles"]
        lines.append(
            f"\n{'━' * 28}\n"
            f"  {t('cost_per_mile.fleet_avg').format(avg=f'${avg_cpm:.2f}')}\n"
            f"  {t('cost_per_mile.total_summary').format(cost=f'${total_cost:,.2f}', miles=f'{total_miles:,}')}"
        )

        full = "\n".join(lines)
        await _show(update, context, [full], keyboard=back_kb())

    except Exception as e:
        logger.error(f"Cost/mile error: {e}")
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())
