"""Telegram message formatting for parking alerts and resolutions.

Uses the unified Option A grammar — see
``capabilities/formatting/severity.py``.  Parking has a third
"breakdown" mode (AI failed to classify; truck appears disabled) which
maps onto the CRITICAL severity tier with a distinct title.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

from capabilities.alerting.pipeline import AlertSeverity
from capabilities.formatting.helpers import escape_html
from capabilities.formatting.severity import badge
from infra.services import get_platform_db, get_tenant_db

logger = logging.getLogger("bot")


def _duration_phrase(duration_h: float) -> str:
    """Render a stop duration in the most readable unit."""
    if duration_h >= 24:
        return f"{duration_h / 24:.1f} days"
    if duration_h >= 1:
        return f"{duration_h:.1f}h"
    mins = int(duration_h * 60)
    return f"{mins} min"


def _format_parking_alert(
    vname: str, address: str, lat: float, lng: float,
    duration_h: float, loc_class: str, ai_analysis: str,
    severity: AlertSeverity,
    is_breakdown: bool = False,
) -> str:
    """Format the parking alert message in the unified Option A grammar.

    Breakdown is its own title but uses the CRITICAL severity tier so
    the badge / body marker / DND-bypass behavior match every other
    truly-urgent alert.
    """
    sev = "critical" if (is_breakdown or severity == AlertSeverity.CRITICAL) else "warning"
    title = "Possible Breakdown" if is_breakdown else "Unauthorized Stop"

    # Escape every external string before HTML interpolation —
    # ``address`` is geocoded text and ``ai_analysis`` is model
    # output; both can carry stray ``<`` / ``>`` / ``&``.
    vname = escape_html(str(vname))
    address = escape_html(address) if address else ""
    ai_analysis = escape_html(ai_analysis) if ai_analysis else ""

    # Location-class chip — the AI's read on whether this is a safe
    # stop (rest area / depot) or an unsafe one (roadside).  Drives
    # the "what to do" decision more than coordinates do.
    class_labels = {
        "unsafe":   "🔴 Roadside / Highway",
        "unknown":  "🟡 Unverified Location",
        "safe":     "🟢 Designated Parking",
        "geofence": "🟢 Inside Geofence",
    }
    class_label = class_labels.get(loc_class, "🟡 Unknown location class")

    lines: list[str] = [f"<b>{badge(sev)}</b> — {title}", ""]

    # Stacked rows (see capabilities/formatting/faults.py rationale).
    lines.append(f"🚛 <b>Vehicle #{vname}</b>")
    if address:
        lines.append(f"📍 {address}")
    lines.append(f"🕐 stopped for <b>{_duration_phrase(duration_h)}</b>")

    # The class label carries its own status circle (🔴/🟡/🟢) so it
    # serves as the body marker — no severity marker prefix here, the
    # two would visually duplicate ("🔴 🔴 Roadside").
    #
    # Raw lat/lng used to render on the line below the class label —
    # removed because the 🗺 View on map button in the inline
    # keyboard already carries the precise coordinates (built by
    # check.py and passed via ``send_alert(maps_url=…)``).  Showing
    # them in the body too was redundant noise.  ``lat`` / ``lng``
    # remain on the function signature because the call site in
    # check.py still passes them and the public API hasn't changed.
    lines.append("")
    lines.append(class_label)
    del lat, lng  # parameters retained on signature for API stability

    if ai_analysis:
        lines.append("")
        lines.append(f"🤖 {ai_analysis}")

    lines.append("")
    if is_breakdown:
        lines.append("💡 Contact driver · vehicle may be disabled")
    elif sev == "critical":
        lines.append("💡 Verify with driver · escalate if no response")
    else:
        lines.append("💡 Contact driver · log reason if approved")

    return "\n".join(lines)


async def _send_parking_resolved(
    bot_app: Application,
    account_id: int,
    vname: str,
    co: str,
    event: dict,
    vid: str = "",
):
    """Send notification that a parking event has been resolved (vehicle moved).

    Threads the resolve receipt as a REPLY to the original parking
    alert in each surface (group topic + per-recipient DMs), so the
    chat shows ``UNSAFE PARKING`` → ``Vehicle Moving`` as a coherent
    pair instead of two unrelated messages.  Matches what the
    fault / health / fuel auto-resolve path does via
    ``_auto_resolve_vehicle_alerts`` — fixes the operator-visible
    inconsistency the user flagged ("parking resolved doesn't reply
    like faults do").

    Also calls ``auto_resolve_alerts_by_vehicle`` to mark the parking
    ack rows resolved, otherwise they sit stale in the table forever
    (parking previously bypassed the standard ack lifecycle and only
    cleared its own ``parking_events`` row).
    """
    duration_h = event.get("duration_hours", 0)
    address = escape_html(event.get("address", "Unknown"))
    vname_esc = escape_html(str(vname))
    dur_str = _duration_phrase(duration_h)

    lines: list[str] = [f"<b>{badge('resolved')}</b> — Vehicle Moving", ""]
    lines.append(f"🚛 <b>Vehicle #{vname_esc}</b>")
    if address and address != "Unknown":
        lines.append(f"📍 was at {address}")
    lines.append(f"🕐 parked for <b>{dur_str}</b>")
    lines.append("")
    lines.append("✅ Stop cleared — vehicle resumed motion")
    lines.append("")
    lines.append("💡 No action needed")
    text = "\n".join(lines)

    tenant = await get_tenant_db(account_id)

    # ── Two-step lookup + resolution ─────────────────────────────
    # Step 1: find the original alert rows REGARDLESS of ack state
    # so we can thread the receipt as a reply to each surface's
    # original message.  If we only used ``auto_resolve_alerts_by_vehicle``
    # (which filters out already-acked rows), any alert a user
    # already pressed Acknowledge on would have no thread target —
    # the receipt would post as a fresh unattached message in the
    # topic with no link back to the alert it cleared.
    #
    # Step 2: AFTER capturing the lookup rows, run the DB resolution
    # sweep to mark un-acked rows as system-acked.  That sweep no
    # longer needs to return rows for us — we already have what we
    # need from step 1.
    reply_targets: list[dict] = []
    if vid:
        try:
            reply_targets = await tenant.get_recent_alerts_for_resolution(
                account_id, "parking", vid,
            )
        except Exception as e:
            logger.debug(
                "Parking reply-target lookup failed (acct=%s vid=%s): %s",
                account_id, vid, e,
            )
        try:
            await tenant.auto_resolve_alerts_by_vehicle(
                account_id, "parking", vid,
            )
        except Exception as e:
            logger.debug(
                "Parking auto-resolve sweep failed (acct=%s vid=%s): %s",
                account_id, vid, e,
            )

    # Look up the forum topic id once so the group-resolve send can
    # route explicitly — same pattern + same rationale as the
    # fault/health/fuel auto-resolve (see escalation.py).
    from capabilities.alerting.pipeline import (
        _FORUM_ROUTING_ENABLED, _PIPELINE_TO_ROUTE_KEY, post_alert_to_topic,
    )
    group_thread_id: int | None = None
    if _FORUM_ROUTING_ENABLED:
        route_key = _PIPELINE_TO_ROUTE_KEY.get("parking")
        if route_key:
            try:
                route = await get_platform_db().get_alert_route(account_id, route_key)
                if route is not None:
                    group_thread_id = route.message_thread_id
            except Exception as e:
                logger.debug("Parking topic route lookup failed: %s", e)

    # ── Thread the resolve as a reply, per surface ───────────────
    group_posted = False
    dm_replied: set[int] = set()  # telegram_ids that got a threaded DM resolve

    for row in reply_targets:
        recipient_id = row.get("sent_to") or 0
        msg_id = row.get("message_id")
        chat_id = row.get("chat_id")
        if not msg_id or not chat_id:
            continue
        is_group_post = recipient_id == 0
        try:
            if is_group_post:
                await bot_app.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=group_thread_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_to_message_id=msg_id,
                )
                group_posted = True
            else:
                await bot_app.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_to_message_id=msg_id,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            f"📋 View Vehicle #{vname_esc}",
                            callback_data=f"covehicle_{co}_{vname}",
                        )],
                    ]),
                )
                dm_replied.add(recipient_id)
        except Exception as e:
            logger.debug(
                "Parking resolve reply failed (chat=%s msg=%s): %s",
                chat_id, msg_id, e,
            )

    # ── Group fallback: when we have no resolved-row to reply to ─
    # (e.g. parking_events row existed but alert_acknowledgments was
    # already cleaned).  Post a fresh message to the topic so the
    # parking topic still sees the resolution.
    if not group_posted:
        try:
            await post_alert_to_topic(
                bot_app, account_id=account_id,
                alert_type="parking", text=text,
                severity="info",
            )
        except Exception as e:
            logger.debug("Parking resolved → group topic post failed: %s", e)

    # ── Spine DM copies: edit them to the resolve receipt in place ──
    # The alert's personal DMs ride the notifications spine (delivery
    # ledger keyed alert:{history_id}); one update edits every copy to
    # 🟢 resolved and clears the ledger — the occurrence's final edit.
    # Replaces the legacy fresh-DM fanout: a subscriber without a
    # recorded copy never got the alert, so they need no receipt.
    try:
        hist = await tenant.get_active_alert_history(
            account_id, "parking", vid) if vid else None
        if hist:
            from capabilities.notifications import (
                NotificationContent as _NotifContent,
                update_delivery as _update_delivery,
            )
            from capabilities.alerting.pipeline import _strip_alert_html
            await _update_delivery(
                tenant, account_id, f"alert:{hist['id']}",
                _NotifContent(title="", body=_strip_alert_html(text),
                              severity="info"),
                # DM copies only — the group copy got its threaded
                # reply above (reply-not-edit, owner decision).
                channels=("telegram_dm",),
                clear=True,
            )
    except Exception as e:
        logger.debug("Parking spine resolve edit failed (acct=%s vid=%s): %s",
                     account_id, vid, e)
