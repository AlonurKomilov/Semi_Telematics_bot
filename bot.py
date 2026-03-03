#!/usr/bin/env python3
"""
Semi Telematics Bot — single-window Telegram UI.
Each button press replaces the current view instead of spawning new messages.
"""

import os
import logging
from dotenv import load_dotenv

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from samsara_client import SamsaraClient
from formatters import (
    format_truck_detail,
    format_low_fuel,
    format_help,
    format_new_fault_alert,
)
from pdf_report import (
    generate_fault_report_pdf,
    generate_critical_report_pdf,
    compute_stats,
)

# ── Configuration ────────────────────────────────────────────────

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SAMSARA_API_KEY = os.getenv("SAMSARA_API_KEY")
SAMSARA_BASE_URL = os.getenv("SAMSARA_BASE_URL", "https://api.samsara.com")
ALERT_INTERVAL = int(os.getenv("ALERT_CHECK_INTERVAL_MINUTES", "30"))
FUEL_THRESHOLD = int(os.getenv("FUEL_LOW_THRESHOLD_PERCENT", "20"))

AUTHORIZED_CHAT_IDS = set()
raw_ids = os.getenv("TELEGRAM_CHAT_IDS", "")
for cid in raw_ids.split(","):
    cid = cid.strip()
    if cid:
        AUTHORIZED_CHAT_IDS.add(int(cid))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Globals ──────────────────────────────────────────────────────

samsara = SamsaraClient(SAMSARA_API_KEY, SAMSARA_BASE_URL)
_known_faults: dict[str, set[str]] = {}
_alerts_enabled: set[int] = set()

# Track bot message IDs per chat so we can delete old "windows"
# chat_id -> list of message_ids
_active_messages: dict[int, list[int]] = {}


# ═══════════════════════════════════════════════════════════════════
# KEYBOARDS
# ═══════════════════════════════════════════════════════════════════

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔧 Faults (PDF)", callback_data="cmd_faults"),
            InlineKeyboardButton("🚨 Critical (PDF)", callback_data="cmd_critical"),
        ],
        [
            InlineKeyboardButton("⛽ Fuel", callback_data="cmd_fuel"),
            InlineKeyboardButton("🔔 Alerts", callback_data="cmd_alerts"),
        ],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def faults_nav_kb(truck_names: list[str]) -> InlineKeyboardMarkup:
    rows = []
    chunk = []
    for name in truck_names[:20]:
        chunk.append(InlineKeyboardButton(f"#{name}", callback_data=f"truck_{name}"))
        if len(chunk) == 4:
            rows.append(chunk)
            chunk = []
    if chunk:
        rows.append(chunk)
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def truck_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔧 All Faults", callback_data="cmd_faults"),
            InlineKeyboardButton("🚨 Critical", callback_data="cmd_critical"),
        ],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


# ═══════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════

def authorized(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query:
            chat_id = update.callback_query.message.chat.id
        else:
            chat_id = update.effective_chat.id
        if AUTHORIZED_CHAT_IDS and chat_id not in AUTHORIZED_CHAT_IDS:
            if update.callback_query:
                await update.callback_query.answer("⛔ Unauthorized", show_alert=True)
            else:
                await update.message.reply_text("⛔ Unauthorized.")
            return
        return await func(update, context)
    return wrapper


# ═══════════════════════════════════════════════════════════════════
# SINGLE-WINDOW ENGINE
#
# The core idea:
#   • For short content (1 message): EDIT the existing message in-place.
#   • For long content (multi-part):  DELETE old messages, SEND new ones.
#   • Track all bot message IDs per chat to always clean up properly.
# ═══════════════════════════════════════════════════════════════════

async def _delete_old_messages(chat_id: int, bot):
    """Delete all tracked bot messages for this chat."""
    msg_ids = _active_messages.pop(chat_id, [])
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except BadRequest:
            pass  # Already deleted or too old


async def _show(update: Update, context: ContextTypes.DEFAULT_TYPE,
                texts: list[str], keyboard=None):
    """
    The single-window display function.

    If triggered by a button press AND content fits in 1 message:
        → Edit the button's own message (instant, no flicker).
    Otherwise:
        → Delete all old bot messages, send fresh ones.
    """
    query = update.callback_query
    bot = context.bot

    # Determine chat_id
    if query:
        chat_id = query.message.chat.id
    else:
        chat_id = update.effective_chat.id

    # CASE 1: Button press + single message → edit in place (smoothest UX)
    if query and len(texts) == 1:
        try:
            await query.edit_message_text(
                text=texts[0],
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            # The edited message keeps the same ID — update tracking
            _active_messages[chat_id] = [query.message.message_id]
            return
        except BadRequest as e:
            # If edit fails (e.g. message too old, identical content),
            # fall through to delete+send
            logger.debug(f"Edit failed, falling back to delete+send: {e}")

    # CASE 2: Delete old window, send new messages
    await _delete_old_messages(chat_id, bot)

    sent_ids = []
    for i, text in enumerate(texts):
        kb = keyboard if i == len(texts) - 1 else None
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        sent_ids.append(msg.message_id)

    _active_messages[chat_id] = sent_ids


async def _show_loading(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """
    Show a loading indicator.
    - Button press: edit the button message to show loading text.
    - Command: send a new loading message (will be cleaned up by _show later).
    """
    query = update.callback_query

    if query:
        try:
            await query.answer()  # Dismiss the button spinner
            await query.edit_message_text(
                text=text,
                parse_mode=ParseMode.HTML,
            )
            return  # The loading text is now in the existing message
        except BadRequest:
            pass

    # For typed commands — send a loading message and track it
    if update.message:
        chat_id = update.effective_chat.id
        msg = await update.message.reply_text(text)
        _active_messages[chat_id] = [msg.message_id]


# ═══════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════════

@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show(update, context, [format_help()], keyboard=main_menu_kb())


@authorized
async def cmd_faults(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_loading(update, context, "⏳ Generating fault report PDF…")

    try:
        faulted = await samsara.get_vehicles_with_faults()
        all_vehicles = await samsara.get_fault_codes()
        total = len(all_vehicles)
        stats = compute_stats(faulted, total)

        pdf_buf = generate_fault_report_pdf(faulted, total)

        query = update.callback_query
        chat_id = query.message.chat.id if query else update.effective_chat.id

        await _delete_old_messages(chat_id, context.bot)

        from datetime import datetime as _dt, timezone as _tz
        ts = _dt.now(_tz.utc).strftime("%Y-%m-%d_%H%M")

        caption = (
            "┌─────────────────────────┐\n"
            "  🔧  <b>FAULT CODE REPORT</b>\n"
            "└─────────────────────────┘\n"
            f"\n  📚  <b>{stats['total']}</b> trucks scanned\n"
            f"  🔴  <b>{stats['faulted']}</b> with faults  ·  "
            f"✅ {stats['clean']} clean\n"
            f"  📄  <b>{stats['total_dtcs']}</b> total fault codes\n"
            f"\n  🛑 {stats['stop']} STOP  ·  "
            f"♨️ {stats['emissions']} EMIS  ·  "
            f"⚠️ {stats['warning']} WARN  ·  "
            f"🔧 {stats['minor']} MINOR"
        )

        msg = await context.bot.send_document(
            chat_id=chat_id,
            document=pdf_buf,
            filename=f"Fault_Report_{ts}.pdf",
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
        _active_messages[chat_id] = [msg.message_id]

    except Exception as e:
        logger.error(f"Error in /faults: {e}")
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@authorized
async def cmd_truck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    truck_name = None
    if context.args:
        truck_name = " ".join(context.args)
    elif update.callback_query and update.callback_query.data.startswith("truck_"):
        truck_name = update.callback_query.data.replace("truck_", "")

    if not truck_name:
        await _show(update, context,
                     ["ℹ️  Type  /truck <b>NUMBER</b>\n\nExample:  /truck 134"],
                     keyboard=back_kb())
        return

    await _show_loading(update, context, f"⏳ Looking up #{truck_name}…")

    try:
        vehicle = await samsara.get_vehicle_detail(truck_name)
        if not vehicle:
            await _show(update, context,
                         [f"❌  Truck <b>#{truck_name}</b> not found.\n\nUse /fleet to see all trucks."],
                         keyboard=back_kb())
            return

        messages = format_truck_detail(vehicle)
        await _show(update, context, messages, keyboard=truck_kb())

    except Exception as e:
        logger.error(f"Error in /truck: {e}")
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@authorized
async def cmd_critical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_loading(update, context, "⏳ Checking critical faults…")

    try:
        critical = await samsara.get_critical_faults()
        all_vehicles = await samsara.get_fault_codes()
        total = len(all_vehicles)

        if not critical:
            await _show(update, context, [
                "┌─────────────────────────┐\n"
                "  ✅  <b>ALL CLEAR</b>\n"
                "└─────────────────────────┘\n"
                "\n"
                "  No STOP, PROTECT, or\n"
                "  EMISSIONS lights active.\n"
                "\n"
                "  All trucks running clean 👍"
            ], keyboard=main_menu_kb())
            return

        pdf_buf = generate_critical_report_pdf(critical, total)

        query = update.callback_query
        chat_id = query.message.chat.id if query else update.effective_chat.id

        await _delete_old_messages(chat_id, context.bot)

        from datetime import datetime as _dt, timezone as _tz
        ts = _dt.now(_tz.utc).strftime("%Y-%m-%d_%H%M")

        stop = sum(1 for v in critical if v.get('_lights', {}).get('stopIsOn'))
        emis = sum(1 for v in critical if v.get('_lights', {}).get('emissionsIsOn'))
        total_dtcs = sum(len(v.get('_dtcs', [])) for v in critical)

        caption = (
            "┌─────────────────────────┐\n"
            "  🚨  <b>CRITICAL FAULTS</b>\n"
            "└─────────────────────────┘\n"
            f"\n  🚛  <b>{len(critical)}</b> trucks need attention\n"
            f"  📄  <b>{total_dtcs}</b> fault codes\n"
            f"\n  🛑 {stop} STOP  ·  ♨️ {emis} EMISSIONS"
        )

        msg = await context.bot.send_document(
            chat_id=chat_id,
            document=pdf_buf,
            filename=f"Critical_Report_{ts}.pdf",
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
        _active_messages[chat_id] = [msg.message_id]

    except Exception as e:
        logger.error(f"Error in /critical: {e}")
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@authorized
async def cmd_fuel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_loading(update, context, "⏳ Checking fuel levels…")

    try:
        low = await samsara.get_low_fuel_vehicles(FUEL_THRESHOLD)
        text = format_low_fuel(low, FUEL_THRESHOLD)
        await _show(update, context, [text], keyboard=back_kb())

    except Exception as e:
        logger.error(f"Error in /fuel: {e}")
        await _show(update, context, [f"❌ Error: {e}"], keyboard=back_kb())


@authorized
async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        chat_id = update.callback_query.message.chat.id
    else:
        chat_id = update.effective_chat.id

    if chat_id in _alerts_enabled:
        _alerts_enabled.discard(chat_id)
        text = (
            "┌─────────────────────────┐\n"
            "  🔕  <b>ALERTS OFF</b>\n"
            "└─────────────────────────┘\n"
            "\n"
            "  Auto-notifications disabled.\n"
            "  Tap 🔔 Alerts to re-enable."
        )
    else:
        _alerts_enabled.add(chat_id)
        text = (
            "┌─────────────────────────┐\n"
            "  🔔  <b>ALERTS ON</b>\n"
            "└─────────────────────────┘\n"
            "\n"
            f"  Checking every {ALERT_INTERVAL} min.\n"
            "  You'll get notified of\n"
            "  new critical faults.\n"
            "\n"
            "  Tap 🔔 Alerts to disable."
        )

    await _show(update, context, [text], keyboard=main_menu_kb())


# ═══════════════════════════════════════════════════════════════════
# CALLBACK ROUTER (all button taps go here)
# ═══════════════════════════════════════════════════════════════════

@authorized
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "cmd_menu":
        await query.answer()
        await _show(update, context, [format_help()], keyboard=main_menu_kb())

    elif data == "cmd_faults":
        await cmd_faults(update, context)

    elif data == "cmd_critical":
        await cmd_critical(update, context)

    elif data == "cmd_fuel":
        await cmd_fuel(update, context)

    elif data == "cmd_alerts":
        await cmd_alerts(update, context)

    elif data.startswith("truck_"):
        await cmd_truck(update, context)

    else:
        await query.answer("Unknown action")


# ═══════════════════════════════════════════════════════════════════
# SCHEDULED ALERTS
# ═══════════════════════════════════════════════════════════════════

async def check_new_faults(app: Application):
    global _known_faults

    if not _alerts_enabled:
        return

    try:
        faulted = await samsara.get_vehicles_with_faults()

        for v in faulted:
            vid = str(v["id"])
            current_codes = set()
            for dtc in v.get("_dtcs", []):
                key = f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}"
                current_codes.add(key)

            previously_known = _known_faults.get(vid, set())
            new_codes = current_codes - previously_known

            if new_codes and previously_known:
                new_dtcs = [
                    dtc for dtc in v.get("_dtcs", [])
                    if f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}" in new_codes
                ]
                if new_dtcs:
                    alert_text = format_new_fault_alert(v, new_dtcs)
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            f"📋 View Truck #{v['name']}",
                            callback_data=f"truck_{v['name']}"
                        )],
                        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
                    ])
                    for chat_id in _alerts_enabled:
                        try:
                            msg = await app.bot.send_message(
                                chat_id=chat_id,
                                text=alert_text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=kb,
                            )
                            # Track alert message so it gets cleaned up too
                            _active_messages.setdefault(chat_id, []).append(msg.message_id)
                        except Exception as e:
                            logger.error(f"Alert failed for {chat_id}: {e}")

            _known_faults[vid] = current_codes

    except Exception as e:
        logger.error(f"Fault check error: {e}")


# ═══════════════════════════════════════════════════════════════════
# INIT
# ═══════════════════════════════════════════════════════════════════

async def initialize_known_faults():
    global _known_faults
    try:
        faulted = await samsara.get_vehicles_with_faults()
        for v in faulted:
            vid = str(v["id"])
            codes = set()
            for dtc in v.get("_dtcs", []):
                key = f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}"
                codes.add(key)
            _known_faults[vid] = codes
        logger.info(f"Known faults loaded for {len(_known_faults)} vehicles")
    except Exception as e:
        logger.error(f"Init faults failed: {e}")


async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "🏠 Main menu"),
        BotCommand("faults", "🔧 Fault report (PDF)"),
        BotCommand("truck", "🚛 Truck detail"),
        BotCommand("critical", "🚨 Critical faults (PDF)"),
        BotCommand("fuel", "⛽ Low fuel"),
        BotCommand("alerts", "🔔 Auto-alerts"),
        BotCommand("help", "ℹ️ Help"),
    ])
    logger.info("Commands set")

    await initialize_known_faults()

    startup = (
        "╔══════════════════════════╗\n"
        "     🟢  <b>Bot is Online</b>\n"
        "╚══════════════════════════╝\n"
        "\n"
        "  Semi Telematics ready.\n"
        "  Tap a button to begin ▾"
    )
    for chat_id in AUTHORIZED_CHAT_IDS:
        try:
            msg = await app.bot.send_message(
                chat_id=chat_id,
                text=startup,
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_kb(),
            )
            _active_messages[chat_id] = [msg.message_id]
        except Exception as e:
            logger.warning(f"Startup msg to {chat_id} failed: {e}")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    if not SAMSARA_API_KEY:
        raise RuntimeError("SAMSARA_API_KEY not set")

    logger.info("Starting Semi Telematics Bot…")

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("faults", cmd_faults))
    app.add_handler(CommandHandler("truck", cmd_truck))
    app.add_handler(CommandHandler("critical", cmd_critical))
    app.add_handler(CommandHandler("fuel", cmd_fuel))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CallbackQueryHandler(handle_callback))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_new_faults, "interval",
        minutes=ALERT_INTERVAL, args=[app], id="fault_check",
    )
    scheduler.start()

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
