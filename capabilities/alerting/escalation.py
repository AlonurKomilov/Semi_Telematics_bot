"""Alert escalation — ACK, auto-resolve."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest as TGBadRequest
from telegram.ext import Application

from capabilities.alerting.pipeline import (
    AlertSeverity,
    build_alert_keyboard,
)
from infra.bot_registry import get_app_for_account
from infra.context import get_company_display
from infra.services import get_db, get_platform_db, get_tenant_db

logger = logging.getLogger("bot")


# ── Alert Acknowledgment Handler ─────────────────────────────────

async def handle_alert_ack(update, context, ack_id: int):
    """Handle the ✅ Acknowledge button press on a critical alert."""
    query = update.callback_query
    try:
        user = context.user_data.get("_db_user")
        tid = query.from_user.id
        acct_id = user.account_id if user else 0
        tenant = await get_tenant_db(acct_id) if acct_id else get_db()
        await tenant.acknowledge_alert(ack_id, tid)

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
            logger.debug("Failed to edit ack message for alert %d", ack_id)
        await query.answer("✅ Alert acknowledged!", show_alert=False)

        # Audit log
        if user:
            await tenant.add_audit_log(
                account_id=user.account_id,
                user_id=user.id,
                action="alert_acknowledged",
                target_type="alert",
                target_id=str(ack_id),
            )
    except Exception as e:
        logger.error("ACK alert %d failed: %s", ack_id, e, exc_info=True)
        await query.answer("Error acknowledging alert", show_alert=True)


# ── Auto-Resolve Helpers ─────────────────────────────────────────

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
    bot_app: Application | None = None,
):
    """Auto-resolve all unacked alerts for a vehicle when the source check
    detects the condition has cleared.

    This is the fast path — called directly from check_health_alerts /
    check_new_faults / check_low_fuel when they see a vehicle is clear.
    Eliminates waiting for the re-alert cycle to auto-resolve.
    Respects working hours — queues notification if user is outside working hours.

    Self-contained: clears alert_history AND resolves alert_acknowledgments so
    callers do not need to call clear_alert_history separately.
    """
    tenant = await get_tenant_db(account_id)

    # Fetch first_seen from the shared history record BEFORE clearing it
    # (get_active_alert_history filters status='active', so must come first)
    hist = await tenant.get_active_alert_history(account_id, alert_type, vehicle_id)
    first_seen_str = hist["first_seen"] if hist else ""

    # Clear the single alert_history record (single source of truth)
    await tenant.clear_alert_history(account_id, alert_type, vehicle_id)

    # Resolve all subscriber ack rows (one per subscriber)
    resolved = await tenant.auto_resolve_alerts_by_vehicle(
        account_id, alert_type, vehicle_id,
    )
    if not resolved:
        return

    # Resolve per-account bot — skip if no account bot registered
    if bot_app is None:
        bot_app = get_app_for_account(account_id)
    if not bot_app:
        logger.warning("No bot for account %d — skipping auto-resolve", account_id)
        return

    vname = vehicle_name or resolved[0].get("vehicle_name", "?")
    atype_label = alert_type.replace("_", " ").title()
    alert_co = co or "?"

    # Build resolve detail from the shared alert_key (same for all subscribers)
    alert_key = resolved[0].get("alert_key", "")
    key_parts = alert_key.split(":", 2)
    detail = key_parts[2] if len(key_parts) > 2 else ""
    detail_lines = _build_resolve_detail(alert_type, detail)

    # Compute duration once from shared first_seen (not per-subscriber created_at)
    duration_str = ""
    if first_seen_str:
        try:
            first_dt = datetime.fromisoformat(first_seen_str)
            mins = int((datetime.now(timezone.utc) - first_dt).total_seconds() / 60)
            if mins >= 60:
                duration_str = f"  🕐 Active for <b>{mins // 60}h {mins % 60}m</b>\n"
            else:
                duration_str = f"  🕐 Active for <b>{mins} min</b>\n"
        except Exception as e:
            logger.debug("Could not compute alert duration: %s", e)

    # Build the resolve message template once — same for all subscribers
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

    # Per-recipient: try to EDIT the existing alert message into a
    # ✅ resolved receipt instead of delete-old + send-new.  The user's
    # chat now shows one persistent record per logical alert with its
    # final state ("auto-resolved 6m ago"), no extra notification ping.
    # Falls back to delete + send when the edit fails (msg deleted, > 48h
    # old, ParseMode mismatch, …) so the user still gets the resolution.
    resolved_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"📋 View Truck #{vname}",
            callback_data=f"covehicle_{alert_co}_{vname}",
        )],
    ])

    for alert in resolved:
        recipient_id = alert.get("sent_to")
        msg_id = alert.get("message_id")
        chat_id = alert.get("chat_id")

        # DND: skip Telegram delivery (audit log still happens below).
        if recipient_id:
            recipient = await get_platform_db().get_user_by_telegram_id(recipient_id)
            if recipient and recipient.is_in_quiet_hours():
                await tenant.queue_dnd_alert(
                    account_id=account_id,
                    telegram_id=recipient_id,
                    alert_type=alert_type,
                    vehicle_name=vname,
                    alert_text=f"✅ Auto-resolved: {alert_type} alert cleared",
                )
                continue

        edited = False
        if msg_id and chat_id:
            try:
                await bot_app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=resolve_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=resolved_kb,
                )
                edited = True
            except TGBadRequest as e:
                if "not modified" in str(e).lower():
                    edited = True
                else:
                    logger.debug(
                        "Auto-resolve edit failed for msg %s: %s — falling back to send-new",
                        msg_id, e,
                    )
            except Exception as e:
                logger.debug("Auto-resolve edit error for msg %s: %s", msg_id, e)

        if edited:
            continue

        # Fallback path: delete the original alert and send a fresh
        # resolved message.  Only reached when the in-place edit failed.
        if msg_id and chat_id:
            try:
                await bot_app.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                logger.debug(
                    "Failed to delete old alert msg %s during auto-resolve fallback",
                    msg_id,
                )
        try:
            await bot_app.bot.send_message(
                chat_id=alert["sent_to"],
                text=resolve_text,
                parse_mode=ParseMode.HTML,
                reply_markup=resolved_kb,
            )
        except Exception as e:
            logger.debug("Could not send auto-resolve message: %s", e)

    await tenant.add_audit_log(
        account_id=account_id,
        user_id=None,
        action="alert_auto_resolved",
        target_type="alert",
        target_id=str(resolved[0]["id"]),
        details=(
            f"{alert_type} alert for Truck {vname} auto-resolved; "
            f"{len(resolved)} subscriber(s) notified"
        ),
    )
    logger.info(
        "Auto-resolved %s alert for Truck %s — notified %d subscriber(s)",
        alert_type, vname, len(resolved),
    )

async def handle_back_to_alert(update, context, ack_id: int):
    """Re-render the alert summary + keyboard when user presses Back from AI Diagnose, etc."""
    query = update.callback_query
    await query.answer()
    try:
        user = context.user_data.get("_db_user")
        acct_id = user.account_id if user else 0
        tenant = await get_tenant_db(acct_id) if acct_id else get_db()
        row = await tenant.get_alert_ack_by_id(ack_id)
        if not row:
            await query.edit_message_text("Alert not found.", parse_mode=ParseMode.HTML)
            return

        alert_key = row.get("alert_key", "")
        parts = alert_key.split(":", 2)
        co = parts[0] if parts else "?"
        vname = row.get("vehicle_name", "?")
        alert_type = row.get("alert_type", "fault")
        detail = parts[2] if len(parts) > 2 else ""
        co_display = get_company_display().get(co, co)

        # Determine severity
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


# ── Re-escalation of unacknowledged CRITICAL alerts ──────────────

def _backoff_hours_for_attempt(attempt_index: int, schedule: tuple[int, ...]) -> int:
    """Pick the wait-hours for the next reminder.

    ``attempt_index`` is the count of reminders ALREADY sent (0 = none yet).
    Returns the schedule entry, clamped to the last value when we're past
    the array length so chronic alerts settle at the longest backoff.
    """
    if not schedule:
        return 1
    if attempt_index < 0:
        attempt_index = 0
    if attempt_index >= len(schedule):
        return schedule[-1]
    return schedule[attempt_index]


async def re_escalate_critical_alerts(app: Application):
    """Hourly job: bump unacknowledged CRITICAL/WARNING alerts.

    Major rewrite (2026-05) addressing the 360+ reminders/recipient noise
    pattern.  Now:

      * Iterates ``alert_history`` (one row per logical alert) instead of
        ``alert_acknowledgments`` (one row per delivery × subscriber).
        Subscriber fan-out happens once per logical alert per attempt.
      * Caps at ``REESCALATE_MAX_ATTEMPTS`` reminders per logical alert
        across its entire lifetime — after that, the alert stays in the
        dashboard pending list but stops pinging Telegram.
      * Exponential backoff per attempt via ``REESCALATE_BACKOFF_HOURS``
        (default 1h → 4h → 12h → 24h between successive reminders).
      * Skips alerts that are muted (alert_mutes table).
      * Edits the existing alert message in place with an "[N reminders]"
        prefix instead of sending a fresh push notification.  Falls back
        to send-new only when the edit fails.
      * Per-account isolation via tenant DB; never mixes data across
        accounts.
    """
    from infra.config import (
        REESCALATE_AFTER_MINUTES,
        REESCALATE_ALERT_TYPES,
        REESCALATE_MAX_ATTEMPTS,
        REESCALATE_BACKOFF_HOURS,
    )
    from infra.services import get_platform_db as _get_platform_db

    if REESCALATE_AFTER_MINUTES <= 0 or not REESCALATE_ALERT_TYPES:
        return
    if REESCALATE_MAX_ATTEMPTS <= 0:
        return

    platform = _get_platform_db()
    try:
        accounts = await platform.list_accounts()
    except Exception as e:
        logger.error("re_escalate: failed to list accounts: %s", e)
        return

    qualified_types = set(REESCALATE_ALERT_TYPES)
    now_dt = datetime.now(timezone.utc)
    sent = 0
    suppressed_muted = 0
    suppressed_capped = 0

    for acct in accounts:
        account_id = getattr(acct, "id", None)
        if not account_id:
            continue
        try:
            tenant = await get_tenant_db(account_id)
        except Exception as e:
            logger.debug("re_escalate: tenant fetch failed for %s: %s", account_id, e)
            continue

        # First reminder fires after REESCALATE_AFTER_MINUTES; further
        # reminders use REESCALATE_BACKOFF_HOURS[count-1].  We compute
        # the strictest cutoff (min of current backoff window) below per
        # row, but the SQL pre-filter uses the loosest one to avoid
        # fetching everything.
        loose_cutoff_min = min(
            REESCALATE_AFTER_MINUTES,
            REESCALATE_BACKOFF_HOURS[0] * 60 if REESCALATE_BACKOFF_HOURS else 60,
        )
        cutoff_iso = (now_dt - timedelta(minutes=loose_cutoff_min)).isoformat()
        try:
            candidates = await tenant.get_active_unacked_history_for_reescalation(
                account_id, cutoff_iso, REESCALATE_MAX_ATTEMPTS,
            )
        except Exception as e:
            logger.debug("re_escalate: candidate fetch failed acct=%d: %s", account_id, e)
            continue
        if not candidates:
            continue

        bot_app = get_app_for_account(account_id) or app
        if not bot_app:
            continue

        for hist in candidates:
            atype = hist.get("alert_type", "alert")
            if atype not in qualified_types:
                continue

            history_id = hist.get("id")
            attempts_so_far = int(hist.get("reescalate_count") or 0)

            # Per-row cooldown using the backoff schedule.
            wait_hours = _backoff_hours_for_attempt(
                attempts_so_far, REESCALATE_BACKOFF_HOURS,
            )
            last_sent = hist.get("reescalate_last_sent_at")
            if last_sent:
                try:
                    last_dt = datetime.fromisoformat(last_sent)
                    if (now_dt - last_dt) < timedelta(hours=wait_hours):
                        continue
                except Exception:
                    pass
            else:
                # First-ever reminder gates on first_seen + REESCALATE_AFTER_MINUTES
                first_seen = hist.get("first_seen") or ""
                if first_seen:
                    try:
                        first_dt = datetime.fromisoformat(first_seen)
                        if (now_dt - first_dt) < timedelta(minutes=REESCALATE_AFTER_MINUTES):
                            continue
                    except Exception:
                        pass

            # Mute check — operator silenced this alert.
            try:
                if await tenant.is_alert_history_muted(history_id, account_id):
                    suppressed_muted += 1
                    continue
            except Exception:
                pass

            vid = hist.get("vehicle_id", "")
            vname = hist.get("vehicle_name", "?")
            try:
                first_dt = datetime.fromisoformat(hist.get("first_seen") or "")
                age_min = int((now_dt - first_dt).total_seconds() / 60)
            except Exception:
                age_min = REESCALATE_AFTER_MINUTES
            age_str = f"{age_min // 60}h {age_min % 60}m" if age_min >= 60 else f"{age_min} min"

            attempt_n = attempts_so_far + 1
            reminder_text = (
                "━━━━━━━━━━━━━━━━━━━\n"
                f"  🔴  <b>UNACKNOWLEDGED ALERT</b>"
                f" (reminder {attempt_n}/{REESCALATE_MAX_ATTEMPTS})\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"\n  🚛 Truck: <b>#{vname}</b>"
                f"\n  🩺 {atype.title()} alert #<b>{history_id}</b>"
                f" active for <b>{age_str}</b>"
                "\n\n  Please acknowledge or mute."
            )

            # Resolve every still-active recipient delivery for this
            # logical alert.  We edit each subscriber's message in place
            # (so they see "[reminder 2/4]" added to the existing alert
            # bubble) instead of sending a brand-new push.
            try:
                deliveries = await tenant.auto_resolve_alerts_by_vehicle  # noqa
            except AttributeError:
                deliveries = None
            try:
                rows = await tenant.read_all(
                    "SELECT * FROM alert_acknowledgments "
                    "WHERE account_id = ? AND alert_type = ? AND vehicle_id = ? "
                    "AND acknowledged_at IS NULL AND status = 'active'",
                    (account_id, atype, vid),
                )
                deliveries = [dict(r) for r in rows]
            except Exception as e:
                logger.debug("re_escalate: deliveries fetch failed: %s", e)
                deliveries = []
            if not deliveries:
                # No active deliveries to remind — likely already acked
                # via a path that didn't clear history.  Bump count so
                # we don't loop on this row.
                try:
                    await tenant.bump_reescalate_attempt(history_id, account_id)
                except Exception:
                    pass
                continue

            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔎 Open alert",
                    callback_data=f"back_alert_{deliveries[0]['id']}",
                ),
            ]])

            attempt_sent_anywhere = False
            for delivery in deliveries:
                recipient_id = delivery.get("sent_to")
                if not recipient_id:
                    continue

                # Respect DND: queue the reminder so it lands when the
                # recipient is back online.
                try:
                    recipient = await _get_platform_db().get_user_by_telegram_id(recipient_id)
                except Exception:
                    recipient = None
                if recipient and recipient.is_in_quiet_hours():
                    try:
                        await tenant.queue_dnd_alert(
                            account_id=account_id,
                            telegram_id=recipient_id,
                            alert_type=atype,
                            vehicle_name=vname,
                            alert_text=reminder_text,
                        )
                    except Exception:
                        pass
                    attempt_sent_anywhere = True
                    continue

                msg_id = delivery.get("message_id")
                chat_id = delivery.get("chat_id")
                edited = False
                if msg_id and chat_id:
                    try:
                        await bot_app.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=msg_id,
                            text=reminder_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=kb,
                        )
                        edited = True
                    except TGBadRequest as e:
                        if "not modified" in str(e).lower():
                            edited = True
                        else:
                            logger.debug(
                                "re_escalate: edit failed for delivery %s: %s",
                                delivery.get("id"), e,
                            )
                    except Exception as e:
                        logger.debug(
                            "re_escalate: edit error for delivery %s: %s",
                            delivery.get("id"), e,
                        )

                if not edited:
                    # Fallback: send fresh.  Only reached when edit
                    # is impossible (msg deleted, > 48 h old, etc.).
                    try:
                        await bot_app.bot.send_message(
                            chat_id=recipient_id,
                            text=reminder_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=kb,
                        )
                    except Exception as e:
                        logger.debug(
                            "re_escalate: send fallback failed for %s: %s",
                            recipient_id, e,
                        )
                        continue
                attempt_sent_anywhere = True

            if attempt_sent_anywhere:
                try:
                    new_count = await tenant.bump_reescalate_attempt(history_id, account_id)
                    sent += 1
                    if new_count >= REESCALATE_MAX_ATTEMPTS:
                        suppressed_capped += 1
                        logger.info(
                            "re_escalate: alert #%d hit max attempts (%d) — silenced",
                            history_id, REESCALATE_MAX_ATTEMPTS,
                        )
                except Exception as e:
                    logger.debug(
                        "re_escalate: bump_reescalate_attempt failed for %d: %s",
                        history_id, e,
                    )

    if sent or suppressed_muted or suppressed_capped:
        logger.info(
            "re_escalate: sent=%d skipped_muted=%d capped=%d",
            sent, suppressed_muted, suppressed_capped,
        )
