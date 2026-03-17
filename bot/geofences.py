"""Geofence Alerts — list geofences and poll for entry/exit events."""

from datetime import datetime as _dt
from zoneinfo import ZoneInfo as _ZI
from math import radians, sin, cos, sqrt, atan2

_TZ_ET = _ZI("America/New_York")

from telegram import Update
from telegram.ext import ContextTypes, Application
from telegram.constants import ParseMode

from database import Role
from permissions import can
from samsara_client import COMPANY_DISPLAY, populate_company_display

from bot.config import db, logger, get_client
from bot.keyboards import back_kb, geofence_list_kb
from bot.helpers import _show, _show_loading
from bot.auth import _require_registered

try:
    import bot.redis_client as rcache
except ImportError:
    rcache = None


def _point_in_circle(lat: float, lng: float,
                     center_lat: float, center_lng: float,
                     radius_m: float) -> bool:
    """Check if a point is inside a circular geofence."""
    R = 6371000  # Earth radius in meters
    dlat = radians(lat - center_lat)
    dlng = radians(lng - center_lng)
    a = sin(dlat / 2) ** 2 + cos(radians(center_lat)) * cos(radians(lat)) * sin(dlng / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    distance = R * c
    return distance <= radius_m


def _point_in_polygon(lat: float, lng: float, vertices: list[dict]) -> bool:
    """Ray-casting point-in-polygon test."""
    n = len(vertices)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi = vertices[i].get("latitude", 0)
        xi = vertices[i].get("longitude", 0)
        yj = vertices[j].get("latitude", 0)
        xj = vertices[j].get("longitude", 0)
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _is_inside_geofence(lat: float, lng: float, geofence: dict) -> bool:
    """Check if a vehicle is inside a geofence (circle or polygon)."""
    # Circle geofence
    circle = geofence.get("circularGeofence")
    if circle:
        c_lat = circle.get("latitude", 0)
        c_lng = circle.get("longitude", 0)
        radius = circle.get("radiusMeters", 0)
        return _point_in_circle(lat, lng, c_lat, c_lng, radius)

    # Polygon geofence
    polygon = geofence.get("polygonGeofence", {})
    vertices = polygon.get("vertices", [])
    if vertices:
        return _point_in_polygon(lat, lng, vertices)

    return False


@_require_registered
async def cmd_geofences(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        company: str | None = None):
    """List geofences from Samsara."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_geofence_all") or can(user.role, "can_geofence_own")):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    companies = await db.get_account_companies(user.account_id)
    populate_company_display(companies)
    samsara = await get_client(user.account_id)

    await _show_loading(update, context, "⏳ Loading geofences…")

    try:
        geofences = await samsara.get_geofences(company=company)
        if not geofences:
            await _show(update, context, [
                "ℹ️ No geofences defined.\n\n"
                "Create geofences in the Samsara dashboard\n"
                "to see them here."
            ], keyboard=back_kb())
            return

        now_et = _dt.now(_TZ_ET)
        total = len(geofences)
        shown = min(total, 15)
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📍  <b>GEOFENCES</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  Showing {shown} of {total} geofence(s)\n"
            f"  {now_et:%b %d, %Y %I:%M %p ET}\n"
        )

        for gf in geofences[:15]:
            name = gf.get("name", "Unknown")
            org = gf.get("_org", "")
            gf_type = "🔵 Circle" if gf.get("circularGeofence") else "🟣 Polygon"
            text += f"\n  {gf_type} <b>{name}</b>"
            if org:
                text += f" [{org}]"

        kb = geofence_list_kb(geofences)
        await _show(update, context, [text], keyboard=kb)

    except Exception as e:
        logger.error(f"Geofences error: {e}")
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


async def check_geofence_events(app: Application):
    """Scheduled job: poll vehicle locations against geofences.

    Runs every 5 minutes. Compares current positions to geofence boundaries.
    On state change (enter/exit), sends alert to subscribed users.
    """
    try:
        accounts = await db.list_accounts()
        for account in accounts:
            companies = await db.get_account_companies(account.id)
            if not companies:
                continue

            try:
                samsara = await get_client(account.id)
            except Exception:
                continue

            try:
                geofences = await samsara.get_geofences()
                vehicles = await samsara.get_fleet_overview()
            except Exception as e:
                logger.debug(f"Geofence check for account {account.id}: {e}")
                continue

            if not geofences or not vehicles:
                continue

            for v in vehicles:
                loc = v.get("location", {})
                lat = loc.get("latitude")
                lng = loc.get("longitude")
                if lat is None or lng is None:
                    continue

                vid = v.get("id", v.get("name", ""))
                vname = v.get("name", "?")

                for gf in geofences:
                    gfid = gf.get("id", "")
                    gfname = gf.get("name", "?")
                    inside = _is_inside_geofence(lat, lng, gf)
                    state_key = f"geofence:{vid}:{gfid}"

                    # Get previous state
                    prev_state = None
                    if rcache and rcache.is_available():
                        raw = await rcache.get(state_key)
                        if raw is not None:
                            prev_state = raw.get("state")
                    current_state = "inside" if inside else "outside"

                    # Store current state
                    if rcache and rcache.is_available():
                        await rcache.set(state_key, {"state": current_state}, ttl=86400)

                    # Detect state change
                    if prev_state is not None and prev_state != current_state:
                        event = "entered" if inside else "exited"
                        emoji = "📍" if inside else "📤"

                        # Notify subscribers (all alert subscribers for this account)
                        subscribers = await db.get_alert_subscribers(account.id)
                        for sub in subscribers:
                            # Driver: only notify about own truck
                            if sub.role == Role.DRIVER and sub.truck_num:
                                if sub.truck_num.lower() not in vname.lower():
                                    continue
                            try:
                                await app.bot.send_message(
                                    chat_id=sub.telegram_id,
                                    text=(
                                        f"{emoji} <b>Geofence Alert</b>\n\n"
                                        f"  🚛 <b>{vname}</b> {event}\n"
                                        f"  📍 <b>{gfname}</b>\n"
                                    ),
                                    parse_mode=ParseMode.HTML,
                                )
                            except Exception as e:
                                logger.debug(f"Geofence notify {sub.telegram_id}: {e}")

    except Exception as e:
        logger.error(f"Geofence check error: {e}")
