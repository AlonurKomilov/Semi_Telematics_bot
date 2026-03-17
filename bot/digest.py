"""Daily/Weekly Digest — subscription management and scheduled delivery."""

from datetime import datetime as _dt, timezone as _tz_mod
from zoneinfo import ZoneInfo as _ZI

_TZ_ET = _ZI("America/New_York")
_tz_utc = _tz_mod.utc

from telegram import Update
from telegram.ext import ContextTypes, Application
from telegram.constants import ParseMode

from database import Role
from permissions import can, get_permissions
from samsara_client import populate_company_display

from bot.config import db, logger, get_client, get_user_company_codes
from bot.keyboards import back_kb, digest_menu_kb
from bot.helpers import _show
from bot.auth import _require_registered


@_require_registered
async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show digest subscription menu."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_digest"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    sub = await db.get_digest_subscription(user.id)
    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  📬  <b>FLEET DIGEST</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n  Receive automated fleet summaries\n"
        "  delivered to your Telegram chat.\n"
    )
    if sub:
        freq = sub.get("frequency", "daily")
        hour = sub.get("send_hour", 7)
        tz = sub.get("timezone", "America/New_York")
        tz_short = tz.split("/")[-1].replace("_", " ")
        text += f"\n  ✅ Subscribed: <b>{freq.title()}</b> at {hour:02d}:00"
        text += f"\n  🕐 Timezone: {tz_short}"
    else:
        text += "\n  Choose a delivery frequency:"

    await _show(update, context, [text], keyboard=digest_menu_kb(sub))


@_require_registered
async def cmd_digest_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               frequency: str = "daily"):
    """Subscribe to digest — starts a wizard for hour/timezone."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_digest"):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    # Store frequency, ask for delivery hour
    context.user_data["_digest_freq"] = frequency
    context.user_data["_pending"] = "digest_hour"

    from bot.keyboards import digest_hour_kb
    await _show(update, context, [
        f"📬 <b>{frequency.title()} Digest</b>\n\n"
        "  <b>What time should we send it?</b>\n"
        "  (In your local timezone)\n"
    ], keyboard=digest_hour_kb())


@_require_registered
async def cmd_digest_set_hour(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              hour: int = 7):
    """Set digest delivery hour, then ask for timezone."""
    user = context.user_data["_db_user"]
    context.user_data["_digest_hour"] = hour

    from bot.keyboards import digest_tz_kb
    await _show(update, context, [
        f"🕐 Delivery at <b>{hour:02d}:00</b>\n\n"
        "  <b>Select your timezone:</b>\n"
    ], keyboard=digest_tz_kb())


@_require_registered
async def cmd_digest_set_tz(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            tz: str = "America/New_York"):
    """Finalize digest subscription with timezone."""
    user = context.user_data["_db_user"]
    freq = context.user_data.pop("_digest_freq", "daily")
    hour = context.user_data.pop("_digest_hour", 7)
    context.user_data.pop("_pending", None)

    await db.subscribe_digest_ext(user.id, frequency=freq, send_hour=hour, timezone=tz)
    sub = await db.get_digest_subscription(user.id)

    tz_short = tz.split("/")[-1].replace("_", " ")
    await _show(update, context, [
        f"✅ Subscribed to <b>{freq}</b> digest!\n\n"
        f"  Delivery: {freq.title()} at {hour:02d}:00\n"
        f"  Timezone: {tz_short}\n"
        f"  You'll receive a fleet summary automatically."
    ], keyboard=digest_menu_kb(sub))


@_require_registered
async def cmd_digest_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unsubscribe from digest."""
    user = context.user_data["_db_user"]
    await db.unsubscribe_digest(user.id)
    await _show(update, context, [
        "🔕 Unsubscribed from digest.\n\n"
        "You can re-subscribe any time."
    ], keyboard=digest_menu_kb(None))


async def _build_digest_content(account_id: int, role: Role,
                                truck_num: str | None = None) -> str:
    """Build digest text based on role."""
    try:
        samsara = await get_client(account_id)
        companies = await db.get_account_companies(account_id)
        populate_company_display(companies)

        now_et = _dt.now(_TZ_ET)
        header = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📬  <b>FLEET DIGEST</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {now_et:%A, %b %d %Y}\n"
        )

        if role == Role.DRIVER and truck_num:
            # Driver gets own truck status
            vehicles = await samsara.get_fleet_overview()
            my_truck = [v for v in vehicles if truck_num.lower() in v["name"].lower()]
            if my_truck:
                v = my_truck[0]
                fc = v.get("fault_codes", {})
                j1939 = fc.get("j1939", {}) if isinstance(fc, dict) else {}
                dtcs = j1939.get("diagnosticTroubleCodes", [])
                faults = len(dtcs)
                fuel_data = v.get("fuel", {})
                fuel = fuel_data.get("value", "N/A") if isinstance(fuel_data, dict) else "N/A"
                return (
                    header +
                    f"\n  🚛 <b>{v['name']}</b>\n"
                    f"  🔧 Faults: {faults}\n"
                    f"  ⛽ Fuel: {fuel}%\n"
                )
            return header + "\n  ℹ️ No data for your truck."

        # Fleet-level digest for managers
        faulted, total, breakdown = await samsara.get_vehicles_with_faults()
        low_fuel = await samsara.get_low_fuel_vehicles()

        fault_count = len(faulted)
        low_fuel_count = len(low_fuel)

        content = header + (
            f"\n  🚛 Active trucks: <b>{total}</b>\n"
            f"  🔧 Trucks with faults: <b>{fault_count}</b>\n"
            f"  ⛽ Low fuel (<20%): <b>{low_fuel_count}</b>\n"
        )

        if role in (Role.DISPATCHER,):
            # Dispatcher gets actionable items
            if faulted:
                content += "\n  ⚠️ <b>Faulted trucks:</b>\n"
                for v in faulted[:5]:
                    content += f"    • {v['name']} ({len(v.get('_dtcs', []))} codes)\n"
            if low_fuel:
                content += "\n  ⛽ <b>Low fuel:</b>\n"
                for v in low_fuel[:5]:
                    content += f"    • {v['name']} — {v.get('_fuel_pct', '?')}%\n"

        return content
    except Exception as e:
        logger.error(f"Digest content build error: {e}")
        return "📬 <b>Fleet Digest</b>\n\n⚠️ Unable to fetch fleet data."


async def send_digests(app: Application):
    """Scheduled job: send digests to subscribers at their chosen local hour.

    Runs every hour. Uses timezone-aware subscriber matching.
    Weekly digests only send on Monday.
    """
    now = _dt.now(_tz_utc)
    current_hour = now.hour
    is_monday = now.weekday() == 0

    # Use the new timezone-aware subscriber lookup
    subscribers = await db.get_digest_subscribers_by_local_hour(current_hour)
    if not subscribers:
        return

    for sub in subscribers:
        freq = sub.get("frequency", "daily")
        if freq == "weekly" and not is_monday:
            continue

        try:
            role = Role.from_str(sub["role"])
            content = await _build_digest_content(
                sub["account_id"], role, sub.get("truck_num"),
            )
            await app.bot.send_message(
                chat_id=sub["telegram_id"],
                text=content,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.warning(f"Digest send to {sub.get('telegram_id')}: {e}")
