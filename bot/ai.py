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
from bot.i18n import t

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
        [InlineKeyboardButton(t('ai.btn_ask'), callback_data="ai_chat")],
        [InlineKeyboardButton(t('ai.btn_summary'), callback_data="ai_summary")],
    ]
    rows.append([InlineKeyboardButton(t('ai.btn_alerts'), callback_data="cmd_ai_alerts")])
    if user_role in (Role.OWNER, Role.ADMIN):
        model_name = ai_client.get_account_model_name(account_id) if account_id is not None else None
        if not model_name:
            model_name = ai_client.get_current_model_name()
        display = ai_client.MODEL_REGISTRY.get(model_name, {}).get("display", model_name)
        rows.append([InlineKeyboardButton(
            f"{t('ai.btn_model')}: {display}", callback_data="ai_models"
        )])
    rows.append([InlineKeyboardButton(t('common.back'), callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def _ai_back_kb() -> InlineKeyboardMarkup:
    """Back to AI menu."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t('ai.btn_ai_menu'), callback_data="cmd_ai")],
        [InlineKeyboardButton(t('common.main_menu'), callback_data="cmd_menu")],
    ])


def _ai_alerts_kb(user) -> InlineKeyboardMarkup:
    """AI proactive alerts toggle keyboard."""
    def _icon(on: bool) -> str:
        return "✅" if on else "❌"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{_icon(user.ai_fault)} {t('ai_alerts.fault_label')}",
            callback_data="ai_toggle_fault",
        )],
        [InlineKeyboardButton(
            f"{_icon(user.ai_health)} {t('ai_alerts.health_label')}",
            callback_data="ai_toggle_health",
        )],
        [InlineKeyboardButton(
            f"{_icon(user.ai_fuel)} {t('ai_alerts.fuel_label')}",
            callback_data="ai_toggle_fuel",
        )],
        [InlineKeyboardButton(
            f"{_icon(user.ai_events)} {t('ai_alerts.events_label')}",
            callback_data="ai_toggle_events",
        )],
        [InlineKeyboardButton(t('common.back'), callback_data="cmd_ai")],
    ])


async def cmd_ai_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show AI proactive alert settings."""
    user = context.user_data["_db_user"]
    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  🔔  <b>{t('ai_alerts.title')}</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  {t('ai_alerts.description')}\n"
        "\n"
        f"  {t('ai_alerts.tap_toggle')}"
    )
    await _show(update, context, [text], keyboard=_ai_alerts_kb(user))





# ── Commands ─────────────────────────────────────────────────────

@_require_registered
async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the AI assistant menu."""
    if not ai_client.is_configured():
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  🤖  <b>{t('ai.menu_title')}</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  {t('ai.not_configured')}\n"
            "\n"
            f"  {t('ai.set_api_key')}"
        )
        await _show(update, context, [text], keyboard=back_kb())
        return

    user = context.user_data["_db_user"]
    await ai_client.ensure_account_model(user.account_id)
    role_desc = t('ai.your_truck') if user.role == Role.DRIVER else t('ai.your_fleet')

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  🤖  <b>{t('ai.menu_title')}</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  {t('ai.ask_about').replace('{scope}', role_desc)}\n"
        "\n"
        f"  {t('ai.ask_label')}\n"
        f"  {t('ai.ask_hint')}\n"
        f"  <i>\"{t('ai.ask_hint_q1')}\"</i>\n"
        f"  <i>\"{t('ai.ask_hint_q2')}\"</i>\n"
        "\n"
        f"  {t('ai.summary_label')}\n"
        f"  {t('ai.summary_hint')}"
    )
    await _show(update, context, [text], keyboard=_ai_menu_kb(user_role=user.role, account_id=user.account_id))


@_require_registered
async def cmd_ai_ask_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt the user to type their question."""
    if not ai_client.is_configured():
        if update.callback_query:
            await update.callback_query.answer(t('ai.not_configured_popup'), show_alert=True)
        return

    context.user_data["_pending"] = "ai_question"
    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  💬  <b>{t('ai.ask_title')}</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  {t('ai.ask_prompt')}\n"
        "\n"
        f"  {t('ai.ask_examples')}\n"
        f"  • {t('ai.ask_example_1')}\n"
        f"  • {t('ai.ask_example_2')}\n"
        f"  • {t('ai.ask_example_3')}\n"
        f"  • {t('ai.ask_example_4')}\n"
        "\n"
        f"  {t('ai.ask_type_below')}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t('ai.cancel'), callback_data="cmd_ai")],
    ])
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_ai_answer(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        question: str):
    """Process the user's AI question and return the answer."""
    user = context.user_data["_db_user"]

    await _show_loading(update, context, t('ai.thinking'))

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
            language=getattr(user, "language", "en"),
        )

        await _log_ai_usage(user.account_id, update.effective_user.id, "question")

        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  🤖  <b>{t('ai.answer_title')}</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n{answer}"
        )
    except Exception as e:
        logger.error(f"AI answer failed: {e}")
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  ❌  <b>{t('ai.error_title')}</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  {t('ai.error_msg')}\n"
            f"\n  <i>{type(e).__name__}</i>"
        )

    await _show(update, context, [text], keyboard=_ai_back_kb())


@_require_registered
async def cmd_ai_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate an AI fleet summary/briefing."""
    if not ai_client.is_configured():
        if update.callback_query:
            await update.callback_query.answer(t('ai.not_configured_popup'), show_alert=True)
        return

    user = context.user_data["_db_user"]
    await _show_loading(update, context, t('ai.summary_thinking'))

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
            language=getattr(user, "language", "en"),
        )

        await _log_ai_usage(user.account_id, update.effective_user.id, "summary")

        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  📊  <b>{t('ai.summary_title')}</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n{summary}"
        )
    except Exception as e:
        logger.error(f"AI summary failed: {e}")
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  ❌  <b>{t('ai.error_title')}</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  {t('ai.summary_error')}\n"
            f"\n  <i>{type(e).__name__}</i>"
        )

    await _show(update, context, [text], keyboard=_ai_back_kb())


@_require_registered
async def cmd_ai_diagnose(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          truck_name: str, company: str | None = None,
                          alert_context: str = "fault",
                          ack_id: int | None = None):
    """AI-diagnose a truck from the alert context.

    alert_context controls what data is fetched and how the prompt is built:
      - "fault" (default): diagnose active DTCs / fault codes
      - "health": diagnose health readings (oil, coolant, battery, DEF) + any faults
      - "fuel": diagnose fuel situation + any faults

    Called from alert keyboards (callback: ai_diag_{alert_type}_{company}_{truck}).
    """
    if not ai_client.is_configured():
        if update.callback_query:
            await update.callback_query.answer(t('ai.not_configured_popup'), show_alert=True)
        return

    user = context.user_data["_db_user"]
    await _show_loading(update, context, t('ai.diagnose_thinking'))

    try:
        samsara = await get_client(user.account_id)
        matches = await samsara.get_vehicle_detail(truck_name, company=company)

        if not matches:
            text = f"  {t('ai.truck_not_found').replace('{name}', truck_name)}"
            await _show(update, context, [text], keyboard=_ai_back_kb())
            return

        v = matches[0]
        co = v.get("_org", company or "?")

        # Extract DTCs (always — used as bonus info for non-fault contexts)
        fc = v.get("fault_codes", {})
        j1939 = fc.get("j1939", {})
        dtcs = v.get("_dtcs") or j1939.get("diagnosticTroubleCodes", [])
        lights = v.get("_lights") or j1939.get("checkEngineLights", {})

        # ── Build context-aware prompt and fetch extra data ──────
        context_data: dict = {"truck": truck_name}
        prompt_parts: list[str] = []
        header_lines: list[str] = []

        if alert_context == "health":
            # Fetch live health readings
            health_vehicles = await samsara.get_vehicle_health(company=company)
            health = {}
            for hv in health_vehicles:
                if hv.get("name", "").lower() == truck_name.lower():
                    health = hv.get("_health", {})
                    break

            readings = {}
            if health.get("oil_psi") is not None:
                readings["Oil Pressure"] = f"{health['oil_psi']} PSI"
            if health.get("coolant_c") is not None:
                readings["Coolant Temp"] = f"{health['coolant_c']}°C"
            if health.get("battery_v") is not None:
                readings["Battery"] = f"{health['battery_v']}V"
            if health.get("def_pct") is not None:
                readings["DEF Level"] = f"{health['def_pct']}%"
            if health.get("rpm") is not None:
                readings["Engine RPM"] = str(int(health["rpm"]))
            if health.get("load_pct") is not None:
                readings["Engine Load"] = f"{health['load_pct']}%"

            context_data["health_readings"] = readings
            context_data["thresholds"] = {
                "Oil Pressure": "< 10 PSI is critical",
                "Coolant Temp": "> 105°C is critical",
                "Battery": "< 12.2V is low",
                "DEF Level": "< 10% is low",
            }
            prompt_parts.append(
                f"Diagnose the health condition of Truck #{truck_name}. "
                f"Current readings: {', '.join(f'{k}: {v}' for k, v in readings.items())}. "
                "Are any readings abnormal? What should the driver do?"
            )
            header_lines.append(f"  📊 {len(readings)} health reading(s)")

        elif alert_context == "fuel":
            # Fuel data is already in vehicle detail
            fuel = v.get("fuel", {})
            fuel_pct = fuel.get("value")
            def_info = v.get("def_level", {})
            def_pct = def_info.get("value")

            fuel_data: dict[str, str] = {}
            if fuel_pct is not None:
                fuel_data["Fuel Level"] = f"{fuel_pct:.0f}%"
            if def_pct is not None:
                fuel_data["DEF Level"] = f"{def_pct}%"

            context_data["fuel_data"] = fuel_data
            prompt_parts.append(
                f"Assess the fuel situation on Truck #{truck_name}. "
                f"Fuel level: {fuel_pct:.0f}% "
                + (f", DEF level: {def_pct}%" if def_pct is not None else "")
                + ". How urgent is this? Estimate remaining range. "
                "What should the driver do?"
            )
            header_lines.append(f"  ⛽ Fuel: {fuel_pct:.0f}%" if fuel_pct is not None else "  ⛽ Fuel data unavailable")

        # Default / fallback: fault diagnosis
        if alert_context == "fault" or not prompt_parts:
            if dtcs:
                context_data["active_faults"] = [
                    {
                        "spn": dtc.get("spnId"),
                        "fmi": dtc.get("fmiId"),
                        "description": dtc.get("spnDescription", "Unknown"),
                        "severity": dtc.get("fmiDescription", "Unknown"),
                    }
                    for dtc in dtcs[:10]
                ]
                context_data["check_engine_lights"] = lights or {}
                prompt_parts.insert(0, (
                    f"Diagnose the active faults on Truck #{truck_name}. "
                    f"There are {len(dtcs)} active fault code(s)."
                ))
                header_lines.insert(0, f"  ⚙️ {len(dtcs)} active fault(s)")

        # For health/fuel context, also mention faults as bonus if present
        if alert_context != "fault" and dtcs:
            context_data["active_faults"] = [
                {
                    "spn": dtc.get("spnId"),
                    "fmi": dtc.get("fmiId"),
                    "description": dtc.get("spnDescription", "Unknown"),
                }
                for dtc in dtcs[:5]
            ]
            prompt_parts.append(
                f"Also note: truck has {len(dtcs)} active fault code(s) — "
                "mention if any relate to the main issue."
            )
            header_lines.append(f"  ⚙️ + {len(dtcs)} active fault(s)")

        # If we have nothing to analyze at all
        if not prompt_parts:
            text = (
                "━━━━━━━━━━━━━━━━━━━\n"
                f"  ✅  <b>{t('ai.diagnose_no_issues_title')}</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"\n  {t('ai.diagnose_no_issues_msg').replace('{truck}', f'Truck #{truck_name}')}"
            )
            await _show(update, context, [text], keyboard=_ai_back_kb())
            return

        prompt = " ".join(prompt_parts)
        diagnosis = await ai_client.generate(
            prompt,
            system=ai_client.FAULT_DIAGNOSIS_SYSTEM,
            context_data=context_data,
            account_id=user.account_id,
            language=getattr(user, "language", "en"),
        )

        header_info = "\n".join(header_lines)
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  🔧  <b>{t('ai.diagnose_title')}</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  🚛  <b>Truck #{truck_name}</b>\n"
            f"{header_info}\n"
            f"\n{diagnosis}"
        )

        kb_rows = []
        # If called from an alert, keep alert action buttons
        if ack_id is not None:
            kb_rows.append([InlineKeyboardButton(
                t('alert_actions.acknowledge'), callback_data=f"ack_alert_{ack_id}",
            )])
            kb_rows.append([InlineKeyboardButton(
                t('alert_actions.snooze'), callback_data=f"snooze_pick_{ack_id}",
            )])
        kb_rows.append([InlineKeyboardButton(
            t('truck.view_truck').replace('{name}', truck_name),
            callback_data=f"cotruck_{co}_{truck_name}",
        )])
        if ack_id is None:
            kb_rows.append([InlineKeyboardButton(t('ai.btn_ai_menu'), callback_data="cmd_ai")])
        kb_rows.append([InlineKeyboardButton(t('common.main_menu'), callback_data="cmd_menu")])
        kb = InlineKeyboardMarkup(kb_rows)
    except Exception as e:
        logger.error(f"AI diagnosis failed for {truck_name}: {e}")
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  ❌  <b>{t('ai.error_title')}</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  {t('ai.diagnose_error_msg').replace('{name}', truck_name)}\n"
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
            await update.callback_query.answer(t('ai.not_configured_popup'), show_alert=True)
        return

    user = context.user_data["_db_user"]
    acct_id = user.account_id
    await ai_client.ensure_account_model(acct_id)

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
        f"  ⚙️  <b>{t('ai.model_title')}</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('ai.model_current').replace('{model}', cur_info.get('display', cur_model))}"
    ]
    lines.append(f"\n  {t('ai.model_select')}")

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
    buttons.append([InlineKeyboardButton(t('ai.btn_usage'), callback_data="cmd_ai_usage")])
    buttons.append([InlineKeyboardButton(t('ai.btn_ai_menu'), callback_data="cmd_ai")])
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
            f"  ✅  <b>{t('ai.model_changed')}</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  🤖 <b>{info['display']}</b>"
            f"\n  {info['description']}"
        )
    except ValueError as e:
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  ❌  <b>{t('ai.error_generic')}</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {e}"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t('ai.btn_models'), callback_data="ai_models")],
        [InlineKeyboardButton(t('ai.btn_ai_menu'), callback_data="cmd_ai")],
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
        await update.callback_query.answer(t('ai.chat_cleared'))
    await cmd_ai_ask_prompt(update, context)
