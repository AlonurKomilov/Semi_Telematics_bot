"""AI-powered fleet intelligence commands.

Features:
- 🤖 AI Assistant: natural language Q&A about fleet data
- 🔧 AI Diagnosis: plain-English fault code explanation
- 📊 AI Summary: executive morning briefing
"""

import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database import Role
from permissions import can

from bot.config import db, logger, get_client, get_user_company_codes
from bot.helpers import _show, _show_loading, _msg_key, escape_html
from bot.keyboards import back_kb
from bot.auth import _require_registered

import ai_client

_SUGGESTION_RE = re.compile(r"^\s*>>\s*(.+)$", re.MULTILINE)

# Regex to detect truck number references in questions
_TRUCK_NUM_RE = re.compile(
    r'(?:truck|unit|vehicle)\s*#?\s*(\d{2,5})\b'
    r'|(?:#\s*(\d{2,5}))\b',
    re.IGNORECASE,
)


def _extract_truck_num(question: str) -> str | None:
    """Extract a truck number from the user's question, if mentioned."""
    m = _TRUCK_NUM_RE.search(question)
    if m:
        return m.group(1) or m.group(2)
    return None


# ── Helpers ──────────────────────────────────────────────────────

async def _gather_fleet_snapshot(account_id: int,
                                 truck_num: str | None = None,
                                 question: str | None = None) -> dict:
    """Build a compact fleet data snapshot for AI context.

    If truck_num is provided (Driver role), only include that truck.
    If question mentions a specific truck, focus on that truck but
    still include fleet-wide stats for context.
    """
    samsara = await get_client(account_id)
    snapshot: dict = {}

    # Smart heuristic: detect truck number in question
    focus_truck = truck_num
    if not focus_truck and question:
        focus_truck = _extract_truck_num(question)

    full_fleet: list = []
    try:
        fleet = await samsara.get_fleet_overview()

        # Always compute fleet-wide stats from the full fleet
        full_fleet = fleet

        if focus_truck:
            fleet = [
                v for v in fleet
                if v.get("name", "").lower() == focus_truck.lower()
            ]

        snapshot["total_vehicles"] = len(fleet)
        snapshot["vehicles"] = []
        for v in fleet[:50]:  # Cap to keep context reasonable
            entry: dict = {
                "name": v.get("name", "?"),
                "company": v.get("_org", "?"),
            }
            # Vehicle specs
            for key in ("make", "model", "year", "vin"):
                val = v.get(key)
                if val and val != "N/A":
                    entry[key] = val
            # Location + speed
            loc = v.get("location", {})
            if loc:
                entry["city"] = loc.get("reverseGeo", {}).get(
                    "formattedLocation", ""
                )
                speed = loc.get("speed")
                if speed is not None:
                    entry["speed_mph"] = round(speed * 0.621371, 1)
            # Fuel
            fuel = v.get("fuel", {})
            if fuel.get("value") is not None:
                entry["fuel_pct"] = fuel["value"]
            # DEF level
            def_lvl = v.get("def_level", {})
            if def_lvl.get("value") is not None:
                entry["def_pct"] = def_lvl["value"]
            # J1939 fault codes (DTCs)
            fc = v.get("fault_codes", {})
            j1939 = fc.get("j1939", {})
            dtcs = j1939.get("diagnosticTroubleCodes", [])
            cel = j1939.get("checkEngineLights", {})
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
            if any(cel.get(k) for k in
                   ("stopIsOn", "protectIsOn", "emissionsIsOn", "warningIsOn")):
                entry["check_engine_lights"] = {
                    k: lv for k, lv in cel.items() if lv
                }
            snapshot["vehicles"].append(entry)
    except Exception as e:
        logger.error(f"AI fleet snapshot failed: {e}")
        snapshot["error"] = str(e)

    # Health data — merge readings into vehicle entries + collect alerts
    try:
        health = await samsara.get_vehicle_health()
        if focus_truck:
            health = [
                v for v in health
                if v.get("name", "").lower() == focus_truck.lower()
            ]
        # Index health readings by truck name
        health_by_name: dict[str, dict] = {}
        for v in health:
            health_by_name[v.get("name", "")] = v.get("_health", {})

        # Merge health readings into snapshot vehicle entries
        for entry in snapshot.get("vehicles", []):
            h = health_by_name.get(entry["name"], {})
            if h.get("battery_v") is not None:
                entry["battery_v"] = h["battery_v"]
            if h.get("coolant_c") is not None:
                entry["coolant_c"] = h["coolant_c"]
            if h.get("oil_psi") is not None:
                entry["oil_psi"] = h["oil_psi"]
            if h.get("def_pct") is not None:
                entry["def_pct"] = h["def_pct"]
            if h.get("load_pct") is not None:
                entry["engine_load_pct"] = h["load_pct"]
            if h.get("engine_on") is not None:
                entry["engine_on"] = h["engine_on"]

    except Exception as e:
        logger.debug(f"AI health snapshot skipped: {e}")

    # Precomputed fleet-wide stats (always from full fleet, not filtered)
    # This gives the AI context even when focusing on one truck.
    stat_src = full_fleet if focus_truck else fleet
    vehicles = snapshot.get("vehicles", [])
    # Quick fault/fuel counts from raw API data (full fleet)
    def _count_faulted(vlist):
        return sum(
            1 for v in vlist
            if v.get("fault_codes", {}).get("j1939", {}).get("diagnosticTroubleCodes")
        )
    def _count_low_fuel(vlist, threshold=20):
        return sum(
            1 for v in vlist
            if (v.get("fuel", {}).get("value") or 100) <= threshold
        )
    running = sum(1 for v in vehicles if v.get("engine_on"))
    fuel_vals = [v["fuel_pct"] for v in vehicles if v.get("fuel_pct") is not None]
    snapshot["stats"] = {
        "total": len(stat_src),
        "running": running,
        "parked": len(vehicles) - running,
        "faulted": _count_faulted(stat_src),
        "low_fuel": _count_low_fuel(stat_src),
        "avg_fuel_pct": round(sum(fuel_vals) / len(fuel_vals)) if fuel_vals else None,
    }
    if focus_truck and not truck_num:
        snapshot["stats"]["note"] = f"Showing truck {focus_truck} detail; stats are fleet-wide"

    return snapshot


def _ai_menu_kb() -> InlineKeyboardMarkup:
    """AI feature menu keyboard."""
    rows = [
        [InlineKeyboardButton("💬 Chat with AI", callback_data="ai_chat")],
        [InlineKeyboardButton("📊 Fleet Summary", callback_data="ai_summary")],
    ]
    rows.append([InlineKeyboardButton("⚙️ AI Model", callback_data="ai_models")])
    rows.append([InlineKeyboardButton("📈 AI Usage", callback_data="cmd_ai_usage")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def _ai_chat_kb(suggestions: list[str] | None = None) -> InlineKeyboardMarkup:
    """Chat keyboard with optional AI suggestion buttons."""
    rows: list[list[InlineKeyboardButton]] = []
    if suggestions:
        for i, s in enumerate(suggestions[:3]):
            rows.append([InlineKeyboardButton(f"💡 {s}", callback_data=f"ai_sug_{i}")])
    rows.append([
        InlineKeyboardButton("🔄 New Chat", callback_data="ai_newchat"),
        InlineKeyboardButton("📋 Menu", callback_data="cmd_menu"),
    ])
    return InlineKeyboardMarkup(rows)


def _ai_back_kb() -> InlineKeyboardMarkup:
    """Back to AI menu with retry option."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Try Again", callback_data="ai_chat")],
        [InlineKeyboardButton("🤖 AI Menu", callback_data="cmd_ai")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def _parse_suggestions(text: str) -> tuple[str, list[str]]:
    """Extract >> suggestion lines from AI text.

    Returns (cleaned_text, list_of_suggestions).
    """
    suggestions = _SUGGESTION_RE.findall(text)
    cleaned = _SUGGESTION_RE.sub("", text).rstrip()
    return cleaned, [s.strip() for s in suggestions if s.strip()]


_FRIENDLY_ERRORS: dict[str, str] = {
    "ResourceExhausted": (
        "The AI service is temporarily busy (quota exceeded).\n"
        "Please try again in a minute."
    ),
    "DeadlineExceeded": (
        "The request took too long to process.\n"
        "Try a simpler question or try again shortly."
    ),
    "ServiceUnavailable": (
        "The AI service is temporarily unavailable.\n"
        "Please try again in a few moments."
    ),
    "InvalidArgument": (
        "There was an issue with the request.\n"
        "Try rephrasing your question."
    ),
    "PermissionDenied": (
        "AI service access is denied.\n"
        "Please contact your administrator."
    ),
}


def _friendly_error(exc: Exception) -> str:
    """Return a user-friendly error message for common AI exceptions."""
    name = type(exc).__name__
    # Check class name and also parent class names
    for cls in type(exc).__mro__:
        msg = _FRIENDLY_ERRORS.get(cls.__name__)
        if msg:
            return msg
    # Check if the string repr contains known keywords
    exc_str = str(exc).lower()
    if "quota" in exc_str or "rate" in exc_str:
        return _FRIENDLY_ERRORS["ResourceExhausted"]
    if "timeout" in exc_str:
        return _FRIENDLY_ERRORS["DeadlineExceeded"]
    return (
        "Something went wrong while processing\n"
        "your request. Please try again."
    )


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
        "  💬  <b>AI CHAT</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "  Type your question about the fleet.\n"
        "  I'll understand any language! 🌍\n"
        "\n"
        "  <b>Examples:</b>\n"
        "  • Which trucks need attention?\n"
        "  • What does fault SPN 110 mean?\n"
        "  • How many trucks are running right now?\n"
        "  • What's the fleet fuel status?\n"
        "\n"
        "  ✍️ <b>Type your question below:</b>"
    )
    # Quick-start suggestion buttons
    rows = [
        [InlineKeyboardButton("💡 Which trucks need attention?", callback_data="ai_sug_0")],
        [InlineKeyboardButton("💡 Fleet fuel status", callback_data="ai_sug_1")],
        [InlineKeyboardButton("💡 Any active health alerts?", callback_data="ai_sug_2")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cmd_ai")],
    ]
    context.user_data["_ai_suggestions"] = [
        "Which trucks need attention?",
        "Fleet fuel status",
        "Any active health alerts?",
    ]
    await _show(update, context, [text], keyboard=InlineKeyboardMarkup(rows))


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
            question=question,
        )
        # Ensure per-account model is loaded
        await ai_client.ensure_account_model(user.account_id)
        samsara = await get_client(user.account_id)
        result = await ai_client.ask_fleet_agent(
            question, snapshot, samsara,
            user_id=update.effective_user.id,
            account_id=user.account_id,
        )
        answer = result["text"]

        # Log AI usage
        usage = ai_client.get_last_usage()
        if usage:
            await db.log_ai_usage(
                account_id=user.account_id,
                user_id=update.effective_user.id,
                model=ai_client.get_model_for_account(user.account_id)[1],
                request_type="chat",
                prompt_tokens=usage.get("prompt_tokens", 0),
                reply_tokens=usage.get("reply_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            )

        # Parse >> suggestions from AI response into buttons
        clean_answer, suggestions = _parse_suggestions(answer)
        context.user_data["_ai_suggestions"] = suggestions

        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  🤖  <b>AI ANSWER</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n{escape_html(clean_answer)}"
        )

        # Stay in chat mode for follow-up questions
        context.user_data["_pending"] = "ai_question"
        await _show(update, context, [text],
                    keyboard=_ai_chat_kb(suggestions))
        return

    except Exception as e:
        logger.error(f"AI answer failed: {e}")
        friendly = _friendly_error(e)
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ❌  <b>AI ERROR</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {friendly}"
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
        await ai_client.ensure_account_model(user.account_id)
        summary = await ai_client.fleet_summary(
            snapshot, account_id=user.account_id,
        )

        # Log AI usage
        usage = ai_client.get_last_usage()
        if usage:
            await db.log_ai_usage(
                account_id=user.account_id,
                user_id=update.effective_user.id,
                model=ai_client.get_model_for_account(user.account_id)[1],
                request_type="summary",
                prompt_tokens=usage.get("prompt_tokens", 0),
                reply_tokens=usage.get("reply_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            )

        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📊  <b>AI FLEET BRIEFING</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n{escape_html(summary)}"
        )
    except Exception as e:
        logger.error(f"AI summary failed: {e}")
        friendly = _friendly_error(e)
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ❌  <b>AI ERROR</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {friendly}"
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

        await ai_client.ensure_account_model(user.account_id)
        diagnosis = await ai_client.diagnose_faults(
            truck_name, dtcs, lights,
            account_id=user.account_id,
        )

        # Log AI usage
        usage = ai_client.get_last_usage()
        if usage:
            await db.log_ai_usage(
                account_id=user.account_id,
                user_id=update.effective_user.id,
                model=ai_client.get_model_for_account(user.account_id)[1],
                request_type="diagnosis",
                prompt_tokens=usage.get("prompt_tokens", 0),
                reply_tokens=usage.get("reply_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            )

        co = v.get("_org", company or "?")
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  🔧  <b>AI DIAGNOSIS</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  🚛  <b>Truck #{truck_name}</b>\n"
            f"  {len(dtcs)} active fault(s)\n"
            f"\n{escape_html(diagnosis)}"
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
        friendly = _friendly_error(e)
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ❌  <b>AI ERROR</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {friendly}"
        )
        kb = _ai_back_kb()

    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_ai_suggest(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         index: int):
    """Handle a suggestion button press — feed it as a question."""
    suggestions = context.user_data.get("_ai_suggestions", [])
    if 0 <= index < len(suggestions):
        question = suggestions[index]
        await cmd_ai_answer(update, context, question=question)
    else:
        await cmd_ai_ask_prompt(update, context)


@_require_registered
async def cmd_ai_newchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear AI conversation history and start fresh."""
    uid = update.effective_user.id
    ai_client.clear_history(uid)
    context.user_data.pop("_ai_suggestions", None)
    await cmd_ai_ask_prompt(update, context)


# ── AI Model Management ─────────────────────────────────────────

@_require_registered
async def cmd_ai_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show AI model selection screen."""
    user = context.user_data["_db_user"]
    await ai_client.ensure_account_model(user.account_id)
    _, current, location = ai_client.get_model_for_account(user.account_id)

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  ⚙️  <b>AI MODEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  Current: <b>{current}</b>\n"
        f"  Region: <b>{location}</b>\n"
    )

    rows: list[list[InlineKeyboardButton]] = []
    for m in ai_client.get_available_models():
        name = m["name"]
        label = m["display"]
        if name == current:
            label += " ✅"
        rows.append([InlineKeyboardButton(label, callback_data=f"ai_setmodel_{name}")])

    rows.append([InlineKeyboardButton(
        "📍 Change Region", callback_data="ai_regions",
    )])
    rows.append([InlineKeyboardButton("🤖 AI Menu", callback_data="cmd_ai")])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])

    await _show(update, context, [text], keyboard=InlineKeyboardMarkup(rows))


@_require_registered
async def cmd_ai_set_model(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           model_name: str):
    """Switch to a different AI model (per-account)."""
    user = context.user_data["_db_user"]
    try:
        ai_client.switch_model(model_name, account_id=user.account_id)
        await ai_client.save_account_model(
            user.account_id, model_name,
            ai_client.get_model_for_account(user.account_id)[2],
        )
        if update.callback_query:
            await update.callback_query.answer(f"Switched to {model_name}")
    except ValueError as e:
        if update.callback_query:
            await update.callback_query.answer(str(e)[:200], show_alert=True)
        return
    await cmd_ai_models(update, context)


@_require_registered
async def cmd_ai_regions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available regions for the current model."""
    user = context.user_data["_db_user"]
    _, current_model, current_loc = ai_client.get_model_for_account(user.account_id)
    locations = ai_client.get_locations_for_model(current_model)

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  📍  <b>SELECT REGION</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  Model: <b>{current_model}</b>\n"
        f"  Current: <b>{current_loc}</b>\n"
    )

    rows: list[list[InlineKeyboardButton]] = []
    for loc in locations[:12]:  # Cap to avoid huge keyboard
        label = loc
        if loc == current_loc:
            label += " ✅"
        rows.append([InlineKeyboardButton(label, callback_data=f"ai_setloc_{loc}")])

    rows.append([InlineKeyboardButton("◀️ Back to Models", callback_data="ai_models")])

    await _show(update, context, [text], keyboard=InlineKeyboardMarkup(rows))


@_require_registered
async def cmd_ai_set_location(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              location: str):
    """Switch to a different region for the current model (per-account)."""
    user = context.user_data["_db_user"]
    _, current_model, _ = ai_client.get_model_for_account(user.account_id)
    try:
        ai_client.switch_model(current_model, location=location,
                               account_id=user.account_id)
        await ai_client.save_account_model(
            user.account_id, current_model, location,
        )
        if update.callback_query:
            await update.callback_query.answer(f"Region: {location}")
    except ValueError as e:
        if update.callback_query:
            await update.callback_query.answer(str(e)[:200], show_alert=True)
        return
    await cmd_ai_models(update, context)
