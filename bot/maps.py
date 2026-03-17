"""Live Location Map — static PNG map with truck markers."""

import asyncio
import io
from datetime import datetime as _dt
from zoneinfo import ZoneInfo as _ZI

_TZ_ET = _ZI("America/New_York")

from telegram import Update
from telegram.ext import ContextTypes

from database import Role
from permissions import can
from samsara_client import COMPANY_DISPLAY, populate_company_display

from bot.config import db, logger, get_client
from bot.keyboards import back_kb, livemap_refresh_kb
from bot.helpers import _show, _show_loading
from bot.auth import _require_registered


def _render_map(vehicles: list[dict]) -> io.BytesIO:
    """Render a static map with vehicle markers.

    Green = moving (speed > 0), Yellow = idle, Red = stopped / no data.
    """
    from staticmap import StaticMap, CircleMarker

    m = StaticMap(800, 600, url_template="https://tile.openstreetmap.org/{z}/{x}/{y}.png")

    has_markers = False
    for v in vehicles:
        loc = v.get("location", {})
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        if lat is None or lng is None:
            continue

        speed = loc.get("speedMilesPerHour", 0) or 0
        if speed > 2:
            color = "#22c55e"  # green — moving
        elif speed > 0:
            color = "#eab308"  # yellow — idle/slow
        else:
            color = "#ef4444"  # red — stopped

        m.add_marker(CircleMarker((lng, lat), color, 10))
        has_markers = True

    if not has_markers:
        return None

    image = m.render()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "fleet_map.png"
    return buf


@_require_registered
async def cmd_livemap(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      company: str | None = None):
    """Generate and send live fleet location map."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_location_map") or can(user.role, "can_location_own")):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    companies = await db.get_account_companies(user.account_id)
    populate_company_display(companies)
    samsara = await get_client(user.account_id)
    company_label = COMPANY_DISPLAY.get(company, "All Companies") if company else "All Companies"

    await _show_loading(update, context, f"⏳ Generating map ({company_label})…")

    try:
        vehicles = await samsara.get_fleet_overview(company=company)

        # Driver: filter to own truck
        if user.role == Role.DRIVER and not can(user.role, "can_location_map"):
            if user.truck_num:
                vehicles = [v for v in vehicles if user.truck_num.lower() in v["name"].lower()]

        if not vehicles:
            await _show(update, context, [
                f"ℹ️ No vehicles found for <b>{company_label}</b>."
            ], keyboard=back_kb())
            return

        map_buf = await asyncio.to_thread(_render_map, vehicles)
        if map_buf is None:
            await _show(update, context, [
                "ℹ️ No GPS data available for map rendering."
            ], keyboard=back_kb())
            return

        now_et = _dt.now(_TZ_ET)
        moving = sum(1 for v in vehicles
                     if (v.get("location", {}).get("speedMilesPerHour", 0) or 0) > 2)
        caption = (
            f"🗺 Fleet Map — {company_label}\n"
            f"🚛 {len(vehicles)} trucks  ·  🟢 {moving} moving\n"
            f"📅 {now_et:%b %d, %I:%M %p ET}"
        )

        chat_id = update.effective_chat.id
        await context.bot.send_photo(
            chat_id=chat_id, photo=map_buf, caption=caption,
        )
        await _show(update, context, [""], keyboard=livemap_refresh_kb(company))

    except ImportError:
        await _show(update, context, [
            "❌ Map rendering unavailable.\n"
            "Install: <code>pip install staticmap</code>"
        ], keyboard=back_kb())
    except Exception as e:
        logger.error(f"Live map error: {e}")
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())
