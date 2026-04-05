"""DND morning delivery — shift handoff reports."""

from __future__ import annotations

from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from telegram.constants import ParseMode

from bot.config import db, logger


async def deliver_dnd_alerts(app: Application):
    """Deliver shift handoff report + queued DND alerts when working hours start.

    Runs hourly. When a user transitions from outside→inside working hours,
    sends a comprehensive shift report including:
    - Queued DND alerts from off-hours
    - Pending (unacknowledged) alerts for the account
    - Recently resolved alerts (last 12h)
    - Pending maintenance tasks
    """
    try:
        subscribers = await db.get_all_alert_subscribers()
        if not subscribers:
            return

        for sub in subscribers:
            if sub.is_in_quiet_hours():
                continue  # still in DND, wait

            queued = await db.get_pending_dnd_alerts(sub.telegram_id)

            # Get shift handoff data regardless of queued alerts
            handoff = await db.get_shift_handoff_data(sub.account_id, sub.telegram_id)
            has_handoff = (
                handoff["pending_alerts"]
                or handoff["resolved_alerts"]
                or handoff["pending_maintenance"]
            )

            if not queued and not has_handoff:
                continue

            lines = [
                "━━━━━━━━━━━━━━━━━━━",
                "  🌅  <b>SHIFT REPORT</b>",
                "━━━━━━━━━━━━━━━━━━━",
            ]

            # ── Queued DND alerts section ──────────────────────
            if queued:
                by_type: dict[str, list[dict]] = {}
                for q in queued:
                    by_type.setdefault(q["alert_type"], []).append(q)

                lines.append(
                    f"\n📬 <b>{len(queued)} alert{'s' if len(queued) != 1 else ''} "
                    "received off-hours:</b>\n"
                )

                for atype, alerts in by_type.items():
                    type_label = {"fault": "⚠️ Faults", "health": "🏥 Health", "fuel": "⛽ Fuel"}.get(atype, f"🔔 {atype}")
                    lines.append(f"  <b>{type_label}</b> ({len(alerts)}):")
                    for a in alerts[:5]:
                        try:
                            dt = datetime.fromisoformat(a["created_at"])
                            ts = dt.strftime("%H:%M")
                        except (ValueError, TypeError):
                            ts = str(a.get("created_at", "?"))[:5]
                        lines.append(f"    • {a['vehicle_name']} — {ts}")
                    if len(alerts) > 5:
                        lines.append(f"    <i>… and {len(alerts) - 5} more</i>")
                    lines.append("")

            # ── Pending (unacknowledged) alerts ────────────────
            pending = handoff["pending_alerts"]
            if pending:
                lines.append(f"🔴 <b>Pending Alerts ({len(pending)}):</b>")
                for p in pending[:8]:
                    vname = p.get("vehicle_name", "?")
                    atype = p.get("alert_type", "alert")
                    lines.append(f"  • {vname} — {atype}")
                if len(pending) > 8:
                    lines.append(f"  <i>… and {len(pending) - 8} more</i>")
                lines.append("")

            # ── Recently resolved alerts ───────────────────────
            resolved = handoff["resolved_alerts"]
            if resolved:
                lines.append(f"✅ <b>Resolved ({len(resolved)}):</b>")
                for r in resolved[:5]:
                    vname = r.get("vehicle_name", "?")
                    atype = r.get("alert_type", "alert")
                    lines.append(f"  • {vname} — {atype}")
                if len(resolved) > 5:
                    lines.append(f"  <i>… and {len(resolved) - 5} more</i>")
                lines.append("")

            # ── Pending maintenance ────────────────────────────
            maint = handoff["pending_maintenance"]
            if maint:
                lines.append(f"🔧 <b>Maintenance Due ({len(maint)}):</b>")
                for m in maint[:5]:
                    vname = m.get("vehicle_name", "?")
                    task = m.get("task_name", m.get("description", "task"))
                    status = m.get("status", "pending")
                    icon = "🔴" if status == "overdue" else "🟡"
                    lines.append(f"  {icon} {vname} — {task}")
                if len(maint) > 5:
                    lines.append(f"  <i>… and {len(maint) - 5} more</i>")
                lines.append("")

            summary_text = "\n".join(lines)

            try:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔔 Pending Alerts", callback_data="cmd_pending_alerts")],
                    [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
                ])
                if queued:
                    await db.mark_dnd_alerts_delivered(sub.telegram_id)
                await app.bot.send_message(
                    chat_id=sub.telegram_id,
                    text=summary_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
            except Exception as e:
                logger.error(f"DND delivery to {sub.telegram_id}: {e}")

    except Exception as e:
        logger.error(f"DND delivery error: {e}")
