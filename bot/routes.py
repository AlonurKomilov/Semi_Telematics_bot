"""Route Replay — GPS history plotted on a static map."""

import asyncio
import io
from datetime import datetime as _dt, timedelta, timezone
from zoneinfo import ZoneInfo as _ZI

_TZ_ET = _ZI("America/New_York")

from telegram import Update
from telegram.ext import ContextTypes

from database import Role
from permissions import can
from samsara_client import COMPANY_DISPLAY, populate_company_display

from bot.config import db, logger, get_client
from bot.keyboards import back_kb, route_date_kb
from bot.helpers import _show, _show_loading
from bot.auth import _require_registered


def _render_route(gps_points: list[dict]) -> tuple[io.BytesIO | None, float]:
    """Render a route polyline on a static map.

    Returns (png_buffer, total_miles).
    """
    from staticmap import StaticMap, CircleMarker, Line

    if len(gps_points) < 2:
        return None, 0

    # Downsample if too many points
    if len(gps_points) > 500:
        step = len(gps_points) // 500
        sampled = gps_points[::step]
        if gps_points[-1] not in sampled:
            sampled.append(gps_points[-1])
        gps_points = sampled

    coords = []
    for pt in gps_points:
        lat = pt.get("latitude") or pt.get("lat")
        lng = pt.get("longitude") or pt.get("lng")
        if lat is not None and lng is not None:
            coords.append((float(lng), float(lat)))

    if len(coords) < 2:
        return None, 0

    # Calculate rough distance
    from math import radians, sin, cos, sqrt, atan2
    total_m = 0
    for i in range(1, len(coords)):
        lon1, lat1 = coords[i - 1]
        lon2, lat2 = coords[i]
        R = 3958.8  # Earth radius in miles
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        total_m += R * c

    m = StaticMap(800, 600, url_template="https://tile.openstreetmap.org/{z}/{x}/{y}.png")

    # Route line
    m.add_line(Line(coords, "#3b82f6", 3))

    # Start marker (green) and end marker (red)
    m.add_marker(CircleMarker(coords[0], "#22c55e", 12))
    m.add_marker(CircleMarker(coords[-1], "#ef4444", 12))

    image = m.render()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "route_replay.png"
    return buf, round(total_m, 1)


@_require_registered
async def cmd_route(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start route replay — show truck picker or auto-select for drivers."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_route_all") or can(user.role, "can_route_own")):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    # Driver with truck_num: auto-select
    if user.role == Role.DRIVER and user.truck_num:
        companies = await db.get_account_companies(user.account_id)
        co_code = companies[0].code if companies else ""
        kb = route_date_kb(user.truck_num, co_code)
        await _show(update, context, [
            f"🛣 <b>Route Replay</b>\n\n"
            f"  🚛 {user.truck_num}\n\n"
            "  Select a date:"
        ], keyboard=kb)
        return

    # Non-driver: ask for truck name
    context.user_data["_pending"] = "route_truck"
    await _show(update, context, [
        "🛣 <b>Route Replay</b>\n\n"
        "Type the truck number/name:"
    ], keyboard=back_kb())


@_require_registered
async def cmd_route_go(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       company: str = "", vehicle_name: str = "", days_ago: int = 0):
    """Fetch GPS history and render route map."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_route_all") or can(user.role, "can_route_own")):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    companies = await db.get_account_companies(user.account_id)
    populate_company_display(companies)
    samsara = await get_client(user.account_id)

    # Date range
    now = _dt.now(timezone.utc)
    end = now - timedelta(days=days_ago)
    start = end.replace(hour=0, minute=0, second=0) - timedelta(days=0)
    end = end.replace(hour=23, minute=59, second=59)

    date_label = (now - timedelta(days=days_ago)).strftime("%b %d, %Y")
    await _show_loading(update, context,
                        f"⏳ Fetching route for {vehicle_name} ({date_label})…")

    try:
        # Get GPS history using the per-company client
        client = samsara.clients.get(company)
        if not client:
            # Try first available
            company = samsara.company_codes[0] if samsara.company_codes else ""
            client = samsara.clients.get(company)
        if not client:
            await _show(update, context, ["❌ No Samsara client available."], keyboard=back_kb())
            return

        gps_data = await client._get_paginated_history("gps", start, end)

        # Find the vehicle
        points = []
        for vid, vdata in gps_data.items():
            if vehicle_name.lower() in vdata.get("name", "").lower():
                for pt in vdata.get("gps", []):
                    val = pt.get("value", pt)
                    if isinstance(val, dict):
                        points.append(val)
                    else:
                        points.append(pt)
                break

        if not points:
            await _show(update, context, [
                f"ℹ️ No GPS data for <b>{vehicle_name}</b> on {date_label}."
            ], keyboard=back_kb())
            return

        map_buf, miles = await asyncio.to_thread(_render_route, points)
        if map_buf is None:
            await _show(update, context, [
                "ℹ️ Not enough GPS points to render a route."
            ], keyboard=back_kb())
            return

        caption = (
            f"🛣 Route — {vehicle_name}\n"
            f"📅 {date_label}  ·  📏 {miles} mi\n"
            f"🟢 Start → 🔴 End"
        )

        chat_id = update.effective_chat.id
        await context.bot.send_photo(
            chat_id=chat_id, photo=map_buf, caption=caption,
        )
        await _show(update, context, [""], keyboard=back_kb())

    except ImportError:
        await _show(update, context, [
            "❌ Map rendering unavailable.\n"
            "Install: <code>pip install staticmap</code>"
        ], keyboard=back_kb())
    except Exception as e:
        logger.error(f"Route replay error: {e}")
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


async def handle_route_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle truck name input for route replay."""
    user = context.user_data.get("_db_user")
    if not user:
        return False

    pending = context.user_data.get("_pending", "")
    if pending != "route_truck":
        return False

    text = update.message.text.strip()
    context.user_data.pop("_pending", None)

    companies = await db.get_account_companies(user.account_id)
    co_code = companies[0].code if companies else ""
    kb = route_date_kb(text, co_code)
    await _show(update, context, [
        f"🛣 <b>Route Replay</b>\n\n"
        f"  🚛 {text}\n\n"
        "  Select a date:"
    ], keyboard=kb)
    return True
