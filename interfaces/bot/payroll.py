"""Bot /my_pay command — driver self-service paystub view."""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from capabilities.permissions.roles import can
from capabilities.localization.i18n import t
from features.payroll import service as svc
from infra.platform import get_platform_db

logger = logging.getLogger(__name__)


def _fmt_cents(c: int | float) -> str:
    return f"${(int(c) / 100):,.2f}"


async def _resolve_driver_id_for_user(account_id: int, user) -> Optional[str]:
    """Return the Samsara driver_id explicitly bound to this user
    (users.samsara_driver_id). Returns None if no admin has linked them.

    The earlier most-recent-event-by-truck heuristic leaked other drivers'
    paystubs after vehicle reassignment, so this binding is now required
    to come from the admin-set users.samsara_driver_id column.
    """
    did = (getattr(user, "samsara_driver_id", None) or "").strip()
    return did or None


async def cmd_my_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the caller their latest paystub + a brief recent history."""
    user = context.user_data.get("_db_user")
    if user is None:
        await update.message.reply_text(t("access.no_access"))
        return

    if not (can(user.role, "can_payroll_admin")
            or can(user.role, "can_payroll_view_own")):
        await update.message.reply_text(t("access.no_access"))
        return

    # Kill-switch
    pdb = get_platform_db()
    acct = await pdb.get_account(user.account_id)
    from capabilities.permissions.modules import module_enabled
    if acct is None or not module_enabled(
        getattr(acct, "disabled_modules", ""), "payroll",
    ):
        await update.message.reply_text(t("payroll.disabled_for_account"))
        return

    driver_id = await _resolve_driver_id_for_user(user.account_id, user)
    if not driver_id:
        await update.message.reply_text(t("payroll.my_pay.no_driver_mapping"))
        return

    items = await svc.get_paystub_history(
        user.account_id, driver_id, limit=6,
    )
    if not items:
        await update.message.reply_text(t("payroll.my_pay.no_history"))
        return

    latest = items[0]
    lines: list[str] = []
    lines.append("<b>" + t("payroll.my_pay.title") + "</b>")
    lines.append("")
    lines.append(t(
        "payroll.my_pay.period",
        start=latest.get("period_start", ""),
        end=latest.get("period_end", ""),
    ))
    lines.append(t(
        "payroll.my_pay.status",
        status=str(latest.get("status", "draft")).upper(),
    ))
    lines.append(t(
        "payroll.my_pay.base",
        amount=_fmt_cents(latest.get("base_pay_cents", 0)),
    ))

    breakdown = latest.get("breakdown") or []
    if breakdown:
        lines.append("")
        lines.append("<b>" + t("payroll.my_pay.bonuses") + "</b>")
        for b in breakdown:
            lines.append(
                f"• {b.get('name', '')} — {_fmt_cents(b.get('amount_cents', 0))}"
            )

    lines.append("")
    lines.append("<b>" + t(
        "payroll.my_pay.total",
        amount=_fmt_cents(latest.get("total_cents", 0)),
    ) + "</b>")

    if len(items) > 1:
        lines.append("")
        lines.append("<b>" + t("payroll.my_pay.history") + "</b>")
        for it in items[1:]:
            lines.append(
                f"• {it.get('period_start', '')} → "
                f"{it.get('period_end', '')}: "
                f"{_fmt_cents(it.get('total_cents', 0))}"
            )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )
