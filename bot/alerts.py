"""Scheduled alert checks — fault, health, fuel — multi-tenant.

Features:
  • Acknowledge buttons on critical alerts
  • Escalation chain: if not acknowledged, re-send to next role up
  • DND quiet hours: non-critical alerts queued for morning delivery
  • Proactive AI diagnosis on critical fault alerts (when configured)
"""

from datetime import datetime, timezone, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from telegram.constants import ParseMode

from database import Role
from samsara_client import populate_company_display
from formatters import (
    format_new_fault_alert, format_critical_fault_alert,
    format_health_alert, format_low_fuel_alert,
)

from bot.config import (
    db, logger, _known_faults, _active_messages, get_client,
    FUEL_THRESHOLD, ESCALATION_TIMEOUT_MINUTES,
)
import bot.redis_client as rcache

# Cooldown: don't re-alert the same company API failure within 6 hours
_API_ALERT_COOLDOWN_S = 6 * 3600
_api_alert_sent: dict[str, float] = {}   # "acctID:CODE" → timestamp

# Health alert dedup — track which vehicles already had each alert type
# Key: "health:{account_id}:{company}:{vehicle_id}" → set of alert names
_known_health: dict[str, set[str]] = {}

# Low fuel dedup — track which vehicles were already flagged
# Key: "lowfuel:{account_id}:{company}:{vehicle_id}" → True
_known_low_fuel: dict[str, bool] = {}

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

    Classifies faults into critical (STOP/PROTECT/EMISSIONS lights or
    "Most Severe" FMI) and normal. Critical faults use a distinct
    message format. Coolant-related SPNs are flagged for health alerting.
    """
    try:
        subscribers = await db.get_all_typed_subscribers("faults")
        if not subscribers:
            return

        # Group subscribers by account
        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        for account_id, subs in acct_subs.items():
            try:
                samsara = await get_client(account_id)
                faulted, _, _ = await samsara.get_vehicles_with_faults()

                # ── Notify admins if any company APIs failed ─────
                if samsara.last_skipped:
                    await _notify_api_errors(
                        app, account_id, samsara.last_skipped
                    )

                # Populate company display for this account
                acct_companies = await db.get_account_companies(account_id)
                populate_company_display(acct_companies)
                company_codes = [o.code for o in acct_companies]

                for v in faulted:
                    co = v.get("_org", "?")
                    vid = f"{account_id}:{co}:{v['id']}"
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
                            # Determine criticality
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

                            # Check for coolant-related DTCs
                            has_coolant_dtc = any(
                                dtc.get("spnId") in COOLANT_SPNS
                                for dtc in new_dtcs
                            )

                            show_co = len(company_codes) > 1
                            if is_critical:
                                alert_text = format_critical_fault_alert(
                                    v, new_dtcs, lights,
                                    show_company=show_co,
                                )
                            else:
                                alert_text = format_new_fault_alert(
                                    v, new_dtcs,
                                    show_company=show_co,
                                )

                            # Add coolant DTC note
                            if has_coolant_dtc:
                                alert_text += (
                                    "\n\n  🌡 <b>Coolant system fault detected</b>"
                                    "\n  Check coolant level and temp"
                                )

                            kb = InlineKeyboardMarkup([
                                [InlineKeyboardButton(
                                    f"📋 View Truck #{v['name']}",
                                    callback_data=f"cotruck_{co}_{v['name']}"
                                )],
                                [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
                            ])

                            # Proactive AI diagnosis for critical faults
                            ai_note = ""
                            if is_critical:
                                ai_note = await _get_ai_diagnosis_note(v, new_dtcs)

                            for sub in subs:
                                # Driver: only alert for their truck
                                if sub.role == Role.DRIVER and sub.truck_num:
                                    if v["name"].lower() != sub.truck_num.lower():
                                        continue
                                # DND: skip non-critical alerts during quiet hours
                                if not is_critical and sub.is_in_quiet_hours():
                                    continue
                                try:
                                    send_text = alert_text
                                    if ai_note:
                                        send_text += ai_note

                                    # Critical alerts get acknowledge button
                                    if is_critical:
                                        next_esc = (datetime.now(timezone.utc) + timedelta(
                                            minutes=ESCALATION_TIMEOUT_MINUTES
                                        )).isoformat()

                                        msg = await app.bot.send_message(
                                            chat_id=sub.telegram_id,
                                            text=send_text,
                                            parse_mode=ParseMode.HTML,
                                            reply_markup=kb,  # temp, updated below
                                        )

                                        alert_key = f"{co}:{v['id']}:{'-'.join(new_codes)}"
                                        ack_id = await db.create_alert_ack(
                                            account_id=account_id,
                                            alert_type="fault",
                                            vehicle_id=v["id"],
                                            vehicle_name=v["name"],
                                            alert_key=alert_key,
                                            message_id=msg.message_id,
                                            chat_id=sub.telegram_id,
                                            sent_to=sub.telegram_id,
                                            next_escalation=next_esc,
                                        )

                                        ack_kb = InlineKeyboardMarkup([
                                            [InlineKeyboardButton(
                                                "✅ Acknowledge",
                                                callback_data=f"ack_alert_{ack_id}"
                                            )],
                                            [InlineKeyboardButton(
                                                f"📋 View Truck #{v['name']}",
                                                callback_data=f"cotruck_{co}_{v['name']}"
                                            )],
                                            [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
                                        ])
                                        await app.bot.edit_message_reply_markup(
                                            chat_id=sub.telegram_id,
                                            message_id=msg.message_id,
                                            reply_markup=ack_kb,
                                        )
                                    else:
                                        msg = await app.bot.send_message(
                                            chat_id=sub.telegram_id,
                                            text=send_text,
                                            parse_mode=ParseMode.HTML,
                                            reply_markup=kb,
                                        )
                                    _active_messages.setdefault(
                                        (sub.telegram_id, sub.telegram_id), []
                                    ).append(msg.message_id)
                                except Exception as e:
                                    logger.error(f"Alert to {sub.telegram_id}: {e}")

                            # Auto-create maintenance tasks for critical faults
                            if is_critical:
                                await auto_create_maintenance_from_faults(
                                    account_id, v["name"], new_dtcs,
                                )

                    await _set_known_faults(vid, current_codes)

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


# ── Health Alerts Scheduled Job ──────────────────────────────────

async def check_health_alerts(app: Application):
    """Check all accounts for vehicle health warnings and push alerts.

    Detects: low battery, low oil pressure, high coolant temp, low DEF.
    Skips seatbelt and engine-load as they are transient. Also scans
    active DTCs for coolant-related SPNs.
    """
    try:
        subscribers = await db.get_all_typed_subscribers("health")
        if not subscribers:
            return

        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        for account_id, subs in acct_subs.items():
            try:
                samsara = await get_client(account_id)
                vehicles = await samsara.get_vehicle_health()

                acct_companies = await db.get_account_companies(account_id)
                populate_company_display(acct_companies)
                company_codes = [o.code for o in acct_companies]

                # Also fetch faulted vehicles for coolant DTC scan
                try:
                    faulted, _, _ = await samsara.get_vehicles_with_faults()
                    faulted_by_id = {v["id"]: v for v in faulted}
                except Exception:
                    faulted_by_id = {}

                for v in vehicles:
                    alerts = v.get("_health_alerts", [])
                    # Filter to push-worthy alerts (skip seatbelt)
                    push_alerts = [
                        a for a in alerts
                        if a in ("low_battery", "low_oil_pressure",
                                 "high_coolant_temp", "low_def")
                    ]

                    # Check for coolant DTC in faulted vehicles
                    fv = faulted_by_id.get(v["id"])
                    if fv:
                        has_coolant_dtc = any(
                            dtc.get("spnId") in COOLANT_SPNS
                            for dtc in fv.get("_dtcs", [])
                        )
                        if has_coolant_dtc and "coolant_dtc" not in push_alerts:
                            push_alerts.append("coolant_dtc")

                    if not push_alerts:
                        continue

                    co = v.get("_org", "?")
                    vid = f"{account_id}:{co}:{v['id']}"
                    previously_known = await _get_known_health(vid)
                    new_alerts = set(push_alerts) - previously_known

                    if not new_alerts:
                        await _set_known_health(vid, set(push_alerts))
                        continue

                    show_co = len(company_codes) > 1
                    health = v.get("_health", {})
                    alert_text = format_health_alert(
                        v, list(new_alerts), health,
                        show_company=show_co,
                    )
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            f"📋 View Truck #{v['name']}",
                            callback_data=f"cotruck_{co}_{v['name']}"
                        )],
                        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
                    ])

                    for sub in subs:
                        if sub.role == Role.DRIVER and sub.truck_num:
                            if v["name"].lower() != sub.truck_num.lower():
                                continue
                        # DND: skip during quiet hours (health alerts are non-critical)
                        if sub.is_in_quiet_hours():
                            continue
                        try:
                            msg = await app.bot.send_message(
                                chat_id=sub.telegram_id,
                                text=alert_text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=kb,
                            )
                            _active_messages.setdefault(
                                (sub.telegram_id, sub.telegram_id), []
                            ).append(msg.message_id)
                        except Exception as e:
                            logger.error(f"Health alert to {sub.telegram_id}: {e}")

                    await _set_known_health(vid, set(push_alerts))

            except Exception as e:
                logger.error(f"Health check for account {account_id}: {e}")

    except Exception as e:
        logger.error(f"Health check error: {e}")


# ── Low Fuel Alerts Scheduled Job ────────────────────────────────

async def check_low_fuel(app: Application):
    """Check all accounts for vehicles below the fuel threshold and push alerts.

    Clears the dedup flag when a vehicle rises back above the threshold.
    """
    try:
        subscribers = await db.get_all_typed_subscribers("fuel")
        if not subscribers:
            return

        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        for account_id, subs in acct_subs.items():
            try:
                samsara = await get_client(account_id)
                low_fuel = await samsara.get_low_fuel_vehicles(FUEL_THRESHOLD)

                acct_companies = await db.get_account_companies(account_id)
                populate_company_display(acct_companies)
                company_codes = [o.code for o in acct_companies]

                low_fuel_ids = set()

                for v in low_fuel:
                    co = v.get("_org", "?")
                    vid = f"{account_id}:{co}:{v['id']}"
                    low_fuel_ids.add(vid)

                    if await _is_known_low_fuel(vid):
                        continue  # already alerted

                    fuel_pct = v.get("_fuel_pct", 0)
                    show_co = len(company_codes) > 1
                    alert_text = format_low_fuel_alert(
                        v, fuel_pct, show_company=show_co,
                    )
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            f"📋 View Truck #{v['name']}",
                            callback_data=f"cotruck_{co}_{v['name']}"
                        )],
                        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
                    ])

                    for sub in subs:
                        if sub.role == Role.DRIVER and sub.truck_num:
                            if v["name"].lower() != sub.truck_num.lower():
                                continue
                        # DND: skip during quiet hours
                        if sub.is_in_quiet_hours():
                            continue
                        try:
                            msg = await app.bot.send_message(
                                chat_id=sub.telegram_id,
                                text=alert_text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=kb,
                            )
                            _active_messages.setdefault(
                                (sub.telegram_id, sub.telegram_id), []
                            ).append(msg.message_id)
                        except Exception as e:
                            logger.error(f"Fuel alert to {sub.telegram_id}: {e}")

                    await _set_low_fuel_flag(vid, True)

                # Clear dedup for vehicles that rose above threshold
                stale_keys = [
                    k for k in list(_known_low_fuel.keys())
                    if k.startswith(f"{account_id}:") and k not in low_fuel_ids
                ]
                for k in stale_keys:
                    await _set_low_fuel_flag(k, False)

            except Exception as e:
                logger.error(f"Fuel check for account {account_id}: {e}")

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


# ── Escalation Checker (scheduled job) ───────────────────────────

# Escalation chain: role priority for alert escalation
_ESCALATION_CHAIN = [Role.DRIVER, Role.DISPATCHER, Role.FLEET_MGR, Role.ADMIN, Role.OWNER]


async def check_alert_escalations(app: Application):
    """Check for unacknowledged alerts that need escalation.

    Runs every 5 minutes. If an alert hasn't been acknowledged
    within ESCALATION_TIMEOUT_MINUTES, re-sends to the next role up.
    """
    try:
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        pending = await db.get_unacked_alerts(before=now_str)

        if not pending:
            return

        for alert in pending:
            try:
                account_id = alert["account_id"]
                current_level = alert["escalation_level"]
                next_level = current_level + 1

                if next_level >= len(_ESCALATION_CHAIN):
                    # Max escalation reached — stop
                    await db.update_alert_escalation(alert["id"], next_level, None)
                    continue

                target_role = _ESCALATION_CHAIN[next_level]

                # Find users with this role or higher
                all_users = await db.list_account_users(account_id)
                escalation_targets = [
                    u for u in all_users
                    if u.alerts_on
                    and u.telegram_id != alert["sent_to"]
                    and _ESCALATION_CHAIN.index(u.role) >= _ESCALATION_CHAIN.index(target_role)
                    if u.role in _ESCALATION_CHAIN
                ]

                if not escalation_targets:
                    await db.update_alert_escalation(alert["id"], next_level, None)
                    continue

                escalation_text = (
                    "━━━━━━━━━━━━━━━━━━━\n"
                    "  🚨  <b>ESCALATED ALERT</b>\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    f"\n  ⚠️ Unacknowledged {alert['alert_type']} alert\n"
                    f"  🚛 Truck: <b>{alert['vehicle_name']}</b>\n"
                    f"\n  This alert was sent {ESCALATION_TIMEOUT_MINUTES} min ago\n"
                    f"  and has not been acknowledged.\n"
                    f"\n  Escalation level: {next_level}\n"
                )

                for target in escalation_targets[:3]:  # limit re-sends
                    try:
                        ack_kb = InlineKeyboardMarkup([
                            [InlineKeyboardButton(
                                "✅ Acknowledge",
                                callback_data=f"ack_alert_{alert['id']}"
                            )],
                            [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
                        ])
                        await app.bot.send_message(
                            chat_id=target.telegram_id,
                            text=escalation_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=ack_kb,
                        )
                    except Exception as e:
                        logger.error(f"Escalation to {target.telegram_id}: {e}")

                # Schedule next escalation
                next_esc = (now + timedelta(
                    minutes=ESCALATION_TIMEOUT_MINUTES
                )).isoformat()
                await db.update_alert_escalation(alert["id"], next_level, next_esc)

            except Exception as e:
                logger.error(f"Escalation for alert {alert['id']}: {e}")

    except Exception as e:
        logger.error(f"Escalation check error: {e}")


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
                # account_id 0 signals system-triggered usage
                await _db.log_ai_usage(
                    account_id=0,
                    user_id=0,
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
