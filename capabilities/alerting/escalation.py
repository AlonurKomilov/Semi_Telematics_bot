"""Alert escalation — ACK, auto-resolve."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

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

        # Update the message to show it's been acknowledged.  The
        # Option A grammar appends a chip suffix to the existing 🔖 id
        # line so the alert stays a single coherent block instead of
        # gaining a trailing "Acknowledged by …" stanza on its own.
        # Falls back to appending a new line when no id chip is found
        # (legacy alerts, manually-edited messages, …).
        try:
            original_text = query.message.text_html or query.message.text or ""
            ack_name = query.from_user.full_name or str(tid)
            ack_chip = (
                f"  ·  ✅ Acked by "
                f"<a href='tg://user?id={tid}'>{ack_name}</a>"
            )
            lines = original_text.split("\n")
            patched = False
            for i, ln in enumerate(lines):
                if ln.lstrip().startswith("🔖"):
                    lines[i] = ln + ack_chip
                    patched = True
                    break
            ack_text = "\n".join(lines) if patched else (
                original_text + f"\n\n✅ <b>Acknowledged</b> by "
                f"<a href='tg://user?id={tid}'>{ack_name}</a>"
            )
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
    describing what was originally alerted.  Every field that
    originated outside our code (DTC ``desc``, ``spn`` / ``fmi``
    codes, raw fault ``entry``) is HTML-escaped before going into the
    output — these flow through parse_mode=HTML and Samsara strings
    occasionally contain ``<``, ``>``, or ``&``.
    """
    from capabilities.formatting.helpers import escape_html
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
            # Either the canonical label (safe constant) or a title-cased
            # fallback derived from the code (alphanumeric — safe).
            label = label_map.get(code, code.replace("_", " ").title())
            lines.append(f"  ✅ {label} — normal")
    elif alert_type == "fault":
        for entry in detail.split("|"):
            if ":" in entry:
                code_part, desc = entry.split(":", 1)
                lines.append(f"  ✅ {escape_html(code_part)} — cleared")
                if desc:
                    lines.append(f"       {escape_html(desc)}")
            else:
                spn_fmi = entry.split("-", 1)
                if len(spn_fmi) == 2:
                    lines.append(
                        f"  ✅ SPN {escape_html(spn_fmi[0])} / "
                        f"FMI {escape_html(spn_fmi[1])} — cleared"
                    )
                elif entry:
                    lines.append(f"  ✅ {escape_html(entry)} — cleared")
    elif alert_type == "fuel":
        fuel_val = detail.split(":")[-1] if ":" in detail else detail
        lines.append(f"  ✅ Fuel was at {escape_html(fuel_val)}% — now above threshold")
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

    # Capture the earliest human ack BEFORE the auto-resolve sweep
    # (which marks every un-acked row as system-acked with
    # ``acknowledged_by = 0``).  When a real user handled the alert
    # before it cleared, we surface "✅ Acked by <name>" on the
    # resolve receipt so the rest of the team sees who closed the
    # loop — even on the auto-resolve message they didn't trigger.
    earliest_ack = await tenant.get_earliest_human_ack(
        account_id, alert_type, vehicle_id,
    )

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

    from capabilities.formatting.helpers import escape_html
    vname = escape_html(str(vehicle_name or resolved[0].get("vehicle_name", "?")))
    alert_co = escape_html(str(co or "?"))

    # Build resolve detail from the shared alert_key (same for all subscribers)
    alert_key = resolved[0].get("alert_key", "")
    key_parts = alert_key.split(":", 2)
    detail = key_parts[2] if len(key_parts) > 2 else ""
    detail_lines = _build_resolve_detail(alert_type, detail)

    # Compute "cleared Xh Ym after first seen" once from shared first_seen.
    duration_phrase = ""
    if first_seen_str:
        try:
            first_dt = datetime.fromisoformat(first_seen_str)
            mins = int((datetime.now(timezone.utc) - first_dt).total_seconds() / 60)
            if mins >= 60:
                duration_phrase = f"cleared {mins // 60}h {mins % 60}m after first seen"
            elif mins >= 1:
                duration_phrase = f"cleared {mins} min after first seen"
            else:
                duration_phrase = "cleared in under a minute"
        except Exception as e:
            logger.debug("Could not compute alert duration: %s", e)

    # ── Build the receipt in Option A grammar ────────────────────
    # 🟢 RESOLVED — <Type> Cleared
    #
    # 🚛 Truck #<name>  ·  🏢 <co>  ·  🕐 cleared 2h 14m after first seen
    #
    # ✅ <what was alerting>
    #
    # 💡 Condition cleared — no action needed
    from capabilities.formatting.severity import badge, default_action
    type_title_map = {
        "fault":  "Fault Cleared",
        "health": "Health Cleared",
        "fuel":   "Fuel Restored",
    }
    title = type_title_map.get(alert_type, "Alert Cleared")

    resolve_lines: list[str] = [f"<b>{badge('resolved')}</b> — {title}", ""]

    where_parts = [f"🚛 <b>Truck #{vname}</b>"]
    if alert_co and alert_co != "?":
        where_parts.append(f"🏢 {alert_co}")
    if duration_phrase:
        where_parts.append(f"🕐 {duration_phrase}")
    resolve_lines.append("  ·  ".join(where_parts))

    if detail_lines:
        # ``detail_lines`` already prefixes each row with ``  ✅`` — drop
        # the leading whitespace so it sits flush with the new grammar.
        cleaned = "\n".join(
            ln.lstrip() for ln in detail_lines.split("\n") if ln.strip()
        )
        resolve_lines.append("")
        resolve_lines.append(cleaned)

    resolve_lines.append("")
    resolve_lines.append(f"💡 {default_action('resolved')}")

    # ── ACK chip (when a human acked before the system cleared) ──
    if earliest_ack:
        ack_tid = earliest_ack.get("acknowledged_by") or 0
        ack_at = earliest_ack.get("acknowledged_at") or ""
        ack_name: str | None = None
        try:
            acker = await get_platform_db().get_user_by_telegram_id(ack_tid)
            if acker:
                ack_name = acker.display_name or str(ack_tid)
        except Exception as e:
            logger.debug("Could not resolve acker name for %s: %s", ack_tid, e)
        ack_name = ack_name or f"user {ack_tid}"

        # "Acked Xh Ym before clear" — anchors the ACK to the same
        # clock as the resolve duration above so the reader can place
        # the two events in order at a glance.
        gap_phrase = ""
        if ack_at and first_seen_str:
            try:
                ack_dt = datetime.fromisoformat(ack_at)
                clr_dt = datetime.now(timezone.utc)
                gap_mins = int((clr_dt - ack_dt).total_seconds() / 60)
                if gap_mins >= 60:
                    gap_phrase = f" · {gap_mins // 60}h {gap_mins % 60}m before clear"
                elif gap_mins >= 1:
                    gap_phrase = f" · {gap_mins} min before clear"
            except Exception as e:
                logger.debug("Could not compute ack→clear gap: %s", e)

        resolve_lines.append(f"🔖 ✅ Acked by <b>{ack_name}</b>{gap_phrase}")

    resolve_text = "\n".join(resolve_lines)

    # Per-recipient: try to EDIT the existing alert message into a
    # ✅ resolved receipt instead of delete-old + send-new.  The user's
    # chat now shows one persistent record per logical alert with its
    # final state ("auto-resolved 6m ago"), no extra notification ping.
    # Falls back to delete + send when the edit fails (msg deleted, > 48h
    # old, ParseMode mismatch, …) so the user still gets the resolution.
    # DM resolves keep the View Truck button — the recipient is the
    # only viewer of their own DM, so editing-in-place is harmless.
    resolved_kb_dm = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"📋 View Truck #{vname}",
            callback_data=f"covehicle_{alert_co}_{vname}",
        )],
    ])
    # Group resolves drop the button entirely — its callback rewrites
    # the message in place and would erase the "AUTO-RESOLVED" receipt
    # for every other shift reading the topic.
    resolved_kb_group = None

    # Look up the forum topic for this alert type once so group resolves
    # can route explicitly.  Telegram only routes a reply into the same
    # topic as the original message when ``message_thread_id`` is passed
    # alongside ``reply_to_message_id`` — relying on the reply-link alone
    # leaks the resolution into the group's General topic (e.g. "Team
    # Chat") whenever the original is too old / edited / not visible.
    # That mismatched routing was visible to operators as Health and
    # Fault "AUTO-RESOLVED" receipts landing in Team Chat instead of
    # the Faults / Health topics where the live alerts post.
    from capabilities.alerting.pipeline import (
        _FORUM_ROUTING_ENABLED,
        _PIPELINE_TO_ROUTE_KEY,
    )
    group_thread_id: int | None = None
    if _FORUM_ROUTING_ENABLED:
        _route_key = _PIPELINE_TO_ROUTE_KEY.get(alert_type)
        if _route_key:
            try:
                _route = await get_platform_db().get_alert_route(account_id, _route_key)
                if _route is not None:
                    group_thread_id = _route.message_thread_id
            except Exception as e:
                logger.debug(
                    "Could not look up forum route for auto-resolve "
                    "(acct=%d type=%s): %s", account_id, alert_type, e,
                )

    for alert in resolved:
        recipient_id = alert.get("sent_to")
        msg_id = alert.get("message_id")
        chat_id = alert.get("chat_id")
        is_group_post = recipient_id == 0  # sentinel from _try_post_to_topic

        # DND: skip Telegram delivery (audit log still happens below).
        # Group posts skip DND entirely — Telegram per-topic mute is
        # each member's personal silencer.  Personal DND uses the SSoT
        # helper that prefers per-user override and falls back to the
        # account's Working Hours for the user's role.
        if recipient_id and not is_group_post:
            recipient = await get_platform_db().get_user_by_telegram_id(recipient_id)
            if recipient:
                from capabilities.alerting.dnd import is_user_dnd_active
                if await is_user_dnd_active(recipient, tenant):
                    await tenant.queue_dnd_alert(
                        account_id=account_id,
                        telegram_id=recipient_id,
                        alert_type=alert_type,
                        vehicle_name=vname,
                        alert_text=f"✅ Auto-resolved: {alert_type} alert cleared",
                    )
                    continue

        # ── Group posts: send resolution as a REPLY to the original ──
        # In a forum topic the original "🚨 ALERT" message has to stay
        # visible — different shifts read the same topic at different
        # times and the alert text + duration + AI analysis is the
        # context they need.  Editing-in-place would erase that and
        # leave only "✅ AUTO-RESOLVED" with no clue what the alert
        # said.  So for group posts we post a NEW resolution message
        # as a reply to the original, preserving both in the thread.
        if is_group_post:
            if msg_id and chat_id:
                try:
                    await bot_app.bot.send_message(
                        chat_id=chat_id,
                        message_thread_id=group_thread_id,
                        text=resolve_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=resolved_kb_group,
                        reply_to_message_id=msg_id,
                    )
                except Exception as e:
                    logger.debug(
                        "Group auto-resolve reply failed (chat=%s msg=%s): %s",
                        chat_id, msg_id, e,
                    )
                    # Last resort: post into the topic without a reply
                    # link so at least the resolution lands somewhere.
                    # ``message_thread_id`` still applies — the reply was
                    # only context, not what kept us inside the topic.
                    try:
                        await bot_app.bot.send_message(
                            chat_id=chat_id,
                            message_thread_id=group_thread_id,
                            text=resolve_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=resolved_kb_group,
                        )
                    except Exception as e2:
                        logger.debug("Group auto-resolve plain-send also failed: %s", e2)
            continue

        # ── DM posts: original behavior (edit-in-place) ──
        # The recipient is the only viewer of their own DM, so
        # collapsing alert+resolve into one persistent record keeps
        # their feed tidy.  Falls back to delete+send only when the
        # edit physically can't happen (msg deleted, >48 h old).
        edited = False
        if msg_id and chat_id:
            try:
                await bot_app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=resolve_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=resolved_kb_dm,
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
                reply_markup=resolved_kb_dm,
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
            lang=getattr(user, "language", None) or "en",
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
            # Demote the badge to 🟡 REMINDER so a chronic open alert
            # doesn't keep looking like a fresh 🔴/🟠 push.  The
            # reminder counter ("reminder 2/4") sits on the same line
            # so dispatch sees how aggressive the loop has been.
            from capabilities.formatting.severity import badge as _badge
            reminder_lines: list[str] = [
                f"<b>{_badge('reminder')}</b> — Unacknowledged Alert  "
                f"<i>(reminder {attempt_n}/{REESCALATE_MAX_ATTEMPTS})</i>",
                "",
                f"🚛 <b>Truck #{vname}</b>",
                f"⏱ <b>{atype.title()}</b> alert active for <b>{age_str}</b>",
                "",
                "💡 Acknowledge or mute — auto-clears when the condition lifts",
                f"🔖 #{history_id}",
            ]
            reminder_text = "\n".join(reminder_lines)

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

            # Keyboard for re-escalation reminders.
            # DM deliveries get a callback "Open alert" button — safe
            # since the recipient is the only viewer.  Group-post
            # deliveries (sent_to=0) get NO keyboard: the back_alert
            # callback would rewrite the edited reminder back into
            # the original alert, erasing it for every other shift.
            kb_dm = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔎 Open alert",
                    callback_data=f"back_alert_{deliveries[0]['id']}",
                ),
            ]])
            kb_group = None

            attempt_sent_anywhere = False
            for delivery in deliveries:
                recipient_id = delivery.get("sent_to")
                # sent_to == 0 is the sentinel for a forum-group post
                # (one message visible to many users).  Group posts get
                # edited in place via (chat_id, message_id) just like a
                # DM delivery, but the DND-queue / per-user lookup is
                # skipped — Telegram per-topic mute is each member's
                # personal silencer in that case.
                is_group_post = recipient_id == 0
                if not is_group_post and not recipient_id:
                    continue

                if not is_group_post:
                    # Respect DND: queue the reminder so it lands when the
                    # recipient is back online.  Uses the SSoT helper —
                    # per-user override first, else derived from the
                    # account's Working Hours for the user's role.
                    try:
                        recipient = await _get_platform_db().get_user_by_telegram_id(recipient_id)
                    except Exception:
                        recipient = None
                    if recipient:
                        from capabilities.alerting.dnd import is_user_dnd_active
                        try:
                            in_dnd = await is_user_dnd_active(recipient, tenant)
                        except Exception:
                            in_dnd = False
                        if in_dnd:
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
                            reply_markup=kb_group if is_group_post else kb_dm,
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
                    if is_group_post:
                        # Group-post edit failed (topic deleted, message
                        # >48h old).  Skip the fresh-send fallback —
                        # we'd need the topic's message_thread_id and
                        # we'd also be spamming the topic with stale
                        # reminders.  Next escalation cycle will retry.
                        logger.debug(
                            "re_escalate: skipping group-post fallback for delivery %s",
                            delivery.get("id"),
                        )
                        continue
                    # DM fallback: send fresh.  Only reached when edit
                    # is impossible (msg deleted, > 48 h old, etc.).
                    # ``is_group_post`` is False here (already bailed
                    # above), so the DM-keyboard is the right choice.
                    try:
                        await bot_app.bot.send_message(
                            chat_id=recipient_id,
                            text=reminder_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=kb_dm,
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
