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

from adapters.storage import Role
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

    maps_url = f"https://maps.google.com/?q={lat},{lng}"

    lines: list[str] = [f"<b>{badge(sev)}</b> — {title}", ""]

    where_parts = [f"🚛 <b>Truck #{vname}</b>"]
    if address:
        where_parts.append(f"📍 {address}")
    lines.append("  ·  ".join(where_parts))

    lines.append(f"🕐 stopped for <b>{_duration_phrase(duration_h)}</b>")

    # The class label carries its own status circle (🔴/🟡/🟢) so it
    # serves as the body marker — no severity marker prefix here, the
    # two would visually duplicate ("🔴 🔴 Roadside").
    lines.append("")
    lines.append(class_label)
    lines.append(f"      <a href='{maps_url}'>View on map</a>  ·  "
                 f"<code>{lat:.5f}, {lng:.5f}</code>")

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
):
    """Send notification that a parking event has been resolved (vehicle moved)."""
    duration_h = event.get("duration_hours", 0)
    address = escape_html(event.get("address", "Unknown"))
    vname = escape_html(str(vname))
    dur_str = _duration_phrase(duration_h)

    lines: list[str] = [f"<b>{badge('resolved')}</b> — Vehicle Moving", ""]
    where_parts = [f"🚛 <b>Truck #{vname}</b>"]
    if address and address != "Unknown":
        where_parts.append(f"📍 was at {address}")
    lines.append("  ·  ".join(where_parts))
    lines.append(f"🕐 parked for <b>{dur_str}</b>")
    lines.append("")
    lines.append("✅ Stop cleared — vehicle resumed motion")
    lines.append("")
    lines.append("💡 No action needed")
    text = "\n".join(lines)

    # Forum routing first: parking-resolved was previously DM-only so
    # the bound group's Parking topic never saw the resolution even
    # though the original "UNSAFE PARKING" alert posted there.  Post
    # once to the topic; DM fanout below still runs for personal
    # acknowledgement of the lifecycle.
    try:
        from capabilities.alerting.pipeline import post_alert_to_topic
        await post_alert_to_topic(
            bot_app, account_id=account_id,
            alert_type="parking", text=text,
        )
    except Exception as e:
        logger.debug("Parking resolved → group topic post failed: %s", e)

    subscribers = await get_platform_db().get_all_typed_subscribers("parking")
    acct_subs = [s for s in subscribers if s.account_id == account_id]

    tenant = await get_tenant_db(account_id)
    for sub in acct_subs:
        # Substring match — consistent with pipeline.send_alert and
        # alerting/service.filter_alerts_by_access.
        if sub.role == Role.DRIVER and sub.truck_num:
            if sub.truck_num.lower() not in vname.lower():
                continue
        # Respect DND / quiet hours for resolved notifications.  Uses
        # SSoT helper so personal overrides AND derived-from-Working-
        # Hours are both honored consistently with the rest of the
        # alerting pipeline.
        from capabilities.alerting.dnd import is_user_dnd_active
        if await is_user_dnd_active(sub, tenant):
            await tenant.queue_dnd_alert(
                account_id=account_id,
                telegram_id=sub.telegram_id,
                alert_type="parking",
                vehicle_name=vname,
                alert_text=text,
            )
            continue
        try:
            await bot_app.bot.send_message(
                chat_id=sub.telegram_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        f"📋 View Truck #{vname}",
                        callback_data=f"covehicle_{co}_{vname}",
                    )],
                    [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
                ]),
            )
        except Exception as e:
            logger.debug(f"Parking resolved notification to {sub.telegram_id}: {e}")
