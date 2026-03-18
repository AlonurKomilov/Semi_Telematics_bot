"""AI-powered fleet intelligence commands.

Features:
- 🤖 AI Assistant: natural language Q&A about fleet data
- 🔧 AI Diagnosis: plain-English fault code explanation
- 📊 AI Summary: executive morning briefing
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database import Role
from permissions import can

from bot.config import db, logger, get_client, get_user_company_codes
from bot.helpers import _show, _show_loading, _msg_key
from bot.keyboards import back_kb
from bot.auth import _require_registered

import ai_client


# ── Helpers ──────────────────────────────────────────────────────

async def _gather_fleet_snapshot(account_id: int,
                                 truck_num: str | None = None) -> dict:
    """Build a compact fleet data snapshot for AI context.

    If truck_num is provided (Driver role), only include that truck.
    """
    samsara = await get_client(account_id)
    snapshot: dict = {}

    try:
        fleet = await samsara.get_fleet_overview()
        if truck_num:
            fleet = [
                v for v in fleet
                if v.get("name", "").lower() == truck_num.lower()
            ]

        snapshot["total_vehicles"] = len(fleet)
        snapshot["vehicles"] = []
        for v in fleet[:50]:  # Cap to keep context reasonable
            entry: dict = {
                "name": v.get("name", "?"),
                "company": v.get("_org", "?"),
            }
            loc = v.get("location", {})
            if loc:
                entry["city"] = loc.get("reverseGeo", {}).get(
                    "formattedLocation", ""
                )
            fuel = v.get("fuel", {})
            if fuel.get("value") is not None:
                entry["fuel_pct"] = fuel["value"]
            dtcs = v.get("_dtcs", [])
            if dtcs:
                entry["fault_count"] = len(dtcs)
                entry["faults"] = [
                    {
                        "spn": d.get("spnId"),
                        "description": d.get("spnDescription", ""),
                        "severity": d.get("fmiDescription", ""),
                    }
                    for d in dtcs[:5]
                ]
            lights = v.get("_lights", {})
            if any(lights.get(k) for k in
                   ("stopIsOn", "protectIsOn", "emissionsIsOn", "warningIsOn")):
                entry["check_engine_lights"] = {
                    k: v for k, v in lights.items() if v
                }
            snapshot["vehicles"].append(entry)
    except Exception as e:
        logger.error(f"AI fleet snapshot failed: {e}")
        snapshot["error"] = str(e)

    # Health data
    try:
        health = await samsara.get_vehicle_health()
        if truck_num:
            health = [
                v for v in health
                if v.get("name", "").lower() == truck_num.lower()
            ]
        alerts_summary = []
        for v in health:
            h_alerts = v.get("_health_alerts", [])
            if h_alerts:
                alerts_summary.append({
                    "truck": v.get("name", "?"),
                    "alerts": h_alerts,
                    "battery_v": v.get("_health", {}).get("battery_v"),
                    "coolant_c": v.get("_health", {}).get("coolant_c"),
                    "oil_psi": v.get("_health", {}).get("oil_psi"),
                    "def_pct": v.get("_health", {}).get("def_pct"),
                })
        if alerts_summary:
            snapshot["health_alerts"] = alerts_summary
    except Exception as e:
        logger.debug(f"AI health snapshot skipped: {e}")

    # Counts
    faulted = [v for v in snapshot.get("vehicles", []) if v.get("fault_count")]
    low_fuel = [v for v in snapshot.get("vehicles", [])
                if v.get("fuel_pct") is not None and v["fuel_pct"] <= 20]
    snapshot["faulted_count"] = len(faulted)
    snapshot["low_fuel_count"] = len(low_fuel)

    return snapshot


def _ai_menu_kb() -> InlineKeyboardMarkup:
    """AI feature menu keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Ask a Question", callback_data="ai_ask")],
        [InlineKeyboardButton("📊 Fleet Summary", callback_data="ai_summary")],
        [InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")],
    ])


def _ai_back_kb() -> InlineKeyboardMarkup:
    """Back to AI menu."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 AI Menu", callback_data="cmd_ai")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


# ── Commands ─────────────────────────────────────────────────────

@_require_registered
async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the AI assistant menu."""
    if not ai_client.is_configured():
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  🤖  <b>AI ASSISTANT</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "  AI features are not configured.\n"
            "\n"
            "  Set <code>GOOGLE_AI_API_KEY</code> in\n"
            "  your environment to enable."
        )
        await _show(update, context, [text], keyboard=back_kb())
        return

    user = context.user_data["_db_user"]
    role_desc = "your truck" if user.role == Role.DRIVER else "your fleet"

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  🤖  <b>AI ASSISTANT</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  Ask me anything about {role_desc}!\n"
        "\n"
        "  <b>💬 Ask a Question</b>\n"
        "  Natural language queries — e.g.\n"
        "  <i>\"which trucks have low fuel?\"</i>\n"
        "  <i>\"what faults does truck 101 have?\"</i>\n"
        "\n"
        "  <b>📊 Fleet Summary</b>\n"
        "  AI-generated morning briefing\n"
        "  with key stats and action items."
    )
    await _show(update, context, [text], keyboard=_ai_menu_kb())


@_require_registered
async def cmd_ai_ask_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt the user to type their question."""
    if not ai_client.is_configured():
        if update.callback_query:
            await update.callback_query.answer("AI not configured", show_alert=True)
        return

    context.user_data["_pending"] = "ai_question"
    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  💬  <b>ASK AI</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "  Type your question about the fleet.\n"
        "\n"
        "  <b>Examples:</b>\n"
        "  • Which trucks need attention?\n"
        "  • What does fault SPN 110 mean?\n"
        "  • How many trucks are running right now?\n"
        "  • What's the fleet fuel status?\n"
        "\n"
        "  ✍️ Type below:"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cmd_ai")],
    ])
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_ai_answer(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        question: str):
    """Process the user's AI question and return the answer."""
    user = context.user_data["_db_user"]

    await _show_loading(update, context, "🤖  <i>Thinking...</i>")

    try:
        # Driver: only sees their own truck
        truck_filter = None
        if user.role == Role.DRIVER and user.truck_num:
            truck_filter = user.truck_num

        snapshot = await _gather_fleet_snapshot(
            user.account_id, truck_num=truck_filter,
        )
        answer = await ai_client.ask_fleet(question, snapshot)

        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  🤖  <b>AI ANSWER</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n{answer}"
        )
    except Exception as e:
        logger.error(f"AI answer failed: {e}")
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ❌  <b>AI ERROR</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "  Sorry, the AI couldn't process\n"
            "  your question right now.\n"
            f"\n  <i>{type(e).__name__}</i>"
        )

    await _show(update, context, [text], keyboard=_ai_back_kb())


@_require_registered
async def cmd_ai_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate an AI fleet summary/briefing."""
    if not ai_client.is_configured():
        if update.callback_query:
            await update.callback_query.answer("AI not configured", show_alert=True)
        return

    user = context.user_data["_db_user"]
    await _show_loading(update, context, "📊  <i>Generating fleet briefing...</i>")

    try:
        truck_filter = None
        if user.role == Role.DRIVER and user.truck_num:
            truck_filter = user.truck_num

        snapshot = await _gather_fleet_snapshot(
            user.account_id, truck_num=truck_filter,
        )
        summary = await ai_client.fleet_summary(snapshot)

        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📊  <b>AI FLEET BRIEFING</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n{summary}"
        )
    except Exception as e:
        logger.error(f"AI summary failed: {e}")
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ❌  <b>AI ERROR</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "  Couldn't generate the fleet briefing.\n"
            f"\n  <i>{type(e).__name__}</i>"
        )

    await _show(update, context, [text], keyboard=_ai_back_kb())


@_require_registered
async def cmd_ai_diagnose(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          truck_name: str, company: str | None = None):
    """AI-diagnose faults for a specific truck.

    Called from the truck detail view (callback: ai_diag_{company}_{truck}).
    """
    if not ai_client.is_configured():
        if update.callback_query:
            await update.callback_query.answer("AI not configured", show_alert=True)
        return

    user = context.user_data["_db_user"]
    await _show_loading(update, context, "🔧  <i>Analyzing faults...</i>")

    try:
        samsara = await get_client(user.account_id)
        matches = await samsara.get_vehicle_detail(truck_name, company=company)

        if not matches:
            text = f"  Truck #{truck_name} not found."
            await _show(update, context, [text], keyboard=_ai_back_kb())
            return

        v = matches[0]
        dtcs = v.get("_dtcs", [])
        lights = v.get("_lights", {})

        if not dtcs:
            text = (
                "━━━━━━━━━━━━━━━━━━━\n"
                "  ✅  <b>NO ACTIVE FAULTS</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"\n  Truck #{truck_name} has no active\n"
                "  fault codes to diagnose."
            )
            await _show(update, context, [text], keyboard=_ai_back_kb())
            return

        diagnosis = await ai_client.diagnose_faults(truck_name, dtcs, lights)

        co = v.get("_org", company or "?")
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  🔧  <b>AI DIAGNOSIS</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  🚛  <b>Truck #{truck_name}</b>\n"
            f"  {len(dtcs)} active fault(s)\n"
            f"\n{diagnosis}"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"📋 View Truck #{truck_name}",
                callback_data=f"cotruck_{co}_{truck_name}",
            )],
            [InlineKeyboardButton("🤖 AI Menu", callback_data="cmd_ai")],
            [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
        ])
    except Exception as e:
        logger.error(f"AI diagnosis failed for {truck_name}: {e}")
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ❌  <b>AI ERROR</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  Couldn't diagnose Truck #{truck_name}.\n"
            f"\n  <i>{type(e).__name__}</i>"
        )
        kb = _ai_back_kb()

    await _show(update, context, [text], keyboard=kb)
