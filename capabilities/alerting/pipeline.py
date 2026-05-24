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
from infra.platform import get_platform_db


# Maps each alert_type the pipeline knows about to the canonical
# alert_routing key.  Single-source-of-truth lives in storage's
# ``ALERT_TYPE_KEYS`` tuple; this table only translates the verbose
# pipeline names to those keys so we don't have to rename every
# call site at once.  If a name isn't in the map we treat the alert
# as un-routable (falls back to DM) — safe default.
_PIPELINE_TO_ROUTE_KEY: dict[str, str] = {
    "fault":        "faults",
    "faults":       "faults",
    "health":       "health",
    "fuel":         "fuel",
    "events":       "events",
    "event":        "events",
    "camera":       "camera",
    "parking":      "parking",
    "geofence":     "geofence",
    "scorecard":    "scorecard",
    "maintenance":  "maintenance",
    "documents":    "documents",
    "doc_expiry":   "documents",
    "system":       "system",
    "samsara_sync": "system",
    "reescalate":   "system",
}

logger = logging.getLogger("bot")


# ─────────────────────────────────────────────────────────────────────
# Per-topic post lock — keeps photo+text pairs atomic in forum topics.
#
# Why: parking and camera alerts post a *photo* (the map / snapshot) and
# then a *text reply* threaded to it.  Multiple vehicles run their
# alert pipelines in parallel (``asyncio.gather`` over vehicles inside
# the per-account check loop), so without serialization the two
# send_photo / send_message calls from different vehicles interleave
# on the Telegram API:
#
#   vehicle A: send_photo  → photoA
#   vehicle B: send_photo  → photoB
#   vehicle A: send_message (replies to photoA)
#   vehicle B: send_message (replies to photoB)
#
# The reply links are intact, but the chat renders top-to-bottom as
# ``photoA, photoB, textA, textB`` instead of ``photoA, textA, photoB,
# textB`` — operators see two photos with no captions, then two
# captions with no photos and can't tell which map belongs to which
# alert.  The lock is per ``(account_id, alert_type)`` so cross-account
# and cross-type traffic is still parallel; only within one topic do
# we serialize.
_TOPIC_POST_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}


def _topic_post_lock(account_id: int, alert_type: str) -> asyncio.Lock:
    """Get or create the per-topic post lock for an account."""
    key = (account_id, alert_type)
    lock = _TOPIC_POST_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _TOPIC_POST_LOCKS[key] = lock
    return lock


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
import os as _os  # noqa: E402 — local config import intentionally follows the documenting comment block above
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

# Forum-routing toggle.  When True (default), send_alert() consults
# alert_routing for the account; when a row exists it posts to the
# group topic instead of DM-fanning to every subscriber.  Flip via
# FORUM_ROUTING_ENABLED=0 to force the legacy DM-only path during a
# rollout incident.
_FORUM_ROUTING_ENABLED = _os.getenv("FORUM_ROUTING_ENABLED", "1") not in ("0", "false", "False")

# When a CRITICAL alert posts to a group topic we still DM the
# subscribers so the on-call doesn't miss a fire while the group is
# muted.  Set FORUM_CRITICAL_MIRROR=0 to send criticals to the group
# only (relies entirely on each user's per-topic notifications being
# loud enough).
_FORUM_CRITICAL_MIRROR = _os.getenv("FORUM_CRITICAL_MIRROR", "1") not in ("0", "false", "False")


def build_alert_keyboard(
    severity: AlertSeverity,
    co: str,
    vehicle_name: str,
    ack_id: int | None = None,
    alert_type: str = "fault",
    vehicle_id: str = "",
    event_id: str = "",
    event_time: str = "",
    lang: str | None = None,
    for_group: bool = False,
) -> InlineKeyboardMarkup:
    """Build keyboard for an alert message based on severity.

    CRITICAL/WARNING with ack_id → ACK + AI Diagnose + Open in Samsara + View Truck
    CRITICAL/WARNING without ack_id → AI Diagnose + Open in Samsara + View Truck (pre-ACK send)
    INFO → Open in Samsara + View Truck only

    *lang* picks the localised button labels (so a Russian-speaking
    driver sees Russian buttons even when other subscribers on the same
    alert get English). When omitted the per-request context variable
    set by ``set_lang()`` is used, otherwise English.

    *for_group* tightens the button set for forum-topic posts where
    the message is visible to many shifts at once.  Buttons whose
    callback rewrites the message in place (AI Diagnose, View Truck,
    Main Menu) would erase the alert for every subsequent reader,
    so we drop them on group posts.  Ack stays (edits in place to
    show "Acknowledged by X" — the intended group-wide effect) and
    Open in Samsara stays (URL-only — opens per-user in a browser,
    never touches the group message).
    """
    from capabilities.localization.i18n import t
    rows: list[list[InlineKeyboardButton]] = []

    if severity in (AlertSeverity.CRITICAL, AlertSeverity.WARNING):
        if ack_id is not None:
            rows.append([InlineKeyboardButton(
                t("alert_actions.acknowledge", lang=lang),
                callback_data=f"ack_alert_{ack_id}",
            )])
        # AI Diagnose: callback rewrites the message in place.  Safe in
        # 1:1 DM (the requester is the only viewer), unsafe in a forum
        # topic (other shifts would lose the alert).  Skip in group.
        if not for_group:
            ai_diag_cb = f"ai_diag_{alert_type}_{co}_{vehicle_name}"
            if ack_id is not None:
                ai_diag_cb += f":{ack_id}"
            rows.append([InlineKeyboardButton(
                t("alert_actions.ai_diagnose", lang=lang),
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
            t("alert_actions.open_in_samsara", lang=lang),
            url=samsara_url,
        )])

    # View Truck + Main Menu both rewrite the message via callback.
    # Same group-safety reasoning as AI Diagnose above — only render
    # them in 1:1 DM where the requester is the sole viewer.
    if for_group:
        return InlineKeyboardMarkup(rows)

    truck_cb = f"covehicle_{co}_{vehicle_name}"
    if ack_id is not None:
        truck_cb += f":{ack_id}"
    rows.append([InlineKeyboardButton(
        t("vehicle.view_vehicle", lang=lang, name=vehicle_name),
        callback_data=truck_cb,
    )])
    rows.append([InlineKeyboardButton(
        t("menu.back_main", lang=lang),
        callback_data="cmd_menu",
    )])

    return InlineKeyboardMarkup(rows)


async def post_alert_to_topic(
    bot_app: Application,
    *,
    account_id: int,
    alert_type: str,
    text: str,
    parse_mode: str = ParseMode.HTML,
    reply_markup=None,
    photo_bytes: bytes | None = None,
    video_url: str = "",
) -> bool:
    """Public group-post helper for alert paths that don't run through
    ``send_alert()`` — maintenance overdue, doc expiry, Samsara sync,
    scorecard drop, camera health.  Each of those has its own
    per-subscriber DM loop; they call this *first* and skip the DM
    loop when it returns True.

    Returns False (caller continues with DM fanout) when:
      • forum routing is disabled globally
      • the account has no route for this alert_type
      • posting failed (route auto-marked inactive on topic-deleted)

    Returns True (caller skips DM fanout) when the group post landed.
    """
    if not _FORUM_ROUTING_ENABLED:
        return False
    route_key = _PIPELINE_TO_ROUTE_KEY.get(alert_type)
    if route_key is None:
        return False

    db = get_platform_db()
    route = await db.get_alert_route(account_id, route_key)
    if route is None:
        return False

    chat_id = route.chat_id
    thread_id = route.message_thread_id

    try:
        # Optional media replied-to-by the text.  Same pattern as the
        # full send_alert() path so users see consistent layout
        # whether the alert ran through send_alert or one of the
        # bespoke check_* loops.
        reply_to: int | None = None
        if video_url:
            try:
                vmsg = await bot_app.bot.send_video(
                    chat_id=chat_id, message_thread_id=thread_id,
                    video=video_url, read_timeout=30, write_timeout=30,
                )
                reply_to = vmsg.message_id
            except Exception as ve:
                logger.debug("Forum video failed acct=%d type=%s: %s",
                             account_id, alert_type, ve)
        elif photo_bytes:
            try:
                import io as _io
                pmsg = await bot_app.bot.send_photo(
                    chat_id=chat_id, message_thread_id=thread_id,
                    photo=_io.BytesIO(photo_bytes),
                    read_timeout=15, write_timeout=15,
                )
                reply_to = pmsg.message_id
            except Exception as pe:
                logger.debug("Forum photo failed acct=%d type=%s: %s",
                             account_id, alert_type, pe)

        await bot_app.bot.send_message(
            chat_id=chat_id, message_thread_id=thread_id,
            text=text, parse_mode=parse_mode,
            reply_markup=reply_markup, reply_to_message_id=reply_to,
        )
        logger.info(
            "forum alert posted (lite) acct=%d type=%s chat=%d thread=%d",
            account_id, alert_type, chat_id, thread_id,
        )
        return True
    except Exception as e:
        # Drift detection — same heuristic as the full path.
        msg = str(e).lower()
        if "topic" in msg and ("delete" in msg or "not found" in msg or "closed" in msg):
            try:
                await db.set_alert_route_active(account_id, route_key, False)
                logger.warning(
                    "Forum route auto-disabled (drift): acct=%d type=%s — %s",
                    account_id, route_key, e,
                )
            except Exception:
                logger.exception("Failed to disable broken route")
        else:
            logger.warning(
                "Forum post (lite) failed acct=%d type=%s: %s",
                account_id, alert_type, e,
            )
        return False


async def _try_post_to_topic(
    bot_app: Application,
    *,
    account_id: int,
    alert_type: str,
    severity: AlertSeverity,
    co: str,
    vehicle_id: str,
    vehicle_name: str,
    send_text: str,
    video_url: str,
    photo_bytes: bytes | None,
    event_id: str,
    event_time: str,
) -> bool:
    """If a forum route exists for ``(account_id, alert_type)`` post the
    alert there and return True.  Returns False when no route is
    configured, the feature is disabled, or posting failed (in which
    case the caller falls back to the DM fanout path).

    The route is auto-marked inactive on ``Bad Request: topic_*`` —
    that's how the system self-heals when an admin deletes a topic
    out from under us.
    """
    if not _FORUM_ROUTING_ENABLED:
        return False

    route_key = _PIPELINE_TO_ROUTE_KEY.get(alert_type)
    if not route_key:
        return False

    db = get_platform_db()
    route = await db.get_alert_route(account_id, route_key)
    if route is None:
        return False

    chat_id = route.chat_id
    thread_id = route.message_thread_id

    try:
        # Hold the per-topic post lock for the duration of the
        # photo+text pair so concurrent vehicles in the same topic
        # don't interleave (see ``_TOPIC_POST_LOCKS`` rationale).
        async with _topic_post_lock(account_id, alert_type):
            # Send the leading media first (if any) so the text reply
            # threads to it, mirroring the DM-fanout layout.
            reply_to: int | None = None
            if video_url:
                try:
                    vmsg = await bot_app.bot.send_video(
                        chat_id=chat_id,
                        message_thread_id=thread_id,
                        video=video_url,
                        caption=f"🎥 {vehicle_name}",
                        read_timeout=30,
                        write_timeout=30,
                    )
                    reply_to = vmsg.message_id
                except Exception as ve:
                    logger.debug("Forum video send failed for %s: %s", vehicle_name, ve)
            elif photo_bytes:
                try:
                    import io as _io
                    pmsg = await bot_app.bot.send_photo(
                        chat_id=chat_id,
                        message_thread_id=thread_id,
                        photo=_io.BytesIO(photo_bytes),
                        caption=f"📍 Parking location — #{vehicle_name}",
                        read_timeout=15,
                        write_timeout=15,
                    )
                    reply_to = pmsg.message_id
                except Exception as pe:
                    logger.debug("Forum photo send failed for %s: %s", vehicle_name, pe)

            # Default-language buttons — the group is a shared surface,
            # we can't pick one user's language without choosing badly
            # for everyone else.  Future enhancement: per-account default
            # language stored on forum_groups.
            basic_kb = build_alert_keyboard(
                severity, co, vehicle_name, alert_type=alert_type,
                vehicle_id=vehicle_id, event_id=event_id, event_time=event_time,
                lang="en",
                for_group=True,
            )
            msg = await bot_app.bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=send_text,
                parse_mode=ParseMode.HTML,
                reply_markup=basic_kb,
                reply_to_message_id=reply_to,
            )

        # Record one alert_ack for the group post so the existing
        # callback-router can resolve the ack by id.  ``sent_to=0``
        # is the sentinel meaning "group post, no specific user" —
        # the callback handler reads the calling user's telegram id
        # from update.effective_user instead.
        tenant = await get_tenant_db(account_id)
        alert_key = f"{co}:{vehicle_id}:group"
        needs_ack = severity in (AlertSeverity.CRITICAL, AlertSeverity.WARNING)
        if needs_ack:
            ack_id = await tenant.create_alert_ack(
                account_id=account_id,
                alert_type=alert_type,
                vehicle_id=vehicle_id,
                vehicle_name=vehicle_name,
                alert_key=alert_key,
                message_id=msg.message_id,
                chat_id=chat_id,
                sent_to=0,
            )
            ack_kb = build_alert_keyboard(
                severity, co, vehicle_name, ack_id=ack_id, alert_type=alert_type,
                vehicle_id=vehicle_id, event_id=event_id, event_time=event_time,
                lang="en",
                for_group=True,
            )
            try:
                await bot_app.bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=msg.message_id,
                    reply_markup=ack_kb,
                )
            except Exception as e:
                logger.debug("Forum ack-keyboard edit failed: %s", e)
        else:
            await tenant.create_info_alert_ack(
                account_id=account_id,
                alert_type=alert_type,
                vehicle_id=vehicle_id,
                vehicle_name=vehicle_name,
                alert_key=alert_key,
                message_id=msg.message_id,
                chat_id=chat_id,
                sent_to=0,
            )

        logger.info(
            "forum alert posted acct=%d type=%s severity=%s chat=%d thread=%d",
            account_id, alert_type, severity.value, chat_id, thread_id,
        )
        return True

    except Exception as e:
        # Drift detection: Telegram returns "Bad Request: topic was
        # deleted" / "message thread not found" when an admin removed
        # the topic.  Soft-disable the route so subsequent alerts
        # fall to DM instead of repeatedly failing.
        msg = str(e).lower()
        if "topic" in msg and ("delete" in msg or "not found" in msg or "closed" in msg):
            try:
                db = get_platform_db()
                await db.set_alert_route_active(account_id, route_key, False)
                logger.warning(
                    "Forum route disabled (drift): acct=%d type=%s — %s",
                    account_id, route_key, e,
                )
            except Exception:
                logger.exception("Failed to disable broken route")
        else:
            logger.warning(
                "Forum post failed acct=%d type=%s — falling back to DM: %s",
                account_id, alert_type, e,
            )
        return False


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
    alert_subkey: str = "",
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

    *alert_subkey* differentiates subtypes within an alert class so each
    gets its own occurrence count.  For events the caller passes the
    event_type (rollingStop, braking, …); fault / fuel / health leave
    it empty and keep their pooled-per-vehicle dedup behavior.
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

    # ── Per-fire alert_history row (no dedup) ─────────────────────
    # Every fire creates its own row so the AlertID surfaced in the
    # message is uniquely addressable across the bot, dashboard and
    # mini-app — a dispatcher quoting "Alert #1234" can find exactly
    # that delivery instead of the deduped logical alert that
    # collapsed many fires under one ID.  We disable the dedup by
    # threading a timestamp + caller-provided detail into the
    # alert_subkey; the UNIQUE(account, alert_type, vehicle_id,
    # alert_subkey) constraint then never matches a prior row.
    _now_str = datetime.now(timezone.utc).isoformat()
    if not alert_subkey:
        alert_subkey = f"{_now_str}:{alert_key_detail}"
    else:
        # Caller already passed something (e.g. event_type for events).
        # Append the timestamp + detail so the combination is unique
        # per fire while keeping the subtype searchable as a prefix.
        alert_subkey = f"{alert_subkey}:{_now_str}:{alert_key_detail}"
    _hist_count = 1                      # always 1 — no dedup
    _hist_first_seen = ""                # always fresh row

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
        alert_subkey=alert_subkey,
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

    # Send-stage timing kept around even after the bulk-ack pre-fetch
    # was retired — the surrounding fanout still wants a wall-clock
    # baseline so the total-fanout metric stays comparable.
    import time as _time
    from infra import observability as _obs
    timings: dict[str, float] = {}
    _send_t0 = _time.perf_counter()

    # ── Forum routing branch ─────────────────────────────────────
    # When this account has a topic mapped for this alert_type we
    # post once to the group instead of fanning out a DM to every
    # subscriber.  Returns False (and falls through to the DM path)
    # whenever no route exists, the feature is disabled, or posting
    # failed — so unconfigured accounts stay on the legacy behavior.
    #
    # AI inclusion is per-alert-type (single source of truth for the
    # whole group).  Two delivery shapes the toggle has to handle:
    #
    #   • Fault / Health: AI text is appended via ``ai_note``.
    #     Easy — just skip the append.
    #   • Parking: AI text is baked into ``alert_text`` by
    #     ``capabilities/parking/formatting.py`` at format time.
    #     We strip the `🤖 AI Analysis:` block from the rendered
    #     text before posting to the topic when the toggle is off.
    #
    # Per-user ``ai_*`` toggles still drive each subscriber's DM
    # (CRITICAL mirror and non-routed accounts) — those are personal
    # preferences and stay separate from the group-wide decision.
    _route_key = _PIPELINE_TO_ROUTE_KEY.get(alert_type, alert_type)
    _ai_setting = await tenant.get_account_setting(
        account_id, f"forum_ai.{_route_key}", default="1",
    )
    _include_ai_in_group = _ai_setting != "0"

    _group_alert_text = alert_text
    if _route_key == "parking" and not _include_ai_in_group:
        # Strip the inline AI Analysis block: from "🤖 AI Analysis:"
        # up to (but not including) the next blank-line section break.
        import re as _re
        _group_alert_text = _re.sub(
            r"\n\s*🤖\s*<b>AI Analysis:</b>.*?(?=\n\n|\Z)",
            "",
            _group_alert_text,
            flags=_re.DOTALL,
        )

    _group_text = (
        _group_alert_text
        + ((ai_note or "") if _include_ai_in_group else "")
        + history_footer
    )
    posted_to_topic = False
    with _obs.time_block(timings, "group_post"):
        posted_to_topic = await _try_post_to_topic(
            bot_app,
            account_id=account_id,
            alert_type=alert_type,
            severity=severity,
            co=co,
            vehicle_id=vid,
            vehicle_name=vname,
            send_text=_group_text,
            video_url=video_url,
            photo_bytes=photo_bytes,
            event_id=event_id,
            event_time=event_time,
        )

    # If the group post succeeded AND this isn't a CRITICAL we're
    # done — no DM fanout.  CRITICAL alerts still DM every
    # subscriber (mirror) when FORUM_CRITICAL_MIRROR is on, so
    # on-call hears the phone even with the group muted.
    if posted_to_topic and not (
        severity == AlertSeverity.CRITICAL and _FORUM_CRITICAL_MIRROR
    ):
        timings["total"] = round(
            (_time.perf_counter() - _send_t0) * 1000, 1,
        )
        logger.info(
            "send_alert acct=%d type=%s severity=%s route=group timings_ms=%s",
            account_id, alert_type, severity.value, timings,
        )
        return

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

        # DND: queue non-critical alerts during quiet hours.  SSoT is
        # ``is_user_dnd_active`` — checks per-user override first, falls
        # back to derived-from-Working-Hours for the user's role.
        from capabilities.alerting.dnd import is_user_dnd_active
        if not bypasses_dnd and await is_user_dnd_active(sub, tenant):
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
                # ── Per-fire model: every alert is unique, always send-new.
                # Edit-in-place and delete-old are intentionally OFF —
                # each fire creates its own alert_history row with its
                # own AlertID, so there is no "previous version" to
                # collapse onto.  Users see a discrete message per
                # event, which is the standard PagerDuty / Datadog /
                # Samsara model and matches what dispatchers asked
                # for ("each alert should have its own ID").
                if False:
                    # Legacy edit-in-place branch (kept for diff context;
                    # never reached because the conditional is False).
                    pass
                else:
                    sub_lang = getattr(sub, "language", None) or "en"
                    basic_kb = build_alert_keyboard(
                        severity, co, vname, alert_type=alert_type,
                        vehicle_id=vid, event_id=event_id, event_time=event_time,
                        lang=sub_lang,
                    )
                    msg = await bot_app.bot.send_message(
                        chat_id=sub.telegram_id,
                        text=send_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=basic_kb,
                        reply_to_message_id=reply_to,
                    )
                    # ── Per-fire model: keep prior alert messages
                    # untouched.  Each fire is its own AlertID and
                    # users can refer back to earlier ones in their
                    # chat history; the bot no longer rewrites or
                    # deletes anything.
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
                        lang=sub_lang,
                    )
                    await bot_app.bot.edit_message_reply_markup(
                        chat_id=sub.telegram_id,
                        message_id=msg.message_id,
                        reply_markup=ack_kb,
                    )
            else:
                # INFO — same per-fire model as CRITICAL/WARNING: each
                # fire posts a fresh message, no edit-in-place, no
                # delete-prior.  The dispatcher keeps each event in
                # their chat history with its own AlertID.
                sub_lang = getattr(sub, "language", None) or "en"
                basic_kb = build_alert_keyboard(
                    severity, co, vname, alert_type=alert_type,
                    vehicle_id=vid, event_id=event_id, event_time=event_time,
                    lang=sub_lang,
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
