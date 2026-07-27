"""DND morning delivery — shift handoff reports."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram.ext import Application

from adapters.storage import Role
from infra.services import get_platform_db, get_tenant_db
from capabilities.alerting.registry import register_alert_source

logger = logging.getLogger("bot")


# ════════════════════════════════════════════════════════════════════
#  SSoT for "is this user currently in DND?"
#  ────────────────────────────────────────────────────────────────────
#  Two layered sources, evaluated in order:
#
#    1. Per-user OVERRIDE — when ``user.quiet_start`` AND
#       ``user.quiet_end`` are both set, that window wins.  Evaluated
#       in the user's own timezone (personal preference).
#
#    2. Account WORKING HOURS — when no per-user override exists, we
#       derive DND from ``work_hours`` table for the user's role.
#       Evaluated in account timezone (admins define shifts in the
#       account's local time).  Union semantics across multiple shifts:
#       on-shift if ANY matching row covers the current hour.
#
#  Fallback: if no per-user override AND no matching ``work_hours``
#  rows exist for the user's role, DND is INACTIVE (alerts deliver
#  24/7).  This matches today's behavior when ``quiet_start IS NULL``.
#
#  This function is THE place all alert-delivery paths consult before
#  queuing a DND alert.  Do not duplicate this logic.
# ════════════════════════════════════════════════════════════════════


def _hour_in_window(hour: int, start: int, end: int) -> bool:
    """True if integer ``hour`` (0-23) falls in [start, end), supporting
    midnight wrap-around.

    Examples:
      _hour_in_window(8,  6, 14)  → True   (regular daytime window)
      _hour_in_window(2, 22,  6)  → True   (overnight window wrapping 0)
      _hour_in_window(8, 22,  6)  → False  (outside overnight window)
      _hour_in_window(6, 12, 12)  → False  (zero-width window is undefined)
    """
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # Wrap-around: e.g., 22→6 covers 22, 23, 0, 1, 2, 3, 4, 5
    return hour >= start or hour < end


async def is_user_on_shift(user, tenant) -> bool:
    """Return True if the user is currently inside any working-hours row
    that applies to their role (in the account's timezone).

    Looks up rows where ``target_role = 'all'`` OR
    ``target_role = user.role``.  Union semantics: if ANY row covers
    the current hour, returns True.

    Returns True when no rows are configured for this user's role —
    "no schedule defined" means "always on shift" so DND defaults to
    off.  This preserves the legacy behavior where a user with no
    ``quiet_start/end`` set received alerts 24/7.
    """
    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
    try:
        rows = await tenant.get_work_hours_for_role(user.account_id, role_val)
    except Exception as e:
        logger.debug("get_work_hours_for_role failed: %s — treating as no rows", e)
        rows = []
    if not rows:
        return True

    # Account timezone — work_hours columns store integers 0..23 in this tz.
    account = await tenant.get_account(user.account_id)
    tz_name = (getattr(account, "timezone", None) or "America/New_York")
    try:
        now_local = datetime.now(ZoneInfo(tz_name))
    except Exception:
        now_local = datetime.now(timezone.utc)
    current_hour = now_local.hour

    for row in rows:
        try:
            start = int(row.get("start_hour", 0))
            end = int(row.get("end_hour", 0))
        except (TypeError, ValueError):
            continue
        if _hour_in_window(current_hour, start, end):
            return True
    return False


async def is_user_dnd_active(user, tenant) -> bool:
    """Single source of truth for "should this user's alert be DND-queued?"

    Returns True when delivery should be deferred (queue for shift-start),
    False when delivery should proceed immediately.

    Priority:
      0. ``user.dnd_enabled`` — personal opt-out toggle (migration 100).
         When ``False`` the user has explicitly opted out of DND from
         their Profile page; always deliver, ignoring the schedule.
      1. Per-user Working Hours override (``quiet_start`` + ``quiet_end``
         both set) — admin assigned this user a custom active window.
      2. Otherwise: derived from account ``work_hours`` for the user's
         role — admin-managed, single-source-of-truth for the team.

    Critical-severity alerts MUST bypass this check at the call site
    via their own ``bypasses_dnd`` flag — this helper doesn't know
    about severity.  Tier 0 (personal opt-out) similarly does NOT
    silence critical alerts: this function returns False then, the
    pipeline delivers, and the call site's bypass for critical never
    even runs.
    """
    # Tier 0: personal opt-out toggle.  ``getattr`` keeps this safe
    # for callers passing a user-shaped object that predates the
    # toggle (default-True semantics handled at the model layer).
    if not getattr(user, "dnd_enabled", True):
        return False
    # Tier 1a: admin-assigned named schedule (migration 101).  Looks
    # up the work_hours row by FK and treats it like a per-user
    # override.  Wraps the lookup defensively so a deleted-schedule
    # FK doesn't break alert delivery — falls through to the legacy
    # free-form override / role-level tiers.
    assigned_id = getattr(user, "assigned_work_hours_id", None)
    if assigned_id is not None:
        try:
            # ``get_work_hour`` (singular) returns one row or None;
            # scoped to the user's account so a stale FK to a deleted
            # / cross-account schedule resolves None and falls through.
            row = await tenant.get_work_hour(assigned_id, user.account_id)
        except Exception as e:
            logger.debug("Assigned-schedule lookup failed: %s — falling through", e)
            row = None
        if row:
            from zoneinfo import ZoneInfo
            try:
                tz = ZoneInfo(user.timezone)
            except Exception:
                tz = ZoneInfo("America/New_York")
            local_hour = datetime.now(timezone.utc).astimezone(tz).hour
            start = int(row.get("start_hour", 0))
            end = int(row.get("end_hour", 0))
            in_working = (
                start <= local_hour < end if start <= end
                else local_hour >= start or local_hour < end
            )
            return not in_working
    # Tier 1b: legacy free-form per-user override (kept for backward
    # compatibility with rows set before the named-schedule selector
    # shipped).
    if user.quiet_start is not None and user.quiet_end is not None:
        return user.is_in_quiet_hours()
    return not await is_user_on_shift(user, tenant)


def _filter_handoff_for_driver(
    items: list[dict], vehicle_nums: list[str], *, name_key: str = "vehicle_name",
) -> list[dict]:
    """Keep only items whose ``vehicle_name`` matches one of the driver's trucks.

    Mirrors the substring match used elsewhere (API/bot parking filters and the
    ``send_alert`` subscriber loop) so a driver receiving a shift-handoff PDF
    or summary never sees rows for trucks they aren't assigned to.  Returns
    an empty list when ``vehicle_nums`` is empty (defensive: a driver with no
    assignment should not see fleet-wide data either).
    """
    if not vehicle_nums:
        return []
    needles = [t.lower() for t in vehicle_nums if t]
    if not needles:
        return []
    return [
        i for i in items
        if any(n in (i.get(name_key) or "").lower() for n in needles)
    ]


@register_alert_source("dnd_delivery", trigger="cron", minute=0)
async def deliver_dnd_alerts(app: Application):
    """Deliver shift handoff report when working hours start.

    Runs hourly. When a user transitions from outside→inside working hours,
    sends a short Telegram summary (counts + critical items only) plus a
    detailed PDF shift report as a document attachment.

    Only delivers once per calendar day (user's local timezone).
    """
    try:
        subscribers = await get_platform_db().get_all_alert_subscribers()
        if not subscribers:
            return

        for sub in subscribers:
            if sub.quiet_start is None or sub.quiet_end is None:
                continue  # no working hours configured

            try:
                user_tz = ZoneInfo(sub.timezone)
            except Exception:
                user_tz = ZoneInfo("America/New_York")
            now_local = datetime.now(timezone.utc).astimezone(user_tz)
            local_hour = now_local.hour

            # Only deliver at the START of working hours
            if local_hour != sub.quiet_start:
                continue

            # ── Prevent duplicate delivery within the same day ──
            today_local = now_local.strftime("%Y-%m-%d")
            if sub.last_shift_report == today_local:
                continue  # already sent today

            tenant = await get_tenant_db(sub.account_id)
            # Off-hours items now ride the notifications spine's quiet
            # queue (delivered by its own hourly flush as an off-shift
            # summary) — the report covers the rest of the handoff.
            queued: list = []

            handoff = await tenant.get_shift_handoff_data(sub.account_id, sub.telegram_id)
            pending = handoff["pending_alerts"]
            resolved = handoff["resolved_alerts"]
            maint = handoff["pending_maintenance"]
            history = handoff.get("recent_history", [])

            # Drivers must only see shift-handoff content for their own truck.
            # ``pending_alerts``/``resolved_alerts`` are already scoped to the
            # subscriber via ``alert_acknowledgments.sent_to`` (the per-driver
            # filter in ``send_alert`` blocks fleet rows from ever being
            # written for them).  ``pending_maintenance`` and
            # ``recent_history``, however, are pulled at account scope and
            # would leak the rest of the fleet's data into the PDF/summary.
            if sub.role == Role.DRIVER:
                trucks = await get_platform_db().get_user_vehicle_nums(sub.id)
                if not trucks and sub.truck_num:
                    trucks = [sub.truck_num]
                maint = _filter_handoff_for_driver(maint, trucks)
                history = _filter_handoff_for_driver(history, trucks)

            if not queued and not pending and not resolved and not maint and not history:
                continue

            # ── Build compact Telegram summary ─────────────────
            summary_text = _build_summary_text(
                sub, queued, pending, resolved, maint, history,
            )

            # ── Generate PDF ───────────────────────────────────
            # ReportLab is sync + CPU-bound (~500 ms-2 s). Off-load to a
            # worker thread so the alerting loop keeps moving while the
            # PDF renders.
            pdf_buf = await asyncio.to_thread(
                _generate_pdf,
                sub, queued, pending, resolved, maint, history, now_local,
            )

            # ── Send ───────────────────────────────────────────
            try:
                # One targeted spine notice: the PDF as a document with
                # the summary as its caption + the two nav buttons as
                # generic specs.  notify_user resolves the connection,
                # honors mutes, and records the delivery.
                from capabilities.alerting.pipeline import _strip_alert_html
                from capabilities.notifications import (
                    NotificationContent, notify_user)
                results = await notify_user(
                    get_platform_db(), sub.account_id, sub.id,
                    NotificationContent(
                        title="",
                        body=_strip_alert_html(summary_text),
                        category="alert.shift_report",
                        severity="info",
                        document_bytes=(pdf_buf.getvalue() if pdf_buf else None),
                        document_name=f"shift_report_{today_local}.pdf",
                        meta={"tg_buttons": [
                            [{"text": "🔔 Pending Alerts",
                              "callback_data": "cmd_pending_alerts"}],
                            [{"text": "◀️ Main Menu",
                              "callback_data": "cmd_menu"}],
                        ]},
                    ),
                    channels=("telegram_dm",),
                )
                if not any(r.ok for r in results):
                    logger.warning(
                        "Shift report not delivered to user %d — will retry "
                        "next window", sub.id)
                    continue

                await get_platform_db().update_user(sub.id, last_shift_report=today_local)
            except Exception as e:
                logger.error(f"DND delivery to {sub.telegram_id}: {e}")

    except Exception as e:
        logger.error(f"DND delivery error: {e}", exc_info=True)


# ── Helpers ──────────────────────────────────────────────────────

def _build_summary_text(sub, queued, pending, resolved, maint, history) -> str:
    """Build a short Telegram-friendly summary with counts and critical items."""
    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        "  🌅  <b>SHIFT REPORT</b>",
        "━━━━━━━━━━━━━━━━━━━",
    ]

    if sub.quiet_start is not None and sub.quiet_end is not None:
        lines.append(
            f"\n🕐 <b>{sub.quiet_start:02d}:00 – {sub.quiet_end:02d}:00</b>"
        )

    # Off-hours alerts: type breakdown (counts only)
    if queued:
        by_type: dict[str, int] = {}
        for q in queued:
            atype = q.get("alert_type", "other")
            by_type[atype] = by_type.get(atype, 0) + 1
        type_icons = {
            "fault": "⚠️", "health": "🏥", "fuel": "⛽",
            "events": "🚨", "geofence": "📍", "camera": "📷",
            "parking": "🅿️",
        }
        parts = [f"{type_icons.get(t, '🔔')} {t}: {c}" for t, c in by_type.items()]
        lines.append(
            f"\n📬 <b>{len(queued)} off-hours alert"
            f"{'s' if len(queued) != 1 else ''}</b>"
        )
        lines.append("  " + "  •  ".join(parts))

    # Recent alert activity summary (from alert_history)
    if history:
        hist_by_type: dict[str, int] = {}
        for h in history:
            ht = h.get("alert_type", "other")
            hist_by_type[ht] = hist_by_type.get(ht, 0) + 1
        hist_parts = [f"{t}: {c}" for t, c in hist_by_type.items()]
        lines.append(
            f"\n📊 <b>{len(history)} alerts in last 24h</b>"
        )
        lines.append("  " + "  •  ".join(hist_parts))

    # One-line counts
    counts = []
    if pending:
        counts.append(f"🔴 {len(pending)} pending")
    if resolved:
        counts.append(f"✅ {len(resolved)} resolved")
    overdue = [m for m in maint if m.get("status") == "overdue"]
    if overdue:
        counts.append(f"🔧🔴 {len(overdue)} overdue maintenance")
    elif maint:
        counts.append(f"🔧 {len(maint)} maintenance due")
    if counts:
        lines.append("\n" + "  •  ".join(counts))

    lines.append("\n📎 <i>Full details in the attached PDF</i>")
    return "\n".join(lines)


def _generate_pdf(sub, queued, pending, resolved, maint, history, now_local):
    """Generate shift report PDF. Returns BytesIO or None on failure."""
    try:
        from capabilities.reporting import generate_shift_report_pdf

        working_hours = None
        if sub.quiet_start is not None and sub.quiet_end is not None:
            working_hours = (sub.quiet_start, sub.quiet_end)

        return generate_shift_report_pdf(
            user_name=sub.label,
            working_hours=working_hours,
            queued_alerts=queued,
            pending_alerts=pending,
            resolved_alerts=resolved,
            pending_maintenance=maint,
            report_date=now_local.strftime("%B %d, %Y  %I:%M %p"),
            timezone_name=sub.timezone,
        )
    except Exception as e:
        logger.error(
            "Shift report PDF generation failed: %s", e, exc_info=True,
        )
        return None
