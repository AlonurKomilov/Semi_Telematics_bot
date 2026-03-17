"""Driver Scorecards — efficiency data per driver."""

import asyncio
import io
import csv
from datetime import datetime as _dt
from zoneinfo import ZoneInfo as _ZI

_TZ_ET = _ZI("America/New_York")

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database import Role
from permissions import can
from samsara_client import COMPANY_DISPLAY, populate_company_display

from bot.config import db, logger, get_client, get_user_company_codes
from bot.keyboards import back_kb, scorecard_format_kb
from bot.helpers import _show, _show_loading, _user_menu_kb
from bot.auth import _require_registered


def _format_scorecard_text(drivers: list[dict], company_label: str) -> list[str]:
    """Build Telegram messages for driver scorecards."""
    if not drivers:
        return [f"ℹ️ No driver data available for <b>{company_label}</b>."]

    now_et = _dt.now(_TZ_ET)
    header = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  🏆  <b>DRIVER SCORECARDS</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {company_label}\n"
        f"  {now_et:%b %d, %Y %I:%M %p ET}\n"
        f"  {len(drivers)} driver(s)\n"
    )

    lines = [header]
    for d in drivers:
        org = d.get("_org", "")
        org_tag = f"  [{org}]" if org else ""
        card = (
            f"\n{'─' * 30}\n"
            f"  👤 <b>{d['driver_name']}</b>{org_tag}\n"
            f"  🛣  {d['_miles']} mi  ·  ⛽ {d['_mpg']} MPG\n"
            f"  🚗 Drive {d['_drive_h']}h ({d['_drive_pct']}%)"
            f"  ·  💤 Idle {d['_idle_h']}h ({d['_idle_pct']}%)\n"
            f"  🌿 Eco: {d['_green_pct']}%  ·  🏎 Overspeed: {d['_overspeed_min']}m\n"
            f"  🛞 Brakes: {d['_antic_pct']}% anticipation"
        )
        lines.append(card)

    # Join and split for Telegram 4000-char limit
    full = "\n".join(lines)
    if len(full) <= 4000:
        return [full]
    parts = []
    current = ""
    for line in full.split("\n"):
        if len(current) + len(line) + 1 > 4000:
            parts.append(current)
            current = line
        else:
            current += "\n" + line if current else line
    if current:
        parts.append(current)
    return parts


def _generate_scorecard_csv(drivers: list[dict]) -> io.BytesIO:
    """Generate CSV export of driver scorecards."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Driver", "Company", "Miles", "MPG", "Drive Hours", "Idle Hours",
        "Drive %", "Eco %", "Overspeed Min", "Coast Min", "Cruise Min",
        "Antic Brakes", "Total Brakes", "Antic %",
    ])
    for d in drivers:
        writer.writerow([
            d["driver_name"], d.get("_org", ""),
            d["_miles"], d["_mpg"], d["_drive_h"], d["_idle_h"],
            d["_drive_pct"], d["_green_pct"], d["_overspeed_min"],
            d["_coast_min"], d["_cruise_min"],
            d["_antic_brakes"], d["_total_brakes"], d["_antic_pct"],
        ])
    out = io.BytesIO(buf.getvalue().encode("utf-8"))
    out.name = "driver_scorecards.csv"
    return out


@_require_registered
async def cmd_scorecards(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         company: str | None = None):
    """Show format picker for driver scorecards."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_scorecard_all") or can(user.role, "can_scorecard_own")):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    company_label = COMPANY_DISPLAY.get(company, "All Companies") if company else "All Companies"
    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  🏆  <b>DRIVER SCORECARDS</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {company_label}\n"
        "\n  Choose export format:"
    )
    kb = scorecard_format_kb(company)
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_scorecards_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             company: str | None = None):
    """Generate scorecard text report."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_scorecard_all") or can(user.role, "can_scorecard_own")):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    companies = await db.get_account_companies(user.account_id)
    populate_company_display(companies)
    samsara = await get_client(user.account_id)
    company_label = COMPANY_DISPLAY.get(company, "All Companies") if company else "All Companies"

    await _show_loading(update, context, f"⏳ Loading scorecards ({company_label})…")
    try:
        drivers = await samsara.get_driver_efficiency(company=company)

        # Driver role: filter to own data
        if user.role == Role.DRIVER and not can(user.role, "can_scorecard_all"):
            if user.truck_num:
                drivers = [d for d in drivers if user.truck_num.lower() in d["driver_name"].lower()]

        texts = _format_scorecard_text(drivers, company_label)
        await _show(update, context, texts, keyboard=back_kb())
    except Exception as e:
        logger.error(f"Scorecards error: {e}")
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@_require_registered
async def cmd_scorecards_csv(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             company: str | None = None):
    """Generate scorecard CSV export."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_scorecard_all") or can(user.role, "can_scorecard_own")):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    companies = await db.get_account_companies(user.account_id)
    populate_company_display(companies)
    samsara = await get_client(user.account_id)
    company_label = COMPANY_DISPLAY.get(company, "All Companies") if company else "All Companies"

    await _show_loading(update, context, f"⏳ Generating CSV ({company_label})…")
    try:
        drivers = await samsara.get_driver_efficiency(company=company)

        if user.role == Role.DRIVER and not can(user.role, "can_scorecard_all"):
            if user.truck_num:
                drivers = [d for d in drivers if user.truck_num.lower() in d["driver_name"].lower()]

        csv_buf = await asyncio.to_thread(_generate_scorecard_csv, drivers)
        chat_id = update.effective_chat.id
        await context.bot.send_document(chat_id=chat_id, document=csv_buf,
                                        caption=f"🏆 Driver Scorecards — {company_label}")
        await _show(update, context, ["✅ CSV exported."], keyboard=back_kb())
    except Exception as e:
        logger.error(f"Scorecards CSV error: {e}")
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())
