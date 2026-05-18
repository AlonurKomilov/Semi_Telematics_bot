"""Driver Scorecards — efficiency data per driver."""

import asyncio
from datetime import datetime as _dt
from constants import TZ_ET as _TZ_ET
from capabilities.localization.i18n import t

from telegram import Update
from telegram.ext import ContextTypes

from adapters.storage import Role
from capabilities.iam.permissions import can
from infra.context import get_company_display
from capabilities.vehicles.service import prepare_companies
from capabilities.telemetry.service import get_driver_efficiency as _svc_driver_efficiency
from capabilities.scoring.service import evaluate_drivers as _svc_evaluate_drivers
from capabilities.reporting.csv_generators import generate_scorecard_csv as _generate_scorecard_csv

from interfaces.bot.config import logger
from interfaces.bot.keyboards import back_kb, scorecard_format_kb
from interfaces.bot.helpers import _show, _show_loading, _safe_error
from interfaces.bot.auth import _require_registered


def _format_scorecard_text(
    drivers: list[dict],
    company_label: str,
    pillar_by_driver: dict[str, dict] | None = None,
    own_only: bool = False,
) -> list[str]:
    """Build Telegram messages for driver scorecards.

    *pillar_by_driver* (optional) — mapping ``driver_id -> composite
    scorecard dict`` produced by ``capabilities.scoring.service``. When
    provided, each card gets a one-line pillar summary
    (🛡 Safety · ⚡ Eff · 🛠 Compliance → total). Backward-compat: when
    None or empty, the formatter falls back to the legacy layout.

    *own_only* — when True the report renders with a personal "My
    Scorecard" header and the company-wide driver count is omitted, so
    drivers with own-only access never see fleet size leak through.
    """
    if not drivers:
        return [t("scorecards.no_data", company=company_label)]

    pillar_by_driver = pillar_by_driver or {}
    now_et = _dt.now(_TZ_ET)
    title_key = "scorecards.my_title" if own_only else "scorecards.title"
    header_lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  🏆  <b>{t(title_key)}</b>",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  {company_label}",
        f"  {now_et:%b %d, %Y %I:%M %p ET}",
    ]
    if not own_only:
        header_lines.append(f"  {t('scorecards.driver_count', count=len(drivers))}")
    header = "\n".join(header_lines) + "\n"

    lines = [header]
    for d in drivers:
        org = d.get("_org", "")
        org_tag = f"  [{org}]" if org else ""
        # Pillar line. Driver lookup tolerates a
        # missing id by falling back to driver_name — mirrors the
        # safety_signals.from_events fallback path.
        sc = pillar_by_driver.get(str(d.get("driver_id") or "")) \
             or pillar_by_driver.get(d.get("driver_name", ""))
        pillar_line = ""
        if sc and sc.get("pillars"):
            p = sc["pillars"]
            s = p.get("safety",     {})
            e = p.get("efficiency", {})
            c = p.get("compliance", {})
            pillar_line = (
                f"\n  🛡 {s.get('subtotal', 0)}/{s.get('cap', 50)} · "
                f"⚡ {e.get('subtotal', 0)}/{e.get('cap', 25)} · "
                f"🛠 {c.get('subtotal', 0)}/{c.get('cap', 25)} "
                f"→ <b>{sc.get('total', sc.get('score', 0))}/100</b>"
            )
            if sc.get("insufficient_data"):
                pillar_line += "  ⚠️ insufficient drive time"
        card = (
            f"\n{'─' * 30}\n"
            f"  👤 <b>{d['driver_name']}</b>{org_tag}"
            f"{pillar_line}\n"
            f"  {t('scorecards.miles_mpg', miles=d['_miles'], mpg=d['_mpg'])}\n"
            f"  {t('scorecards.drive_idle', drive_h=d['_drive_h'], drive_pct=d['_drive_pct'], idle_h=d['_idle_h'], idle_pct=d['_idle_pct'])}\n"
            f"  {t('scorecards.eco_overspeed', green_pct=d['_green_pct'], overspeed_min=d['_overspeed_min'])}\n"
            f"  {t('scorecards.brakes_antic', antic_pct=d['_antic_pct'])}"
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



@_require_registered
async def cmd_scorecards(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         company: str | None = None):
    """Show format picker for driver scorecards."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_scorecard_all") or can(user.role, "can_scorecard_own")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    company_label = get_company_display().get(company, t("common.all_companies")) if company else t("common.all_companies")
    own_only = user.role == Role.DRIVER and not can(user.role, "can_scorecard_all")
    title_key = "scorecards.my_title" if own_only else "scorecards.title"
    text = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  🏆  <b>{t(title_key)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {company_label}\n"
        f"\n  {t('scorecards.choose_format')}"
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
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await prepare_companies(user.account_id)
    company_label = get_company_display().get(company, t("common.all_companies")) if company else t("common.all_companies")

    await _show_loading(update, context, t("scorecards.loading", company=company_label))
    try:
        own_only = user.role == Role.DRIVER and not can(user.role, "can_scorecard_all")
        vehicle_filter: list[str] | None = [user.truck_num] if (own_only and user.truck_num) else ([] if own_only else None)
        drivers = await _svc_driver_efficiency(user.account_id, company=company, vehicle_nums=vehicle_filter)

        # Best-effort composite scorecards for pillar line. Failure
        # falls back silently to the legacy text — the bot must keep
        # working even if the scoring engine is mid-deploy or signals
        # are missing.
        pillar_by_driver: dict[str, dict] = {}
        try:
            sc_rows = await _svc_evaluate_drivers(
                user.account_id, company=company, vehicle_nums=vehicle_filter,
            )
            for row in sc_rows:
                key = str(row.get("driver_id") or row.get("driver_name") or "")
                if key:
                    pillar_by_driver[key] = row
        except Exception:
            logger.exception("Scorecards: composite enrichment failed; falling back to flat text")

        texts = _format_scorecard_text(drivers, company_label, pillar_by_driver, own_only=own_only)
        await _show(update, context, texts, keyboard=back_kb())
    except Exception as e:
        logger.error(f"Scorecards error: {e}")
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


@_require_registered
async def cmd_scorecards_csv(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             company: str | None = None):
    """Generate scorecard CSV export."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_scorecard_all") or can(user.role, "can_scorecard_own")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    await prepare_companies(user.account_id)
    company_label = get_company_display().get(company, t("common.all_companies")) if company else t("common.all_companies")

    await _show_loading(update, context, t("scorecards.loading_csv", company=company_label))
    try:
        own_only = user.role == Role.DRIVER and not can(user.role, "can_scorecard_all")
        truck_filter2: list[str] | None = [user.truck_num] if (own_only and user.truck_num) else ([] if own_only else None)
        drivers = await _svc_driver_efficiency(user.account_id, company=company, vehicle_nums=truck_filter2)

        # Best-effort pillar enrichment for the CSV.
        pillar_by_driver: dict[str, dict] = {}
        try:
            sc_rows = await _svc_evaluate_drivers(
                user.account_id, company=company, vehicle_nums=truck_filter2,
            )
            for row in sc_rows:
                key = str(row.get("driver_id") or row.get("driver_name") or "")
                if key:
                    pillar_by_driver[key] = row
        except Exception:
            logger.exception("Scorecards CSV: composite enrichment failed; exporting flat columns only")

        csv_buf = await asyncio.to_thread(
            _generate_scorecard_csv, drivers, pillar_by_driver,
        )
        chat_id = update.effective_chat.id
        await context.bot.send_document(chat_id=chat_id, document=csv_buf,
                                        caption=t("scorecards.csv_caption", company=company_label))
        await _show(update, context, [t("scorecards.csv_exported")], keyboard=back_kb())
    except Exception as e:
        logger.error(f"Scorecards CSV error: {e}")
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())
