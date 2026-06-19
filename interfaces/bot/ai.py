"""AI-powered telematics intelligence commands.

Features:
- 🤖 AI Assistant: natural language Q&A about fleet data
- 🔧 AI Diagnosis: plain-English fault code explanation
- 📊 AI Summary: executive morning briefing
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from adapters.storage import Role
from capabilities.permissions.roles import can, is_management_role

from interfaces.bot.config import logger
from interfaces.bot.state import get_client, get_platform_db, get_tenant_db
from interfaces.bot.helpers import _show, _show_loading, _edit_active, escape_html
from interfaces.bot.keyboards import back_kb
from interfaces.bot.auth import _require_registered
from capabilities.localization.i18n import t

import capabilities.ai as ai
from capabilities.ai.usage import build_user_ai_context, log_ai_usage as _log_ai_usage_fn, parse_ai_suggestions as _parse_suggestions


# ── Helpers ──────────────────────────────────────────────────────

def _sanitize_ai_html(text: str) -> str:
    """Sanitize AI output for Telegram HTML parse mode.

    Telegram supports a tiny HTML subset (``<b> <i> <u> <s> <a> <code>
    <pre>``).  The shared AI prompt asks the model to emit richer HTML
    (``<p>`` paragraphs, ``<ul>/<li>`` lists, ``<br>`` breaks, ``<h1>``
    headings) because the dashboard renders that natively — but in
    Telegram those tags fall through to ``&lt;p&gt;`` literals that
    show up as visible angle-bracket noise.

    This function rewrites the structural tags into Telegram-friendly
    equivalents BEFORE escaping the remainder, so a model response of
    ``<p>Hello</p><ul><li>One</li><li>Two</li></ul>`` becomes
    ``Hello\n\n• One\n• Two\n`` instead of literal markup.  The
    Telegram-supported inline tags (``<b>`` etc.) survive the escape
    via the re-enable pass at the end.
    """
    import re

    s = text or ""

    # ── 1) Strip code-block fences the AI sometimes emits.
    # The model occasionally wraps the whole reply in ```html ... ```.
    # Drop the fence markers; keep the inner content.
    s = re.sub(r"```(?:html)?\s*", "", s)
    s = s.replace("```", "")

    # ── 2) Block-level structural tags → newlines / bullet text.
    # Order matters: do <li> before <ul>/<ol> so the bullet survives.
    s = re.sub(r"<\s*li\s*>", "• ", s, flags=re.IGNORECASE)
    s = re.sub(r"<\s*/\s*li\s*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<\s*/?\s*(?:ul|ol)\s*>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<\s*p\s*>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<\s*/\s*p\s*>", "\n\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.IGNORECASE)
    # Headings → bold + newline.  h1..h6 in one pass.
    s = re.sub(r"<\s*h[1-6]\s*>", "<b>", s, flags=re.IGNORECASE)
    s = re.sub(r"<\s*/\s*h[1-6]\s*>", "</b>\n", s, flags=re.IGNORECASE)
    # <strong>/<em> are synonyms for <b>/<i>; map them so the
    # re-enable pass below keeps the formatting.
    s = re.sub(r"<\s*strong\s*>", "<b>", s, flags=re.IGNORECASE)
    s = re.sub(r"<\s*/\s*strong\s*>", "</b>", s, flags=re.IGNORECASE)
    s = re.sub(r"<\s*em\s*>", "<i>", s, flags=re.IGNORECASE)
    s = re.sub(r"<\s*/\s*em\s*>", "</i>", s, flags=re.IGNORECASE)
    # <div>/<span> wrappers — keep inner text, drop the tag.
    s = re.sub(r"<\s*/?\s*(?:div|span)\s*[^>]*>", "", s, flags=re.IGNORECASE)
    # Collapse 3+ consecutive newlines down to 2 so heavy paragraph
    # use doesn't push the message into a wall of whitespace.
    s = re.sub(r"\n{3,}", "\n\n", s)

    # ── 3) Escape everything else, then re-enable the safe inline tags.
    escaped = escape_html(s)
    for tag in ("b", "i", "code", "pre", "u", "s"):
        escaped = escaped.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        escaped = escaped.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return escaped


async def _build_user_context(user) -> dict:
    """Build the AI user-context + attach the Vehicle-Access data scope.

    ``scoped_vehicle_nums`` is resolved from the same Team Management
    Vehicle Access SSOT the dashboard uses (All / Company / Vehicle), so the
    bot's AI honors the exact scope set for the user.  On error we leave the
    key UNSET so the gate's back-compat path still isolates drivers via their
    assigned trucks rather than failing open.
    """
    ctx = build_user_ai_context(user)
    try:
        from capabilities.ai.scope import resolve_vehicle_scope
        ctx["scoped_vehicle_nums"] = await resolve_vehicle_scope(
            get_platform_db(), user.account_id, user.id,
            getattr(user, "role", None), truck_num=getattr(user, "truck_num", None),
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("AI scope resolution failed (bot), using fallback: %s", e)
    return ctx

async def _log_ai_usage(account_id: int, telegram_user_id: int, action: str,
                        usage: dict | None,
                        *,
                        role: str | None = None,
                        latency_ms: int | None = None,
                        tool_success_count: int | None = None):
    """Delegates to the shared logger in capabilities.ai.usage.

    Passes router telemetry (``role`` / ``latency_ms`` /
    ``tool_success_count``) when the caller has it so the bot's
    per-account chat history feeds the same scorer as the dashboard.
    """
    platform_db = get_platform_db()  # synchronous — returns PlatformDB directly
    await _log_ai_usage_fn(
        ai, platform_db, account_id, telegram_user_id, action, usage,
        role=role, latency_ms=latency_ms,
        tool_success_count=tool_success_count,
    )


async def _dtcs_from_ack(account_id: int, ack_id: int) -> list[dict]:
    """Recover DTC info from an alert_acknowledgments record.

    When a fault auto-resolves before the user taps AI Diagnose,
    live data shows no DTCs.  The alert_key stores the original fault
    details as ``co:vid:SPN-FMI:desc|SPN-FMI:desc``.
    """
    tenant = await get_tenant_db(account_id)
    row = await tenant.get_alert_ack_by_id(ack_id)
    if not row:
        return []
    key = row.get("alert_key", "")
    # Format: "co:vid:detail1|detail2"
    parts = key.split(":", 2)
    if len(parts) < 3 or not parts[2]:
        return []
    detail_str = parts[2]
    dtcs: list[dict] = []
    for item in detail_str.split("|"):
        # Each item: "SPN-FMI:description"
        spn_fmi, _, desc = item.partition(":")
        spn_str, _, fmi_str = spn_fmi.partition("-")
        try:
            spn = int(spn_str)
        except ValueError:
            spn = spn_str
        try:
            fmi = int(fmi_str)
        except ValueError:
            fmi = fmi_str
        dtcs.append({
            "spnId": spn,
            "fmiId": fmi,
            "spnDescription": desc or "Unknown",
            "fmiDescription": "From alert history",
            "_historical": True,
        })
    return dtcs


async def _gather_vehicles_snapshot(account_id: int,
                                 vehicle_num: str | None = None) -> dict:
    """Build a compact fleet data snapshot for AI context.

    Delegates to the shared builder in ai.intelligence.
    """
    return await ai.build_context(account_id, vehicle_num=vehicle_num)


def _ai_menu_kb(user_role=None, account_id=None) -> InlineKeyboardMarkup:
    """AI feature menu keyboard."""
    rows = [
        [InlineKeyboardButton("💬 Ask a Question", callback_data="ai_chat")],
        [InlineKeyboardButton("📊 Fleet Summary", callback_data="ai_summary")],
    ]
    rows.append([InlineKeyboardButton(
        "🤖 AI Alert Analysis", callback_data="cmd_ai_alerts",
    )])
    if is_management_role(user_role):
        # Text model button
        text_model = ai.get_account_model_name(account_id) if account_id is not None else None
        if not text_model:
            text_model = ai.get_current_model_name()
        text_disp = ai.MODEL_REGISTRY.get(text_model, {}).get("display", text_model)
        rows.append([InlineKeyboardButton(
            f"💬 Text: {text_disp}", callback_data="ai_models_text",
        )])
        # Vision model button
        vision_model = ai.get_account_vision_model_name(account_id) if account_id is not None else None
        if not vision_model:
            vision_model = ai.DEFAULT_VISION_MODEL
        vision_disp = ai.MODEL_REGISTRY.get(vision_model, {}).get("display", vision_model)
        rows.append([InlineKeyboardButton(
            f"👁 Vision: {vision_disp}", callback_data="ai_models_vision",
        )])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def _ai_back_kb() -> InlineKeyboardMarkup:
    """Back to AI menu."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 AI Menu", callback_data="cmd_ai")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def _ai_chat_kb() -> InlineKeyboardMarkup:
    """Keyboard shown when entering chat mode."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 New Chat", callback_data="ai_newchat")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def _build_chat_kb(suggestions: list[str] | None = None) -> InlineKeyboardMarkup:
    """Build chat keyboard with optional AI-suggested follow-up questions."""
    rows = []
    if suggestions:
        for i, s in enumerate(suggestions[:5]):
            # Truncate display label to keep it readable
            label = s if len(s) <= 50 else f"{s[:47]}…"
            rows.append([InlineKeyboardButton(label, callback_data=f"ai_sug_{i}")])
    rows.append([InlineKeyboardButton("🤖 AI Menu", callback_data="cmd_ai")])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)



# ── Commands ─────────────────────────────────────────────────────

@_require_registered
async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the AI assistant menu."""
    if not ai.is_configured():
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
    await ai.ensure_account_model(user.account_id)
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
    await _show(update, context, [text], keyboard=_ai_menu_kb(
        user_role=user.role, account_id=user.account_id,
    ))


@_require_registered
async def cmd_ai_ask_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt the user to type their question."""
    if not ai.is_configured():
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


def _format_ai_progress(steps: list[str]) -> str:
    """Render the live tool-call checklist shown while the agent works.

    Completed steps get a ✓; the most recent gets a spinner.  Keeps the
    user oriented during multi-tool answers instead of a static
    "Thinking…".  When Bot API 10.1 rich drafts land in PTB, this same
    step list maps directly onto a RichBlockThinking stream — the
    progress model is already in the right shape.
    """
    lines = ["🤖  <i>Working on it…</i>", ""]
    for s in steps[:-1]:
        lines.append(f"  ✓ {escape_html(s)}")
    if steps:
        lines.append(f"  ⚙️ {escape_html(steps[-1])}…")
    return "\n".join(lines)


@_require_registered
async def cmd_ai_answer(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        question: str):
    """Process the user's AI question and return the answer.

    Consumes ``ask_agent_stream`` so the user watches tool calls happen
    live (progress edits) instead of a frozen "Thinking…".  The final
    answer still ships as one message + keyboard — only the WAIT got
    richer.  This is the PTB-independent groundwork for Bot API 10.1
    ``sendRichMessageDraft`` streaming: swap the ``_edit_active`` progress
    edits for rich drafts once PTB ships native support.
    """
    user = context.user_data["_db_user"]
    user_ctx = await _build_user_context(user)
    lang = getattr(user, "language", "en") or "en"

    await _show_loading(update, context, "🤖  <i>Thinking...</i>")

    suggestions = []
    try:
        # Driver: only sees their own truck
        vehicle_filter = None
        if user.role == Role.DRIVER and user.truck_num:
            vehicle_filter = user.truck_num

        snapshot = await _gather_vehicles_snapshot(
            user.account_id, vehicle_num=vehicle_filter,
        )
        samsara = await get_client(user.account_id)
        tenant_db = await get_tenant_db(user.account_id)

        import time as _t
        # Implicit-satisfaction markers — re-ask within 30 s (timing)
        # AND dissatisfaction-phrase prefix in the user's question
        # ("no, that's not what I asked", localised).  Both flip
        # ``had_reask`` on the prior turn's row so the router
        # downweights the model that produced an unsatisfying answer.
        # Telegram users especially benefit from the phrase signal —
        # the bot UI has no regenerate button, so phrase detection is
        # the only explicit dissatisfaction signal available there.
        await ai.detect_reask_and_mark(user.account_id, update.effective_user.id)
        await ai.detect_phrase_dissatisfaction_and_mark(
            user.account_id, update.effective_user.id, question,
            language=lang,
        )

        # Auto-mode tier resolution — when this user's stored tier is
        # "auto" we classify the question and switch to the right tier
        # before ask_agent runs.  No-op for explicit-tier users.
        await ai.resolve_tier_for_request(
            question,
            account_id=user.account_id,
            user_id=update.effective_user.id,
        )
        _started = _t.monotonic()
        # Stream the agent: surface each tool call as a live progress
        # tick instead of a frozen "Thinking…".  The generator yields
        # {"type":"tool",...} per call and one {"type":"done",...} (or
        # {"type":"error",...}) carrying the already-parsed reply +
        # suggestions.
        steps: list[str] = []
        done_ev = None
        stream_error = None
        _last_edit = 0.0
        async for ev in ai.ask_agent_stream(
            question, snapshot,
            samsara_client=samsara,
            user_id=update.effective_user.id,
            account_id=user.account_id,
            db=tenant_db,
            language=lang,
            user_context=user_ctx,
        ):
            etype = ev.get("type")
            if etype == "tool":
                steps.append(ev.get("label") or ev.get("name") or "working")
                # Throttle edits — Telegram rate-limits message edits and
                # tool ticks can burst.  ~1/sec is smooth without risking
                # 429s; the final answer renders regardless of ticks.
                now = _t.monotonic()
                if now - _last_edit > 0.8:
                    _last_edit = now
                    await _edit_active(
                        update, context, _format_ai_progress(steps),
                    )
            elif etype == "done":
                done_ev = ev
            elif etype == "error":
                stream_error = ev.get("message") or "AI error"

        if done_ev is None:
            raise RuntimeError(stream_error or "AI stream ended without a result")

        latency_ms = int((_t.monotonic() - _started) * 1000)
        # The stream already parsed suggestions out of the reply.
        clean_answer = done_ev.get("reply") or ""
        suggestions = done_ev.get("suggestions") or []
        tool_results = done_ev.get("tool_results") or []
        tool_success_count = sum(
            1 for tr in tool_results
            if not (isinstance(tr.get("data"), dict) and tr["data"].get("error"))
        )
        # Per-turn observability row.  ask_agent emits per-attempt
        # rows with action="question" internally; this row uses
        # "question_turn" so the two slices don't pollute each other.
        await _log_ai_usage(
            user.account_id, update.effective_user.id, "question_turn",
            done_ev.get("usage"),
            role=(user_ctx or {}).get("role"),
            latency_ms=latency_ms,
            tool_success_count=tool_success_count,
        )

        # Heuristic refusal detector — flips had_reask when the AI
        # refused with phrasing like "I don't have…" but a relevant
        # tool was available and uncalled.  Bot users benefit
        # especially: they have no thumbs-up/down UI, so explicit
        # dissatisfaction signals are scarcer here than on the
        # dashboard.
        await ai.detect_unjustified_refusal_and_mark(
            user.account_id, update.effective_user.id,
            question, clean_answer, tool_results,
            language=lang,
        )

        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  🤖  <b>AI ANSWER</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n{_sanitize_ai_html(clean_answer)}"
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

    # Store suggestions in user_data so button callbacks can retrieve them
    if suggestions:
        context.user_data["_ai_suggestions"] = suggestions
    kb = _build_chat_kb(suggestions) if suggestions else _ai_back_kb()
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_ai_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate an AI fleet summary/briefing."""
    if not ai.is_configured():
        if update.callback_query:
            await update.callback_query.answer("AI not configured", show_alert=True)
        return

    user = context.user_data["_db_user"]
    user_ctx = await _build_user_context(user)
    lang = getattr(user, "language", "en") or "en"
    await _show_loading(update, context, "📊  <i>Generating fleet briefing...</i>")

    try:
        vehicle_filter = None
        if user.role == Role.DRIVER and user.truck_num:
            vehicle_filter = user.truck_num

        snapshot = await _gather_vehicles_snapshot(
            user.account_id, vehicle_num=vehicle_filter,
        )
        # generate_summary() passes action="summary" through generate(),
        # which writes a router telemetry row per model attempt — no
        # external log call needed (would duplicate the row).
        summary, _usage = await ai.generate_summary(
            snapshot, account_id=user.account_id,
            language=lang, user_context=user_ctx,
        )

        clean_summary, suggestions = _parse_suggestions(summary)

        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📊  <b>AI FLEET BRIEFING</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n{_sanitize_ai_html(clean_summary)}"
        )
    except Exception as e:
        logger.error(f"AI summary failed: {e}")
        suggestions = []
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ❌  <b>AI ERROR</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "  Couldn't generate the fleet briefing.\n"
            f"\n  <i>{type(e).__name__}</i>"
        )

    if suggestions:
        context.user_data["_ai_suggestions"] = suggestions
    kb = _build_chat_kb(suggestions) if suggestions else _ai_back_kb()
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_ai_diagnose(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          vehicle_name: str, company: str | None = None,
                          alert_context: str = "fault",
                          ack_id: int | None = None):
    """AI-diagnose a truck from the alert context.

    alert_context controls what data is fetched and how the prompt is built:
      - "fault" (default): diagnose active DTCs / fault codes
      - "health": diagnose health readings (oil, coolant, battery, DEF) + any faults
      - "fuel": diagnose fuel situation + any faults

    Called from alert keyboards (callback: ai_diag_{alert_type}_{company}_{truck}).
    """
    if not ai.is_configured():
        if update.callback_query:
            await update.callback_query.answer(t('ai.not_configured_popup'), show_alert=True)
        return

    user = context.user_data["_db_user"]
    await _show_loading(update, context, t('ai.diagnose_thinking'))

    try:
        # Route through service layer (SSOT) — warehouse-aware when available,
        # falls back to live Samsara otherwise.
        from features.vehicles.service import (
            get_vehicle_detail as _svc_vehicle_detail,
        )
        matches = await _svc_vehicle_detail(
            user.account_id, vehicle_name, company=company,
        )

        if not matches:
            text = f"  {t('ai.vehicle_not_found').replace('{name}', vehicle_name)}"
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
        context_data: dict = {"truck": vehicle_name}
        prompt_parts: list[str] = []
        header_lines: list[str] = []

        if alert_context == "health":
            from capabilities.telemetry.service import get_vehicle_health as _svc_health
            health_vehicles = await _svc_health(user.account_id, company=company)
            health = {}
            for hv in health_vehicles:
                if hv.get("name", "").lower() == vehicle_name.lower():
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
                f"Diagnose the health condition of Truck #{vehicle_name}. "
                f"Current readings: {', '.join(f'{k}: {v}' for k, v in readings.items())}. "
                "Are any readings abnormal? What should the driver do?"
            )
            header_lines.append(f"  📊 {len(readings)} health reading(s)")

        elif alert_context == "fuel":
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
                f"Assess the fuel situation on Truck #{vehicle_name}. "
                f"Fuel level: {fuel_pct:.0f}% "
                + (f", DEF level: {def_pct}%" if def_pct is not None else "")
                + ". How urgent is this? Estimate remaining range. "
                "What should the driver do?"
            )
            header_lines.append(
                f"  ⛽ Fuel: {fuel_pct:.0f}%" if fuel_pct is not None
                else "  ⛽ Fuel data unavailable"
            )

        # Default / fallback: fault diagnosis
        if alert_context == "fault" or not prompt_parts:
            # If no live DTCs, try to recover fault info from the alert record
            if not dtcs and ack_id is not None:
                dtcs = await _dtcs_from_ack(user.account_id, ack_id)

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
                historical = any(d.get("_historical") for d in dtcs)
                if historical:
                    prompt_parts.insert(0, (
                        f"Diagnose these faults that were recently reported on "
                        f"Truck #{vehicle_name} (they may have since auto-resolved). "
                        f"There were {len(dtcs)} fault code(s)."
                    ))
                    header_lines.insert(0, f"  ⚙️ {len(dtcs)} reported fault(s) (recently cleared)")
                else:
                    prompt_parts.insert(0, (
                        f"Diagnose the active faults on Truck #{vehicle_name}. "
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
                f"\n  {t('ai.diagnose_no_issues_msg').replace('{truck}', f'Truck #{vehicle_name}')}"
            )
            await _show(update, context, [text], keyboard=_ai_back_kb())
            return

        prompt = " ".join(prompt_parts)
        lang = getattr(user, "language", "en") or "en"
        user_ctx = await _build_user_context(user)
        # action="bot_diagnosis" — router telemetry fires inside
        # generate(); skipping external log to avoid double-counting.
        diagnosis, _usage = await ai.generate(
            prompt,
            system=ai.FAULT_DIAGNOSIS_SYSTEM,
            context_data=context_data,
            account_id=user.account_id,
            language=lang,
            user_context=user_ctx,
            action="bot_diagnosis",
        )

        header_info = "\n".join(header_lines)
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  🔧  <b>{t('ai.diagnose_title')}</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  🚛  <b>Truck #{vehicle_name}</b>\n"
            f"{header_info}\n"
            f"\n{_sanitize_ai_html(diagnosis)}"
        )

        kb_rows = []
        if ack_id is not None:
            kb_rows.append([InlineKeyboardButton(
                t('alert_actions.acknowledge'), callback_data=f"ack_alert_{ack_id}",
            )])
        truck_cb = f"covehicle_{co}_{vehicle_name}"
        if ack_id is not None:
            truck_cb += f":{ack_id}"
        kb_rows.append([InlineKeyboardButton(
            t('vehicle.view_vehicle').replace('{name}', vehicle_name),
            callback_data=truck_cb,
        )])
        if ack_id is not None:
            kb_rows.append([InlineKeyboardButton(
                "↩️ Back to Alert", callback_data=f"back_alert_{ack_id}",
            )])
        else:
            kb_rows.append([InlineKeyboardButton(t('ai.btn_ai_menu'), callback_data="cmd_ai")])
            kb_rows.append([InlineKeyboardButton(t('common.main_menu'), callback_data="cmd_menu")])
        kb = InlineKeyboardMarkup(kb_rows)
    except Exception as e:
        logger.error(f"AI diagnosis failed for {vehicle_name}: {e}")
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  ❌  <b>{t('ai.error_title')}</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  {t('ai.diagnose_error_msg').replace('{name}', vehicle_name)}\n"
            f"\n  <i>{type(e).__name__}</i>"
        )
        kb = _ai_back_kb()

    await _show(update, context, [text], keyboard=kb)


# ── AI Suggestion Follow-up ─────────────────────────────────────

@_require_registered
async def cmd_ai_suggest(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         index: int = -1):
    """Handle an AI suggestion button press (ai_sug_{index})."""
    if not ai.is_configured():
        if update.callback_query:
            await update.callback_query.answer("AI not configured", show_alert=True)
        return

    suggestions = context.user_data.get("_ai_suggestions", [])
    if index < 0 or index >= len(suggestions):
        if update.callback_query:
            await update.callback_query.answer("Suggestion expired", show_alert=True)
        return

    question = suggestions[index]
    await cmd_ai_answer(update, context, question=question)


# ── New Chat ─────────────────────────────────────────────────────

@_require_registered
async def cmd_ai_newchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear conversation context and start fresh."""
    context.user_data.pop("_ai_suggestions", None)
    context.user_data.pop("_ai_history", None)
    if update.callback_query:
        await update.callback_query.answer("Chat cleared")
    await cmd_ai(update, context)


# ── Model Selection ──────────────────────────────────────────────

@_require_registered
async def cmd_ai_models(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        mode: str = "text"):
    """Show available AI models for selection.

    mode: "text" for chat/diagnose models, "vision" for camera/image models.
    """
    if not ai.is_configured():
        if update.callback_query:
            await update.callback_query.answer("AI not configured", show_alert=True)
        return

    user = context.user_data["_db_user"]
    acct_id = user.account_id
    await ai.ensure_account_model(acct_id)

    if mode == "vision":
        # Vision model selector
        cur_model = ai.DEFAULT_VISION_MODEL
        acct_info = ai.get_account_vision_model_info(acct_id)
        if acct_info:
            cur_model = acct_info[0]
        models = ai.get_vision_models()
        title = "👁  Vision Models"
        subtitle = (
            "  Used for camera check, image\n"
            "  analysis, and visual inspection."
        )
        cb_prefix = "ai_setvision_"
        back_label = "ai_models_text"
        back_text = "💬 Text Models"
    else:
        # Text model selector
        cur_model = ai.get_current_model_name()
        acct_info = ai.get_account_model_info(acct_id)
        if acct_info:
            cur_model = acct_info[0]
        models = ai.get_text_models()
        title = "💬  Text Models"
        subtitle = (
            "  Used for AI chat, fleet summary,\n"
            "  diagnostics, and AI alerts."
        )
        cb_prefix = "ai_setmodel_"
        back_label = "ai_models_vision"
        back_text = "👁 Vision Models"

    cur_info = ai.MODEL_REGISTRY.get(cur_model, {})

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  <b>{title}</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"\n  Current: <b>{cur_info.get('display', cur_model)}</b>",
        f"\n{subtitle}",
        "",
    ]

    buttons = []
    for m in models:
        name = m["name"]
        disp = m["display"]
        est = m.get("est_cost", 0)
        price_tag = f"~${est:.3f}" if est < 0.01 else f"~${est:.2f}"
        prefix = "✅ " if name == cur_model else ""
        label = f"{prefix}{disp} · {price_tag}"
        buttons.append([InlineKeyboardButton(
            label, callback_data=f"{cb_prefix}{name}",
        )])
    buttons.append([InlineKeyboardButton(back_text, callback_data=back_label)])
    buttons.append([InlineKeyboardButton("📈 AI Usage", callback_data="cmd_ai_usage")])
    buttons.append([InlineKeyboardButton("🤖 AI Menu", callback_data="cmd_ai")])

    text = "\n".join(lines)
    await _show(update, context, [text], keyboard=InlineKeyboardMarkup(buttons))


@_require_registered
async def cmd_ai_set_model(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           model_name: str):
    """Switch the AI text model for this account."""
    user = context.user_data["_db_user"]
    acct_id = user.account_id
    try:
        ai.switch_model(model_name, account_id=acct_id)
        loc = ai.MODEL_REGISTRY[model_name]["locations"][0]
        await ai.save_account_model(acct_id, model_name, loc)

        info = ai.MODEL_REGISTRY[model_name]
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ✅  <b>Model Changed</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  💬 <b>{info['display']}</b>"
            f"\n  {info['description']}"
        )
    except ValueError as e:
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ❌  <b>Error</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {e}"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Text Models", callback_data="ai_models_text")],
        [InlineKeyboardButton("🤖 AI Menu", callback_data="cmd_ai")],
    ])
    await _show(update, context, [text], keyboard=kb)


@_require_registered
async def cmd_ai_set_vision_model(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                  model_name: str):
    """Switch the AI vision model for this account."""
    user = context.user_data["_db_user"]
    acct_id = user.account_id
    try:
        ai.switch_vision_model(model_name, account_id=acct_id)
        loc = ai.MODEL_REGISTRY[model_name]["locations"][0]
        await ai.save_account_vision_model(acct_id, model_name, loc)

        info = ai.MODEL_REGISTRY[model_name]
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ✅  <b>Vision Model Changed</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  👁 <b>{info['display']}</b>"
            f"\n  {info['description']}"
        )
    except ValueError as e:
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ❌  <b>Error</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {e}"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 Vision Models", callback_data="ai_models_vision")],
        [InlineKeyboardButton("🤖 AI Menu", callback_data="cmd_ai")],
    ])
    await _show(update, context, [text], keyboard=kb)


# ── AI Alert Preferences ────────────────────────────────────────

@_require_registered
async def cmd_ai_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show AI-enhanced alert toggle screen."""
    user = context.user_data["_db_user"]

    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("No access", show_alert=True)
        return

    fault_icon = "✅" if user.ai_fault else "❌"
    health_icon = "✅" if user.ai_health else "❌"
    fuel_icon = "✅" if user.ai_fuel else "❌"

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  🤖  <b>AI ALERT ANALYSIS</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "  When enabled, trigger alerts include\n"
        "  an AI-generated diagnosis.\n"
        "\n"
        f"  {fault_icon}  Fault alerts\n"
        f"  {health_icon}  Health alerts\n"
        f"  {fuel_icon}  Fuel alerts\n"
    )

    kb = _ai_alerts_kb(user)

    await _show(update, context, [text], keyboard=kb)


def _ai_alerts_kb(user) -> InlineKeyboardMarkup:
    """Build the AI alert preference toggle keyboard."""
    fault_icon = "✅" if user.ai_fault else "❌"
    health_icon = "✅" if user.ai_health else "❌"
    fuel_icon = "✅" if user.ai_fuel else "❌"
    parking_icon = "✅" if getattr(user, "ai_parking", False) else "❌"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{fault_icon} AI Fault Analysis",
            callback_data="ai_toggle_fault",
        )],
        [InlineKeyboardButton(
            f"{health_icon} AI Health Analysis",
            callback_data="ai_toggle_health",
        )],
        [InlineKeyboardButton(
            f"{fuel_icon} AI Fuel Analysis",
            callback_data="ai_toggle_fuel",
        )],
        [InlineKeyboardButton(
            f"{parking_icon} AI Parking Analysis",
            callback_data="ai_toggle_parking",
        )],
        [InlineKeyboardButton("🤖 AI Menu", callback_data="cmd_ai")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


# ── AI usage stats (used by callbacks) ───────────────────────────

async def handle_ai_usage(update, context, user, data):
    """Handle AI usage statistics display (cmd_ai_usage, ai_usage_*)."""
    query = update.callback_query
    if not can(user.role, "can_manage_account"):
        await query.answer(t("access.no_access"), show_alert=True)
        return
    await query.answer()
    days = 30
    if data.startswith("ai_usage_"):
        try:
            days = int(data.replace("ai_usage_", ""))
        except ValueError:
            days = 30
    stats = await get_platform_db().get_ai_usage_stats(user.account_id, days=days)
    daily = await get_platform_db().get_ai_usage_daily(user.account_id, days=min(days, 7))

    total_cost = 0.0
    model_costs: dict[str, float] = {}
    if stats["by_model"]:
        for m, minfo in stats["by_model"].items():
            prompt_tok = minfo.get("prompt_tokens", 0)
            reply_tok = minfo.get("reply_tokens", 0)
            total_tok = minfo.get("tokens", 0)
            think_tok = minfo.get("thinking_tokens", 0)
            c = ai.estimate_cost(m, prompt_tok, reply_tok, total_tok, think_tok)
            model_costs[m] = c
            total_cost += c

    # Try to get real Vertex AI cost from Cloud Monitoring
    cloud = None
    try:
        cloud = await ai.get_vertex_ai_cloud_usage(days=days)
    except Exception as e:
        logger.debug("Cloud Monitoring API unavailable: %s", e)

    tok_str = f"{stats['total_tokens']:,}"
    if cloud:
        cost_str = f"${cloud['totals']['cost']:.4f}"
    else:
        cost_str = f"${total_cost:.4f}"
    lines = [
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {t('ai_usage.title')}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {t('ai_usage.period').format(days=days)}\n"
        f"\n  {t('ai_usage.total_requests').format(count=stats['total_requests'])}"
        f"\n  {t('ai_usage.total_tokens').format(count=tok_str)}"
        f"\n  {t('ai_usage.est_cost').format(cost=cost_str)}\n"
    ]
    if cloud:
        ct = cloud["totals"]
        lines.append(
            f"\n  ☁️ <b>Vertex AI ({days}d)</b>: {ct['requests']:,} req"
            f"\n     {ct['input']:,} in / {ct['output']:,} out"
            f"\n     Base: ${ct['base_cost']:.4f} · +30%: ${ct['cost']:.4f}\n"
        )
    if stats["by_type"]:
        lines.append(f"\n  {t('ai_usage.by_type')}")
        for rt, info in stats["by_type"].items():
            lines.append(f"  • {rt}: {info['requests']} req ({info['tokens']:,} tok)")
    if stats["by_model"]:
        lines.append(f"\n  {t('ai_usage.by_model')}")
        for m, info in stats["by_model"].items():
            disp = ai.MODEL_REGISTRY.get(m, {}).get("display", m)
            mc = model_costs.get(m, 0)
            pt = info.get("prompt_tokens", 0)
            rt = info.get("reply_tokens", 0)
            lines.append(
                f"  • {disp}: {info['requests']} req\n"
                f"    {pt:,} in / {rt:,} out · ${mc:.4f}"
            )
    if daily:
        lines.append(f"\n  {t('ai_usage.daily')}")
        for d in daily:
            lines.append(f"  • {d['day']}: {d['requests']} req ({d['tokens'] or 0:,} tok)")

    text = "\n".join(lines)
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t('ai_usage.btn_7d'), callback_data="ai_usage_7"),
            InlineKeyboardButton(t('ai_usage.btn_30d'), callback_data="ai_usage_30"),
            InlineKeyboardButton(t('ai_usage.btn_90d'), callback_data="ai_usage_90"),
        ],
        [InlineKeyboardButton(t('ai.btn_models'), callback_data="ai_models")],
        [InlineKeyboardButton(t('menu.back'), callback_data="cmd_menu")],
    ])
    await _show(update, context, [text], keyboard=kb)
