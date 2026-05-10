"""Shared alert pipeline — severity tiers, keyboard builder, send_alert().

Universal alert pipeline with severity tiers:
  • CRITICAL — bypasses DND, requires ACK
  • WARNING  — respects DND, requires ACK
  • INFO     — respects DND, no ACK needed, history tracking only
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from telegram.constants import ParseMode
from telegram.error import BadRequest as TGBadRequest, TelegramError

from adapters.storage import Role
from adapters.samsara.client import (
    samsara_vehicle_url, samsara_event_url, samsara_fault_url,
)
from infra.context import get_org_ids
from infra.bot_registry import get_app_for_account
from capabilities.formatting import format_alert_history_footer

import logging
from infra.config import (
    FAULT_ALERT_COOLDOWN_HOURS,
    HEALTH_ALERT_COOLDOWN_HOURS,
)
from infra.services import get_tenant_db

logger = logging.getLogger("bot")


# ═══════════════════════════════════════════════════════════════════
#  Severity Tiers & Alert Configuration
# ═══════════════════════════════════════════════════════════════════

class AlertSeverity(str, Enum):
    """Universal severity tiers for all alert types."""
    CRITICAL = "critical"   # 🔴 bypasses DND, ACK required
    WARNING  = "warning"    # 🟡 respects DND, ACK required
    INFO     = "info"       # 🔵 respects DND, no ACK, history only


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

# Occurrence numbers that force a fresh push (instead of silent edit-in-place).
# 1 = the original alert.  10/25/50/100/250/500/1000 are "still not fixed?"
# nudges that get progressively rarer so a chronic alert doesn't churn the
# notification panel every milestone.  Tune via PIPELINE_ESCALATION env var
# (comma-separated ints).
import os as _os
_ESCALATION_OCCURRENCES: frozenset[int] = frozenset(
    int(x) for x in _os.getenv(
        "PIPELINE_ESCALATION", "1,10,25,50,100,250,500,1000",
    ).split(",") if x.strip().isdigit()
) or frozenset({1})

# Startup warm-up: first cycle of each check only populates caches
# without sending alerts. Prevents alert bursts on server restart.
_warmup_done: dict[str, set[int]] = {"health": set(), "fuel": set()}

# Telegram bot API rate limit is roughly 30 msg/sec globally per bot
# token. Each subscriber's path can issue 2-5 Telegram calls (delete +
# send + edit_reply_markup, sometimes a video/photo too) so 20 parallel
# subscribers staying under that ceiling is comfortably safe; tunable
# via ``ALERT_FANOUT_CONCURRENCY`` if a deployment runs multiple bots.
_ALERT_FANOUT_CONCURRENCY = int(_os.getenv("ALERT_FANOUT_CONCURRENCY", "20"))


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

    CRITICAL/WARNING with ack_id → ACK + AI Diagnose + Open in Samsara + View Truck
    CRITICAL/WARNING without ack_id → AI Diagnose + Open in Samsara + View Truck (pre-ACK send)
    INFO → Open in Samsara + View Truck only

    alert_type is encoded in the AI Diagnose callback so the AI knows
    the context (fault / health / fuel).
    """
    from capabilities.localization.i18n import t
    rows: list[list[InlineKeyboardButton]] = []

    if severity in (AlertSeverity.CRITICAL, AlertSeverity.WARNING):
        if ack_id is not None:
            rows.append([InlineKeyboardButton(
                "✅ Acknowledge", callback_data=f"ack_alert_{ack_id}",
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
    org_id = get_org_ids().get(co, "")
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

    truck_cb = f"covehicle_{co}_{vehicle_name}"
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
    bot_app: Application | None = None,
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

    # Resolve per-account bot — skip if no account bot registered
    if bot_app is None:
        bot_app = get_app_for_account(account_id)
    if not bot_app:
        logger.warning("No bot for account %d — skipping alert delivery", account_id)
        return

    tenant = await get_tenant_db(account_id)

    # ── ONE shared alert history record per vehicle+type ──────────
    # Upsert BEFORE the subscriber loop so occurrence_count increments exactly
    # once per alert event — not once per subscriber.  All subscribers then
    # receive a notification that shows the same occurrence number and
    # first_seen timestamp (single source of truth).
    existing_hist = await tenant.get_active_alert_history(account_id, alert_type, vid)
    _hist_count = (existing_hist["occurrence_count"] if existing_hist else 0) + 1
    _hist_first_seen = existing_hist["first_seen"] if existing_hist else ""
    _now_str = datetime.now(timezone.utc).isoformat()

    # Snapshot the truck's location so dashboard/mini-app rows can show
    # "📍 Mojave Freeway, CA" without an extra Samsara round-trip.
    # Prefer the human-readable formattedLocation; fall back to
    # whatever the raw `address` field carries.
    _loc_dict = vehicle.get("location") or {}
    _location_snapshot = (
        (_loc_dict.get("reverseGeo") or {}).get("formattedLocation")
        or _loc_dict.get("address")
        or vehicle.get("formattedAddress")
        or vehicle.get("address")
        or ""
    )

    history_record = await tenant.upsert_alert_history(
        account_id=account_id,
        alert_type=alert_type,
        vehicle_id=vid,
        vehicle_name=vname,
        last_detail=alert_key_detail,
        # Severity-as-string so the storage layer doesn't import the
        # AlertSeverity enum.  AlertSeverity inherits from str so .value
        # round-trips cleanly to the persisted form.
        severity=str(severity.value) if isinstance(severity, AlertSeverity) else str(severity).lower(),
        location=str(_location_snapshot or ""),
    )

    # Footer carries the canonical AlertID + history info so every
    # subscriber sees the same "Alert #1234 / × N occurrences" line.
    history_footer = format_alert_history_footer(
        _hist_count, _hist_first_seen, _now_str,
        history_id=(history_record or {}).get("id"),
    )

    # ── Per-alert mute check (D2) ────────────────────────────────
    # Operators can mute a specific alert_history row for N hours so
    # known/in-progress issues stop pinging.  We still upsert the
    # history above so the dashboard shows the alert is still active —
    # we just skip Telegram delivery.  CRITICAL alerts ignore mutes
    # because something genuinely on fire should not stay quiet.
    if (
        severity != AlertSeverity.CRITICAL
        and history_record
        and await tenant.is_alert_history_muted(history_record["id"], account_id)
    ):
        logger.info(
            "alert muted: acct=%d type=%s vid=%s history_id=%s — skipping delivery",
            account_id, alert_type, vid, history_record["id"],
        )
        return

    # ── Bulk pre-fetch acks once ────────────────────────────────
    # For S subscribers, the per-subscriber path used to do 1-2 ack
    # lookups each = 2 S DB round-trips. Pre-fetching here turns that
    # into 2 chunked queries regardless of S.
    import time as _time
    from infra import observability as _obs
    timings: dict[str, float] = {}
    _send_t0 = _time.perf_counter()
    sub_telegram_ids = [s.telegram_id for s in subscribers if s.telegram_id]
    with _obs.time_block(timings, "bulk_acks"):
        try:
            bulk_active_acks = await tenant.get_active_vehicle_acks_bulk(
                account_id, vid, sub_telegram_ids,
            )
        except Exception:
            logger.debug("bulk_active_acks lookup failed", exc_info=True)
            bulk_active_acks = {}
        try:
            bulk_info_acks = await tenant.get_info_alert_acks_bulk(
                account_id, vid, alert_type, sub_telegram_ids,
            )
        except Exception:
            logger.debug("bulk_info_acks lookup failed", exc_info=True)
            bulk_info_acks = {}

    fanout_sem = asyncio.Semaphore(_ALERT_FANOUT_CONCURRENCY)

    async def _send_to_one_sub(sub):
      async with fanout_sem:
        # Driver: only alert for their own truck.
        # Substring match (case-insensitive) to mirror
        # ``filter_alerts_by_access`` in alerting/service.py — the API +
        # miniapp use the same shape, so the bot and dashboard stay
        # consistent for names like "Truck 105" vs assignment "105".
        if sub.role == Role.DRIVER and sub.truck_num:
            if sub.truck_num.lower() not in vname.lower():
                return

        # DND: queue non-critical alerts during quiet hours
        if not bypasses_dnd and sub.is_in_quiet_hours():
            await tenant.queue_dnd_alert(
                account_id=account_id,
                telegram_id=sub.telegram_id,
                alert_type=alert_type,
                vehicle_name=vname,
                alert_text=alert_text,
            )
            return

        try:
            # Old "delete prior INFO message" hop has been folded into
            # the INFO branch below — it now tries edit-in-place first
            # and only falls back to delete+send when the edit fails.

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
                    vmsg = await bot_app.bot.send_video(
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
                    pmsg = await bot_app.bot.send_photo(
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
                # ── Edit-in-place if there's an existing active alert
                #    for this (subscriber, alert_type, vehicle).
                # Production behaviour was: every re-fire deleted the
                # previous Telegram message and sent a fresh one — which
                # produced the "722 messages in one shift" complaint.
                # We now look up the most recent un-acked delivery for
                # this triple and try to edit it in place: same message
                # in the chat, no notification ping, just a fresh
                # occurrence count in the footer.
                #
                # Edit is skipped (and we fall back to send-new) when:
                #   - no prior active ack exists (this is a fresh alert)
                #   - the prior message attached media (video / photo);
                #     Telegram doesn't let us swap a text msg in/out of
                #     a media one
                #   - the edit fails (user deleted the message, > 48 h
                #     old, ParseMode mismatch, etc.)
                old_acks = bulk_active_acks.get(sub.telegram_id, [])
                same_type_acks = [a for a in old_acks if a.get("alert_type") == alert_type]
                edited_in_place = False
                if same_type_acks and not video_url and not photo_bytes:
                    most_recent = max(
                        same_type_acks,
                        key=lambda a: a.get("created_at") or "",
                    )
                    edit_ack_id = most_recent["id"]
                    edit_msg_id = most_recent.get("message_id")
                    edit_chat_id = most_recent.get("chat_id")
                    if edit_msg_id and edit_chat_id:
                        try:
                            await bot_app.bot.edit_message_text(
                                chat_id=edit_chat_id,
                                message_id=edit_msg_id,
                                text=send_text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=build_alert_keyboard(
                                    severity, co, vname,
                                    ack_id=edit_ack_id,
                                    alert_type=alert_type,
                                    vehicle_id=vid,
                                    event_id=event_id,
                                    event_time=event_time,
                                ),
                            )
                            edited_in_place = True
                        except TGBadRequest as e:
                            err = str(e).lower()
                            if "not modified" in err:
                                # Identical text — counts as success
                                edited_in_place = True
                            else:
                                logger.debug(
                                    "edit-in-place failed for user %s msg %s: %s — falling back to send-new",
                                    sub.telegram_id, edit_msg_id, e,
                                )
                        except TelegramError as e:
                            logger.debug(
                                "Telegram error during edit for user %s msg %s: %s",
                                sub.telegram_id, edit_msg_id, e,
                            )

                # ── Escalation thresholds ─────────────────────────
                # Most re-fires should silently edit in place (above).
                # But every "push-worthy" milestone (occurrence 1, 10,
                # 25, 50, 100, 250, 500, 1000…) we force a fresh
                # message so the user gets a visible nudge: "This has
                # now fired N times — please look at it."
                # If we already edited above, but the milestone wants a
                # push, undo the edit decision and fall through to
                # delete-old + send-new.
                if edited_in_place and _hist_count in _ESCALATION_OCCURRENCES:
                    edited_in_place = False
                    logger.info(
                        "alert escalation milestone: acct=%d type=%s vid=%s n=%d — sending fresh",
                        account_id, alert_type, vid, _hist_count,
                    )

                if edited_in_place:
                    # Supersede the OTHER same-type acks (defensive — usually empty).
                    other_ids = [
                        a["id"] for a in same_type_acks
                        if a["id"] != edit_ack_id
                    ]
                    if other_ids:
                        await tenant.supersede_alert_acks_bulk(other_ids)
                else:
                    # Fallback: legacy delete-old + send-new.  Used on
                    # first-occurrence (no prior ack) AND on edit-failure.
                    if same_type_acks:
                        await tenant.supersede_alert_acks_bulk(
                            [a["id"] for a in same_type_acks],
                        )
                    for old_ack in same_type_acks:
                        if old_ack.get("message_id") and old_ack.get("chat_id"):
                            try:
                                await bot_app.bot.delete_message(
                                    chat_id=old_ack["chat_id"],
                                    message_id=old_ack["message_id"],
                                )
                            except Exception:
                                logger.debug("Failed to delete superseded alert msg %s",
                                             old_ack["message_id"])

                    basic_kb = build_alert_keyboard(
                        severity, co, vname, alert_type=alert_type,
                        vehicle_id=vid, event_id=event_id, event_time=event_time,
                    )
                    msg = await bot_app.bot.send_message(
                        chat_id=sub.telegram_id,
                        text=send_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=basic_kb,
                        reply_to_message_id=reply_to,
                    )
                    alert_key = f"{co}:{vid}:{alert_key_detail}"
                    ack_id = await tenant.create_alert_ack(
                        account_id=account_id,
                        alert_type=alert_type,
                        vehicle_id=vid,
                        vehicle_name=vname,
                        alert_key=alert_key,
                        message_id=msg.message_id,
                        chat_id=sub.telegram_id,
                        sent_to=sub.telegram_id,
                    )
                    # Swap the keyboard now that we have an ack_id
                    ack_kb = build_alert_keyboard(
                        severity, co, vname, ack_id=ack_id, alert_type=alert_type,
                        vehicle_id=vid, event_id=event_id, event_time=event_time,
                    )
                    await bot_app.bot.edit_message_reply_markup(
                        chat_id=sub.telegram_id,
                        message_id=msg.message_id,
                        reply_markup=ack_kb,
                    )
            else:
                # INFO — try edit-in-place on the prior INFO message
                # (same recipient, same vehicle+type) so re-fires update
                # the live status row instead of pinging again.  If
                # there's no prior message or the edit fails, fall back
                # to send-new just like the CRITICAL/WARNING branch.
                old_info = bulk_info_acks.get(sub.telegram_id)
                edited_info = False
                if old_info and not video_url and not photo_bytes:
                    msg_id = old_info.get("message_id")
                    chat_id = old_info.get("chat_id")
                    if msg_id and chat_id:
                        try:
                            await bot_app.bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=msg_id,
                                text=send_text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=build_alert_keyboard(
                                    severity, co, vname,
                                    alert_type=alert_type,
                                    vehicle_id=vid,
                                    event_id=event_id,
                                    event_time=event_time,
                                ),
                            )
                            edited_info = True
                        except TGBadRequest as e:
                            if "not modified" in str(e).lower():
                                edited_info = True
                            else:
                                logger.debug(
                                    "INFO edit-in-place failed for user %s msg %s: %s",
                                    sub.telegram_id, msg_id, e,
                                )
                        except TelegramError as e:
                            logger.debug(
                                "Telegram error during INFO edit for user %s msg %s: %s",
                                sub.telegram_id, msg_id, e,
                            )

                if not edited_info:
                    # Fallback: existing path (delete prior, send fresh).
                    if old_info and old_info.get("message_id"):
                        try:
                            await bot_app.bot.delete_message(
                                chat_id=sub.telegram_id,
                                message_id=old_info["message_id"],
                            )
                        except Exception:
                            logger.debug(
                                "Failed to delete old INFO msg %s for user %s",
                                old_info["message_id"], sub.telegram_id,
                            )
                    basic_kb = build_alert_keyboard(
                        severity, co, vname, alert_type=alert_type,
                        vehicle_id=vid, event_id=event_id, event_time=event_time,
                    )
                    msg = await bot_app.bot.send_message(
                        chat_id=sub.telegram_id,
                        text=send_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=basic_kb,
                        reply_to_message_id=reply_to,
                    )
                    alert_key = f"{co}:{vid}:{alert_key_detail}"
                    await tenant.create_info_alert_ack(
                        account_id=account_id,
                        alert_type=alert_type,
                        vehicle_id=vid,
                        vehicle_name=vname,
                        alert_key=alert_key,
                        message_id=msg.message_id,
                        chat_id=sub.telegram_id,
                        sent_to=sub.telegram_id,
                    )
        except Exception as e:
            logger.error("%s alert delivery failed for user %s (account %d): %s",
                         alert_type, sub.telegram_id, account_id, e, exc_info=True)

    # Fan out to subscribers in parallel — bounded by fanout_sem so we
    # stay under Telegram's ~30 msg/sec global rate limit. gather()
    # captures any per-sub exception (already logged inside) so one
    # bad recipient never sinks the rest of the cohort.
    if subscribers:
        with _obs.time_block(timings, "fanout"):
            await asyncio.gather(
                *(_send_to_one_sub(s) for s in subscribers),
                return_exceptions=True,
            )

    timings["total"] = round(
        (_time.perf_counter() - _send_t0) * 1000, 1,
    )
    logger.info(
        "send_alert acct=%d type=%s severity=%s subs=%d timings_ms=%s",
        account_id, alert_type, severity.value, len(subscribers), timings,
    )


async def is_vehicle_suppressed(account_id: int, vehicle_name: str) -> bool:
    """Check if alerts should be suppressed for a vehicle in active maintenance."""
    try:
        tenant = await get_tenant_db(account_id)
        return await tenant.is_vehicle_in_maintenance(account_id, vehicle_name)
    except Exception:
        logger.debug("Maintenance suppression check failed for %s (account %d)",
                     vehicle_name, account_id)
        return False
