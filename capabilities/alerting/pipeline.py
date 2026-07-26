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
from typing import Optional
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
import re as _re_html
import html as _html_mod
from infra.config import (
    FAULT_ALERT_COOLDOWN_HOURS,
    HEALTH_ALERT_COOLDOWN_HOURS,
)
from infra.services import get_tenant_db
from infra.platform import get_platform_db
from infra.observability import record_alert_flood


async def _tg_send_with_retry(send, *, what: str):
    """Run one ``bot.send_*`` coroutine factory, retrying ONCE after
    Telegram flood control.

    The Application-level AIORateLimiter queues sends under the global
    limits, but the per-group window (~20 msg/min) can still return
    ``RetryAfter`` during an alert burst — without this, that alert was
    silently lost.  ``send`` is a zero-arg callable returning a fresh
    coroutine (a lambda around the send call) so the retry re-issues
    the request instead of awaiting a spent coroutine.  A second
    ``RetryAfter`` propagates to the caller's existing failure handling
    and is counted as ``dropped``.
    """
    from telegram.error import RetryAfter
    try:
        return await send()
    except RetryAfter as e:
        delay = float(getattr(e, "retry_after", 3)) + 0.5
        record_alert_flood("retried")
        logger.warning("Telegram flood control on %s — retrying in %.1fs", what, delay)
        await asyncio.sleep(delay)
        try:
            result = await send()
            record_alert_flood("delivered_after_retry")
            return result
        except RetryAfter:
            record_alert_flood("dropped")
            logger.error("Telegram flood control persisted on %s — send dropped", what)
            raise


def _pick_sender(primary: Application, account_id: int, target) -> Application:
    """The Application that posts THIS target: the role's Sub bot
    when one is attached and running, else the account's primary bot.

    The owner_admin AGGREGATE cross-post always uses the primary bot —
    it's the account-level digest, not a role surface.  Fail-open
    toward the primary: an attached-but-down Sub bot never eats alerts.
    """
    if getattr(target, "is_aggregate", False):
        return primary
    persona = getattr(target, "persona", "") or ""
    if not persona:
        return primary
    from infra.bot_registry import get_sender_for_persona
    return get_sender_for_persona(account_id, persona) or primary


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


# Telegram media-caption limit — applies to both ``send_photo`` and
# ``send_video``.  Used by the photo+text merge path to decide whether
# the assembled alert body fits inside one caption (preferred — one
# coherent message) or has to fall back to the legacy two-message
# pattern (photo, then text reply).  We leave a small safety margin
# under the documented 1024 so HTML entity expansion or trailing
# history-footer rows don't push us over.
_CAPTION_CHAR_LIMIT = 1024


def _caption_fits(text: str) -> bool:
    """Whether ``text`` is short enough to sit inside one media caption."""
    return len(text or "") <= _CAPTION_CHAR_LIMIT


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
# alert.
#
# The lock is keyed by the Telegram *destination* (chat_id,
# message_thread_id) rather than (account_id, alert_type) because
# per_persona_groups mode collapses several alert_types onto one
# persona chat (e.g. parking + fuel + geofence all land in the
# dispatcher chat).  Keying by destination preserves legacy
# single_group behaviour byte-for-byte (each forum topic has a unique
# (chat_id, thread_id) pair, same lock-granularity as before) AND
# correctly serializes per-persona-mode posts to the same persona
# chat across different alert_types — preventing photo+text
# interleaving for the CRITICAL aggregate cross-post too.
_TOPIC_POST_LOCKS: dict[tuple[int, Optional[int]], asyncio.Lock] = {}


def _topic_post_lock(
    chat_id: int, message_thread_id: Optional[int] = None,
) -> asyncio.Lock:
    """Get or create the lock for a Telegram destination."""
    key = (chat_id, message_thread_id)
    lock = _TOPIC_POST_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _TOPIC_POST_LOCKS[key] = lock
    return lock


async def _compose_persona_critical_mention(
    *, account_id: int, target, severity_is_critical: bool,
) -> str:
    """Return an HTML mention prefix for CRITICAL persona-mode primary
    targets, or "" otherwise.

    Shared by ``_post_one_target`` (send_alert path) and
    ``post_alert_to_topic`` (the lite per-check helpers — camera,
    parking, doc-expiry, etc.) so a CRITICAL camera digest in
    per_persona_groups mode pings the on-shift safety operators the
    same way a CRITICAL send_alert event does.

    Mentions never go on the aggregate cross-post (the owner_admin
    group's role is the cross-cutting digest, not the pager) and never
    in legacy single_group mode (topics have no on-shift state).

    Compose failures (DB outage, etc.) return "" so the alert still
    ships — operators get an unattributed post, not a dropped one.
    """
    if not target.persona or target.is_aggregate or not severity_is_critical:
        return ""
    try:
        from .on_shift import users_on_shift_for_persona, format_mentions
        on_shift_users = await users_on_shift_for_persona(
            account_id, target.persona,
        )
        return format_mentions(on_shift_users)
    except Exception as me:
        logger.debug(
            "On-shift mention compose failed acct=%d persona=%s: %s",
            account_id, target.persona, me,
        )
        return ""


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

# Live cross-channel fanout.  Telegram delivery is always on its own
# proven path (DM fanout + group topics below).  When this flag is on,
# every alert that survives the suppression/mute gates ALSO hands the
# same semantic event to the source-agnostic Notifications service,
# which delivers it over the *new* channels a user opted into — email
# and web push — using the per-user notification matrix.  Ships OFF so
# an account switches these channels on deliberately; Telegram is
# untouched either way.  This is the N2 "switch-on" for the
# notifications spine built in N1.
_NOTIFICATIONS_LIVE_DISPATCH = _os.getenv(
    "NOTIFICATIONS_LIVE_DISPATCH", "0") not in ("0", "false", "False")

# ``alert_text`` is composed as Telegram HTML (sent with ParseMode.HTML)
# — it carries tags (<b>, <code>, <a>) and HTML entities.  The
# Notifications ``NotificationContent.body`` must be RAW plain text, so
# each channel escapes exactly once at render time.  Strip tags and
# unescape entities on the way across the seam.
_ALERT_TAG_RE = _re_html.compile(r"<[^>]+>")


def _strip_alert_html(text: str) -> str:
    return _html_mod.unescape(_ALERT_TAG_RE.sub("", text or "")).strip()


async def dispatch_new_channels(
    *,
    account_id: int,
    alert_type: str,
    severity: "AlertSeverity",
    vehicle_name: str,
    alert_text: str,
    co: str = "",
    maps_url: str | None = None,
) -> None:
    """Hand one firing alert to the source-agnostic Notifications service
    for delivery over the *new* channels (email + web push).

    Telegram is NOT touched here — it runs on its own proven path.  This
    is the N2 seam: the same semantic event, rendered per-channel by
    ``dispatch()`` for every user who opted the alert's category in on
    email / web push via the notification matrix.

    No-op unless ``NOTIFICATIONS_LIVE_DISPATCH`` is on, and fully
    non-fatal — a Notifications failure must never sink an alert Telegram
    is about to deliver, so every error is logged and swallowed.

    * **Category must exist.** The pipeline's verbose ``alert_type``
      ("fault") is mapped to the canonical registry key ("faults") and we
      only dispatch when a category is actually registered for it — so an
      alert type without a real category + audience rule (doc-expiry,
      scorecard, system…) simply stays Telegram-only instead of fanning
      out ungated.
    * **Company scope: per-user (parity with Telegram).**  A recipient
      restricted to certain companies (Team Management → Company Access)
      must never be emailed/pushed about another company's vehicle.  We
      build the same gate the Telegram DM path uses (``company_scope.py``)
      and pass it to ``dispatch()`` as an OPAQUE predicate — so the
      notification core enforces it without ever importing alerting or
      learning what a "company" is.  Fail-open: no ``co``, no per-user
      scoping, or a scope-load error delivers to all, exactly like
      Telegram.  (This replaced an earlier account-wide fail-closed hold.)

    DND (Telegram quiet hours) is intentionally NOT applied here — it's a
    Telegram queue-and-flush concept; the new channels use the separate
    cadence/digest system instead.
    """
    if not _NOTIFICATIONS_LIVE_DISPATCH:
        return
    try:
        from capabilities.notifications import (
            dispatch as _notif_dispatch,
            NotificationContent as _NotifContent,
        )
        from capabilities.notifications.categories import get_category

        # Verbose pipeline alert_type ("fault") → canonical registry key
        # ("faults").  Skip when no category is registered for it.
        route_key = _PIPELINE_TO_ROUTE_KEY.get(alert_type, alert_type)
        category = f"alert.{route_key}"
        if get_category(category) is None:
            logger.debug(
                "notifications: no category for %r (acct=%d) — "
                "new-channel delivery skipped", category, account_id)
            return

        # Per-user company-scope predicate (parity with the Telegram path).
        # Built here — alerting owns company scope — and handed to dispatch()
        # as (user_id, role) -> keep, so a user restricted to another company
        # is dropped WITHOUT the notification core knowing what a company is.
        # Only built when there's a company AND some user is actually scoped;
        # otherwise None (fail-open, deliver to all).
        from capabilities.alerting.company_scope import (
            load_company_scope, user_sees_company)
        scope = await load_company_scope(account_id)
        recipient_filter = None
        if co and any(scope.values()):
            recipient_filter = (
                lambda uid, role: user_sees_company(uid, role, co, scope))

        content = _NotifContent(
            title=f"{vehicle_name} — {alert_type}",
            body=_strip_alert_html(alert_text),
            category=category,
            severity=severity.value,
            url=maps_url or "",
        )
        await _notif_dispatch(
            get_platform_db(), account_id, content,
            channels=("email", "web_push"),
            recipient_filter=recipient_filter,
        )
    except Exception as exc:
        logger.error(
            "notifications dispatch failed acct=%d type=%s: %s",
            account_id, alert_type, exc, exc_info=True,
        )


async def _dm_via_spine(tenant, account_id: int) -> bool:
    """Per-account switch for spine-delivered alert DMs
    (``alert_dm_spine`` account setting; ships OFF).  Guarded so a
    settings read error can never block delivery — it just means the
    proven legacy path runs."""
    try:
        return str(await tenant.get_account_setting(
            account_id, "alert_dm_spine") or "0") in ("1", "true", "on")
    except Exception as e:
        logger.debug("alert_dm_spine read failed acct=%d: %s", account_id, e)
        return False


async def _spine_dm_fanout(
    *,
    account_id: int,
    alert_type: str,
    severity: "AlertSeverity",
    alert_text: str,
    vname: str,
    photo_bytes: bytes | None,
    video_url: str,
    history_id: int | None,
    needs_ack: bool,
    subscribers: list,
    co: str,
) -> bool:
    """Personal DM delivery through the notifications spine.

    The spine reads the matrix prefs, applies quiet-hours deferral,
    records deliveries in the ledger (keyed ``alert:{history_id}`` so the
    ack button, reminder edits, and resolve receipts can find every
    copy), and renders the ✅ Acknowledge action.  Alerting contributes
    the two scoping rules the spine stays blind to — company scope and
    the driver-truck rule — as one ``recipient_filter`` predicate.

    Returns True when the spine handled DM delivery; False → the caller
    falls back to the legacy loop (unregistered category, or an outer
    dispatch error — delivery is guaranteed over dedup during the parity
    window).  A parity line compares the two paths' recipient sets while
    both implementations exist.
    """
    try:
        from capabilities.notifications import (
            dispatch as _notif_dispatch,
            NotificationContent as _NotifContent,
        )
        from capabilities.notifications.categories import get_category

        route_key = _PIPELINE_TO_ROUTE_KEY.get(alert_type, alert_type)
        category = f"alert.{route_key}"
        if get_category(category) is None:
            logger.warning(
                "spine-dm: no category for %r (acct=%d) — legacy DM path",
                category, account_id)
            return False

        # Company scope — same predicate build as the email/push seam.
        from capabilities.alerting.company_scope import (
            load_company_scope, user_sees_company)
        scope = await load_company_scope(account_id)
        co_pred = None
        if co and any(scope.values()):
            co_pred = (
                lambda uid, role: user_sees_company(uid, role, co, scope))

        # Driver-truck rule, mirroring the legacy loop exactly: a driver
        # WITH an assigned truck only hears their truck (substring match
        # on the vehicle name); a driver without one is not narrowed.
        truck_by_uid = {
            s.id: (getattr(s, "truck_num", "") or "")
            for s in subscribers
            if getattr(s, "role", None) == Role.DRIVER
        }
        _vname_l = (vname or "").lower()

        def _pred(uid: int, role: "str | None") -> bool:
            if co_pred is not None and not co_pred(uid, role):
                return False
            if str(role or "") == "driver":
                truck = truck_by_uid.get(uid, "")
                if truck and truck.lower() not in _vname_l:
                    return False
            return True

        correlation_key = (
            f"alert:{int(history_id)}" if history_id else "")
        content = _NotifContent(
            title="",
            body=_strip_alert_html(alert_text),
            category=category,
            severity=severity.value,
            url=video_url or "",
            photo_bytes=photo_bytes,
            actions=[{"id": "ack", "label": "✅ Acknowledge"}]
            if (needs_ack and correlation_key) else [],
        )
        pdb = get_platform_db()
        await _notif_dispatch(
            pdb, account_id, content,
            channels=("telegram_dm",),
            recipient_filter=_pred,
            correlation_key=correlation_key,
        )

        # Parity line (dual-implementation window): the legacy loop's
        # would-be recipient set vs what the ledger says the spine sent.
        # Diffs are expected for quiet-deferred users (spine holds them
        # for the shift-start flush) and matrix-vs-legacy pref drift.
        try:
            legacy_ids = {
                s.id for s in subscribers
                if not (getattr(s, "role", None) == Role.DRIVER
                        and (getattr(s, "truck_num", "") or "")
                        and s.truck_num.lower() not in _vname_l)
            }
            spine_ids: set = set()
            if correlation_key:
                rows = await pdb.get_notification_deliveries(
                    account_id, correlation_key, channel="telegram_dm")
                spine_ids = {int(r["recipient_id"]) for r in rows
                             if str(r["recipient_id"]).isdigit()}
            logger.info(
                "spine-dm parity acct=%d type=%s legacy=%d spine=%d "
                "only_legacy=%s only_spine=%s",
                account_id, alert_type, len(legacy_ids), len(spine_ids),
                sorted(legacy_ids - spine_ids)[:10],
                sorted(spine_ids - legacy_ids)[:10],
            )
        except Exception as pe:
            logger.debug("spine-dm parity log failed: %s", pe)
        return True
    except Exception as exc:
        logger.error(
            "spine-dm fanout failed acct=%d type=%s — legacy DM path: %s",
            account_id, alert_type, exc, exc_info=True,
        )
        return False


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
    maps_url: str | None = None,
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
        # vehicle_name powers the ``?q=`` search filter inside
        # Samsara's safety inbox so dispatch lands pre-filtered to
        # the truck even if Samsara doesn't focus the exact event row.
        samsara_url = samsara_event_url(org_id, event_id, vehicle_name=vehicle_name)
    elif alert_type in ("fault", "health"):
        samsara_url = samsara_fault_url(org_id, vehicle_id)
    else:
        samsara_url = samsara_vehicle_url(org_id, vehicle_id, alert_type)
    if samsara_url:
        rows.append([InlineKeyboardButton(
            t("alert_actions.open_in_samsara", lang=lang),
            url=samsara_url,
        )])

    # "View on map" URL button — same family as "Open in Samsara":
    # tap-target friendly, opens in a browser, never touches the
    # message, so it's safe in group topics where callbacks would
    # rewrite the alert for every other shift.  Caller passes
    # ``maps_url`` (already-built Google Maps deep-link) only for
    # alerts where a specific lat/lng is meaningful (parking,
    # safety events, future telematics events).
    if maps_url:
        rows.append([InlineKeyboardButton(
            t("alert_actions.view_on_map", lang=lang),
            url=maps_url,
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
    severity: str = "",
    subtype: str = "",
) -> bool:
    """Public group-post helper for alert paths that don't run through
    ``send_alert()`` — maintenance overdue, doc expiry, Samsara sync,
    scorecard drop, camera health.  Each of those has its own
    per-subscriber DM loop; they call this *first* and skip the DM
    loop when it returns True.

    Routing is delegated to ``routing_resolver.resolve_alert_targets``
    so the legacy single_group path (one topic per alert_type) and the
    new per_persona_groups path (one flat group per persona, plus an
    owner_admin cross-post on CRITICAL) share this call site.

    Returns False (caller continues with DM fanout) when:
      • forum routing is disabled globally
      • the account has no route configured in either mode
      • every group post failed

    Returns True (caller skips DM fanout) when ≥1 group post landed.
    """
    if not _FORUM_ROUTING_ENABLED:
        return False

    from .routing_resolver import resolve_alert_targets
    targets = await resolve_alert_targets(
        account_id=account_id, alert_type=alert_type, severity=severity,
        subtype=subtype,
    )
    if not targets:
        return False

    db = get_platform_db()
    sev_lower = (severity or "").strip().lower()
    severity_is_critical = sev_lower == "critical"
    any_success = False
    for target in targets:
        chat_id = target.chat_id
        thread_id = target.message_thread_id
        # Role Sub bot when attached; primary otherwise.
        send_app = _pick_sender(bot_app, account_id, target)

        # CRITICAL persona-mode primary targets get on-shift @-mentions
        # the same way the send_alert path does (shared composer).
        mention_html = await _compose_persona_critical_mention(
            account_id=account_id,
            target=target,
            severity_is_critical=severity_is_critical,
        )
        send_text = f"{mention_html}\n{text}" if mention_html else text

        try:
            # Hold the per-destination post lock for the duration of
            # the photo+text pair so this lite path serializes against
            # concurrent posts to the same chat/thread — both other
            # lite-path callers AND the heavy send_alert path that
            # acquires the same lock in ``_post_one_target``.  Without
            # this, a camera digest's photo + caption can interleave
            # with a maintenance text post on the same persona group.
            async with _topic_post_lock(chat_id, thread_id):
                reply_to: int | None = None
                if video_url:
                    try:
                        vmsg = await send_app.bot.send_video(
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
                        pmsg = await send_app.bot.send_photo(
                            chat_id=chat_id, message_thread_id=thread_id,
                            photo=_io.BytesIO(photo_bytes),
                            read_timeout=15, write_timeout=15,
                        )
                        reply_to = pmsg.message_id
                    except Exception as pe:
                        logger.debug("Forum photo failed acct=%d type=%s: %s",
                                     account_id, alert_type, pe)

                await _tg_send_with_retry(
                    lambda: send_app.bot.send_message(
                        chat_id=chat_id, message_thread_id=thread_id,
                        text=send_text, parse_mode=parse_mode,
                        reply_markup=reply_markup, reply_to_message_id=reply_to,
                    ),
                    what=f"forum alert acct={account_id} type={alert_type}",
                )
            logger.info(
                "forum alert posted (lite) acct=%d type=%s chat=%d thread=%s%s",
                account_id, alert_type, chat_id,
                thread_id if thread_id is not None else "-",
                " [aggregate]" if target.is_aggregate else "",
            )
            any_success = True
        except Exception as e:
            # Drift auto-disable applies to the LEGACY single_group
            # route only.  Per-persona groups aren't soft-disabled
            # automatically — the admin UI inspects them when an
            # operator notices missing alerts, because Telegram-side
            # group permissions changes (bot kicked, lost admin) are
            # a different failure mode than a deleted topic and need
            # a human in the loop.
            msg = str(e).lower()
            is_legacy = target.message_thread_id is not None and not target.persona
            if is_legacy and "topic" in msg and (
                "delete" in msg or "not found" in msg or "closed" in msg
            ):
                route_key = _PIPELINE_TO_ROUTE_KEY.get(alert_type)
                if route_key:
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
                    "Forum post (lite) failed acct=%d type=%s chat=%d: %s",
                    account_id, alert_type, chat_id, e,
                )
            continue
    return any_success


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
    maps_url: str | None = None,
    subtype: str = "",
    send_text_plain: str | None = None,
    ai_account_default: bool = True,
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

    from .routing_resolver import resolve_alert_targets
    sev_value = severity.value if hasattr(severity, "value") else str(severity)
    targets = await resolve_alert_targets(
        account_id=account_id, alert_type=alert_type, severity=sev_value,
        subtype=subtype,
    )
    if not targets:
        return False

    any_success = False
    for target in targets:
        ok = await _post_one_target(
            bot_app,
            target=target,
            account_id=account_id,
            alert_type=alert_type,
            severity=severity,
            co=co,
            vehicle_id=vehicle_id,
            vehicle_name=vehicle_name,
            send_text=send_text,
            video_url=video_url,
            photo_bytes=photo_bytes,
            event_id=event_id,
            event_time=event_time,
            maps_url=maps_url,
            # NOTE: no ``subtype`` here — the resolver already consumed it
            # picking targets; ``_post_one_target`` neither accepts nor
            # needs it (passing it was a latent TypeError masked for weeks
            # by the wrong-scope NameError on the two kwargs above).
            send_text_plain=send_text_plain,
            ai_account_default=ai_account_default,
        )
        if ok:
            any_success = True
    return any_success


async def _post_one_target(
    bot_app: Application,
    *,
    target,  # routing_resolver.AlertTarget
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
    maps_url: str | None,
    send_text_plain: str | None = None,
    ai_account_default: bool = True,
) -> bool:
    """Post one alert message to a single resolved target (chat ±
    thread).  Returns True on success.  Extracted from
    ``_try_post_to_topic`` so the same per-target body powers both the
    legacy single_group target and the per-persona-groups primary +
    aggregate targets.

    Drift detection (Telegram "topic deleted" / "not found") only
    auto-disables the LEGACY route — per-persona group failures
    require an admin to inspect (different failure modes:
    bot-removed, lost admin, group-deleted).
    """
    chat_id = target.chat_id
    thread_id = target.message_thread_id
    route_key = _PIPELINE_TO_ROUTE_KEY.get(alert_type)
    # Role Sub bot when attached; the caller's primary otherwise.
    # Every send below rides this pick.
    bot_app = _pick_sender(bot_app, account_id, target)

    # Shared-AI contract (owner decision): the AI answer is generated
    # ONCE per alert; each role's toggle only chooses whether THEIR
    # post includes it.  persona_ai.{role}.{key} falls back to the
    # account-wide forum_ai default; the aggregate follows the default.
    if send_text_plain is not None and send_text_plain != send_text:
        _persona = getattr(target, "persona", "") or ""
        if _persona and not getattr(target, "is_aggregate", False):
            try:
                _t = await get_tenant_db(account_id)
                _v = await _t.get_account_setting(
                    account_id, f"persona_ai.{_persona}.{route_key}", default="",
                )
                _include_ai = ai_account_default if not _v else _v != "0"
            except Exception:
                _include_ai = ai_account_default
            if not _include_ai:
                send_text = send_text_plain

    # CRITICAL-only @-mention for per-persona-mode primary targets.
    # Composed by the shared helper so the lite ``post_alert_to_topic``
    # path (camera digest, parking, doc-expiry, etc.) and this heavy
    # path apply the same mention rules.
    send_text_for_target = send_text
    mention_html = await _compose_persona_critical_mention(
        account_id=account_id,
        target=target,
        severity_is_critical=(severity == AlertSeverity.CRITICAL),
    )
    if mention_html:
        send_text_for_target = f"{mention_html}\n{send_text}"

    try:
        # Hold the per-destination post lock for the duration of the
        # photo+text pair so concurrent vehicles posting to the same
        # Telegram chat/topic don't interleave (see ``_TOPIC_POST_LOCKS``
        # rationale).  Keyed by the resolved chat+thread so per-persona
        # mode (which collapses several alert_types onto one chat)
        # serializes correctly alongside the legacy one-topic-per-type
        # case.
        async with _topic_post_lock(chat_id, thread_id):
            # Build the keyboard once — used either on the merged
            # photo-with-caption message OR on the trailing text
            # reply in the fallback path.
            basic_kb = build_alert_keyboard(
                severity, co, vehicle_name, alert_type=alert_type,
                vehicle_id=vehicle_id, event_id=event_id, event_time=event_time,
                lang="en",
                for_group=True,
                maps_url=maps_url,
            )

            # ── Merged path (preferred): one message per alert ──
            # When there's media AND the alert body fits in a caption
            # we send ``send_photo(caption=send_text)`` so the photo
            # IS the alert.  Reads as a single coherent block in the
            # topic — no more "two photos then two captions" confusion
            # the operators reported on parking-rich check cycles.
            msg = None
            if (video_url or photo_bytes) and _caption_fits(send_text_for_target):
                try:
                    if video_url:
                        msg = await bot_app.bot.send_video(
                            chat_id=chat_id,
                            message_thread_id=thread_id,
                            video=video_url,
                            caption=send_text_for_target,
                            parse_mode=ParseMode.HTML,
                            reply_markup=basic_kb,
                            read_timeout=30, write_timeout=30,
                        )
                    else:
                        import io as _io
                        # Compound guard ``(video_url or photo_bytes)`` above
                        # already proves ``photo_bytes`` is non-None on this
                        # branch (else taken iff ``not video_url`` so
                        # ``photo_bytes`` must be truthy).  Narrow for mypy.
                        assert photo_bytes is not None
                        msg = await bot_app.bot.send_photo(
                            chat_id=chat_id,
                            message_thread_id=thread_id,
                            photo=_io.BytesIO(photo_bytes),
                            caption=send_text_for_target,
                            parse_mode=ParseMode.HTML,
                            reply_markup=basic_kb,
                            read_timeout=15, write_timeout=15,
                        )
                except Exception as me:
                    # Media send failed (Telegram-side error, S3 expiry on
                    # video URL, etc.).  Drop through to the two-message
                    # fallback so the alert still ships.
                    logger.debug(
                        "Forum media-with-caption send failed for %s: %s — "
                        "falling back to media+reply", vehicle_name, me,
                    )
                    msg = None

            # ── Fallback path (legacy two-message) ──
            # Triggered when (a) there's no media, (b) the caption
            # overflows the 1024-char Telegram limit, or (c) the
            # media send above failed.  Identical behaviour to the
            # pre-merge implementation.
            if msg is None:
                reply_to: int | None = None
                if video_url:
                    try:
                        vmsg = await bot_app.bot.send_video(
                            chat_id=chat_id,
                            message_thread_id=thread_id,
                            video=video_url,
                            caption=f"🎥 {vehicle_name}",
                            read_timeout=30, write_timeout=30,
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
                            read_timeout=15, write_timeout=15,
                        )
                        reply_to = pmsg.message_id
                    except Exception as pe:
                        logger.debug("Forum photo send failed for %s: %s", vehicle_name, pe)

                msg = await _tg_send_with_retry(
                    lambda: bot_app.bot.send_message(
                        chat_id=chat_id,
                        message_thread_id=thread_id,
                        text=send_text_for_target,
                        parse_mode=ParseMode.HTML,
                        reply_markup=basic_kb,
                        reply_to_message_id=reply_to,
                    ),
                    what="parking group post",
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
                severity=severity.value if hasattr(severity, "value") else str(severity),
            )
            ack_kb = build_alert_keyboard(
                severity, co, vehicle_name, ack_id=ack_id, alert_type=alert_type,
                vehicle_id=vehicle_id, event_id=event_id, event_time=event_time,
                lang="en",
                for_group=True,
                maps_url=maps_url,
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
            "forum alert posted acct=%d type=%s severity=%s chat=%d thread=%s%s",
            account_id, alert_type, severity.value, chat_id,
            thread_id if thread_id is not None else "-",
            " [aggregate]" if target.is_aggregate else "",
        )
        return True

    except Exception as e:
        # Drift detection: Telegram returns "Bad Request: topic was
        # deleted" / "message thread not found" when an admin removed
        # the topic.  Soft-disable the legacy ``alert_routing`` row so
        # subsequent alerts fall to DM instead of repeatedly failing.
        # Per-persona group failures (target.persona != "") aren't
        # auto-disabled — they need an admin to investigate because
        # the failure modes (bot kicked, lost group admin, group
        # deleted) require human resolution, not silent rerouting.
        is_legacy = thread_id is not None and not target.persona
        msg = str(e).lower()
        if (
            is_legacy
            and route_key
            and "topic" in msg
            and ("delete" in msg or "not found" in msg or "closed" in msg)
        ):
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
                "Forum post failed acct=%d type=%s chat=%d — %s",
                account_id, alert_type, chat_id, e,
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
    subtype: str = "",
    video_url: str = "",
    event_id: str = "",
    event_time: str = "",
    photo_bytes: bytes | None = None,
    bot_app: Application | None = None,
    maps_url: str | None = None,
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

    # Auto-derive ``maps_url`` from ``vehicle["location"]`` so every
    # alert that hands us a Samsara vehicle dict (fault, health, fuel)
    # gets the 🗺 View on map button for free.  Callers with non-vehicle
    # coord shapes (events.py uses ``event["latitude"]``) still pass
    # ``maps_url=`` explicitly and override this fallback.
    if maps_url is None:
        _loc = vehicle.get("location") or {}
        _lat = _loc.get("latitude")
        _lng = _loc.get("longitude")
        if _lat is not None and _lng is not None:
            maps_url = f"https://maps.google.com/?q={_lat},{_lng}"

    # Resolve per-account bot — skip if no account bot registered
    if bot_app is None:
        bot_app = get_app_for_account(account_id)
    if not bot_app:
        logger.warning("No bot for account %d — skipping alert delivery", account_id)
        return

    tenant = await get_tenant_db(account_id)

    # ── Company-scope gate for DM delivery ────────────────────────
    # Drop subscribers whose company restriction (per-user override, or
    # their role's company assignment) excludes THIS alert's company, so
    # they aren't DM'd about another company's vehicle.  Fail-open: owner/
    # unrestricted subscribers and unknown-company alerts deliver to all.
    # Group/forum delivery is per-account and intentionally NOT gated.
    from capabilities.alerting.company_scope import filter_subscribers_by_company
    subscribers = await filter_subscribers_by_company(subscribers, co, account_id)

    # ── Chronic-pattern suppression gate (Fix C) ──────────────────
    # When the operator has marked this ``(alert_type, vehicle_id)``
    # as a known chronic issue, suppress new Telegram delivery + ack
    # row creation entirely.  We STILL update ``alert_history`` below
    # (so the data is preserved + occurrence_count keeps ticking)
    # but the chat stays quiet until the mute expires or the operator
    # explicitly unmutes.  Critical bypasses chronic mute — even a
    # known-chronic vehicle needs to ping when the SEV is critical.
    if severity != AlertSeverity.CRITICAL:
        try:
            if await tenant.is_chronic_pattern_muted(
                account_id, alert_type, vid,
            ):
                logger.info(
                    "Chronic-mute suppress acct=%d type=%s vid=%s",
                    account_id, alert_type, vid,
                )
                # Still touch alert_history so the audit trail shows
                # the fire happened; just skip Telegram + ack rows.
                try:
                    await tenant.upsert_alert_history(
                        account_id=account_id,
                        alert_type=alert_type,
                        vehicle_id=vid,
                        vehicle_name=vname,
                        last_detail=alert_key_detail,
                        severity=(
                            severity.value if hasattr(severity, "value")
                            else str(severity)
                        ),
                        alert_subkey=alert_subkey,
                    )
                except Exception as hist_err:
                    logger.debug(
                        "Chronic-mute upsert_alert_history failed: %s", hist_err,
                    )
                return
        except Exception as e:
            # Mute check failure should NOT block alert delivery —
            # fall through to normal flow on any error.
            logger.debug("Chronic-mute check failed (continuing): %s", e)

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
    # ``health`` is the one alert family that's signal-based instead
    # of event-based — the same condition can flap on/off many times
    # in an hour from sensor noise.  For those, we DON'T thread the
    # timestamp into the subkey, so re-fires of the same signal
    # collapse onto the same ``alert_history`` row (occurrence_count
    # increments).  Operators see one "Low battery (×13)" instead of
    # thirteen separate IDs.  All other alert types keep the
    # per-fire-unique behavior described above.
    if alert_type == "health":
        if not alert_subkey:
            alert_subkey = alert_key_detail
        # else: caller-supplied subkey wins — kept as-is.
    elif not alert_subkey:
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

    # ── New-channel fanout via the source-agnostic Notifications service.
    # We're past every suppression/mute gate, so the alert is genuinely
    # firing.  Hand the SAME semantic event to the non-Telegram channels
    # (email + web push) HERE — before the group-vs-DM routing fork below
    # — so those subscribers hear it whether or not this account is
    # forum-routed (the DM path early-returns for group-routed accounts).
    # Telegram stays entirely on its own path; this never touches it.
    await dispatch_new_channels(
        account_id=account_id,
        alert_type=alert_type,
        severity=severity,
        vehicle_name=vname,
        alert_text=alert_text,
        co=co,
        maps_url=maps_url,
    )

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

    # ONE shared AI answer (ai_note is generated once per alert); each
    # target only chooses whether ITS post includes it.  Compose both
    # variants here so the per-target pick costs nothing extra.
    _group_text = (
        _group_alert_text
        + ((ai_note or "") if _include_ai_in_group else "")
        + history_footer
    )
    _group_text_plain = _group_alert_text + history_footer
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
            maps_url=maps_url,
            subtype=subtype,
            send_text_plain=_group_text_plain,
            ai_account_default=_include_ai_in_group,
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

    # ── Spine DM fanout (the reader/writer switch) ───────────────
    # Per-account setting ``alert_dm_spine``: when on, personal DM
    # delivery goes through the notifications spine — matrix prefs,
    # quiet-hours deferral, delivery ledger + ack button — instead of
    # the legacy loop below (docs/architecture/alert-dm-migration.md).
    # False from the fanout (unregistered category / outer error) falls
    # through to the legacy loop: delivery is guaranteed over dedup
    # during the parity window.
    if await _dm_via_spine(tenant, account_id):
        _spine_handled = False
        with _obs.time_block(timings, "fanout"):
            _spine_handled = await _spine_dm_fanout(
                account_id=account_id,
                alert_type=alert_type,
                severity=severity,
                alert_text=alert_text + history_footer,
                vname=vname,
                photo_bytes=photo_bytes,
                video_url=video_url,
                history_id=(history_record or {}).get("id"),
                needs_ack=needs_ack,
                subscribers=subscribers,
                co=co,
            )
        if _spine_handled:
            timings["total"] = round(
                (_time.perf_counter() - _send_t0) * 1000, 1,
            )
            logger.info(
                "send_alert acct=%d type=%s severity=%s route=spine-dm "
                "timings_ms=%s",
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

            # ── Helper: send the alert as one merged photo+caption
            # message when the body fits in 1024 chars, otherwise
            # fall back to the legacy photo + text-reply pattern.
            # Returns the message object the caller uses for ack
            # tracking (photo message in merged path, text message
            # in fallback).
            async def _send_alert_dm(keyboard):
                # Merged path — preferred when there's media AND the
                # text fits in a Telegram caption.  One coherent
                # message instead of "two photos / two captions"
                # confusion the operators reported.
                if (video_url or photo_bytes) and _caption_fits(send_text):
                    try:
                        if video_url:
                            return await bot_app.bot.send_video(
                                chat_id=sub.telegram_id,
                                video=video_url,
                                caption=send_text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=keyboard,
                                read_timeout=30, write_timeout=30,
                            )
                        import io as _io
                        return await bot_app.bot.send_photo(
                            chat_id=sub.telegram_id,
                            photo=_io.BytesIO(photo_bytes),
                            caption=send_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=keyboard,
                            read_timeout=15, write_timeout=15,
                        )
                    except Exception as me:
                        logger.debug(
                            "DM media-with-caption send failed for %s: %s "
                            "— falling back to media+reply", vname, me,
                        )
                # Fallback path — no media, caption overflow, or
                # merged send failed above.  Photo/video posts
                # first (caption = identity), then text replies to it.
                _reply_to: int | None = None
                if video_url:
                    try:
                        vmsg = await bot_app.bot.send_video(
                            chat_id=sub.telegram_id,
                            video=video_url,
                            caption=f"🎥 {vname}",
                            read_timeout=30, write_timeout=30,
                        )
                        _reply_to = vmsg.message_id
                    except Exception as ve:
                        logger.debug(f"Video send failed for {vname}: {ve}")
                elif photo_bytes:
                    try:
                        import io as _io
                        pmsg = await bot_app.bot.send_photo(
                            chat_id=sub.telegram_id,
                            photo=_io.BytesIO(photo_bytes),
                            caption=f"📍 Parking location — #{vname}",
                            read_timeout=15, write_timeout=15,
                        )
                        _reply_to = pmsg.message_id
                    except Exception as pe:
                        logger.debug(f"Photo send failed for {vname}: {pe}")
                return await _tg_send_with_retry(
                    lambda: bot_app.bot.send_message(
                        chat_id=sub.telegram_id,
                        text=send_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=keyboard,
                        reply_to_message_id=_reply_to,
                    ),
                    what="alert DM",
                )

            if needs_ack:
                # Per-fire model: every alert is unique, always send-new.
                # Each fire creates its own alert_history row with its
                # own AlertID, so there is no "previous version" to
                # collapse onto — matches PagerDuty / Datadog / Samsara.
                sub_lang = getattr(sub, "language", None) or "en"
                basic_kb = build_alert_keyboard(
                    severity, co, vname, alert_type=alert_type,
                    vehicle_id=vid, event_id=event_id, event_time=event_time,
                    lang=sub_lang,
                    maps_url=maps_url,
                )
                msg = await _send_alert_dm(basic_kb)
                # Per-fire model: keep prior alert messages untouched.
                # Each fire is its own AlertID; users refer back to
                # earlier ones in their chat history.
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
                    severity=severity.value if hasattr(severity, "value") else str(severity),
                )
                # Swap the keyboard now that we have an ack_id.
                # ``edit_message_reply_markup`` works on photo/video
                # messages just as well as text — the keyboard sits
                # under whichever message type ``_send_alert_dm`` chose.
                ack_kb = build_alert_keyboard(
                    severity, co, vname, ack_id=ack_id, alert_type=alert_type,
                    vehicle_id=vid, event_id=event_id, event_time=event_time,
                    lang=sub_lang,
                    maps_url=maps_url,
                )
                await bot_app.bot.edit_message_reply_markup(
                    chat_id=sub.telegram_id,
                    message_id=msg.message_id,
                    reply_markup=ack_kb,
                )
            else:
                # INFO — same per-fire + merged-media model as
                # CRITICAL/WARNING but no ack-keyboard swap (INFO
                # doesn't show the Acknowledge button at all).
                sub_lang = getattr(sub, "language", None) or "en"
                basic_kb = build_alert_keyboard(
                    severity, co, vname, alert_type=alert_type,
                    vehicle_id=vid, event_id=event_id, event_time=event_time,
                    lang=sub_lang,
                    maps_url=maps_url,
                )
                msg = await _send_alert_dm(basic_kb)
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
