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


async def _log_ai_usage(account_id: int, telegram_user_id: int, action: str):
    """Log AI token usage for the last AI call."""
    usage = ai_client.get_last_usage()
    model_name = ai_client.get_account_model_name(account_id) or ai_client.get_current_model_name()
    if usage:
        await db.log_ai_usage(
            account_id, telegram_user_id,
            model_name, action,
            usage.get("prompt_tokens", 0),
            usage.get("reply_tokens", 0),
            usage.get("total_tokens", 0),
        )


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


def _ai_menu_kb(user_role=None, account_id=None) -> InlineKeyboardMarkup:
    """AI feature menu keyboard."""
    rows = [
        [InlineKeyboardButton("💬 Ask a Question", callback_data="ai_chat")],
        [InlineKeyboardButton("📊 Fleet Summary", callback_data="ai_summary")],
    ]
    if user_role in (Role.OWNER, Role.ADMIN):
        model_name = ai_client.get_account_model_name(account_id) if account_id is not None else None
        if not model_name:
            model_name = ai_client.get_current_model_name()
        display = ai_client.MODEL_REGISTRY.get(model_name, {}).get("display", model_name)
        rows.append([InlineKeyboardButton(
            f"⚙️ Model: {display}", callback_data="ai_models"
        )])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


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
    await _show(update, context, [text], keyboard=_ai_menu_kb(user_role=user.role, account_id=user.account_id))


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

        await ai_client.ensure_account_model(user.account_id)
        snapshot = await _gather_fleet_snapshot(
            user.account_id, truck_num=truck_filter,
        )
        answer = await ai_client.ask_fleet(
            question, snapshot, user_id=update.effective_user.id,
            account_id=user.account_id,
        )

        await _log_ai_usage(user.account_id, update.effective_user.id, "question")

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
        await ai_client.ensure_account_model(user.account_id)
        truck_filter = None
        if user.role == Role.DRIVER and user.truck_num:
            truck_filter = user.truck_num

        snapshot = await _gather_fleet_snapshot(
            user.account_id, truck_num=truck_filter,
        )
        summary = await ai_client.fleet_summary(
            snapshot, account_id=user.account_id,
        )

        await _log_ai_usage(user.account_id, update.effective_user.id, "summary")

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
        # Extract DTCs from raw fault_codes (same logic as get_vehicles_with_faults)
        fc = v.get("fault_codes", {})
        j1939 = fc.get("j1939", {})
        dtcs = v.get("_dtcs") or j1939.get("diagnosticTroubleCodes", [])
        lights = v.get("_lights") or j1939.get("checkEngineLights", {})

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


# ── Model Selection ──────────────────────────────────────────────

@_require_registered
async def cmd_ai_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available AI models for the user to choose from."""
    if not ai_client.is_configured():
        if update.callback_query:
            await update.callback_query.answer("AI not configured", show_alert=True)
        return

    user = context.user_data["_db_user"]
    acct_id = user.account_id

    # Determine current model for this account
    cur_model = ai_client.get_current_model_name()
    cur_loc = ai_client.get_current_location()
    acct_info = ai_client.get_account_model_info(acct_id)
    if acct_info:
        cur_model, cur_loc, _ = acct_info

    models = ai_client.get_available_models()
    cur_info = ai_client.MODEL_REGISTRY.get(cur_model, {})

    lines = [
        "━━━━━━━━━━━━━━━━━━━\n"
        "  ⚙️  <b>AI MODEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  Current: <b>{cur_info.get('display', cur_model)}</b>"
    ]
    lines.append("\n  Select a model:")

    text = "\n".join(lines)

    buttons = []
    for m in models:
        name = m["name"]
        disp = m["display"]
        est = m.get("est_cost", 0)
        price_tag = f"~${est:.3f}" if est < 0.01 else f"~${est:.2f}"
        prefix = "✅ " if name == cur_model else ""
        label = f"{prefix}{disp} · {price_tag}"
        buttons.append([InlineKeyboardButton(
            label, callback_data=f"ai_setmodel_{name}",
        )])
    buttons.append([InlineKeyboardButton("📊 AI Usage", callback_data="cmd_ai_usage")])
    buttons.append([InlineKeyboardButton("◀️ AI Menu", callback_data="cmd_ai")])
    kb = InlineKeyboardMarkup(buttons)
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_ai_set_model(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           model_name: str):
    """Switch to a different AI model (auto-selects best region)."""
    user = context.user_data["_db_user"]
    acct_id = user.account_id

    try:
        ai_client.switch_model(model_name, account_id=acct_id)
        loc = ai_client.MODEL_REGISTRY[model_name]["locations"][0]
        await ai_client.save_account_model(acct_id, model_name, loc)

        info = ai_client.MODEL_REGISTRY[model_name]
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ✅  <b>MODEL CHANGED</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  🤖 <b>{info['display']}</b>"
            f"\n  {info['description']}"
        )
    except ValueError as e:
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ❌  <b>ERROR</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {e}"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Models", callback_data="ai_models")],
        [InlineKeyboardButton("🤖 AI Menu", callback_data="cmd_ai")],
    ])
    await _show(update, context, [text], keyboard=kb)


# ── Suggest / New Chat ───────────────────────────────────────────

@_require_registered
async def cmd_ai_suggest(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         index: int = -1):
    """Handle follow-up suggestion tap (>> lines in AI responses)."""
    suggestions = context.user_data.get("_ai_suggestions", [])
    if 0 <= index < len(suggestions):
        question = suggestions[index]
        await cmd_ai_answer(update, context, question=question)
    else:
        await cmd_ai_ask_prompt(update, context)


@_require_registered
async def cmd_ai_newchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear conversation history and start fresh."""
    uid = update.effective_user.id
    ai_client.clear_history(uid)
    if update.callback_query:
        await update.callback_query.answer("Chat history cleared")
    await cmd_ai_ask_prompt(update, context)
