"""Shared alert pipeline — severity tiers, keyboard builder, send_alert().

Universal alert pipeline with severity tiers:
  • CRITICAL — bypasses DND, requires ACK
  • WARNING  — respects DND, requires ACK
  • INFO     — respects DND, no ACK needed, history tracking only
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

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


def _keyboard_rows_as_specs(markup) -> list:
    """InlineKeyboardMarkup → the generic ``meta["tg_buttons"]`` specs
    the spine renders — so the spine never learns Telegram markup types
    and the DM buttons stay byte-identical with the legacy builder."""
    specs: list = []
    for row in getattr(markup, "inline_keyboard", []) or []:
        specs.append([
            {"text": b.text,
             **({"url": b.url} if b.url else {}),
             **({"callback_data": b.callback_data} if b.callback_data else {})}
            for b in row
        ])
    return specs


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
    ai_note: str = "",
    vehicle_id: str = "",
    event_id: str = "",
    event_time: str = "",
    maps_url: str | None = None,
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
        # The DM's utility buttons (AI Diagnose / Open in Samsara / View
        # on map / View Truck) come from the SAME legacy builder, passed
        # to the spine as generic specs — byte-identical rows, default
        # language (one shared render replaces per-user keyboards).
        # ack_id=None: the ✅ Acknowledge action row is spine-routed.
        tg_buttons: list = []
        try:
            tg_buttons = build_alert_button_specs(
                severity, co, vname, ack_id=None, alert_type=alert_type,
                vehicle_id=vehicle_id, event_id=event_id,
                event_time=event_time, maps_url=maps_url,
            )
        except Exception as ke:
            logger.debug("spine-dm keyboard build failed: %s", ke)

        actions = [{"id": "ack", "label": "✅ Acknowledge"}] \
            if (needs_ack and correlation_key) else []
        base_body = _strip_alert_html(alert_text)

        def _content(body: str) -> "_NotifContent":
            return _NotifContent(
                title="",
                body=body,
                category=category,
                severity=severity.value,
                video_url=video_url or "",
                photo_bytes=photo_bytes,
                actions=list(actions),
                meta={"tg_buttons": tg_buttons} if tg_buttons else {},
            )

        pdb = get_platform_db()
        # Per-user AI-note toggle (users.ai_<type>): two cohorts, same
        # correlation key — recipients with the toggle get the note
        # appended, everyone else the base text.  One dispatch when the
        # note is empty or nobody opted in.
        ai_field = {"fault": "ai_fault", "health": "ai_health",
                    "fuel": "ai_fuel", "events": "ai_events",
                    "parking": "ai_parking"}.get(alert_type)
        ai_on_ids = {
            s.id for s in subscribers
            if ai_field and getattr(s, ai_field, False)
        } if ai_note else set()

        if ai_on_ids:
            await _notif_dispatch(
                pdb, account_id,
                _content(_strip_alert_html(alert_text + ai_note)),
                channels=("telegram_dm",),
                recipient_filter=lambda uid, role: (
                    uid in ai_on_ids and _pred(uid, role)),
                correlation_key=correlation_key,
            )
            await _notif_dispatch(
                pdb, account_id, _content(base_body),
                channels=("telegram_dm",),
                recipient_filter=lambda uid, role: (
                    uid not in ai_on_ids and _pred(uid, role)),
                correlation_key=correlation_key,
            )
        else:
            await _notif_dispatch(
                pdb, account_id, _content(base_body),
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


def build_alert_button_specs(
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
) -> list:
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
    rows: list = []

    if severity in (AlertSeverity.CRITICAL, AlertSeverity.WARNING):
        if ack_id is not None:
            rows.append([{"text": t("alert_actions.acknowledge", lang=lang),
                "callback_data": f"ack_alert_{ack_id}",
            }])
        # AI Diagnose: callback rewrites the message in place.  Safe in
        # 1:1 DM (the requester is the only viewer), unsafe in a forum
        # topic (other shifts would lose the alert).  Skip in group.
        if not for_group:
            ai_diag_cb = f"ai_diag_{alert_type}_{co}_{vehicle_name}"
            if ack_id is not None:
                ai_diag_cb += f":{ack_id}"
            rows.append([{"text": t("alert_actions.ai_diagnose", lang=lang),
                "callback_data": ai_diag_cb,
            }])

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
        rows.append([{"text": t("alert_actions.open_in_samsara", lang=lang),
            "url": samsara_url,
        }])

    # "View on map" URL button — same family as "Open in Samsara":
    # tap-target friendly, opens in a browser, never touches the
    # message, so it's safe in group topics where callbacks would
    # rewrite the alert for every other shift.  Caller passes
    # ``maps_url`` (already-built Google Maps deep-link) only for
    # alerts where a specific lat/lng is meaningful (parking,
    # safety events, future telematics events).
    if maps_url:
        rows.append([{"text": t("alert_actions.view_on_map", lang=lang),
            "url": maps_url,
        }])

    # View Truck + Main Menu both rewrite the message via callback.
    # Same group-safety reasoning as AI Diagnose above — only render
    # them in 1:1 DM where the requester is the sole viewer.
    if for_group:
        return rows

    truck_cb = f"covehicle_{co}_{vehicle_name}"
    if ack_id is not None:
        truck_cb += f":{ack_id}"
    rows.append([{"text": t("vehicle.view_vehicle", lang=lang, name=vehicle_name),
        "callback_data": truck_cb,
    }])
    rows.append([{"text": t("menu.back_main", lang=lang),
        "callback_data": "cmd_menu",
    }])

    return rows


async def post_alert_to_topic(
    bot_app,   # unused transport handle — kept for caller compatibility
    *,
    account_id: int,
    alert_type: str,
    text: str,
    parse_mode: str = "HTML",   # kept for caller compat; plan renders
    reply_markup=None,
    photo_bytes: bytes | None = None,
    video_url: str = "",
    severity: str = "",
    subtype: str = "",
    subject_id: str = "",
    subject_name: str = "",
    dedup_key: str = "",
    co: str = "",
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
    # ── Profile-driven lifecycle (owner decision 2026-07-26) ─────
    # Board rows + personal channels run REGARDLESS of group routing —
    # an account with no groups still gets its Board row and any
    # opted-in personal copies.  The return value stays "posted to a
    # group?" (the callers' DM-fallback contract).
    from capabilities.alerting.profiles import get_profile
    _profile = get_profile(alert_type)
    _history_id = None
    if _profile.board and subject_id:
        try:
            _t = await get_tenant_db(account_id)
            _plain_first = _strip_alert_html(text).split("\n", 1)[0][:200]
            _hist = await _t.upsert_alert_history(
                account_id, alert_type, subject_id,
                subject_name or subject_id,
                last_detail=_plain_first,
                severity=(severity or "warning").strip().lower() or "warning",
                alert_subkey=dedup_key,
            )
            _history_id = (_hist or {}).get("id")
        except Exception as _be:
            logger.debug("profile board row failed (%s): %s", alert_type, _be)
    if _profile.personal and subject_id:
        # Subjectless calls (the camera digest — its own per-sub loop owns
        # personal delivery) keep group-only behavior: without a subject
        # there is no Board row to correlate and no clean dedup, and
        # double-delivery to camera subscribers would be a regression.
        try:
            from capabilities.notifications import (
                NotificationContent as _NotifContent,
                dispatch as _notif_dispatch,
            )
            _rk = _PIPELINE_TO_ROUTE_KEY.get(alert_type, alert_type)
            # Company scope (Team Management → Company Access) — same
            # predicate build as send_alert's seam: a user restricted
            # from this alert's company never receives a personal copy.
            # Empty ``co`` fail-opens, parity with every legacy path.
            _co_pred = None
            if co:
                from capabilities.alerting.company_scope import (
                    load_company_scope, user_sees_company)
                _scope = await load_company_scope(account_id)
                if any(_scope.values()):
                    _co_pred = (lambda uid, role, _co=co, _s=_scope:
                                user_sees_company(uid, role, _co, _s))
            # Effective-permission gate (§9d closed): the dispatch
            # subscriber list is prefs-based, so resolve — once, before
            # the sync predicate — which members' per-account matrix
            # actually allows this type, and gate by users.id.  On
            # resolution failure the gate drops out (fail-open, logged):
            # a perms hiccup must not silence the fan-out.
            _allowed_ids = None
            try:
                from capabilities.alerting.relevance import (
                    filter_users_by_alert_access)
                _members = await get_platform_db().list_account_users(account_id)
                _allowed = await filter_users_by_alert_access(
                    _members, alert_type)
                _allowed_ids = {u.id for u in _allowed}
            except Exception as _fe:
                logger.debug("effective-perm gate unavailable (%s): %s",
                             alert_type, _fe)
            _rf = None
            if _co_pred is not None or _allowed_ids is not None:
                _rf = (lambda uid, role, _p=_co_pred, _a=_allowed_ids:
                       (_a is None or uid in _a)
                       and (_p is None or _p(uid, role)))
            await _notif_dispatch(
                get_platform_db(), account_id,
                _NotifContent(
                    title="",
                    body=_strip_alert_html(text),
                    category=f"alert.{_rk}",
                    severity=(severity or "warning").strip().lower() or "warning",
                ),
                channels=("telegram_dm", "email", "web_push"),
                recipient_filter=_rf,
                correlation_key=(f"alert:{_history_id}" if _history_id else ""),
            )
        except Exception as _pe:
            logger.debug("profile personal fanout failed (%s): %s",
                         alert_type, _pe)

    if not _FORUM_ROUTING_ENABLED:
        return False

    from .routing_resolver import resolve_alert_targets
    targets = await resolve_alert_targets(
        account_id=account_id, alert_type=alert_type, severity=severity,
        subtype=subtype,
    )
    if not targets:
        return False

    # ── Group delivery via the notifications plan ────────────────
    # Same executor as send_alert's heavy path: sender hints pick Sub
    # bots, per-destination locks live in the channel, on-shift
    # mentions ride as post-render prefixes.  Lite posts carry no
    # actions (the trio isn't ackable; digest-style posts have no
    # history to correlate) and write no ack rows — nothing read them.
    from capabilities.notifications import (
        DeliveryPlan,
        NotificationContent as _NotifContent,
        Target as _PlanTarget,
        deliver as _plan_deliver,
    )
    sev_norm = (severity or "info").strip().lower() or "info"
    content = _NotifContent(
        title="",
        body=_strip_alert_html(text),
        category=f"alert.{_PIPELINE_TO_ROUTE_KEY.get(alert_type, alert_type)}",
        severity=sev_norm,
        photo_bytes=photo_bytes,
        video_url=video_url or "",
        meta=({"tg_buttons": _keyboard_rows_as_specs(reply_markup)}
              if reply_markup is not None else {}),
    )
    plan_targets: list = []
    for t in targets:
        prefix = await _compose_persona_critical_mention(
            account_id=account_id, target=t,
            severity_is_critical=(sev_norm == "critical"),
        )
        thread = getattr(t, "message_thread_id", None)
        addr = str(t.chat_id) + (f":{thread}" if thread is not None else "")
        plan_targets.append(_PlanTarget(
            channel="telegram_topic",
            address=addr,
            id=(getattr(t, "persona", "")
                or _PIPELINE_TO_ROUTE_KEY.get(alert_type, alert_type) or addr),
            sender_hint="" if getattr(t, "is_aggregate", False)
            else (getattr(t, "persona", "") or ""),
            prefix_html=prefix,
        ))
    result = await _plan_deliver(
        get_platform_db(), account_id,
        DeliveryPlan(contents=[content], shared=plan_targets, personal=[]),
    )
    any_success = False
    for orig, (_pt, res) in zip(targets, result.shared):
        if res.ok:
            any_success = True
            continue
        err = (res.error or "").lower()
        is_legacy = (getattr(orig, "message_thread_id", None) is not None
                     and not getattr(orig, "persona", ""))
        rk = _PIPELINE_TO_ROUTE_KEY.get(alert_type)
        if (is_legacy and rk and "topic" in err
                and ("delet" in err or "not found" in err or "closed" in err)):
            try:
                await get_platform_db().set_alert_route_active(
                    account_id, rk, False)
                logger.warning(
                    "Forum route auto-disabled (drift): acct=%d type=%s — %s",
                    account_id, rk, res.error)
            except Exception:
                logger.exception("Failed to disable broken route")
        elif getattr(orig, "persona", ""):
            try:
                await get_platform_db().record_persona_group_failure(
                    account_id, orig.persona, res.error or "send failed")
            except Exception:
                pass
            logger.warning("Forum post (lite) failed acct=%d type=%s "
                           "persona=%s — %s",
                           account_id, alert_type, orig.persona, res.error)
        else:
            logger.warning("Forum post (lite) failed acct=%d type=%s "
                           "chat=%s — %s",
                           account_id, alert_type, orig.chat_id, res.error)
    return any_success


async def _deliver_groups_via_plan(
    *,
    account_id: int,
    alert_type: str,
    severity: AlertSeverity,
    co: str,
    vehicle_id: str,
    vehicle_name: str,
    send_text: str,
    send_text_plain: "str | None",
    ai_account_default: bool,
    photo_bytes: "bytes | None",
    video_url: str,
    history_id: "int | None",
    needs_ack: bool,
    alert_key_detail: str,
    subtype: str = "",
) -> bool:
    """Group-topic delivery as a notifications delivery PLAN.

    Alerting keeps every decision — target resolution, per-persona AI
    inclusion, on-shift mentions, ack-row state, drift policy — and the
    spine executes the sends (Sub-bot pick, locks, ledger, ✅ button).
    Returns True when at least one team copy landed.

    The ✅ Acknowledge button on team copies is the spine action row
    (history-level ack + fan-edit of every recorded copy — DMs and
    groups together), replacing the legacy per-ack-row keyboard.  Ack
    ROWS are still written from the returned handles so re-escalation /
    resolve threading / the shift report keep their state until those
    reads move to the ledger.
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

    from capabilities.notifications import (
        DeliveryPlan,
        NotificationContent as _NotifContent,
        Target as _PlanTarget,
        deliver as _plan_deliver,
    )
    route_key = _PIPELINE_TO_ROUTE_KEY.get(alert_type, alert_type)
    correlation_key = f"alert:{int(history_id)}" if history_id else ""

    def _mk(body: str) -> "_NotifContent":
        # NO Acknowledge action on group posts (owner decision
        # 2026-07-27): acking is a PERSONAL act and lives on the DM
        # copies — a shared topic button let anyone clear an alert the
        # team was still reading.  The ack ROWS below still track the
        # group message ids (reminders/receipts/drift edit through
        # them); only the button is gone.
        return _NotifContent(
            title="",
            body=_strip_alert_html(body),
            category=f"alert.{route_key}",
            severity=sev_value,
            photo_bytes=photo_bytes,
            video_url=video_url or "",
        )

    contents = [_mk(send_text)]
    has_plain = send_text_plain is not None and send_text_plain != send_text
    if has_plain:
        contents.append(_mk(send_text_plain))

    tenant = await get_tenant_db(account_id)
    plan_targets: list = []
    for t in targets:
        # Shared-AI contract: persona toggle picks WITH-AI vs plain;
        # the aggregate and legacy single-mode follow the account default.
        idx = 0
        if has_plain:
            persona = getattr(t, "persona", "") or ""
            if persona and not getattr(t, "is_aggregate", False):
                try:
                    v = await tenant.get_account_setting(
                        account_id, f"persona_ai.{persona}.{route_key}",
                        default="")
                    include_ai = ai_account_default if not v else v != "0"
                except Exception:
                    include_ai = ai_account_default
            else:
                include_ai = ai_account_default
            if not include_ai:
                idx = 1
        prefix = await _compose_persona_critical_mention(
            account_id=account_id, target=t,
            severity_is_critical=severity == AlertSeverity.CRITICAL,
        )
        thread = getattr(t, "message_thread_id", None)
        addr = str(t.chat_id) + (f":{thread}" if thread is not None else "")
        plan_targets.append(_PlanTarget(
            channel="telegram_topic",
            address=addr,
            id=(getattr(t, "persona", "") or route_key or addr),
            sender_hint="" if getattr(t, "is_aggregate", False)
            else (getattr(t, "persona", "") or ""),
            prefix_html=prefix,
            content_index=idx,
        ))

    result = await _plan_deliver(
        get_platform_db(), account_id,
        DeliveryPlan(correlation_key=correlation_key, contents=contents,
                     shared=plan_targets, personal=[]),
    )

    any_ok = False
    alert_key = f"{co}:{vehicle_id}:{alert_key_detail}"
    for orig, (_ptarget, res) in zip(targets, result.shared):
        if res.ok:
            any_ok = True
            logger.info(
                "forum alert posted acct=%d type=%s severity=%s chat=%s "
                "thread=%s%s route=plan",
                account_id, alert_type, sev_value, orig.chat_id,
                getattr(orig, "message_thread_id", None) or "-",
                " [aggregate]" if getattr(orig, "is_aggregate", False) else "",
            )
            mid = (res.handle or {}).get("message_id")
            chat = (res.handle or {}).get("chat_id")
            if mid and chat:
                try:
                    if needs_ack:
                        await tenant.create_alert_ack(
                            account_id=account_id, alert_type=alert_type,
                            vehicle_id=vehicle_id, vehicle_name=vehicle_name,
                            alert_key=alert_key, message_id=mid,
                            chat_id=chat, sent_to=0, severity=sev_value,
                        )
                    else:
                        await tenant.create_info_alert_ack(
                            account_id=account_id, alert_type=alert_type,
                            vehicle_id=vehicle_id, vehicle_name=vehicle_name,
                            alert_key=alert_key, message_id=mid,
                            chat_id=chat, sent_to=0,
                        )
                except Exception as ae:
                    logger.debug("group ack-row write failed: %s", ae)
            continue
        # Failure policy — alerting's decision, spine's report.
        err = (res.error or "").lower()
        is_legacy = (getattr(orig, "message_thread_id", None) is not None
                     and not getattr(orig, "persona", ""))
        if (is_legacy and route_key and "topic" in err
                and ("delet" in err or "not found" in err or "closed" in err)):
            try:
                await get_platform_db().set_alert_route_active(
                    account_id, route_key, False)
                logger.warning(
                    "Forum route disabled (drift): acct=%d type=%s — %s",
                    account_id, route_key, res.error,
                )
            except Exception:
                logger.exception("Failed to disable broken route")
        elif getattr(orig, "persona", ""):
            try:
                await get_platform_db().record_persona_group_failure(
                    account_id, orig.persona, res.error or "send failed")
            except Exception:
                pass
            logger.warning(
                "Forum post failed acct=%d type=%s persona=%s — %s",
                account_id, alert_type, orig.persona, res.error,
            )
        else:
            logger.warning(
                "Forum post failed acct=%d type=%s chat=%s — %s",
                account_id, alert_type, orig.chat_id, res.error,
            )
    return any_ok


async def send_alert(
    app,   # unused transport handle — kept for caller compatibility
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
    bot_app=None,   # unused — kept for caller compatibility
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
      posted_to_topic = await _deliver_groups_via_plan(
            account_id=account_id,
            alert_type=alert_type,
            severity=severity,
            co=co,
            vehicle_id=vid,
            vehicle_name=vname,
            send_text=_group_text,
            send_text_plain=_group_text_plain,
            ai_account_default=_include_ai_in_group,
            photo_bytes=photo_bytes,
            video_url=video_url,
            history_id=(history_record or {}).get("id"),
            needs_ack=needs_ack,
            alert_key_detail=alert_key_detail,
            subtype=subtype,
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

    # ── Personal DMs: the notifications spine, always ────────────
    # Matrix prefs decide recipients; quiet hours defer; the delivery
    # ledger + ✅ Acknowledge action ride along.  The legacy per-sub
    # loop is gone (docs/architecture/alert-dm-migration.md — legacy
    # cleanup): a False here (unregistered category / spine error)
    # means DMs are skipped for THIS occurrence — logged loudly; the
    # group post and alert_history above are unaffected.
    handled = False
    with _obs.time_block(timings, "fanout"):
        handled = await _spine_dm_fanout(
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
            ai_note=ai_note,
            vehicle_id=vid,
            event_id=event_id,
            event_time=event_time,
            maps_url=maps_url,
        )
    if not handled:
        logger.error(
            "spine-dm fanout unavailable acct=%d type=%s — personal DMs "
            "skipped for this occurrence (group post + history delivered)",
            account_id, alert_type,
        )
    timings["total"] = round(
        (_time.perf_counter() - _send_t0) * 1000, 1,
    )
    logger.info(
        "send_alert acct=%d type=%s severity=%s route=spine-dm timings_ms=%s",
        account_id, alert_type, severity.value, timings,
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
