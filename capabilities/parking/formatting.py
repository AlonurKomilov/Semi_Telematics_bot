"""Telegram message formatting for parking alerts and resolutions."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

from adapters.storage import Role
from capabilities.alerting.pipeline import AlertSeverity
from infra.services import get_platform_db, get_tenant_db

logger = logging.getLogger("bot")


def _format_parking_alert(
    vname: str, address: str, lat: float, lng: float,
    duration_h: float, loc_class: str, ai_analysis: str,
    severity: AlertSeverity,
    is_breakdown: bool = False,
) -> str:
    """Format the parking alert message."""
    sep = "━━━━━━━━━━━━━━━━━━━"

    if is_breakdown:
        icon = "🆘"
        level = "POSSIBLE BREAKDOWN"
    elif severity == AlertSeverity.CRITICAL:
        icon = "🚨"
        level = "CRITICAL"
    else:
        icon = "⚠️"
        level = "WARNING"

    # Duration formatting
    if duration_h >= 24:
        dur_str = f"{duration_h / 24:.1f} days"
    else:
        dur_str = f"{duration_h:.1f}h"

    # Location class label
    class_labels = {
        "unsafe": "🔴 Roadside / Highway",
        "unknown": "🟡 Unverified Location",
        "safe": "🟢 Designated Parking",
        "geofence": "🟢 Inside Geofence",
    }
    class_label = class_labels.get(loc_class, "🟡 Unknown")

    # Google Maps link
    maps_url = f"https://maps.google.com/?q={lat},{lng}"

    title = "🆘  POSSIBLE BREAKDOWN" if is_breakdown else f"{icon}  UNSAFE PARKING — {level}"
    text = (
        f"{sep}\n"
        f"  {title}\n"
        f"{sep}\n"
        f"\n  🚛 Truck: <b>#{vname}</b>\n"
        f"\n  📍 <b>{lat:.5f}°, {lng:.5f}°</b>\n"
    )
    if address:
        text += f"  🏷 {address}\n"
    text += (
        f"  {class_label}\n"
        f"\n  🕐 Stopped for: <b>{dur_str}</b>\n"
        f"\n  🗺 <a href='{maps_url}'>View on Map</a>\n"
    )

    if ai_analysis:
        text += f"\n  🤖 <b>AI Analysis:</b>\n  {ai_analysis}\n"

    if is_breakdown:
        text += (
            "\n  🆘 <b>No AI classification possible.</b>\n"
            "  Vehicle may be disabled or have a\n"
            "  mechanical issue. Contact driver.\n"
        )
    elif severity == AlertSeverity.CRITICAL:
        text += (
            "\n  ❗ <b>Immediate attention required</b>\n"
            "  Vehicle has been parked in an unsafe\n"
            "  location for an extended period.\n"
        )

    return text


async def _send_parking_resolved(
    bot_app: Application,
    account_id: int,
    vname: str,
    co: str,
    event: dict,
):
    """Send notification that a parking event has been resolved (vehicle moved)."""
    duration_h = event.get("duration_hours", 0)
    address = event.get("address", "Unknown")

    if duration_h >= 24:
        dur_str = f"{duration_h / 24:.1f} days"
    else:
        dur_str = f"{duration_h:.1f}h"

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  ✅  <b>PARKING RESOLVED</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  🚛 Truck: <b>#{vname}</b>\n"
        f"  📍 Was at: {address}\n"
        f"  🕐 Parked for: <b>{dur_str}</b>\n"
        f"\n  Vehicle is now moving.\n"
    )

    subscribers = await get_platform_db().get_all_typed_subscribers("parking")
    acct_subs = [s for s in subscribers if s.account_id == account_id]

    tenant = await get_tenant_db(account_id)
    for sub in acct_subs:
        # Substring match — consistent with pipeline.send_alert and
        # alerting/service.filter_alerts_by_access.
        if sub.role == Role.DRIVER and sub.truck_num:
            if sub.truck_num.lower() not in vname.lower():
                continue
        # Respect DND / quiet hours for resolved notifications
        if sub.is_in_quiet_hours():
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
