"""Parking safety callback handlers — events list, history, detail view."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from adapters.storage import Role
from capabilities.permissions.roles import can

from interfaces.bot.state import get_tenant_db
from interfaces.bot.keyboards import back_kb, parking_events_kb, parking_history_kb
from interfaces.bot.helpers import _show
from capabilities.localization.i18n import t


def _own_only(user) -> bool:
    """True when this caller may only see parking events for their own truck.

    A driver who lacks ``can_parking_all`` (the account-wide flag) but holds
    ``can_parking_vehicle`` falls into this branch.  The list/history/detail
    callbacks must filter results to the user's assigned truck before they
    are rendered or the bot will leak every vehicle's parking activity.
    """
    return (
        user.role == Role.DRIVER
        and not can(user.role, "can_parking_all")
        and can(user.role, "can_parking_vehicle")
    )


def _filter_to_own_vehicles(events: list[dict], user) -> list[dict]:
    """Keep only events whose ``vehicle_name`` matches the user's truck(s).

    Mirrors the substring match used by the API layer
    (``interfaces/api/routes/parking.py``) so the bot and miniapp see the
    same row-set for the same caller.
    """
    truck = (user.truck_num or "").strip().lower()
    if not truck:
        return []
    return [
        e for e in events
        if truck in (e.get("vehicle_name") or "").lower()
    ]


async def _handle_parking_events(update, context, user, show_all: bool = False):
    """Show active parking events list (attention-only or all)."""
    query = update.callback_query
    await query.answer()

    if not can(user.role, "can_parking_all") and not can(user.role, "can_parking_vehicle"):
        await query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    events = await tenant.get_active_parking_events(
        user.account_id, attention_only=not show_all,
    )
    own_only = _own_only(user)
    if own_only:
        events = _filter_to_own_vehicles(events, user)

    if not events:
        if own_only:
            label = "stopped" if show_all else "needing attention"
            text = f"✅ Your truck is not {label} right now."
        else:
            label = "stopped vehicles" if show_all else "vehicles needing attention"
            text = f"✅ No {label} right now."
    elif own_only:
        text = (
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "  🅿️  <b>Your Parking</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "\n  Your truck — recent stop\n"
            "  Tap for details."
        )
    else:
        count = len(events)
        label = "all stopped vehicles" if show_all else "vehicles needing attention"
        text = (
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"  🅿️  <b>Parking Safety</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {count} {label}\n"
            "  Tap a vehicle for details."
        )

    await _show(update, context, [text],
                keyboard=parking_events_kb(events, show_all=show_all))


async def _handle_parking_history(update, context, user, days: int = 7):
    """Show resolved parking events history."""
    query = update.callback_query
    await query.answer()

    if not can(user.role, "can_parking_all") and not can(user.role, "can_parking_vehicle"):
        await query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    history = await tenant.get_parking_history(user.account_id, days=days)
    own_only = _own_only(user)
    if own_only:
        history = _filter_to_own_vehicles(history, user)

    if not history:
        text = f"📅 No resolved parking events in the last {days} days."
    else:
        title = "Your Parking History" if own_only else f"Parking History — {days}d"
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━",
            f"  📅  <b>{title}</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        for ev in history[:15]:
            vname = ev.get("vehicle_name", "?")
            dur = ev.get("duration_hours", 0)
            loc_class = ev.get("location_class", "unknown")
            address = ev.get("address", "Unknown")
            icon = {"unsafe": "🔴", "unknown": "🟡"}.get(loc_class, "⚪")
            dur_str = f"{dur / 24:.1f}d" if dur >= 24 else f"{dur:.1f}h"
            lines.append(f"  {icon} <b>#{vname}</b> — {dur_str}")
            if address:
                lines.append(f"      📍 {address[:50]}")
        if len(history) > 15:
            lines.append(f"\n  … and {len(history) - 15} more")
        text = "\n".join(lines)

    await _show(update, context, [text], keyboard=parking_history_kb(days=days))


async def _handle_parking_detail(update, context, user, event_id: int):
    """Show detail view for a single parking event."""
    query = update.callback_query
    await query.answer()

    if not can(user.role, "can_parking_all") and not can(user.role, "can_parking_vehicle"):
        await query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    event = await tenant.get_parking_event_by_id(event_id)
    if not event or event.get("account_id") != user.account_id:
        await _show(update, context, ["❌ Parking event not found."],
                    keyboard=back_kb())
        return

    # Drivers without account-wide access may only inspect events for the
    # truck they are assigned to.  Without this gate any driver could
    # enumerate other vehicles' parking history just by guessing event
    # ids in the parking_detail_<id> callback.
    if _own_only(user):
        truck = (user.truck_num or "").strip().lower()
        ev_truck = (event.get("vehicle_name") or "").lower()
        if not truck or truck not in ev_truck:
            await _show(update, context, ["❌ Parking event not found."],
                        keyboard=back_kb())
            return

    vname = event.get("vehicle_name", "?")
    address = event.get("address", "Unknown")
    lat = event.get("latitude", 0)
    lng = event.get("longitude", 0)
    duration_h = event.get("duration_hours", 0)
    loc_class = event.get("location_class", "unknown")
    ai_analysis = event.get("ai_analysis", "")
    alert_level = event.get("alert_level", "none")
    first_stopped = event.get("first_stopped", "")
    co = event.get("company_code", "?")

    from capabilities.alerting import get_parking_classification_reason
    reason = get_parking_classification_reason(address, loc_class, ai_analysis)

    class_labels = {
        "unsafe": "🔴 Roadside / Highway",
        "unknown": "🟡 Unverified Location",
        "safe": "🟢 Designated Parking",
        "geofence": "🟢 Inside Geofence",
    }
    class_label = class_labels.get(loc_class, "🟡 Unknown")
    dur_str = f"{duration_h / 24:.1f} days" if duration_h >= 24 else f"{duration_h:.1f}h"
    maps_url = f"https://maps.google.com/?q={lat},{lng}"

    alert_icons = {
        "none": "⚪ None",
        "warning": "⚠️ WARNING",
        "critical": "🚨 CRITICAL",
        "breakdown": "🆘 BREAKDOWN",
    }
    alert_str = alert_icons.get(alert_level, alert_level)

    text = (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"  🅿️  <b>Parking Detail</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n  🚛 Truck: <b>#{vname}</b>\n"
        f"\n  📍 <b>{lat:.5f}°, {lng:.5f}°</b>\n"
    )
    if address:
        text += f"  🏷 {address}\n"
    text += (
        f"  {class_label}\n"
        f"\n  🕐 Stopped for: <b>{dur_str}</b>\n"
        f"  📊 Alert Level: {alert_str}\n"
        f"  📝 Reason: {reason}\n"
        f"\n  🗺 <a href='{maps_url}'>View on Map</a>\n"
    )
    if ai_analysis:
        text += f"\n  🤖 <b>AI Analysis:</b>\n  {ai_analysis}\n"

    rows = [
        [InlineKeyboardButton(
            f"📋 View Truck #{vname}",
            callback_data=f"covehicle_{co}_{vname}",
        )],
        [InlineKeyboardButton("◀️ Back", callback_data="cmd_parking_events")],
    ]
    await _show(update, context, [text],
                keyboard=InlineKeyboardMarkup(rows))


# ── Callback entry points ───────────────────────────────────────

async def parking_events_handler(update, context):
    user = context.user_data["_db_user"]
    await _handle_parking_events(update, context, user, show_all=False)


async def parking_all_handler(update, context):
    user = context.user_data["_db_user"]
    await _handle_parking_events(update, context, user, show_all=True)


async def parking_history_handler(update, context):
    user = context.user_data["_db_user"]
    data = update.callback_query.data
    days = 7
    if data == "cmd_parking_history_30":
        days = 30
    await _handle_parking_history(update, context, user, days=days)


async def parking_detail_handler(update, context):
    data = update.callback_query.data
    event_id = int(data.replace("parking_detail_", ""))
    user = context.user_data["_db_user"]
    await _handle_parking_detail(update, context, user, event_id)


def register(router):
    """Register parking safety routes."""
    router.exact("cmd_parking_events", parking_events_handler)
    router.exact("cmd_parking_all", parking_all_handler)
    router.exact("cmd_parking_history", parking_history_handler)
    router.exact("cmd_parking_history_7", parking_history_handler)
    router.exact("cmd_parking_history_30", parking_history_handler)
    router.prefix("parking_detail_", parking_detail_handler)
