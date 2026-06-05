"""Core AI text generation with retry and fallback."""

from __future__ import annotations

import json
import logging

from capabilities.ai.registry import (
    MODEL_REGISTRY,
    _FALLBACK_CHAIN,
    _is_openai_compat,
    _maas_base_url,
    _anthropic_url,
    _mistral_url,
    _get_credentials,
)
from capabilities.ai.cache import _cache_key, _cache_get, _cache_put, _snapshot_hash
from capabilities.ai.chat import _chat_histories, _store_history
# For _model (reassigned via global), import the *module* so attribute
# access always reflects the latest value.
from capabilities.ai import models as _models_mod
from capabilities.ai.models import (
    _account_models,
    get_current_model_name,
    get_current_location,
    _ensure_model,
    _build_model,
)

logger = logging.getLogger("bot.ai")

# ── Usage tracking ───────────────────────────────────────────────
#
# Usage now travels with the call return value, NOT through a module
# global.  Concurrent users sharing a process used to clobber each
# other's ``_last_usage`` — one user's token cost would land in
# another user's audit row.  Callers receive ``(text, usage)`` tuples
# (or a ``"usage"`` key in result dicts) and forward usage explicitly
# to ``log_ai_usage(... usage=...)``.


def _capture_usage(response) -> dict | None:
    """Extract token usage metadata from a Vertex AI / google-genai response.

    Pure function — returns the usage dict or None; no side effects.
    """
    try:
        meta = getattr(response, "usage_metadata", None)
        if not meta:
            return None
        prompt = getattr(meta, "prompt_token_count", 0) or 0
        reply = getattr(meta, "candidates_token_count", 0) or 0
        thinking = getattr(meta, "thoughts_token_count", 0) or 0
        total = getattr(meta, "total_token_count", 0) or 0
        # Ensure total includes thinking if SDK didn't sum it
        if total < prompt + reply + thinking:
            total = prompt + reply + thinking
        return {
            "prompt_tokens": prompt,
            "reply_tokens": reply,
            "thinking_tokens": thinking,
            "total_tokens": total,
        }
    except Exception:
        return None


# ── System Prompts ───────────────────────────────────────────────

ASSISTANT_SYSTEM = """\
You are a telematics AI assistant embedded in a vehicle operations platform. \
You help all types of users — owners, administrators, dispatchers, safety \
supervisors, fleet operators, and drivers — understand their vehicle and \
operations data. Tailor every response to the user's role and permissions.

IMPORTANT — Language:
- ALWAYS detect the language of the user's message.
- ALWAYS respond in the SAME language the user used.
- If the user writes in Spanish, reply in Spanish. If Russian, reply in \
  Russian. If English, reply in English. And so on for any language.
- Follow-up suggestions (>> lines) must also be in the user's language.

DATA YOU HAVE ACCESS TO (per vehicle in the fleet snapshot):
- name: truck number (e.g. "231")
- company: company code (e.g. PTG, OSY, CFT, RMR)
- make, model, year, vin: vehicle specifications
- city: current location address
- speed_mph: current speed in mph
- fuel_pct: fuel level percentage
- def_pct: DEF (diesel exhaust fluid) level percentage
- coolant_c: engine coolant temperature in °C
- oil_psi: engine oil pressure in PSI
- battery_v: battery voltage
- engine_load_pct: engine load percentage
- engine_on: whether engine is currently running
- fault_count + faults[]: J1939 diagnostic trouble codes (SPN + FMI)
- check_engine_lights: stop/protect/emissions/warning light status
- health_alerts[]: triggered alerts (low_battery, low_oil_pressure, \
  high_coolant_temp, low_def, seatbelt_unbuckled)

ADDITIONAL DATA IN SNAPSHOT:
- recent_events: safety events summary for last 7 days (event counts \
  by type, top incidents). When discussing events, mention the time period.
- maintenance: pending and overdue maintenance tasks per truck \
  (task type, due date, due miles, status)
- fuel_costs: fuel cost summary per truck (total gallons, total cost, \
  avg price per gallon, cost per mile)

Not all fields are available for every vehicle — some Samsara gateways \
don't support certain sensor types. If a field is missing, it means the \
data is not available from the hardware, not that things are normal. \
Maintenance and fuel cost data come from manual entries — if empty, \
it means no entries have been recorded yet.

USER AWARENESS:
You may receive a "User profile" block with the user's name, role, \
assigned truck, department, timezone, and their permission summary \
(what they CAN and CANNOT access). Use this to tailor responses:
- You know the user's name but do NOT greet them by name in every message. \
  Use their name only occasionally when it feels natural (e.g. first greeting \
  of a session). Most replies should skip the name entirely.
- Only discuss data categories the user has permission to see. \
  If they ask about something they CANNOT access, politely explain \
  they don't have access to that feature.
- Follow the behavioral guidance included in the profile.
- Respect the user's timezone when mentioning times.
- If no user profile is provided, answer only with general information \
  and avoid disclosing any specific vehicle, driver, or operational data.

Rules:
- Be concise. Responses should be short and scannable.
- Use simple language — the audience includes truck drivers and operations staff, \
  not engineers.
- Never make up data. If the provided fleet data doesn't contain \
  what the user asked for, say so honestly and explain which fields \
  are unavailable.
- AMBIGUOUS QUESTIONS: before refusing because you "don't have access \
  to X", check whether you have a tool that could answer.  If the \
  user's wording is ambiguous (e.g. "10 days off" could mean a truck \
  parked 10 days OR a driver off-duty 10 days), ask ONE short \
  clarifying question instead of refusing flat-out.  Only refuse if \
  no tool plausibly covers either interpretation.
- If a tool returns an "error" field, you MUST report the error to the \
  user honestly — never hide tool failures or pretend everything is fine. \
  Say something like "I tried to check [X] but the tool returned an error: \
  [error message]. Please try again or check manually."
- When discussing faults/DTCs, explain in plain English what the \
  fault means, how severe it is, and what action to take.
- Amounts in USD, distances in miles, temperatures in °F.
- FORMATTING — emit a portable subset that renders correctly on both \
  the dashboard AND inside Telegram:
  * Inline emphasis ONLY: <b>bold</b>, <i>italic</i>, <code>code</code>. \
    No <p>, <ul>, <ol>, <li>, <br>, <h1>..<h6>, <div>, <span>, or \
    any other structural tags — Telegram renders them as visible \
    "<p>" / "<li>" literal text.
  * For lists, emit one bullet per line using the literal characters \
    "• " (bullet + space) at the start of each line. Example:
        Here are the trucks low on fuel:
        • Truck 102 — 18%
        • Truck 117 — 12%
  * Separate paragraphs with a single blank line, not with <p> tags.
  * Do NOT use markdown either (**bold**, ### headings, `- lists`, \
    `code`) — the dashboard expects HTML inline tags and Telegram \
    expects the same.
- For camera/dashcam checks: you can only check ONE truck at a time. \
  If the user asks to check all cameras, suggest using the Camera Check \
  section of the dashboard for a full fleet scan.
- At the END of every response, add EXACTLY 2-3 short follow-up question
  suggestions on new lines, each prefixed with ">>". These help the user
  continue the conversation. Keep them under 40 characters each.
  Example:
  >> Which truck needs attention first?
  >> Show me driver efficiency
  >> Any trucks with low DEF?
"""

FAULT_DIAGNOSIS_SYSTEM = """\
You are a diesel truck mechanic AI. Given J1939/OBD fault codes \
(SPN + FMI), explain:
1. What the fault means in plain English
2. Severity (informational / warning / critical — pull over immediately)
3. Likely root cause
4. Recommended action (can the driver keep driving? should they stop?)

IMPORTANT — Language:
- Detect the language of the user's message or context.
- ALWAYS respond in the SAME language the user used.

Rules:
- Be concise — 2-4 sentences per fault.
- Use HTML formatting (<b>bold</b> for severity).
- Audience: operations staff and truck drivers, not engineers.
- If you recognize the SPN, name the actual component.
- If it's a critical fault (STOP light), emphasize urgency.
"""

SUMMARY_SYSTEM = """\
You are a vehicle operations intelligence AI. Given raw vehicle and operations \
data, produce a brief status summary adapted to the user's role. For \
owners/managers: executive summary. For drivers: personal truck status \
briefing. For safety supervisors: safety-focused summary. For dispatchers: \
route and availability summary. Include:
- Overall vehicle health status (good / some issues / needs attention)
- Key metrics at a glance
- Any vehicles that need immediate attention
- Brief trend observations if data supports it

IMPORTANT — Language:
- Detect the language of the user's message or context.
- ALWAYS respond in the SAME language the user used.

Rules:
- 4-6 short paragraphs max.
- HTML formatting only (<b>bold</b>, no markdown).
- Prioritize actionable information over raw stats.
- Be direct — no filler phrases.
"""


# ── Per-backend generators ───────────────────────────────────────


async def _generate_openai_compat(
    model_id: str, location: str, system: str, prompt: str,
    max_tokens: int = 4096,
) -> tuple[str, dict | None]:
    """Generate via the Vertex AI OpenAI-compatible endpoint."""
    import asyncio
    import os
    import requests
    from google.auth.transport.requests import Request

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    creds = _get_credentials()
    if not project or not creds:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT or credentials not set.")
    creds.refresh(Request())

    url = _maas_base_url(location, project)
    body = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "top_p": 0.8,
    }

    def _call():
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=120,
        )
        r.raise_for_status()
        return r.json()

    data = await asyncio.to_thread(_call)
    text = ""
    usage = None
    if "choices" in data and data["choices"]:
        msg = data["choices"][0].get("message", {})
        text = msg.get("content", "")
    if "usage" in data:
        u = data["usage"]
        prompt = u.get("prompt_tokens", 0)
        reply = u.get("completion_tokens", 0)
        thinking = (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
        total = u.get("total_tokens", 0)
        if total < prompt + reply + thinking:
            total = prompt + reply + thinking
        usage = {
            "prompt_tokens": prompt,
            "reply_tokens": reply,
            "thinking_tokens": thinking,
            "total_tokens": total,
        }
    return text.strip(), usage


async def _generate_anthropic(
    model_id: str, location: str, system: str, prompt: str,
    max_tokens: int = 4096,
) -> tuple[str, dict | None]:
    """Generate via Anthropic rawPredict on Vertex AI."""
    import asyncio
    import os
    import requests
    from google.auth.transport.requests import Request

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    creds = _get_credentials()
    if not project or not creds:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT or credentials not set.")
    creds.refresh(Request())

    url = _anthropic_url(location, project, model_id)
    body = {
        "anthropic_version": "vertex-2023-10-16",
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "top_p": 0.8,
    }

    def _call():
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=120,
        )
        r.raise_for_status()
        return r.json()

    data = await asyncio.to_thread(_call)
    text = ""
    usage = None
    if "content" in data and data["content"]:
        text = data["content"][0].get("text", "")
    if "usage" in data:
        u = data["usage"]
        inp = u.get("input_tokens", 0)
        out = u.get("output_tokens", 0)
        usage = {
            "prompt_tokens": inp,
            "reply_tokens": out,
            "thinking_tokens": 0,
            "total_tokens": inp + out,
        }
    return text.strip(), usage


async def _generate_mistral_raw(
    model_id: str, publisher: str, location: str,
    system: str, prompt: str, max_tokens: int = 4096,
) -> tuple[str, dict | None]:
    """Generate via Mistral rawPredict on Vertex AI."""
    import asyncio
    import os
    import requests
    from google.auth.transport.requests import Request

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    creds = _get_credentials()
    if not project or not creds:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT or credentials not set.")
    creds.refresh(Request())

    url = _mistral_url(location, project, publisher, model_id)
    body = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "top_p": 0.8,
    }

    def _call():
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=120,
        )
        r.raise_for_status()
        return r.json()

    data = await asyncio.to_thread(_call)
    text = ""
    usage = None
    if "choices" in data and data["choices"]:
        msg = data["choices"][0].get("message", {})
        text = msg.get("content", "")
    if "usage" in data:
        u = data["usage"]
        prompt = u.get("prompt_tokens", 0)
        reply = u.get("completion_tokens", 0)
        total = u.get("total_tokens", 0)
        usage = {
            "prompt_tokens": prompt,
            "reply_tokens": reply,
            "thinking_tokens": 0,
            "total_tokens": total if total >= prompt + reply else prompt + reply,
        }
    return text.strip(), usage


# ── Helpers ──────────────────────────────────────────────────────


def _is_rate_limit_error(exc: Exception) -> bool:
    """Return True if the exception is a 429 / ResourceExhausted error."""
    s = str(exc).lower()
    return '429' in s or 'resource exhausted' in s


# ── Single-shot generation (used by fallback chain) ─────────────


async def _generate_with_model(
    model_name: str,
    location: str,
    system: str,
    user_content: str,
    *,
    account_id: int | None = None,
    user_id: int | None = None,
    role: str | None = None,
    action: str | None = None,
) -> tuple[str, dict | None]:
    """Run a single generation attempt with a specific model (no retries).

    Returns ``(text, usage)`` — usage is the token-count dict or ``None``
    if the backend didn't report it.  Times the call and writes a row
    to ``ai_usage`` for the router's per-model scoring (success +
    failure both logged so error_rate is computable).
    """
    import asyncio
    import re
    import time as _t

    from capabilities.ai.usage import record_call_attempt, classify_error

    info = MODEL_REGISTRY.get(model_name)
    if not info:
        raise ValueError(f"Unknown model: {model_name}")

    started = _t.monotonic()
    try:
        if _is_openai_compat(model_name):
            api_type = info.get("api_type", "openai_compat")
            loc = location or info["locations"][0]
            max_tokens = min(info.get("max_output_tokens", 4096), 8192)

            if api_type == "anthropic":
                text, usage = await _generate_anthropic(
                    info["anthropic_model_id"], loc,
                    system, user_content, max_tokens,
                )
            elif api_type == "mistral_raw":
                text, usage = await _generate_mistral_raw(
                    info["mistral_model_id"],
                    info.get("mistral_publisher", "mistralai"),
                    loc, system, user_content, max_tokens,
                )
            else:
                text, usage = await _generate_openai_compat(
                    info["maas_model_id"], loc,
                    system, user_content, max_tokens,
                )
        else:
            loc = location or info["locations"][0]
            model_obj = _build_model(model_name, loc, info)
            full_prompt = system + "\n\n" + user_content
            response = await asyncio.to_thread(
                model_obj.generate_content, full_prompt
            )
            if not response.candidates:
                raise RuntimeError("Gemini response blocked")
            candidate = response.candidates[0]
            if candidate.finish_reason and candidate.finish_reason.name == "SAFETY":
                raise RuntimeError("Gemini safety filter blocked")
            try:
                text = response.text.strip()
            except ValueError:
                parts = getattr(getattr(candidate, 'content', None), 'parts', [])
                text = ''.join(getattr(p, 'text', '') for p in parts).strip()
            usage = _capture_usage(response)

        if not text:
            raise RuntimeError("Empty response from model")
    except Exception as e:
        latency_ms = int((_t.monotonic() - started) * 1000)
        await record_call_attempt(
            account_id=account_id, user_id=user_id, role=role, action=action,
            model=model_name, latency_ms=latency_ms,
            error_type=classify_error(e), usage=None,
        )
        raise

    text = text.replace("**", "").replace("##", "").replace("# ", "")
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
    latency_ms = int((_t.monotonic() - started) * 1000)
    await record_call_attempt(
        account_id=account_id, user_id=user_id, role=role, action=action,
        model=model_name, latency_ms=latency_ms,
        error_type="ok", usage=usage,
    )
    return text, usage


# ── Main generate (with retries) ────────────────────────────────


async def _generate_impl(prompt: str, system: str = ASSISTANT_SYSTEM,
                         context_data: dict | str | None = None,
                         user_id: int | None = None,
                         account_id: int | None = None,
                         language: str = "en",
                         user_context: dict | None = None,
                         *,
                         action: str | None = None) -> tuple[str, dict | None]:
    """Core generate implementation (before fallback wrapper).

    Returns ``(text, usage)`` — usage may be ``None`` on cache hits or
    when the backend didn't report token counts.

    ``action`` is a label like ``"question"`` / ``"summary"`` /
    ``"diagnosis"`` that gets written to the ai_usage telemetry row so
    the router can score per (model x role x action) — different
    actions favour different models (briefings benefit from Thinking,
    quick lookups don't).
    """
    import asyncio
    import re
    import time as _t

    from capabilities.ai.usage import record_call_attempt, classify_error

    user_role = (user_context or {}).get("role")

    if language and language != "en":
        from capabilities.localization.i18n import LANGUAGE_NAMES
        lang_name = LANGUAGE_NAMES.get(language, language)
        system = system + f"\n\nIMPORTANT: You MUST respond in {lang_name}. All your output text must be in {lang_name}."

    if account_id is not None and account_id in _account_models:
        cur_model_name, cur_location, model_obj = _account_models[account_id]
    else:
        cur_model_name = get_current_model_name()
        cur_location = get_current_location()
        model_obj = None

    has_history = bool(user_id and (user_id, account_id or 0) in _chat_histories)
    snap_h = _snapshot_hash(context_data) if not has_history else ""
    ck = _cache_key(
        prompt, snap_h, cur_model_name,
        account_id=account_id or 0,
        user_id=user_id or 0,
        language=language,
    ) if not has_history else ""
    if not has_history and ck:
        cached = _cache_get(ck)
        if cached is not None:
            logger.debug("Cache hit for generate()")
            if user_id is not None:
                _store_history(user_id, prompt, cached, account_id=account_id or 0)
            return cached, None

    user_parts = []

    # Inject user identity so the AI knows who it's talking to
    if user_context:
        uc = user_context
        profile_lines = ["User profile:"]
        if uc.get("name"):
            profile_lines.append(f"- Name: {uc['name']}")
        if uc.get("role"):
            profile_lines.append(f"- Role: {uc['role']}")
        if uc.get("department") and uc["department"] != "general":
            profile_lines.append(f"- Department: {uc['department']}")
        if uc.get("vehicle_num"):
            profile_lines.append(f"- Assigned vehicle: {uc['vehicle_num']}")
        if uc.get("timezone"):
            profile_lines.append(f"- Timezone: {uc['timezone']}")
        # Dynamic permission guidance from ROLE_PERMISSIONS
        if uc.get("role"):
            from capabilities.iam.permissions import build_role_guidance
            guidance = build_role_guidance(uc["role"])
            profile_lines.append(f"\n{guidance}")
        user_parts.append("\n".join(profile_lines) + "\n\n")

    if context_data:
        if isinstance(context_data, dict):
            data_str = json.dumps(context_data, separators=(',', ':'), default=str)
        else:
            data_str = str(context_data)
        if len(data_str) > 30000:
            data_str = data_str[:30000] + "\n... (truncated)"
        user_parts.append(f"Fleet data:\n```\n{data_str}\n```\n\n")

    if user_id and (user_id, account_id or 0) in _chat_histories:
        history = _chat_histories[(user_id, account_id or 0)]
        user_parts.append("Previous conversation:\n")
        for entry in history:
            role = entry["role"]
            user_parts.append(f"{role}: {entry['text']}\n")
        user_parts.append("\n")

    user_parts.append(f"User question: {prompt}")
    user_content = "".join(user_parts)

    # ── Non-Gemini API paths ─────────────────────────────────
    if _is_openai_compat(cur_model_name):
        info = MODEL_REGISTRY[cur_model_name]
        api_type = info.get("api_type", "openai_compat")
        location = cur_location or info["locations"][0]
        max_tokens = min(info.get("max_output_tokens", 4096), 8192)

        max_retries = 2
        last_exc = None
        for attempt in range(max_retries + 1):
            started = _t.monotonic()
            try:
                if api_type == "anthropic":
                    text, usage = await _generate_anthropic(
                        info["anthropic_model_id"], location,
                        system, user_content, max_tokens,
                    )
                elif api_type == "mistral_raw":
                    text, usage = await _generate_mistral_raw(
                        info["mistral_model_id"],
                        info.get("mistral_publisher", "mistralai"),
                        location, system, user_content, max_tokens,
                    )
                else:
                    text, usage = await _generate_openai_compat(
                        info["maas_model_id"], location,
                        system, user_content, max_tokens,
                    )
                latency_ms = int((_t.monotonic() - started) * 1000)
                await record_call_attempt(
                    account_id=account_id, user_id=user_id,
                    role=user_role, action=action,
                    model=cur_model_name, latency_ms=latency_ms,
                    error_type="ok", usage=usage,
                )
                if not text:
                    return (
                        "The AI generated an empty response. "
                        "Try rephrasing or asking a more specific question."
                    ), usage
                text = text.replace("**", "").replace("##", "").replace("# ", "")
                text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
                if user_id is not None:
                    _store_history(user_id, prompt, text, account_id=account_id or 0)
                if ck and not has_history:
                    _cache_put(ck, text)
                return text, usage
            except Exception as e:
                latency_ms = int((_t.monotonic() - started) * 1000)
                await record_call_attempt(
                    account_id=account_id, user_id=user_id,
                    role=user_role, action=action,
                    model=cur_model_name, latency_ms=latency_ms,
                    error_type=classify_error(e), usage=None,
                )
                last_exc = e
                err_str = str(e).lower()
                if ('429' in err_str or 'resource exhausted' in err_str) and attempt < max_retries:
                    wait = 2 ** attempt
                    logger.warning(f"Rate limited (attempt {attempt+1}/{max_retries+1}), retrying in {wait}s")
                    await asyncio.sleep(wait)
                    continue
                logger.error(f"MaaS generation failed: {e}")
                raise
        raise last_exc  # type: ignore[misc]

    # ── Gemini SDK path ──────────────────────────────────────
    if model_obj is None:
        model_obj = _models_mod._model if _models_mod._model is not None else _ensure_model(cur_model_name, cur_location)
    full_prompt = system + "\n\n" + user_content

    max_retries = 2
    last_exc = None
    for attempt in range(max_retries + 1):
        started = _t.monotonic()
        try:
            response = await asyncio.to_thread(
                model_obj.generate_content, full_prompt
            )

            if not response.candidates:
                block_reason = "Unknown"
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    block_reason = str(response.prompt_feedback.block_reason)
                logger.warning(f"Gemini response blocked: {block_reason}")
                latency_ms = int((_t.monotonic() - started) * 1000)
                await record_call_attempt(
                    account_id=account_id, user_id=user_id,
                    role=user_role, action=action,
                    model=cur_model_name, latency_ms=latency_ms,
                    error_type="empty", usage=None,
                )
                return (
                    "I couldn't generate a response for that question. "
                    "Please try rephrasing it."
                ), None

            candidate = response.candidates[0]
            if candidate.finish_reason and candidate.finish_reason.name == "SAFETY":
                logger.warning("Gemini response blocked by safety filter")
                latency_ms = int((_t.monotonic() - started) * 1000)
                await record_call_attempt(
                    account_id=account_id, user_id=user_id,
                    role=user_role, action=action,
                    model=cur_model_name, latency_ms=latency_ms,
                    error_type="content_filter", usage=None,
                )
                return (
                    "I couldn't generate a response due to content filters. "
                    "Please try rephrasing your question."
                ), None

            try:
                text = response.text.strip()
            except ValueError:
                fr = getattr(candidate, 'finish_reason', None)
                reason_name = getattr(fr, 'name', str(fr)) if fr else 'unknown'
                if reason_name == 'MAX_TOKENS':
                    parts = getattr(getattr(candidate, 'content', None), 'parts', [])
                    if parts:
                        text = ''.join(getattr(p, 'text', '') for p in parts).strip()
                    else:
                        logger.warning(f"Gemini response empty (finish_reason={reason_name})")
                        return (
                            "The AI ran out of space generating the response. "
                            "Try asking a more specific question."
                        ), _capture_usage(response)
                else:
                    logger.warning(f"Cannot get response text (finish_reason={reason_name})")
                    return (
                        "I couldn't generate a response for that question. "
                        "Please try rephrasing it."
                    ), _capture_usage(response)

            usage = _capture_usage(response)
            latency_ms = int((_t.monotonic() - started) * 1000)
            await record_call_attempt(
                account_id=account_id, user_id=user_id,
                role=user_role, action=action,
                model=cur_model_name, latency_ms=latency_ms,
                error_type="ok", usage=usage,
            )
            if not text:
                return (
                    "The AI generated an empty response. "
                    "Try rephrasing or asking a more specific question."
                ), usage
            text = text.replace("**", "").replace("##", "").replace("# ", "")
            if user_id is not None:
                _store_history(user_id, prompt, text, account_id=account_id or 0)
            if ck and not has_history:
                _cache_put(ck, text)
            return text, usage
        except Exception as e:
            latency_ms = int((_t.monotonic() - started) * 1000)
            await record_call_attempt(
                account_id=account_id, user_id=user_id,
                role=user_role, action=action,
                model=cur_model_name, latency_ms=latency_ms,
                error_type=classify_error(e), usage=None,
            )
            last_exc = e
            err_str = str(e).lower()
            if ('429' in err_str or 'resource exhausted' in err_str) and attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(f"Rate limited (attempt {attempt+1}/{max_retries+1}), retrying in {wait}s")
                await asyncio.sleep(wait)
                continue
            logger.error(f"Vertex AI generation failed: {e}")
            raise
    raise last_exc  # type: ignore[misc]


# ── generate() with automatic fallback ──────────────────────────


async def generate(prompt: str, system: str = ASSISTANT_SYSTEM,
                   context_data: dict | str | None = None,
                   user_id: int | None = None,
                   account_id: int | None = None,
                   language: str = "en",
                   user_context: dict | None = None,
                   *,
                   action: str | None = None) -> tuple[str, dict | None]:
    """Generate a response with automatic fallback on 429.

    Returns ``(text, usage)`` — usage is the token-count dict or
    ``None`` on cache hits / when the backend doesn't report it.
    Forward the usage to ``log_ai_usage(... usage=...)``.

    ``action`` (e.g. ``"question"`` / ``"summary"`` / ``"diagnosis"``)
    enables router telemetry — each model attempt writes a row to
    ai_usage so the next call's fallback can prefer the historically
    fastest + most-reliable model for this account+role+action.
    """

    user_role = (user_context or {}).get("role")

    _saved_exc: Exception | None = None
    try:
        return await _generate_impl(
            prompt, system=system, context_data=context_data,
            user_id=user_id, account_id=account_id,
            language=language, user_context=user_context,
            action=action,
        )
    except Exception as primary_exc:
        if not _is_rate_limit_error(primary_exc):
            raise
        _saved_exc = primary_exc

    if account_id is not None and account_id in _account_models:
        failed_model = _account_models[account_id][0]
    else:
        failed_model = get_current_model_name()

    user_parts: list[str] = []
    if context_data:
        if isinstance(context_data, dict):
            data_str = json.dumps(context_data, separators=(',', ':'), default=str)
        else:
            data_str = str(context_data)
        if len(data_str) > 30000:
            data_str = data_str[:30000] + "\n... (truncated)"
        user_parts.append(f"Fleet data:\n```\n{data_str}\n```\n\n")
    if user_id and (user_id, account_id or 0) in _chat_histories:
        history = _chat_histories[(user_id, account_id or 0)]
        user_parts.append("Previous conversation:\n")
        for entry in history:
            user_parts.append(f"{entry['role']}: {entry['text']}\n")
        user_parts.append("\n")
    user_parts.append(f"User question: {prompt}")
    user_content = "".join(user_parts)

    # Build ordered candidate list.  Start from the static chain
    # (excludes the failed primary), then re-rank by router score so
    # the historically-best fallback gets tried first instead of
    # walking down the static list.
    candidates = [m for m in _FALLBACK_CHAIN if m != failed_model and m in MODEL_REGISTRY]
    candidates = await _order_candidates_by_score(
        candidates, account_id=account_id, role=user_role, action=action,
    )

    last_exc = _saved_exc
    for fb_model in candidates:
        info = MODEL_REGISTRY[fb_model]
        fb_location = info["locations"][0]
        try:
            logger.info(f"Fallback: trying {fb_model} after {failed_model} quota exceeded")
            text, usage = await _generate_with_model(
                fb_model, fb_location, system, user_content,
                account_id=account_id, user_id=user_id,
                role=user_role, action=action,
            )
            if user_id is not None:
                _store_history(user_id, prompt, text, account_id=account_id or 0)
            logger.info(f"Fallback succeeded with {fb_model}")
            return text, usage
        except Exception as fb_exc:
            last_exc = fb_exc
            if _is_rate_limit_error(fb_exc):
                logger.warning(f"Fallback {fb_model} also rate-limited, trying next")
                continue
            else:
                logger.error(f"Fallback {fb_model} failed (non-429): {fb_exc}")
                continue

    # Every fallback exhausted — surface the last exception so the caller
    # can map it to a real HTTP error instead of seeing a silent ``None``.
    logger.error(f"All fallback models exhausted after {failed_model} failed")
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"All fallback models exhausted after {failed_model} failed")


async def _order_candidates_by_score(
    candidates: list[str],
    *,
    account_id: int | None,
    role: str | None,
    action: str | None,
    epsilon: float = 0.10,
) -> list[str]:
    """Reorder ``candidates`` by rolling router score, ε-greedy.

    90 % of calls get the highest-scored model first (deterministic
    exploit).  10 % swap the top with a random other candidate so the
    router keeps gathering data on alternatives — without this an
    initially-bad model would never get retried after its scores
    decayed.

    Cold-start (no telemetry yet for this account): returns the input
    order unchanged.  The router only fires when there's enough data
    to be meaningful.
    """
    if account_id is None or not candidates or len(candidates) == 1:
        return candidates
    try:
        from infra.platform import get_platform_db
        pdb = get_platform_db()
        scores = await pdb.get_ai_model_scores(
            account_id, role=role, candidates=candidates,
        )
    except Exception as e:
        logger.debug("Score lookup failed; using static order: %s", e)
        return candidates

    # ε-greedy: most of the time pick the top by score and put the
    # rest in score order; sometimes promote a random non-top.
    import random as _random
    ranked = sorted(
        candidates,
        key=lambda m: scores.get(m, {}).get("score", 0.5),
        reverse=True,
    )
    if _random.random() < epsilon and len(ranked) > 1:
        # Swap top with a random other slot so an "untried" or "stale"
        # model occasionally gets primary placement.
        i = _random.randint(1, len(ranked) - 1)
        ranked[0], ranked[i] = ranked[i], ranked[0]
        logger.debug("ε-greedy explore: promoted %s", ranked[0])
    return ranked
