"""Shared alert pipeline — severity tiers, keyboard builder, send_alert().

Universal alert pipeline with severity tiers:
  • CRITICAL — bypasses DND, requires ACK, re-alerts if unacknowledged
  • WARNING  — respects DND, requires ACK, re-alerts if unacknowledged
  • INFO     — respects DND, no ACK needed, history tracking only
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from enum import Enum
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from telegram.constants import ParseMode

from database import Role
from samsara_client import (
    COMPANY_DISPLAY, ORG_IDS,
    samsara_vehicle_url, samsara_event_url, samsara_fault_url,
)
from formatters import format_alert_history_footer

from bot.config import (
    db, logger, _active_messages,
    ESCALATION_TIMEOUT_MINUTES,
    FAULT_ALERT_COOLDOWN_HOURS,
    HEALTH_ALERT_COOLDOWN_HOURS,
)


# ═══════════════════════════════════════════════════════════════════
#  Severity Tiers & Alert Configuration
# ═══════════════════════════════════════════════════════════════════

class AlertSeverity(str, Enum):
    """Universal severity tiers for all alert types."""
    CRITICAL = "critical"   # 🔴 bypasses DND, ACK required, re-alerts
    WARNING  = "warning"    # 🟡 respects DND, ACK required, re-alerts
    INFO     = "info"       # 🔵 respects DND, no ACK, history only


# Re-alert configuration (replaces old escalation chains)
ACK_WINDOW_MINUTES = max(ESCALATION_TIMEOUT_MINUTES, 60)   # minutes before re-alert (min 60)
MAX_REALERTS = 2                                    # max re-alerts before auto-expire
SNOOZE_MINUTES = 15                                 # default snooze
SNOOZE_OPTIONS = [15, 30, 60, 120, 240]              # snooze choices (minutes)

# Sentinel user ID for system-initiated actions (auto-resolve, AI usage logging)
SYSTEM_USER_ID = -1

# Per-type cooldowns (prevent spam from sensor oscillation)
_COOLDOWN_HOURS = {
    "fault": FAULT_ALERT_COOLDOWN_HOURS,    # default 2h
    "health": HEALTH_ALERT_COOLDOWN_HOURS,  # default 4h
    "fuel": 0,                               # uses hysteresis instead
    "geofence": 0,                           # event-based, no cooldown
}

# J1939 SPNs related to coolant system
COOLANT_SPNS = {110, 111, 2609, 441, 1691}  # temp, level, low-level, pressure, additive

# Startup warm-up: first cycle of each check only populates caches
# without sending alerts. Prevents alert bursts on server restart.
_warmup_done: dict[str, bool] = {"health": False, "fuel": False}


def build_alert_keyboard(
    severity: AlertSeverity,
    co: str,
    vehicle_name: str,
    ack_id: int | None = None,
    alert_type: str = "fault",
    vehicle_id: str = "",
    event_id: str = "",
    event_time: str = "",
) -> InlineKeyboardMarkup:
    """Build keyboard for an alert message based on severity.

    CRITICAL/WARNING with ack_id → ACK + Snooze + AI Diagnose + Open in Samsara + View Truck
    CRITICAL/WARNING without ack_id → AI Diagnose + Open in Samsara + View Truck (pre-ACK send)
    INFO → Open in Samsara + View Truck only

    alert_type is encoded in the AI Diagnose callback so the AI knows
    the context (fault / health / fuel).
    """
    from bot.i18n import t
    rows: list[list[InlineKeyboardButton]] = []

    if severity in (AlertSeverity.CRITICAL, AlertSeverity.WARNING):
        if ack_id is not None:
            rows.append([InlineKeyboardButton(
                "✅ Acknowledge", callback_data=f"ack_alert_{ack_id}",
            )])
            rows.append([InlineKeyboardButton(
                "⏰ Snooze",
                callback_data=f"snooze_pick_{ack_id}",
            )])
        # Encode ack_id in AI Diagnose callback so diagnosis can show alert actions
        ai_diag_cb = f"ai_diag_{alert_type}_{co}_{vehicle_name}"
        if ack_id is not None:
            ai_diag_cb += f":{ack_id}"
        rows.append([InlineKeyboardButton(
            "🤖 AI Diagnose",
            callback_data=ai_diag_cb,
        )])

    # "Open in Samsara" deep-link (URL button — opens browser)
    org_id = ORG_IDS.get(co, "")
    if alert_type == "events" and event_id:
        samsara_url = samsara_event_url(org_id, event_id)
    elif alert_type in ("fault", "health"):
        samsara_url = samsara_fault_url(org_id, vehicle_id)
    else:
        samsara_url = samsara_vehicle_url(org_id, vehicle_id, alert_type)
    if samsara_url:
        rows.append([InlineKeyboardButton(
            t("alert_actions.open_in_samsara"),
            url=samsara_url,
        )])

    truck_cb = f"cotruck_{co}_{vehicle_name}"
    if ack_id is not None:
        truck_cb += f":{ack_id}"
    rows.append([InlineKeyboardButton(
        f"📋 View Truck #{vehicle_name}",
        callback_data=truck_cb,
    )])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])

    return InlineKeyboardMarkup(rows)


async def send_alert(
    app: Application,
    *,
    account_id: int,
    alert_type: str,
    severity: AlertSeverity,
    vehicle: dict,
    alert_text: str,
    subscribers: list,
    co: str,
    ai_note: str = "",
    alert_key_detail: str = "",
    video_url: str = "",
    event_id: str = "",
    event_time: str = "",
    photo_bytes: bytes | None = None,
):
    """Universal alert delivery pipeline.

    For each eligible subscriber: filters by role, handles DND, consolidates
    history (delete old → send new with occurrence footer), creates ACK records
    for CRITICAL/WARNING, and tracks active messages.
    """
    vid = vehicle["id"]
    vname = vehicle.get("name", "?")
    needs_ack = severity in (AlertSeverity.CRITICAL, AlertSeverity.WARNING)
    bypasses_dnd = severity == AlertSeverity.CRITICAL

    for sub in subscribers:
        # Driver: only alert for their own truck
        if sub.role == Role.DRIVER and sub.truck_num:
            if vname.lower() != sub.truck_num.lower():
                continue

        # DND: queue non-critical alerts during quiet hours
        if not bypasses_dnd and sub.is_in_quiet_hours():
            await db.queue_dnd_alert(
                account_id=account_id,
                telegram_id=sub.telegram_id,
                alert_type=alert_type,
                vehicle_name=vname,
                alert_text=alert_text,
            )
            continue

        try:
            # ── History consolidation — delete old, count occurrences ──
            existing_hist = await db.get_active_alert_history(
                account_id, alert_type, vid, sub.telegram_id,
            )
            if existing_hist and existing_hist["message_id"]:
                try:
                    await app.bot.delete_message(
                        chat_id=sub.telegram_id,
                        message_id=existing_hist["message_id"],
                    )
                except Exception:
                    pass

            count = (existing_hist["occurrence_count"] if existing_hist else 0) + 1
            first_seen = existing_hist["first_seen"] if existing_hist else ""
            now_str = datetime.now(timezone.utc).isoformat()
            history_footer = format_alert_history_footer(count, first_seen, now_str)

            # Build message text
            send_text = alert_text
            if ai_note:
                # Only include AI note if this subscriber has AI enabled for this alert type
                ai_field = {"fault": "ai_fault", "health": "ai_health",
                            "fuel": "ai_fuel", "events": "ai_events",
                            "parking": "ai_parking"}.get(alert_type)
                if ai_field and getattr(sub, ai_field, False):
                    send_text += ai_note
            send_text += history_footer

            # ── Send dashcam video first (events) so text can reply to it ──
            video_msg_id = None
            if video_url:
                try:
                    vmsg = await app.bot.send_video(
                        chat_id=sub.telegram_id,
                        video=video_url,
                        caption=f"🎥 {vname}",
                        read_timeout=30,
                        write_timeout=30,
                    )
                    video_msg_id = vmsg.message_id
                except Exception as ve:
                    logger.debug(f"Video send failed for {vname}: {ve}")

            # ── Send map photo (parking alerts) so text can reply to it ──
            photo_msg_id = None
            if photo_bytes and not video_msg_id:
                try:
                    import io as _io
                    pmsg = await app.bot.send_photo(
                        chat_id=sub.telegram_id,
                        photo=_io.BytesIO(photo_bytes),
                        caption=f"📍 Parking location — #{vname}",
                        read_timeout=15,
                        write_timeout=15,
                    )
                    photo_msg_id = pmsg.message_id
                except Exception as pe:
                    logger.debug(f"Photo send failed for {vname}: {pe}")

            reply_to = video_msg_id or photo_msg_id

            if needs_ack:
                # Supersede old unacked alerts for this vehicle/type/subscriber
                old_acks = await db.get_active_vehicle_acks(
                    account_id, vid, sub.telegram_id,
                )
                for old_ack in old_acks:
                    if old_ack.get("alert_type") == alert_type:
                        await db.supersede_alert_ack(old_ack["id"])
                        if old_ack.get("message_id") and old_ack.get("chat_id"):
                            try:
                                await app.bot.delete_message(
                                    chat_id=old_ack["chat_id"],
                                    message_id=old_ack["message_id"],
                                )
                            except Exception:
                                pass

                # Send with basic keyboard (no ack_id yet)
                basic_kb = build_alert_keyboard(
                    severity, co, vname, alert_type=alert_type,
                    vehicle_id=vid, event_id=event_id, event_time=event_time,
                )
                msg = await app.bot.send_message(
                    chat_id=sub.telegram_id,
                    text=send_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=basic_kb,
                    reply_to_message_id=reply_to,
                )

                # Create ACK record
                next_realert = (datetime.now(timezone.utc) + timedelta(
                    minutes=ACK_WINDOW_MINUTES,
                )).isoformat()
                alert_key = f"{co}:{vid}:{alert_key_detail}"
                ack_id = await db.create_alert_ack(
                    account_id=account_id,
                    alert_type=alert_type,
                    vehicle_id=vid,
                    vehicle_name=vname,
                    alert_key=alert_key,
                    message_id=msg.message_id,
                    chat_id=sub.telegram_id,
                    sent_to=sub.telegram_id,
                    next_escalation=next_realert,
                )

                # Update keyboard with ACK/Snooze buttons
                ack_kb = build_alert_keyboard(
                    severity, co, vname, ack_id=ack_id, alert_type=alert_type,
                    vehicle_id=vid, event_id=event_id, event_time=event_time,
                )
                await app.bot.edit_message_reply_markup(
                    chat_id=sub.telegram_id,
                    message_id=msg.message_id,
                    reply_markup=ack_kb,
                )
            else:
                # INFO — no ACK tracking needed
                basic_kb = build_alert_keyboard(
                    severity, co, vname, alert_type=alert_type,
                    vehicle_id=vid, event_id=event_id, event_time=event_time,
                )
                msg = await app.bot.send_message(
                    chat_id=sub.telegram_id,
                    text=send_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=basic_kb,
                    reply_to_message_id=reply_to,
                )

            # Update alert history
            await db.upsert_alert_history(
                account_id=account_id,
                alert_type=alert_type,
                vehicle_id=vid,
                vehicle_name=vname,
                chat_id=sub.telegram_id,
                message_id=msg.message_id,
                last_detail=alert_key_detail,
            )
            _active_messages.setdefault(
                (sub.telegram_id, sub.telegram_id), []
            ).append(msg.message_id)
        except Exception as e:
            logger.error(f"{alert_type} alert to {sub.telegram_id}: {e}")


async def is_vehicle_suppressed(account_id: int, vehicle_name: str) -> bool:
    """Check if alerts should be suppressed for a vehicle in active maintenance."""
    try:
        return await db.is_vehicle_in_maintenance(account_id, vehicle_name)
    except Exception:
        return False
