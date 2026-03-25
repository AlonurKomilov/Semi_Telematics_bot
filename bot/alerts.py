"""Scheduled alert checks — fault, health, fuel — multi-tenant.

Universal alert pipeline with severity tiers:
  • CRITICAL — bypasses DND, requires ACK, re-alerts if unacknowledged
  • WARNING  — respects DND, requires ACK, re-alerts if unacknowledged
  • INFO     — respects DND, no ACK needed, history tracking only

Features:
  • Unified send_alert() pipeline for all alert types
  • Re-alert system: same user gets reminded after ACK_WINDOW_MINUTES
  • Role-based delivery (drivers see own truck, fleet mgrs see all, etc.)
  • DND quiet hours: non-critical alerts queued for morning delivery
  • Proactive AI diagnosis (when configured)
  • Auto-maintenance creation from critical faults
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from enum import Enum
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from telegram.constants import ParseMode

from database import Role
from samsara_client import populate_company_display, ORG_IDS, samsara_vehicle_url
from formatters import (
    format_new_fault_alert, format_critical_fault_alert,
    format_health_alert, format_low_fuel_alert,
    format_alert_history_footer, format_event_alert,
)

from bot.config import (
    db, logger, _known_faults, _active_messages, get_client,
    FUEL_THRESHOLD, FUEL_HYSTERESIS_PERCENT,
    HEALTH_ALERT_COOLDOWN_HOURS, FAULT_ALERT_COOLDOWN_HOURS,
    ESCALATION_TIMEOUT_MINUTES, ESCALATION_MAX_HOURS,
)
import bot.redis_client as rcache


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

# Cooldown: don't re-alert the same company API failure within 6 hours
_API_ALERT_COOLDOWN_S = 6 * 3600
_api_alert_sent: dict[str, float] = {}   # "acctID:CODE" → timestamp

# Startup warm-up: first cycle of each check only populates caches
# without sending alerts. Prevents alert bursts on server restart.
_warmup_done: dict[str, bool] = {"health": False, "fuel": False}

# ── Dedup state dicts (in-memory fallback when Redis unavailable) ─

# Health alert dedup — track which vehicles already had each alert type
_known_health: dict[str, set[str]] = {}

# Health alert cooldown — track when each alert was last sent per vehicle
_health_last_sent: dict[str, float] = {}

# Low fuel dedup — track which vehicles were already flagged
_known_low_fuel: dict[str, bool] = {}

# Fault alert cooldown — track when each vehicle last had a fault alert sent
_fault_last_sent: dict[str, float] = {}

# Events dedup — track which event IDs have already been alerted
_known_event_ids: dict[int, set[str]] = {}  # account_id → set of event IDs

# J1939 SPNs related to coolant system
COOLANT_SPNS = {110, 111, 2609, 441, 1691}  # temp, level, low-level, pressure, additive


async def _get_known_faults(vid: str) -> set[str]:
    """Get previously known fault codes for a vehicle (Redis → in-memory)."""
    if rcache.is_available():
        return await rcache.smembers(f"faults:{vid}")
    return _known_faults.get(vid, set())


async def _set_known_faults(vid: str, codes: set[str]):
    """Store known fault codes for a vehicle (Redis → in-memory)."""
    if rcache.is_available():
        await rcache.sset(f"faults:{vid}", codes, ttl=86400)
    else:
        _known_faults[vid] = codes


async def check_new_faults(app: Application):
    """Check all accounts with fault-alert subscribers for new faults.

    Classifies faults into CRITICAL (STOP/PROTECT/EMISSIONS lights or
    "Most Severe" FMI) and WARNING (normal faults). Uses the universal
    send_alert() pipeline for delivery.
    """
    try:
        subscribers = await db.get_all_typed_subscribers("faults")
        if not subscribers:
            return

        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        for account_id, subs in acct_subs.items():
            try:
                samsara = await get_client(account_id)
                faulted, _, _ = await samsara.get_vehicles_with_faults()

                if samsara.last_skipped:
                    await _notify_api_errors(app, account_id, samsara.last_skipped)

                acct_companies = await db.get_account_companies(account_id)
                populate_company_display(acct_companies)
                await samsara.ensure_org_ids()
                company_codes = [o.code for o in acct_companies]

                for v in faulted:
                    co = v.get("_org", "?")
                    vid = f"{account_id}:{co}:{v['id']}"

                    if await is_vehicle_suppressed(account_id, v["name"]):
                        continue

                    current_codes = set()
                    for dtc in v.get("_dtcs", []):
                        key = f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}"
                        current_codes.add(key)

                    previously_known = await _get_known_faults(vid)
                    new_codes = current_codes - previously_known

                    if new_codes and previously_known:
                        new_dtcs = [
                            dtc for dtc in v.get("_dtcs", [])
                            if f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}" in new_codes
                        ]
                        if new_dtcs:
                            # ── Classify severity ────────────────
                            lights = v.get("_lights", {})
                            is_critical = (
                                lights.get("stopIsOn", False)
                                or lights.get("protectIsOn", False)
                                or lights.get("emissionsIsOn", False)
                            )
                            if not is_critical:
                                for dtc in new_dtcs:
                                    fmi_desc = dtc.get("fmiDescription", "").lower()
                                    if "most severe" in fmi_desc:
                                        is_critical = True
                                        break

                            severity = (AlertSeverity.CRITICAL if is_critical
                                        else AlertSeverity.WARNING)

                            # Cooldown: skip WARNING faults if recently alerted
                            if severity != AlertSeverity.CRITICAL:
                                last_sent = await _get_fault_last_sent(vid)
                                if _is_fault_on_cooldown(last_sent):
                                    await _set_known_faults(vid, current_codes)
                                    continue

                            # Check for coolant-related DTCs
                            has_coolant_dtc = any(
                                dtc.get("spnId") in COOLANT_SPNS
                                for dtc in new_dtcs
                            )

                            show_co = len(company_codes) > 1
                            if severity == AlertSeverity.CRITICAL:
                                alert_text = format_critical_fault_alert(
                                    v, new_dtcs, lights, show_company=show_co,
                                )
                            else:
                                alert_text = format_new_fault_alert(
                                    v, new_dtcs, show_company=show_co,
                                )

                            if has_coolant_dtc:
                                alert_text += (
                                    "\n\n  🌡 <b>Coolant system fault detected</b>"
                                    "\n  Check coolant level and temp"
                                )

                            # Proactive AI — only if any subscriber enabled it
                            ai_note = ""
                            if any(getattr(s, 'ai_fault', False) for s in subs):
                                ai_note = await _get_ai_diagnosis_note(v, new_dtcs)

                            # Build fault detail with descriptions
                            fault_details = []
                            for dtc in new_dtcs:
                                spn = dtc.get('spnId', '?')
                                fmi = dtc.get('fmiId', '?')
                                desc = dtc.get('spnDescription', '')
                                fault_details.append(f"{spn}-{fmi}:{desc}")
                            fault_detail_str = "|".join(sorted(fault_details))

                            # ── Universal pipeline ───────────────
                            await send_alert(
                                app,
                                account_id=account_id,
                                alert_type="fault",
                                severity=severity,
                                vehicle=v,
                                alert_text=alert_text,
                                subscribers=subs,
                                co=co,
                                ai_note=ai_note,
                                alert_key_detail=fault_detail_str,
                            )

                            if severity == AlertSeverity.CRITICAL:
                                await auto_create_maintenance_from_faults(
                                    account_id, v["name"], new_dtcs,
                                )

                            await _set_fault_last_sent(vid)

                    await _set_known_faults(vid, current_codes)

                # Clear fault alert history for vehicles that no longer
                # have faults — delete old messages from chat
                current_faulted_vids = {
                    f"{account_id}:{v.get('_org', '?')}:{v['id']}"
                    for v in faulted
                }
                stale_fault_keys = [
                    k for k in list(_known_faults.keys())
                    if k.startswith(f"{account_id}:")
                    and k not in current_faulted_vids
                ]
                for k in stale_fault_keys:
                    parts = k.split(":", 2)
                    if len(parts) == 3:
                        co = parts[1]
                        v_id = parts[2]
                        cleared = await db.clear_alert_history(
                            account_id, "fault", v_id,
                        )
                        for rec in cleared:
                            if rec.get("message_id") and rec.get("chat_id"):
                                try:
                                    await app.bot.delete_message(
                                        chat_id=rec["chat_id"],
                                        message_id=rec["message_id"],
                                    )
                                except Exception:
                                    pass
                        # Auto-resolve unacked fault alerts for this vehicle
                        await _auto_resolve_vehicle_alerts(
                            app, account_id, "fault", v_id, "", co,
                        )
                    await _set_known_faults(k, set())

            except Exception as e:
                logger.error(f"Fault check for account {account_id}: {e}")

    except Exception as e:
        logger.error(f"Fault check error: {e}")


async def initialize_known_faults():
    """Pre-populate known faults for all accounts with alert subscribers."""
    try:
        subscribers = await db.get_all_alert_subscribers()
        account_ids = set(s.account_id for s in subscribers)

        for account_id in account_ids:
            try:
                samsara = await get_client(account_id)
                faulted, _, _ = await samsara.get_vehicles_with_faults()
                for v in faulted:
                    co = v.get("_org", "?")
                    vid = f"{account_id}:{co}:{v['id']}"
                    codes = set()
                    for dtc in v.get("_dtcs", []):
                        key = f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}"
                        codes.add(key)
                    await _set_known_faults(vid, codes)
            except Exception as e:
                logger.error(f"Init faults for account {account_id}: {e}")

        logger.info(f"Known faults loaded for {len(_known_faults)} vehicles")
    except Exception as e:
        logger.error(f"Init faults failed: {e}")


# ── Health-alert dedup helpers ───────────────────────────────────

async def _get_known_health(vid: str) -> set[str]:
    """Previously known health alert names for a vehicle."""
    if rcache.is_available():
        return await rcache.smembers(f"health:{vid}")
    return _known_health.get(vid, set())


async def _set_known_health(vid: str, alerts: set[str]):
    if rcache.is_available():
        await rcache.sset(f"health:{vid}", alerts, ttl=86400)
    else:
        _known_health[vid] = alerts


async def _get_health_last_sent(vid: str) -> float:
    """Get timestamp of last health alert sent for this vehicle."""
    if rcache.is_available():
        val = await rcache.get(f"health_ts:{vid}")
        return float(val) if val else 0.0
    return _health_last_sent.get(vid, 0.0)


async def _set_health_last_sent(vid: str):
    """Record that a health alert was just sent for this vehicle."""
    now = datetime.now(timezone.utc).timestamp()
    ttl = int(HEALTH_ALERT_COOLDOWN_HOURS * 3600) + 300  # slight buffer
    if rcache.is_available():
        await rcache.set(f"health_ts:{vid}", now, ttl=ttl)
    else:
        _health_last_sent[vid] = now


def _is_health_on_cooldown(last_sent: float) -> bool:
    """Return True if a health alert was sent too recently."""
    if last_sent == 0.0:
        return False
    now = datetime.now(timezone.utc).timestamp()
    return (now - last_sent) < (HEALTH_ALERT_COOLDOWN_HOURS * 3600)


async def _is_known_low_fuel(vid: str) -> bool:
    if rcache.is_available():
        return await rcache.exists(f"lowfuel:{vid}")
    return _known_low_fuel.get(vid, False)


async def _set_low_fuel_flag(vid: str, flagged: bool):
    if rcache.is_available():
        if flagged:
            await rcache.setex_flag(f"lowfuel:{vid}", 86400)
        else:
            # Let it expire naturally; no explicit delete needed
            pass
    else:
        if flagged:
            _known_low_fuel[vid] = True
        else:
            _known_low_fuel.pop(vid, None)


# ── Fault alert cooldown helpers ─────────────────────────────────

async def _get_fault_last_sent(vid: str) -> float:
    """Get timestamp of last fault alert sent for this vehicle."""
    if rcache.is_available():
        val = await rcache.get(f"fault_ts:{vid}")
        return float(val) if val else 0.0
    return _fault_last_sent.get(vid, 0.0)


async def _set_fault_last_sent(vid: str):
    """Record that a fault alert was just sent for this vehicle."""
    now = datetime.now(timezone.utc).timestamp()
    ttl = int(FAULT_ALERT_COOLDOWN_HOURS * 3600) + 300
    if rcache.is_available():
        await rcache.set(f"fault_ts:{vid}", now, ttl=ttl)
    else:
        _fault_last_sent[vid] = now


def _is_fault_on_cooldown(last_sent: float) -> bool:
    """Return True if a fault alert was sent too recently."""
    if last_sent == 0.0:
        return False
    now = datetime.now(timezone.utc).timestamp()
    return (now - last_sent) < (FAULT_ALERT_COOLDOWN_HOURS * 3600)


# ═══════════════════════════════════════════════════════════════════
#  Universal Alert Pipeline
# ═══════════════════════════════════════════════════════════════════

def build_alert_keyboard(
    severity: AlertSeverity,
    co: str,
    vehicle_name: str,
    ack_id: int | None = None,
    alert_type: str = "fault",
    vehicle_id: str = "",
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
    samsara_url = samsara_vehicle_url(org_id, vehicle_id, alert_type)
    if samsara_url:
        rows.append([InlineKeyboardButton(
            t("alert_actions.open_in_samsara"),
            url=samsara_url,
        )])

    rows.append([InlineKeyboardButton(
        f"📋 View Truck #{vehicle_name}",
        callback_data=f"cotruck_{co}_{vehicle_name}",
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
):
    """Universal alert delivery pipeline.

    For each eligible subscriber: filters by role, handles DND, consolidates
    history (delete old → send new with occurrence footer), creates ACK records
    for CRITICAL/WARNING, and tracks active messages.
    """
    vid = vehicle["id"]
    vname = vehicle.get("name", "?")
    samsara_url = _build_samsara_url(vehicle.get("_org_id"), vid)
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
                send_text += ai_note
            send_text += history_footer

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
                    vehicle_id=vid,
                )
                msg = await app.bot.send_message(
                    chat_id=sub.telegram_id,
                    text=send_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=basic_kb,
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
                    vehicle_id=vid,
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
                    vehicle_id=vid,
                )
                msg = await app.bot.send_message(
                    chat_id=sub.telegram_id,
                    text=send_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=basic_kb,
                )

            # Send dashcam video as media when available (events)
            if video_url:
                try:
                    await app.bot.send_video(
                        chat_id=sub.telegram_id,
                        video=video_url,
                        caption=f"🎥 {vname}",
                        read_timeout=30,
                        write_timeout=30,
                    )
                except Exception as ve:
                    logger.debug(f"Video send failed for {vname}: {ve}")

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


# ── Health Alerts Scheduled Job ──────────────────────────────────

# Health-alert severity classification
_CRITICAL_HEALTH = {"low_oil_pressure", "high_coolant_temp"}
_WARNING_HEALTH = {"low_battery", "low_def", "coolant_dtc"}


async def check_health_alerts(app: Application):
    """Check all accounts for vehicle health warnings and push alerts.

    Detects: low battery, low oil pressure, high coolant temp, low DEF.
    Skips seatbelt and engine-load as they are transient. Also scans
    active DTCs for coolant-related SPNs.

    Severity classification:
      CRITICAL — low oil pressure, high coolant temp (engine damage risk)
      WARNING  — low battery, low DEF, coolant DTC
    """
    try:
        subscribers = await db.get_all_typed_subscribers("health")
        if not subscribers:
            return

        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        # Startup warm-up: first cycle only populates caches, no alerts.
        # Prevents burst of alerts on server restart when all dedup state
        # is empty (every existing issue looks "new").
        is_warmup = not _warmup_done.get("health", False)

        for account_id, subs in acct_subs.items():
            try:
                samsara = await get_client(account_id)
                vehicles = await samsara.get_vehicle_health()

                if is_warmup:
                    # Warm-up: populate dedup caches without sending alerts
                    for v in vehicles:
                        co = v.get("_org", "?")
                        vid = f"{account_id}:{co}:{v['id']}"
                        health_alerts = [
                            a for a in v.get("_health_alerts", [])
                            if a in ("low_battery", "low_oil_pressure",
                                     "high_coolant_temp", "low_def")
                        ]
                        if health_alerts:
                            await _set_known_health(vid, set(health_alerts))
                            await _set_health_last_sent(vid)
                    continue  # Next account — skip alerting

                acct_companies = await db.get_account_companies(account_id)
                populate_company_display(acct_companies)
                await samsara.ensure_org_ids()
                company_codes = [o.code for o in acct_companies]

                try:
                    faulted, _, _ = await samsara.get_vehicles_with_faults()
                    faulted_by_id = {v["id"]: v for v in faulted}
                except Exception:
                    faulted_by_id = {}

                for v in vehicles:
                    if await is_vehicle_suppressed(account_id, v.get("name", "")):
                        continue

                    alerts = v.get("_health_alerts", [])
                    push_alerts = [
                        a for a in alerts
                        if a in ("low_battery", "low_oil_pressure",
                                 "high_coolant_temp", "low_def")
                    ]

                    fv = faulted_by_id.get(v["id"])
                    if fv:
                        has_coolant_dtc = any(
                            dtc.get("spnId") in COOLANT_SPNS
                            for dtc in fv.get("_dtcs", [])
                        )
                        if has_coolant_dtc and "coolant_dtc" not in push_alerts:
                            push_alerts.append("coolant_dtc")

                    if not push_alerts:
                        co = v.get("_org", "?")
                        vid = f"{account_id}:{co}:{v['id']}"
                        cleared = await db.clear_alert_history(
                            account_id, "health", v["id"],
                        )
                        for rec in cleared:
                            if rec.get("message_id") and rec.get("chat_id"):
                                try:
                                    await app.bot.delete_message(
                                        chat_id=rec["chat_id"],
                                        message_id=rec["message_id"],
                                    )
                                except Exception:
                                    pass
                        # Auto-resolve any unacked health alerts for this vehicle
                        await _auto_resolve_vehicle_alerts(
                            app, account_id, "health", v["id"],
                            v.get("name", "?"), co,
                        )
                        # Clear dedup so returning issues are detected as new
                        await _set_known_health(vid, set())
                        continue

                    co = v.get("_org", "?")
                    vid = f"{account_id}:{co}:{v['id']}"
                    previously_known = await _get_known_health(vid)
                    new_alerts = set(push_alerts) - previously_known

                    if not new_alerts:
                        await _set_known_health(vid, set(push_alerts))
                        continue

                    # ── Classify severity ────────────────────
                    if new_alerts & _CRITICAL_HEALTH:
                        severity = AlertSeverity.CRITICAL
                    else:
                        severity = AlertSeverity.WARNING

                    # Cooldown: skip WARNING if recently alerted.
                    # CRITICAL always bypasses cooldown (safety-first).
                    if severity != AlertSeverity.CRITICAL:
                        last_sent = await _get_health_last_sent(vid)
                        if _is_health_on_cooldown(last_sent):
                            # Don't update _known_health here — preserves
                            # new codes so they alert after cooldown expires
                            continue

                    show_co = len(company_codes) > 1
                    health = v.get("_health", {})
                    alert_text = format_health_alert(
                        v, list(new_alerts), health,
                        show_company=show_co,
                    )

                    # Proactive AI — only if any subscriber enabled it
                    ai_note = ""
                    if any(getattr(s, 'ai_health', False) for s in subs):
                        ai_note = await _get_ai_health_note(v, list(new_alerts), health)

                    # ── Universal pipeline ───────────────────
                    await send_alert(
                        app,
                        account_id=account_id,
                        alert_type="health",
                        severity=severity,
                        vehicle=v,
                        alert_text=alert_text,
                        subscribers=subs,
                        co=co,
                        ai_note=ai_note,
                        alert_key_detail="-".join(sorted(new_alerts)),
                    )

                    await _set_known_health(vid, set(push_alerts))
                    await _set_health_last_sent(vid)

            except Exception as e:
                logger.error(f"Health check for account {account_id}: {e}")

        if is_warmup:
            _warmup_done["health"] = True
            logger.info("Health alert warm-up complete — caches populated, no alerts sent")

    except Exception as e:
        logger.error(f"Health check error: {e}")


# ── Low Fuel Alerts Scheduled Job ────────────────────────────────

# Fuel severity threshold: below this percentage → CRITICAL
FUEL_CRITICAL_PCT = 10


async def check_low_fuel(app: Application):
    """Check all accounts for vehicles below the fuel threshold and push alerts.

    Uses hysteresis: alerts when fuel drops below FUEL_THRESHOLD, but
    only clears the dedup flag when fuel rises above
    FUEL_THRESHOLD + FUEL_HYSTERESIS_PERCENT to prevent oscillation spam.

    Severity classification:
      CRITICAL — fuel below FUEL_CRITICAL_PCT (e.g. <10%)
      WARNING  — fuel below FUEL_THRESHOLD but above FUEL_CRITICAL_PCT
    """
    try:
        subscribers = await db.get_all_typed_subscribers("fuel")
        if not subscribers:
            return

        clear_threshold = FUEL_THRESHOLD + FUEL_HYSTERESIS_PERCENT

        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        # Startup warm-up for fuel (same as health)
        is_warmup = not _warmup_done.get("fuel", False)

        for account_id, subs in acct_subs.items():
            try:
                samsara = await get_client(account_id)
                low_fuel = await samsara.get_low_fuel_vehicles(FUEL_THRESHOLD)

                if is_warmup:
                    for v in low_fuel:
                        co = v.get("_org", "?")
                        vid = f"{account_id}:{co}:{v['id']}"
                        await _set_low_fuel_flag(vid, True)
                    continue

                acct_companies = await db.get_account_companies(account_id)
                populate_company_display(acct_companies)
                await samsara.ensure_org_ids()
                company_codes = [o.code for o in acct_companies]

                low_fuel_ids = set()

                for v in low_fuel:
                    co = v.get("_org", "?")
                    vid = f"{account_id}:{co}:{v['id']}"
                    low_fuel_ids.add(vid)

                    if await is_vehicle_suppressed(account_id, v.get("name", "")):
                        continue

                    if await _is_known_low_fuel(vid):
                        continue

                    fuel_pct = v.get("_fuel_pct", 0)
                    show_co = len(company_codes) > 1
                    alert_text = format_low_fuel_alert(
                        v, fuel_pct, show_company=show_co,
                    )

                    # ── Classify severity ────────────────────
                    severity = (AlertSeverity.CRITICAL if fuel_pct < FUEL_CRITICAL_PCT
                                else AlertSeverity.WARNING)

                    # ── Universal pipeline ───────────────────
                    await send_alert(
                        app,
                        account_id=account_id,
                        alert_type="fuel",
                        severity=severity,
                        vehicle=v,
                        alert_text=alert_text,
                        subscribers=subs,
                        co=co,
                        alert_key_detail=f"fuel:{fuel_pct:.0f}",
                    )

                    await _set_low_fuel_flag(vid, True)

                # Hysteresis: only clear dedup for vehicles that rose well
                # above the threshold
                stale_keys = [
                    k for k in list(_known_low_fuel.keys())
                    if k.startswith(f"{account_id}:") and k not in low_fuel_ids
                ]
                if stale_keys:
                    try:
                        all_vehicles = await samsara.get_low_fuel_vehicles(
                            clear_threshold
                        )
                        still_below_clear = {
                            f"{account_id}:{v.get('_org', '?')}:{v['id']}"
                            for v in all_vehicles
                        }
                    except Exception:
                        still_below_clear = set()

                    for k in stale_keys:
                        if k not in still_below_clear:
                            await _set_low_fuel_flag(k, False)
                            parts = k.split(":", 2)
                            if len(parts) == 3:
                                co = parts[1]
                                v_id = parts[2]
                                cleared = await db.clear_alert_history(
                                    account_id, "fuel", v_id,
                                )
                                for rec in cleared:
                                    if rec.get("message_id") and rec.get("chat_id"):
                                        try:
                                            await app.bot.delete_message(
                                                chat_id=rec["chat_id"],
                                                message_id=rec["message_id"],
                                            )
                                        except Exception:
                                            pass
                                # Auto-resolve unacked fuel alerts
                                await _auto_resolve_vehicle_alerts(
                                    app, account_id, "fuel", v_id, "", co,
                                )

            except Exception as e:
                logger.error(f"Fuel check for account {account_id}: {e}")

        if is_warmup:
            _warmup_done["fuel"] = True
            logger.info("Fuel alert warm-up complete — caches populated, no alerts sent")

    except Exception as e:
        logger.error(f"Fuel check error: {e}")


# ── API Health Notifications ─────────────────────────────────────

async def _notify_api_errors(
    app: Application,
    account_id: int,
    skipped_codes: list[str],
):
    """Send a one-time Telegram alert to account owners/admins when
    a company's Samsara API call fails.  Throttled to one alert per
    company per _API_ALERT_COOLDOWN_S seconds.
    """
    now = datetime.now(timezone.utc).timestamp()
    codes_to_alert = []
    for code in skipped_codes:
        key = f"{account_id}:{code}"
        redis_key = f"api_alert:{key}"

        # Check cooldown — Redis first, then in-memory fallback
        if rcache.is_available():
            if await rcache.exists(redis_key):
                continue
            codes_to_alert.append(code)
            await rcache.setex_flag(redis_key, _API_ALERT_COOLDOWN_S)
        else:
            last = _api_alert_sent.get(key, 0)
            if now - last >= _API_ALERT_COOLDOWN_S:
                codes_to_alert.append(code)
                _api_alert_sent[key] = now

    if not codes_to_alert:
        return

    admins = await db.get_account_admins(account_id)
    if not admins:
        return

    codes_str = ", ".join(codes_to_alert)
    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  🚨  <b>API ERROR</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  Company: <b>{codes_str}</b>\n"
        "\n"
        "  The Samsara API returned an error\n"
        "  for this company. Reports will be\n"
        "  missing data until the issue is fixed.\n"
        "\n"
        "  <b>Possible causes:</b>\n"
        "  • API token expired or revoked\n"
        "  • Token missing required permissions\n"
        "  • Samsara service outage\n"
        "\n"
        "  Check your Samsara dashboard or\n"
        "  re-add the API key to resolve."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Check Status", callback_data="cmd_api_status")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])

    for admin in admins:
        try:
            await app.bot.send_message(
                chat_id=admin.telegram_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        except Exception as e:
            logger.error(f"API alert to admin {admin.telegram_id}: {e}")

    logger.warning(
        f"API error alert sent for account {account_id}, "
        f"companies: {codes_str}"
    )


# ── Alert Acknowledgment Handler ─────────────────────────────────

async def handle_alert_ack(update, context, ack_id: int):
    """Handle the ✅ Acknowledge button press on a critical alert."""
    query = update.callback_query
    try:
        user = context.user_data.get("_db_user")
        tid = query.from_user.id
        await db.acknowledge_alert(ack_id, tid)

        # Update the message to show it's been acknowledged
        try:
            original_text = query.message.text_html or query.message.text or ""
            ack_name = query.from_user.full_name or str(tid)
            ack_text = original_text + f"\n\n✅ <b>Acknowledged</b> by <a href='tg://user?id={tid}'>{ack_name}</a>"
            # Keep only the truck view button
            new_kb = InlineKeyboardMarkup([
                row for row in (query.message.reply_markup.inline_keyboard
                                if query.message.reply_markup else [])
                if any("ack_alert" not in (b.callback_data or "") for b in row)
            ])
            await query.edit_message_text(
                text=ack_text,
                parse_mode=ParseMode.HTML,
                reply_markup=new_kb if new_kb.inline_keyboard else None,
            )
        except Exception:
            pass
        await query.answer("✅ Alert acknowledged!", show_alert=False)

        # Audit log
        if user:
            await db.add_audit_log(
                account_id=user.account_id,
                user_id=user.id,
                action="alert_acknowledged",
                target_type="alert",
                target_id=str(ack_id),
            )
    except Exception as e:
        logger.error(f"ACK alert {ack_id}: {e}")
        await query.answer("Error acknowledging alert", show_alert=True)


# ── Re-Alert Checker (scheduled job) ─────────────────────────────

async def _expire_stale_alerts(app: Application):
    """Auto-expire unacknowledged alerts older than ESCALATION_MAX_HOURS."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=ESCALATION_MAX_HOURS)
    ).isoformat()
    expired = await db.expire_stale_alerts(older_than=cutoff)
    for alert in expired:
        try:
            await db.add_audit_log(
                account_id=alert["account_id"],
                user_id=None,
                action="alert_expired",
                target_type="alert",
                target_id=str(alert["id"]),
                details=(
                    f"Auto-expired after {ESCALATION_MAX_HOURS}h — "
                    f"{alert['alert_type']} alert for Truck {alert['vehicle_name']}, "
                    f"re-alerted {alert['escalation_level']} time(s), never acknowledged"
                ),
            )
        except Exception as e:
            logger.error(f"Audit log for expired alert {alert['id']}: {e}")
    if expired:
        logger.info(f"Auto-expired {len(expired)} stale alerts (>{ESCALATION_MAX_HOURS}h)")


# Health-alert severity thresholds for display in re-alerts
_HEALTH_REALERT_LABELS: dict[str, tuple[str, str, str]] = {
    "low_oil_pressure": ("🛢", "Low Oil Pressure", "oil_psi"),
    "high_coolant_temp": ("🌡", "High Coolant Temp", "coolant_c"),
    "low_battery": ("🔋", "Low Battery", "battery_v"),
    "low_def": ("💧", "Low DEF Level", "def_pct"),
    "coolant_dtc": ("🌡", "Coolant System Fault", ""),
}
_HEALTH_THRESHOLDS: dict[str, str] = {
    "low_oil_pressure": "threshold: 10 PSI",
    "high_coolant_temp": "threshold: 105°C",
    "low_battery": "threshold: 12.2V",
    "low_def": "threshold: 10%",
}
_HEALTH_UNITS: dict[str, str] = {
    "oil_psi": "PSI",
    "coolant_c": "°C",
    "battery_v": "V",
    "def_pct": "%",
}


def _build_realert_detail(
    alert_type: str,
    alert_key: str,
    live_health: dict | None = None,
    live_fuel_pct: float | None = None,
    live_dtcs: list[dict] | None = None,
) -> str:
    """Build human-readable detail lines from the stored alert_key.

    alert_key format: {co}:{vid}:{detail}
    - health detail: 'low_oil_pressure-high_coolant_temp'
    - fault detail:  'SPN123-SPN456'
    - fuel detail:   'fuel:15'

    When live data is available, shows current readings alongside thresholds.
    """
    parts = alert_key.split(":", 2)
    detail = parts[2] if len(parts) > 2 else ""
    if not detail:
        return ""

    lines: list[str] = []
    if alert_type == "health":
        for code in detail.split("-"):
            info = _HEALTH_REALERT_LABELS.get(code)
            if info:
                emoji, label, health_key = info
                value_str = ""
                if live_health and health_key:
                    val = live_health.get(health_key)
                    if val is not None:
                        unit = _HEALTH_UNITS.get(health_key, "")
                        threshold = _HEALTH_THRESHOLDS.get(code, "")
                        value_str = f"\n       Currently: <b>{val}{unit}</b>"
                        if threshold:
                            value_str += f" ({threshold})"
                lines.append(f"  {emoji}  <b>{label}</b>{value_str}")
            else:
                lines.append(f"  ⚠️  <b>{code.replace('_', ' ').title()}</b>")
    elif alert_type == "fault":
        if live_dtcs:
            for dtc in live_dtcs[:5]:
                spn = dtc.get("spnId", "?")
                fmi = dtc.get("fmiId", "?")
                desc = dtc.get("spnDescription", "Unknown")
                lines.append(f"  ⚙️  <b>SPN {spn} / FMI {fmi}</b>\n       {desc}")
        else:
            # Parse stored fault detail: "SPN-FMI:description|SPN-FMI:description"
            # Also handles legacy format without descriptions
            for entry in detail.split("|"):
                if ":" in entry:
                    code_part, desc = entry.split(":", 1)
                    spn_fmi = code_part.split("-", 1)
                    if len(spn_fmi) == 2:
                        lines.append(
                            f"  ⚙️  <b>SPN {spn_fmi[0]} / FMI {spn_fmi[1]}</b>"
                            f"\n       {desc or 'Unknown'}")
                    else:
                        lines.append(f"  ⚙️  <b>{code_part}</b>\n       {desc}")
                else:
                    # Legacy format: "SPN-FMI" without description
                    spn_fmi = entry.split("-", 1)
                    if len(spn_fmi) == 2:
                        lines.append(f"  ⚙️  <b>SPN {spn_fmi[0]} / FMI {spn_fmi[1]}</b>")
                    elif entry:
                        lines.append(f"  ⚙️  <b>{entry}</b>")
    elif alert_type == "fuel":
        if live_fuel_pct is not None:
            lines.append(f"  ⛽  <b>Fuel Level: {live_fuel_pct:.0f}%</b>")
        else:
            fuel_val = detail.split(":")[-1] if ":" in detail else detail
            lines.append(f"  ⛽  <b>Low Fuel: {fuel_val}%</b>")
    else:
        lines.append(f"  ⚠️  {detail}")

    return "\n".join(lines)


async def _auto_resolve_vehicle_alerts(
    app: Application,
    account_id: int,
    alert_type: str,
    vehicle_id: str,
    vehicle_name: str,
    co: str,
):
    """Auto-resolve all unacked alerts for a vehicle when the source check
    detects the condition has cleared.

    This is the fast path — called directly from check_health_alerts /
    check_new_faults / check_low_fuel when they see a vehicle is clear.
    Eliminates waiting for the re-alert cycle to auto-resolve.
    """
    resolved = await db.auto_resolve_alerts_by_vehicle(
        account_id, alert_type, vehicle_id,
    )
    if not resolved:
        return

    for alert in resolved:
        vname = vehicle_name or alert.get("vehicle_name", "?")

        # Delete old alert message from chat
        if alert.get("message_id") and alert.get("chat_id"):
            try:
                await app.bot.delete_message(
                    chat_id=alert["chat_id"],
                    message_id=alert["message_id"],
                )
            except Exception:
                pass

        # Send brief auto-resolved notification
        atype_label = alert_type.replace("_", " ").title()
        alert_co = co or "?"
        resolve_text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  ✅  <b>AUTO-RESOLVED</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  🚛 Truck: <b>{vname}</b>\n"
            f"\n  {atype_label} alert resolved — condition cleared.\n"
        )
        try:
            await app.bot.send_message(
                chat_id=alert["sent_to"],
                text=resolve_text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        f"📋 View Truck #{vname}",
                        callback_data=f"cotruck_{alert_co}_{vname}",
                    )],
                ]),
            )
        except Exception:
            pass

    # Single audit log entry for all resolved records
    log_name = vehicle_name or resolved[0].get("vehicle_name", "?")
    await db.add_audit_log(
        account_id=account_id,
        user_id=None,
        action="alert_auto_resolved",
        target_type="alert",
        target_id=str(resolved[0]["id"]),
        details=(
            f"{alert_type} alert for Truck {log_name} "
            f"auto-resolved (source check, {len(resolved)} records)"
        ),
    )
    logger.info(
        f"Auto-resolved {len(resolved)} {alert_type} alert(s) "
        f"for Truck {log_name} (source check)"
    )


def _is_alert_resolved(
    alert_type: str,
    detail: str,
    vehicle_name: str,
    *,
    live_health_alerts: list[str] | None = None,
    live_fuel_pct: float | None = None,
    live_dtcs: list[dict] | None = None,
) -> str:
    """Check if an alert's underlying condition has cleared.

    Returns a human-readable reason string if resolved, empty string if not.
    Only resolves when live data is available — if we can't check, we
    don't auto-resolve (fail-safe).
    """
    if alert_type == "health" and live_health_alerts is not None:
        # detail contains health codes separated by "-"
        alerted_codes = set(detail.split("-")) if detail else set()
        still_active = alerted_codes & set(live_health_alerts)
        if not still_active:
            label_map = {
                "low_oil_pressure": "Oil pressure normal",
                "high_coolant_temp": "Coolant temp normal",
                "low_battery": "Battery voltage normal",
                "low_def": "DEF level normal",
                "coolant_dtc": "Coolant DTC cleared",
            }
            resolved_items = [
                label_map.get(c, c.replace("_", " ").title())
                for c in alerted_codes
            ]
            return "  ✅ " + "\n  ✅ ".join(resolved_items)
        return ""

    if alert_type == "fuel" and live_fuel_pct is not None:
        clear_threshold = FUEL_THRESHOLD + FUEL_HYSTERESIS_PERCENT
        if live_fuel_pct > clear_threshold:
            return f"  ⛽ Fuel now at {live_fuel_pct:.0f}% (above {clear_threshold}% threshold)"
        return ""

    if alert_type == "fault" and live_dtcs is not None:
        # detail: "SPN-FMI:desc|SPN-FMI:desc" or legacy "SPN-FMI"
        alerted_spn_fmi: set[str] = set()
        for entry in detail.split("|"):
            code_part = entry.split(":", 1)[0] if ":" in entry else entry
            if code_part:
                alerted_spn_fmi.add(code_part)

        # Build set of currently active SPN-FMI pairs
        current_spn_fmi = {
            f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}"
            for dtc in live_dtcs
        }

        still_active = alerted_spn_fmi & current_spn_fmi
        if not still_active:
            return "  ⚙️ All alerted fault codes have cleared"
        return ""

    # No live data available — don't auto-resolve
    return ""


async def check_alert_realerts(app: Application):
    """Check for unacknowledged alerts that need re-alerting.

    Runs every 5 minutes. If an alert hasn't been acknowledged within
    ACK_WINDOW_MINUTES, re-sends a reminder to the SAME user (no role
    escalation). After MAX_REALERTS attempts, auto-expires the alert.

    Fetches live vehicle data so re-alerts show current readings.
    """
    try:
        await _expire_stale_alerts(app)

        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        pending = await db.get_unacked_alerts(before=now_str)

        if not pending:
            return

        # Group by account_id to fetch live data once per account
        by_account: dict[int, list[dict]] = {}
        for alert in pending:
            by_account.setdefault(alert["account_id"], []).append(alert)

        for account_id, alerts in by_account.items():
            # Fetch live data for this account (cached by samsara client)
            live_health_by_name: dict[str, dict] = {}
            live_health_alerts_by_name: dict[str, list[str]] = {}
            live_fuel_by_name: dict[str, float] = {}
            live_faults_by_name: dict[str, list[dict]] = {}
            try:
                samsara = await get_client(account_id)
                await samsara.ensure_org_ids()

                # Determine what data we need based on alert types
                alert_types = {a["alert_type"] for a in alerts}
                if "health" in alert_types:
                    health_vehicles = await samsara.get_vehicle_health()
                    for hv in health_vehicles:
                        live_health_by_name[hv["name"]] = hv.get("_health", {})
                        live_health_alerts_by_name[hv["name"]] = hv.get("_health_alerts", [])

                if "fuel" in alert_types:
                    fuel_vehicles = await samsara.get_low_fuel_vehicles(threshold=100)
                    for fv in fuel_vehicles:
                        pct = fv.get("_fuel_pct")
                        if pct is not None:
                            live_fuel_by_name[fv["name"]] = pct

                if "fault" in alert_types:
                    faulted, _ = await samsara.get_vehicles_with_faults()
                    for fv in faulted:
                        live_faults_by_name[fv["name"]] = fv.get("_dtcs", [])

            except Exception as e:
                logger.debug(f"Live data fetch for re-alerts (account {account_id}): {e}")

            for alert in alerts:
                try:
                    realert_count = alert["escalation_level"]  # repurposed field

                    if realert_count >= MAX_REALERTS:
                        await db.update_alert_escalation(alert["id"], realert_count, None)
                        await db.add_audit_log(
                            account_id=account_id,
                            user_id=None,
                            action="alert_max_realerts",
                            target_type="alert",
                            target_id=str(alert["id"]),
                            details=(
                                f"Max re-alerts ({MAX_REALERTS}) reached — "
                                f"{alert['alert_type']} alert for Truck {alert['vehicle_name']}"
                            ),
                        )
                        continue

                    # ── Auto-resolve: skip re-alert if live data shows issue cleared ──
                    vname = alert["vehicle_name"]
                    alert_key = alert.get("alert_key", "")
                    key_parts = alert_key.split(":", 2)
                    co = key_parts[0] if key_parts else "?"
                    detail = key_parts[2] if len(key_parts) > 2 else ""
                    atype = alert["alert_type"]

                    resolved = _is_alert_resolved(
                        atype, detail, vname,
                        live_health_alerts=live_health_alerts_by_name.get(vname),
                        live_fuel_pct=live_fuel_by_name.get(vname),
                        live_dtcs=live_faults_by_name.get(vname),
                    )

                    if resolved:
                        # Auto-resolve: mark acknowledged, delete old message, notify user
                        await db.acknowledge_alert(alert["id"], user_id=SYSTEM_USER_ID)
                        if alert.get("message_id") and alert.get("chat_id"):
                            try:
                                await app.bot.delete_message(
                                    chat_id=alert["chat_id"],
                                    message_id=alert["message_id"],
                                )
                            except Exception:
                                pass

                        # Also clear alert history so the normal alert cycle
                        # doesn't keep a stale entry
                        vid = key_parts[1] if len(key_parts) > 1 else ""
                        await db.clear_alert_history(account_id, atype, vid)

                        # Send auto-resolved notification
                        resolve_text = (
                            "━━━━━━━━━━━━━━━━━━━\n"
                            f"  ✅  <b>AUTO-RESOLVED</b>\n"
                            "━━━━━━━━━━━━━━━━━━━\n"
                            f"\n  🚛 Truck: <b>{vname}</b>\n"
                            f"\n  The <b>{atype.replace('_', ' ').title()}</b> alert "
                            "has been automatically resolved.\n"
                            f"\n  {resolved}\n"
                        )
                        try:
                            await app.bot.send_message(
                                chat_id=alert["sent_to"],
                                text=resolve_text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton(
                                        f"📋 View Truck #{vname}",
                                        callback_data=f"cotruck_{co}_{vname}",
                                    )],
                                    [InlineKeyboardButton(
                                        "◀️ Main Menu", callback_data="cmd_menu",
                                    )],
                                ]),
                            )
                        except Exception:
                            pass

                        await db.add_audit_log(
                            account_id=account_id,
                            user_id=None,
                            action="alert_auto_resolved",
                            target_type="alert",
                            target_id=str(alert["id"]),
                            details=(
                                f"{atype} alert for Truck {vname} auto-resolved — "
                                f"{resolved}"
                            ),
                        )
                        logger.info(
                            f"Auto-resolved {atype} alert #{alert['id']} "
                            f"for Truck {vname}: {resolved}"
                        )
                        continue

                    new_count = realert_count + 1
                    next_realert = (now + timedelta(
                        minutes=ACK_WINDOW_MINUTES,
                    )).isoformat()

                    # Re-alert the SAME user
                    tid = alert["sent_to"]
                    mins_ago = int(
                        (now - datetime.fromisoformat(alert["created_at"])).total_seconds() / 60
                    )

                    # ── Delete previous message to keep chat clean ────
                    if alert.get("message_id") and alert.get("chat_id"):
                        try:
                            await app.bot.delete_message(
                                chat_id=alert["chat_id"],
                                message_id=alert["message_id"],
                            )
                        except Exception:
                            pass

                    # Build detail lines with live data when available
                    detail_lines = _build_realert_detail(
                        atype,
                        alert_key,
                        live_health=live_health_by_name.get(vname),
                        live_fuel_pct=live_fuel_by_name.get(vname),
                        live_dtcs=live_faults_by_name.get(vname),
                    )

                    atype_label = atype.replace("_", " ").title()
                    realert_text = (
                        "━━━━━━━━━━━━━━━━━━━\n"
                        f"  🔔  <b>RE-ALERT ({new_count}/{MAX_REALERTS})</b>\n"
                        "━━━━━━━━━━━━━━━━━━━\n"
                        f"\n  🚛 Truck: <b>{vname}</b>\n"
                        f"\n  ⚠️  Unacknowledged <b>{atype_label}</b> alert\n"
                    )

                    if detail_lines:
                        realert_text += f"\n{detail_lines}\n"

                    realert_text += (
                        f"\n  🕐 Sent {mins_ago} min ago — not yet acknowledged.\n"
                    )

                    # Re-use build_alert_keyboard for consistent buttons + correct AI callback
                    ack_kb = build_alert_keyboard(
                        AlertSeverity.WARNING,  # re-alerts always show ACK buttons
                        co,
                        vname,
                        ack_id=alert["id"],
                        alert_type=atype,
                        vehicle_id=alert.get("vehicle_id", ""),
                    )

                    msg = await app.bot.send_message(
                        chat_id=tid,
                        text=realert_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=ack_kb,
                    )

                    # Update DB with new message_id so next cycle can delete it
                    await db.update_alert_escalation(
                        alert["id"], new_count, next_realert,
                        message_id=msg.message_id,
                    )

                    await db.add_audit_log(
                        account_id=account_id,
                        user_id=None,
                        action="alert_realerted",
                        target_type="alert",
                        target_id=str(alert["id"]),
                        details=(
                            f"{atype} re-alert {new_count}/{MAX_REALERTS} "
                            f"for Truck {vname} — sent to {tid}"
                        ),
                    )

                except Exception as e:
                    logger.error(f"Re-alert for alert {alert['id']}: {e}")

    except Exception as e:
        logger.error(f"Re-alert check error: {e}")


# ── Proactive AI on Critical Alerts ──────────────────────────────

async def _get_ai_diagnosis_note(vehicle: dict, dtcs: list[dict]) -> str:
    """Generate a short AI diagnosis note for critical fault alerts.

    Returns an HTML string to append to the alert, or empty string if
    AI is not configured or fails.
    """
    try:
        import ai_client
        if not ai_client.is_configured():
            return ""

        # Build a compact context for AI
        fault_descs = []
        for dtc in dtcs[:5]:
            spn = dtc.get("spnId", "?")
            fmi = dtc.get("fmiId", "?")
            desc = dtc.get("spnDescription", "Unknown")
            fault_descs.append(f"SPN {spn} / FMI {fmi}: {desc}")

        prompt = (
            f"In 2-3 sentences, diagnose these faults on Truck #{vehicle.get('name', '?')}: "
            + "; ".join(fault_descs)
            + ". What's the likely cause and should the driver stop?"
        )

        response = await ai_client.generate(
            prompt,
            system=ai_client.FAULT_DIAGNOSIS_SYSTEM,
        )

        # Track proactive AI usage
        usage = ai_client.get_last_usage()
        if usage:
            try:
                from bot.config import db as _db
                # system-triggered usage
                await _db.log_ai_usage(
                    account_id=SYSTEM_USER_ID,
                    user_id=SYSTEM_USER_ID,
                    model=ai_client.get_current_model_name(),
                    request_type="proactive_diagnosis",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    reply_tokens=usage.get("reply_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                )
            except Exception:
                pass

        if response and len(response) < 500:
            return f"\n\n🤖 <b>AI Diagnosis:</b>\n{response}"
        elif response:
            return f"\n\n🤖 <b>AI Diagnosis:</b>\n{response[:500]}…"
    except Exception as e:
        logger.debug(f"AI diagnosis for alert failed: {e}")
    return ""


async def _get_ai_health_note(
    vehicle: dict, alert_names: list[str], health: dict,
) -> str:
    """Generate a short AI note for health alerts (low oil, high coolant, etc.).

    Returns an HTML string to append to the alert, or empty string if
    AI is not configured or fails.
    """
    try:
        import ai_client
        if not ai_client.is_configured():
            return ""

        condition_strs = []
        label_map = {
            "low_battery": "Low battery voltage",
            "low_oil_pressure": "Low oil pressure",
            "high_coolant_temp": "High coolant temperature",
            "low_def": "Low DEF level",
            "coolant_dtc": "Coolant-system DTC active",
        }
        for a in alert_names:
            desc = label_map.get(a, a)
            val = health.get(a)
            if val is not None:
                desc += f" ({val})"
            condition_strs.append(desc)

        prompt = (
            f"In 2-3 sentences, assess these health conditions on "
            f"Truck #{vehicle.get('name', '?')}: "
            + "; ".join(condition_strs)
            + ". What should the driver do immediately?"
        )

        response = await ai_client.generate(
            prompt,
            system=ai_client.FAULT_DIAGNOSIS_SYSTEM,
        )

        usage = ai_client.get_last_usage()
        if usage:
            try:
                from bot.config import db as _db
                await _db.log_ai_usage(
                    account_id=SYSTEM_USER_ID,
                    user_id=SYSTEM_USER_ID,
                    model=ai_client.get_current_model_name(),
                    request_type="proactive_health_diagnosis",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    reply_tokens=usage.get("reply_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                )
            except Exception:
                pass

        if response and len(response) < 500:
            return f"\n\n🤖 <b>AI Assessment:</b>\n{response}"
        elif response:
            return f"\n\n🤖 <b>AI Assessment:</b>\n{response[:500]}…"
    except Exception as e:
        logger.debug(f"AI health note failed: {e}")
    return ""


# ── Auto-Maintenance from Critical Faults ────────────────────────

# SPN → maintenance task type mapping
_SPN_MAINTENANCE_MAP = {
    110: "custom",   # Coolant temp → custom inspection
    111: "custom",   # Coolant level
    100: "oil",      # Oil pressure
    101: "oil",      # Oil level
    91: "brakes",    # Brake pressure
    97: "custom",    # Water in fuel
    190: "custom",   # Engine speed (overspeed)
    4331: "custom",  # DEF quality
    3031: "custom",  # DEF level
    5246: "custom",  # DEF tank
}

_SPN_DESCRIPTIONS = {
    110: "Coolant temperature issue",
    111: "Coolant level issue",
    100: "Engine oil pressure issue",
    101: "Engine oil level issue",
    91: "Brake system pressure issue",
    97: "Water-in-fuel detected",
    190: "Engine overspeed event",
    4331: "DEF quality issue",
    3031: "DEF level low",
    5246: "DEF tank issue",
}


async def auto_create_maintenance_from_faults(
    account_id: int, vehicle_name: str, dtcs: list[dict],
):
    """Auto-create maintenance tasks from critical fault codes.

    Only creates a task if one doesn't already exist (pending/overdue)
    for the same vehicle and task type.
    """
    try:
        existing = await db.get_maintenance_tasks(account_id, vehicle_name=vehicle_name)
        existing_types = {
            (t["vehicle_name"], t["task_type"])
            for t in existing
            if t["status"] in ("pending", "overdue")
        }

        for dtc in dtcs:
            spn = dtc.get("spnId")
            if spn not in _SPN_MAINTENANCE_MAP:
                continue

            task_type = _SPN_MAINTENANCE_MAP[spn]
            if (vehicle_name, task_type) in existing_types:
                continue  # already has a pending task

            desc = _SPN_DESCRIPTIONS.get(spn, f"Auto-created from SPN {spn}")
            fmi_desc = dtc.get("fmiDescription", "")
            if fmi_desc:
                desc += f" ({fmi_desc})"

            await db.add_maintenance_task(
                account_id=account_id,
                company_code="",
                vehicle_name=vehicle_name,
                task_type=task_type,
                description=f"🤖 Auto-created: {desc}",
                created_by=0,  # system-generated
            )
            existing_types.add((vehicle_name, task_type))
            logger.info(
                f"Auto-maintenance: {vehicle_name} → {task_type} (SPN {spn})"
            )
    except Exception as e:
        logger.error(f"Auto-maintenance creation failed: {e}")


async def check_api_health(account_id: int) -> dict[str, str]:
    """Test each company's Samsara API and return status dict.

    Returns: {company_code: "ok" | "error: <message>"}
    """
    companies = await db.get_account_companies(account_id)
    results: dict[str, str] = {}

    for co in companies:
        from samsara_client import SamsaraClient
        client = SamsaraClient(
            api_key=co.samsara_api_key,
            base_url="https://api.samsara.com",
        )
        try:
            vehicles = await client.get_vehicles()
            results[co.code] = f"ok ({len(vehicles)} vehicles)"
        except Exception as e:
            results[co.code] = f"error: {e}"
        finally:
            await client.close()

    return results


# ── DND Morning Delivery ─────────────────────────────────────────

async def deliver_dnd_alerts(app: Application):
    """Deliver queued DND alerts to users who are no longer in quiet hours.

    Runs hourly. Groups all pending alerts into a single morning summary
    rather than blasting individual messages.
    """
    try:
        subscribers = await db.get_all_alert_subscribers()
        if not subscribers:
            return

        for sub in subscribers:
            if sub.is_in_quiet_hours():
                continue  # still in DND, wait

            queued = await db.get_pending_dnd_alerts(sub.telegram_id)
            if not queued:
                continue

            # Group by alert type for a clean summary
            by_type: dict[str, list[dict]] = {}
            for q in queued:
                by_type.setdefault(q["alert_type"], []).append(q)

            lines = [
                "━━━━━━━━━━━━━━━━━━━",
                "  🌅  <b>MORNING ALERT SUMMARY</b>",
                "━━━━━━━━━━━━━━━━━━━",
                f"\n  {len(queued)} alert{'s' if len(queued) != 1 else ''} "
                "received during quiet hours:\n",
            ]

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

            summary_text = "\n".join(lines)

            try:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
                ])
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


# ── Alert Snooze Handler ─────────────────────────────────────────


async def handle_snooze_pick(update, context, ack_id: int):
    """Show snooze duration options for an alert."""
    query = update.callback_query
    await query.answer()

    def _label(m: int) -> str:
        if m < 60:
            return f"{m} min"
        h = m // 60
        return f"{h} hour" if h == 1 else f"{h} hours"

    rows = [
        [InlineKeyboardButton(
            f"⏰ {_label(m)}",
            callback_data=f"snooze_set_{ack_id}_{m}",
        )]
        for m in SNOOZE_OPTIONS
    ]
    # Back button restores the original alert keyboard
    rows.append([InlineKeyboardButton("↩️ Back", callback_data=f"snooze_back_{ack_id}")])

    try:
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(rows),
        )
    except Exception:
        pass


async def handle_alert_snooze(update, context, ack_id: int, minutes: int = SNOOZE_MINUTES):
    """Handle the ⏰ Snooze button — postpone escalation without acking."""
    query = update.callback_query
    try:
        snooze_until = (
            datetime.now(timezone.utc) + timedelta(minutes=minutes)
        ).isoformat()
        await db.snooze_alert(ack_id, snooze_until)

        def _label(m: int) -> str:
            if m < 60:
                return f"{m} min"
            h = m // 60
            return f"{h} hour" if h == 1 else f"{h} hours"

        label = _label(minutes)

        # Update message to show snooze
        try:
            original_text = query.message.text_html or query.message.text or ""
            snooze_text = original_text + (
                f"\n\n⏰ <b>Snoozed</b> for {label} by "
                f"<a href='tg://user?id={query.from_user.id}'>"
                f"{query.from_user.full_name or query.from_user.id}</a>"
            )
            await query.edit_message_text(
                text=snooze_text,
                parse_mode=ParseMode.HTML,
                reply_markup=query.message.reply_markup,
            )
        except Exception:
            pass

        await query.answer(f"⏰ Snoozed for {label}", show_alert=False)

        # Audit
        user = context.user_data.get("_db_user")
        if user:
            await db.add_audit_log(
                account_id=user.account_id,
                user_id=user.id,
                action="alert_snoozed",
                target_type="alert",
                target_id=str(ack_id),
                details=f"Snoozed for {label}",
            )
    except Exception as e:
        logger.error(f"Snooze alert {ack_id}: {e}")
        await query.answer("Error snoozing alert", show_alert=True)


async def _restore_alert_keyboard(update, context, ack_id: int):
    """Restore the original alert keyboard (e.g. after snooze picker or AI diagnose)."""
    query = update.callback_query
    await query.answer()
    try:
        row = await db.get_alert_ack_by_id(ack_id)
        if not row:
            return
        alert_key = row.get("alert_key", "")
        parts = alert_key.split(":", 2)
        co = parts[0] if parts else "?"
        vname = row.get("vehicle_name", "?")
        alert_type = row.get("alert_type", "fault")
        severity = (AlertSeverity.CRITICAL if alert_type == "health"
                     else AlertSeverity.WARNING)
        kb = build_alert_keyboard(severity, co, vname, ack_id=ack_id,
                                  alert_type=alert_type,
                                  vehicle_id=row.get("vehicle_id", ""))
        await query.edit_message_reply_markup(reply_markup=kb)
    except Exception as e:
        logger.error(f"Restore alert keyboard {ack_id}: {e}")


# ── Maintenance Suppression Check ────────────────────────────────

async def is_vehicle_suppressed(account_id: int, vehicle_name: str) -> bool:
    """Check if alerts should be suppressed for a vehicle in active maintenance."""
    try:
        return await db.is_vehicle_in_maintenance(account_id, vehicle_name)
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════
#  Events Alert Check
# ═══════════════════════════════════════════════════════════════════

# Warmup flag — first cycle only populates known IDs without sending.
_events_warmup_done: dict[int, bool] = {}  # account_id → bool


def _event_severity(event: dict) -> AlertSeverity:
    """Map event type + g-force to severity tier."""
    etype = event.get("event_type", "")
    gf = event.get("g_force", 0.0)
    if etype == "crash":
        return AlertSeverity.CRITICAL
    if etype == "braking" and gf > 0.8:
        return AlertSeverity.WARNING
    return AlertSeverity.INFO


async def _get_known_event_ids(account_id: int) -> set[str]:
    """Get already-alerted event IDs (Redis → in-memory)."""
    rkey = f"events_seen:{account_id}"
    if rcache.is_available():
        return await rcache.smembers(rkey)
    return _known_event_ids.get(account_id, set())


async def _add_known_event_ids(account_id: int, ids: set[str]):
    """Mark event IDs as alerted (Redis → in-memory)."""
    if not ids:
        return
    rkey = f"events_seen:{account_id}"
    if rcache.is_available():
        existing = await rcache.smembers(rkey)
        await rcache.sset(rkey, existing | ids, ttl=86400)
    else:
        _known_event_ids.setdefault(account_id, set()).update(ids)


async def check_events(app: Application):
    """Scheduled job: poll Samsara safety events and send alerts for new ones.

    Runs every 5 minutes. Short time window (1 day) to catch recent events.
    Deduplicates by event_id so each event is only alerted once.
    First cycle per account is a warm-up (populate known IDs, don't send).
    """
    try:
        accounts = await db.list_accounts()
        for account in accounts:
            companies = await db.get_account_companies(account.id)
            if not companies:
                continue

            try:
                samsara = await get_client(account.id)
            except Exception:
                continue

            await samsara.ensure_org_ids()

            try:
                events = await samsara.get_events(days=1)
            except Exception as e:
                logger.debug(f"Events check for account {account.id}: {e}")
                continue

            if not events:
                if account.id not in _events_warmup_done:
                    _events_warmup_done[account.id] = True
                continue

            known = await _get_known_event_ids(account.id)
            new_events = [e for e in events if e.get("event_id") not in known]

            # Mark all current event IDs as known
            all_ids = {e.get("event_id") for e in events if e.get("event_id")}
            await _add_known_event_ids(account.id, all_ids)

            # Warmup: first cycle populates known set without sending
            if not _events_warmup_done.get(account.id):
                _events_warmup_done[account.id] = True
                logger.info(
                    f"Events warmup for account {account.id}: "
                    f"populated {len(all_ids)} known event IDs"
                )
                continue

            if not new_events:
                continue

            # Get event-alert subscribers
            subscribers = await db.get_typed_alert_subscribers(
                account.id, "events"
            )
            if not subscribers:
                continue

            for event in new_events:
                vname = event.get("vehicle_name", "?")

                if await is_vehicle_suppressed(account.id, vname):
                    continue

                severity = _event_severity(event)
                alert_text = format_event_alert(event)
                vid = event.get("vehicle_id", vname)
                co = event.get("_org", "?")
                vehicle_dict = {"id": vid, "name": vname, "_org": co}
                eid = event.get("event_id", "")

                await send_alert(
                    app,
                    account_id=account.id,
                    alert_type="events",
                    severity=severity,
                    vehicle=vehicle_dict,
                    alert_text=alert_text,
                    subscribers=subscribers,
                    co=co,
                    alert_key_detail=f"{event.get('event_type', '')}:{eid}",
                    video_url=event.get("video_url") or "",
                )

    except Exception as e:
        logger.error(f"Events check error: {e}")
