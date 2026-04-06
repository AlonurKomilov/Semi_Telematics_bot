"""DND morning delivery — shift handoff reports."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from telegram.constants import ParseMode

from bot.config import db, logger


async def deliver_dnd_alerts(app: Application):
    """Deliver shift handoff report when working hours start.

    Runs hourly. When a user transitions from outside→inside working hours,
    sends a short Telegram summary (counts + critical items only) plus a
    detailed PDF shift report as a document attachment.

    Only delivers once per calendar day (user's local timezone).
    """
    try:
        subscribers = await db.get_all_alert_subscribers()
        if not subscribers:
            return

        for sub in subscribers:
            if sub.quiet_start is None or sub.quiet_end is None:
                continue  # no working hours configured

            try:
                user_tz = ZoneInfo(sub.timezone)
            except Exception:
                user_tz = ZoneInfo("America/New_York")
            now_local = datetime.now(timezone.utc).astimezone(user_tz)
            local_hour = now_local.hour

            # Only deliver at the START of working hours
            if local_hour != sub.quiet_start:
                continue

            # ── Prevent duplicate delivery within the same day ──
            today_local = now_local.strftime("%Y-%m-%d")
            if sub.last_shift_report == today_local:
                continue  # already sent today

            queued = await db.get_pending_dnd_alerts(sub.telegram_id)

            handoff = await db.get_shift_handoff_data(sub.account_id, sub.telegram_id)
            pending = handoff["pending_alerts"]
            resolved = handoff["resolved_alerts"]
            maint = handoff["pending_maintenance"]
            history = handoff.get("recent_history", [])

            if not queued and not pending and not resolved and not maint and not history:
                continue

            # ── Build compact Telegram summary ─────────────────
            summary_text = _build_summary_text(
                sub, queued, pending, resolved, maint, history,
            )

            # ── Generate PDF ───────────────────────────────────
            pdf_buf = _generate_pdf(
                sub, queued, pending, resolved, maint, history, now_local,
            )

            # ── Send ───────────────────────────────────────────
            try:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔔 Pending Alerts", callback_data="cmd_pending_alerts")],
                    [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
                ])

                if queued:
                    await db.mark_dnd_alerts_delivered(sub.telegram_id)

                if pdf_buf:
                    filename = f"shift_report_{today_local}.pdf"
                    await app.bot.send_document(
                        chat_id=sub.telegram_id,
                        document=pdf_buf,
                        filename=filename,
                        caption="🌅 Shift Handoff Report",
                    )

                await app.bot.send_message(
                    chat_id=sub.telegram_id,
                    text=summary_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )

                await db.update_user(sub.id, last_shift_report=today_local)
            except Exception as e:
                logger.error(f"DND delivery to {sub.telegram_id}: {e}")

    except Exception as e:
        logger.error(f"DND delivery error: {e}")


# ── Helpers ──────────────────────────────────────────────────────

def _build_summary_text(sub, queued, pending, resolved, maint, history) -> str:
    """Build a short Telegram-friendly summary with counts and critical items."""
    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        "  🌅  <b>SHIFT REPORT</b>",
        "━━━━━━━━━━━━━━━━━━━",
    ]

    if sub.quiet_start is not None and sub.quiet_end is not None:
        lines.append(
            f"\n🕐 <b>{sub.quiet_start:02d}:00 – {sub.quiet_end:02d}:00</b>"
        )

    # Off-hours alerts: type breakdown (counts only)
    if queued:
        by_type: dict[str, int] = {}
        for q in queued:
            atype = q.get("alert_type", "other")
            by_type[atype] = by_type.get(atype, 0) + 1
        type_icons = {
            "fault": "⚠️", "health": "🏥", "fuel": "⛽",
            "events": "🚨", "geofence": "📍", "camera": "📷",
            "parking": "🅿️",
        }
        parts = [f"{type_icons.get(t, '🔔')} {t}: {c}" for t, c in by_type.items()]
        lines.append(
            f"\n📬 <b>{len(queued)} off-hours alert"
            f"{'s' if len(queued) != 1 else ''}</b>"
        )
        lines.append("  " + "  •  ".join(parts))

    # Recent alert activity summary (from alert_history)
    if history:
        hist_by_type: dict[str, int] = {}
        for h in history:
            ht = h.get("alert_type", "other")
            hist_by_type[ht] = hist_by_type.get(ht, 0) + 1
        hist_parts = [f"{t}: {c}" for t, c in hist_by_type.items()]
        lines.append(
            f"\n📊 <b>{len(history)} alerts in last 24h</b>"
        )
        lines.append("  " + "  •  ".join(hist_parts))

    # One-line counts
    counts = []
    if pending:
        counts.append(f"🔴 {len(pending)} pending")
    if resolved:
        counts.append(f"✅ {len(resolved)} resolved")
    overdue = [m for m in maint if m.get("status") == "overdue"]
    if overdue:
        counts.append(f"🔧🔴 {len(overdue)} overdue maintenance")
    elif maint:
        counts.append(f"🔧 {len(maint)} maintenance due")
    if counts:
        lines.append("\n" + "  •  ".join(counts))

    lines.append("\n📎 <i>Full details in the attached PDF</i>")
    return "\n".join(lines)


def _generate_pdf(sub, queued, pending, resolved, maint, history, now_local):
    """Generate shift report PDF. Returns BytesIO or None on failure."""
    try:
        from reports import generate_shift_report_pdf

        working_hours = None
        if sub.quiet_start is not None and sub.quiet_end is not None:
            working_hours = (sub.quiet_start, sub.quiet_end)

        return generate_shift_report_pdf(
            user_name=sub.label,
            working_hours=working_hours,
            queued_alerts=queued,
            pending_alerts=pending,
            resolved_alerts=resolved,
            pending_maintenance=maint,
            report_date=now_local.strftime("%B %d, %Y  %I:%M %p"),
            timezone_name=sub.timezone,
        )
    except Exception as e:
        logger.error(f"Shift PDF generation failed: {e}")
        return None
