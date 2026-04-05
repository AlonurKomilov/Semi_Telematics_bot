"""Alert escalation — ACK, re-alert, auto-resolve, snooze."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from telegram.constants import ParseMode

from samsara_client import COMPANY_DISPLAY

from bot.config import (
    db, logger, get_client,
    FUEL_THRESHOLD, FUEL_HYSTERESIS_PERCENT,
    ESCALATION_MAX_HOURS,
)

from bot.alerts.pipeline import (
    AlertSeverity, ACK_WINDOW_MINUTES, MAX_REALERTS,
    SNOOZE_MINUTES, SNOOZE_OPTIONS, SYSTEM_USER_ID,
    build_alert_keyboard,
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


def _build_resolve_detail(alert_type: str, detail: str) -> str:
    """Build human-readable detail for auto-resolved notifications.

    Parses the stored alert detail and returns a formatted string
    describing what was originally alerted.
    """
    if not detail:
        return ""
    lines: list[str] = []
    if alert_type == "health":
        label_map = {
            "low_oil_pressure": "🛢 Low Oil Pressure",
            "high_coolant_temp": "🌡 High Coolant Temp",
            "low_battery": "🔋 Low Battery",
            "low_def": "💧 Low DEF Level",
            "coolant_dtc": "🌡 Coolant System Fault",
        }
        for code in detail.split("-"):
            label = label_map.get(code, code.replace("_", " ").title())
            lines.append(f"  ✅ {label} — normal")
    elif alert_type == "fault":
        for entry in detail.split("|"):
            if ":" in entry:
                code_part, desc = entry.split(":", 1)
                lines.append(f"  ✅ {code_part} — cleared")
                if desc:
                    lines.append(f"       {desc}")
            else:
                spn_fmi = entry.split("-", 1)
                if len(spn_fmi) == 2:
                    lines.append(f"  ✅ SPN {spn_fmi[0]} / FMI {spn_fmi[1]} — cleared")
                elif entry:
                    lines.append(f"  ✅ {entry} — cleared")
    elif alert_type == "fuel":
        fuel_val = detail.split(":")[-1] if ":" in detail else detail
        lines.append(f"  ✅ Fuel was at {fuel_val}% — now above threshold")
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
    Respects working hours — queues notification if user is outside working hours.
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

        # Check DND — skip sending if user is outside working hours
        recipient_id = alert.get("sent_to")
        if recipient_id:
            recipient = await db.get_user_by_telegram_id(recipient_id)
            if recipient and recipient.is_in_quiet_hours():
                await db.queue_dnd_alert(
                    account_id=account_id,
                    telegram_id=recipient_id,
                    alert_type=alert_type,
                    vehicle_name=vname,
                    alert_text=f"✅ Auto-resolved: {alert_type} alert cleared",
                )
                continue

        # Build detailed auto-resolved notification
        atype_label = alert_type.replace("_", " ").title()
        alert_co = co or "?"

        # Extract what was originally alerted from alert_key
        alert_key = alert.get("alert_key", "")
        key_parts = alert_key.split(":", 2)
        detail = key_parts[2] if len(key_parts) > 2 else ""
        detail_lines = _build_resolve_detail(alert_type, detail)

        # Calculate how long the alert was active
        created = alert.get("created_at", "")
        duration_str = ""
        if created:
            try:
                created_dt = datetime.fromisoformat(created)
                mins = int((datetime.now(timezone.utc) - created_dt).total_seconds() / 60)
                if mins >= 60:
                    duration_str = f"  🕐 Active for <b>{mins // 60}h {mins % 60}m</b>\n"
                else:
                    duration_str = f"  🕐 Active for <b>{mins} min</b>\n"
            except Exception:
                pass

        resolve_text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  ✅  <b>AUTO-RESOLVED</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  🚛 Truck: <b>#{vname}</b>\n"
            f"\n  {atype_label} alert resolved — condition cleared.\n"
        )
        if detail_lines:
            resolve_text += f"\n  <b>Was alerting:</b>\n{detail_lines}\n"
        if duration_str:
            resolve_text += f"\n{duration_str}"

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

                        # Calculate alert duration
                        created = alert.get("created_at", "")
                        duration_str = ""
                        if created:
                            try:
                                created_dt = datetime.fromisoformat(created)
                                mins = int((now - created_dt).total_seconds() / 60)
                                if mins >= 60:
                                    duration_str = f"\n  🕐 Was active for <b>{mins // 60}h {mins % 60}m</b>\n"
                                else:
                                    duration_str = f"\n  🕐 Was active for <b>{mins} min</b>\n"
                            except Exception:
                                pass

                        co_display = COMPANY_DISPLAY.get(co, co)
                        co_suffix = f"  ({co_display})" if co_display else ""

                        # Check DND — queue if user is outside working hours
                        resolve_recipient = await db.get_user_by_telegram_id(alert["sent_to"])
                        if resolve_recipient and resolve_recipient.is_in_quiet_hours():
                            await db.queue_dnd_alert(
                                account_id=account_id,
                                telegram_id=alert["sent_to"],
                                alert_type=atype,
                                vehicle_name=vname,
                                alert_text=f"✅ Auto-resolved: {atype} alert cleared",
                            )
                            continue

                        # Send auto-resolved notification
                        resolve_text = (
                            "━━━━━━━━━━━━━━━━━━━\n"
                            f"  ✅  <b>AUTO-RESOLVED</b>\n"
                            "━━━━━━━━━━━━━━━━━━━\n"
                            f"\n  🚛 Truck: <b>#{vname}</b>{co_suffix}\n"
                            f"\n  The <b>{atype.replace('_', ' ').title()}</b> alert "
                            "has been automatically resolved.\n"
                            f"\n{resolved}\n"
                            f"{duration_str}"
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

                    # Check DND for re-alerts — only CRITICAL bypasses working hours
                    realert_recipient = await db.get_user_by_telegram_id(tid)
                    if realert_recipient and realert_recipient.is_in_quiet_hours():
                        sev = alert.get("alert_type", "")
                        # Only truly critical faults bypass DND
                        if sev != "fault" or not any(
                            kw in (alert.get("alert_key", "")).lower()
                            for kw in ("stop", "protect")
                        ):
                            # Postpone re-alert until working hours
                            await db.update_alert_escalation(
                                alert["id"], realert_count, next_realert,
                            )
                            continue

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
                    co_display = COMPANY_DISPLAY.get(co, co)

                    # Urgency escalation based on attempt number
                    if new_count >= MAX_REALERTS:
                        urgency_icon = "🚨"
                        urgency_label = "FINAL REMINDER"
                        urgency_note = "  ❗ This is the <b>last reminder</b>.\n  The alert will expire if not acknowledged.\n"
                    else:
                        urgency_icon = "🔔"
                        urgency_label = "REMINDER"
                        urgency_note = ""

                    # Time formatting
                    hours_ago = mins_ago // 60
                    if hours_ago >= 1:
                        time_str = f"{hours_ago}h {mins_ago % 60}m"
                    else:
                        time_str = f"{mins_ago} min"

                    realert_text = (
                        "━━━━━━━━━━━━━━━━━━━\n"
                        f"  {urgency_icon}  <b>{urgency_label} ({new_count}/{MAX_REALERTS})</b>\n"
                        "━━━━━━━━━━━━━━━━━━━\n"
                        f"\n  🚛 Truck: <b>#{vname}</b>"
                    )
                    if co_display:
                        realert_text += f"  ({co_display})"
                    realert_text += "\n"

                    # Alert type header
                    type_icons = {
                        "fault": "⚙️",
                        "health": "🛑",
                        "fuel": "⛽",
                    }
                    type_icon = type_icons.get(atype, "⚠️")
                    realert_text += f"\n  {type_icon}  <b>{atype_label} Alert</b>\n"

                    if detail_lines:
                        realert_text += f"\n{detail_lines}\n"

                    realert_text += (
                        f"\n  🕐 Unacknowledged for <b>{time_str}</b>\n"
                    )
                    if urgency_note:
                        realert_text += f"\n{urgency_note}"

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


# ── Alert Snooze Handlers ────────────────────────────────────────


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


async def handle_back_to_alert(update, context, ack_id: int):
    """Re-render the alert summary + keyboard when user presses Back from AI Diagnose, etc."""
    from bot.i18n import t
    query = update.callback_query
    await query.answer()
    try:
        row = await db.get_alert_ack_by_id(ack_id)
        if not row:
            await query.edit_message_text("Alert not found.", parse_mode=ParseMode.HTML)
            return

        alert_key = row.get("alert_key", "")
        parts = alert_key.split(":", 2)
        co = parts[0] if parts else "?"
        vname = row.get("vehicle_name", "?")
        alert_type = row.get("alert_type", "fault")
        detail = parts[2] if len(parts) > 2 else ""
        co_display = COMPANY_DISPLAY.get(co, co)

        # Determine severity (same logic as _restore_alert_keyboard)
        severity = (AlertSeverity.CRITICAL if alert_type == "health"
                    else AlertSeverity.WARNING)

        # Status line
        acked = row.get("acknowledged_at")
        status = row.get("status", "active")
        if acked:
            status_line = "  ✅ <b>Acknowledged</b>"
        elif status == "expired":
            status_line = "  ⏳ <b>Expired</b>"
        else:
            status_line = "  🔴 <b>Unacknowledged</b>"

        # Build alert type header/icon
        type_icons = {
            "fault": "⚙️", "health": "🩺", "fuel": "⛽",
            "events": "🚨", "parking": "🅿️",
        }
        icon = type_icons.get(alert_type, "🔔")

        # Build detail lines from alert_key
        detail_lines = ""
        if alert_type == "fault" and detail:
            for item in detail.split("|")[:3]:
                spn_fmi, _, desc = item.partition(":")
                detail_lines += f"\n  {icon} {spn_fmi}"
                if desc:
                    detail_lines += f"\n     {desc}"
        elif detail:
            detail_lines = f"\n  {icon} {detail}"

        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  🔔  <b>{alert_type.upper()} ALERT</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  🚛 Truck: <b>#{vname}</b>  ({co_display})"
            f"{detail_lines}\n"
            f"\n{status_line}"
        )

        kb = build_alert_keyboard(
            severity, co, vname, ack_id=ack_id,
            alert_type=alert_type,
            vehicle_id=row.get("vehicle_id", ""),
        )

        await query.edit_message_text(
            text=text, parse_mode=ParseMode.HTML, reply_markup=kb,
        )
    except Exception as e:
        logger.error(f"Back to alert {ack_id}: {e}")
