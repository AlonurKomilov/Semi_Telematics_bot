"""Vertex AI Gemini client for fleet intelligence.

Uses the ``google-cloud-aiplatform`` (Vertex AI) SDK so all charges
go through your Google Cloud billing account (= Cloud credits).

Environment:
    GOOGLE_APPLICATION_CREDENTIALS — path to service-account JSON
    GOOGLE_CLOUD_PROJECT           — GCP project ID (e.g. "semi-telematics")
    GOOGLE_CLOUD_LOCATION          — region (default: us-central1)
    VERTEX_AI_MODEL                — model name (default: gemini-2.5-flash)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger("bot.ai")

# Lazy-loaded SDK
_model = None
_current_model_name: str = ""
_current_location: str = ""
_chat_histories: dict[int, list[dict]] = {}  # user_id → last N exchanges
_MAX_HISTORY = 10  # keep last 10 exchanges (5 user + 5 assistant)

# Usage tracking for the last API call
_last_usage: dict | None = None

# Per-account model cache: account_id → (model_name, location, model_obj)
_account_models: dict[int, tuple[str, str, Any]] = {}

# Database reference (set by init_account_models)
_db = None


def set_db(db_instance):
    """Set the database reference for account model persistence."""
    global _db
    _db = db_instance

# ── Response Cache ───────────────────────────────────────────────
#
# Short-TTL in-memory cache for identical question+snapshot combos.
# Avoids burning tokens when users retry or multiple users ask the
# same question within a short window.

_response_cache: dict[str, tuple[float, str]] = {}  # hash → (ts, text)
_CACHE_TTL = 90  # seconds
_CACHE_MAX = 50  # max entries


def _cache_key(question: str, snapshot_hash: str, model: str) -> str:
    """Build a deterministic cache key."""
    raw = f"{model}:{question.strip().lower()}:{snapshot_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _snapshot_hash(data: dict | str | None) -> str:
    """Fast hash of the fleet snapshot."""
    if not data:
        return "empty"
    raw = json.dumps(data, separators=(',', ':'), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_get(key: str) -> str | None:
    """Return cached response text or None if miss/expired."""
    entry = _response_cache.get(key)
    if entry is None:
        return None
    ts, text = entry
    if time.time() - ts > _CACHE_TTL:
        _response_cache.pop(key, None)
        return None
    return text


def _cache_put(key: str, text: str):
    """Store a response in the cache."""
    # Evict oldest if full
    if len(_response_cache) >= _CACHE_MAX:
        oldest_key = min(_response_cache, key=lambda k: _response_cache[k][0])
        _response_cache.pop(oldest_key, None)
    _response_cache[key] = (time.time(), text)


# ── Model Registry ───────────────────────────────────────────────
#
# Static catalog of models with ALL known Vertex AI regions.  The
# live health-check (probe_model_availability) tests which of these
# the current GCP project actually has access to, and the bot only
# shows the verified subset to users.

MODEL_REGISTRY: dict[str, dict] = {
    "gemini-2.5-flash": {
        "display": "Gemini 2.5 Flash",
        "description": "Smart & fast, great balance of speed/quality",
        "category": "gemini-2.5",
        "locations": [
            "us-central1", "us-east4", "us-west1", "us-west4", "us-south1",
            "europe-west1", "europe-west2", "europe-west3", "europe-west4",
            "europe-west9", "europe-north1",
            "asia-northeast1", "asia-northeast3", "asia-southeast1",
            "australia-southeast1",
            "northamerica-northeast1",
        ],
        "max_output_tokens": 16384,
    },
    "gemini-2.5-pro": {
        "display": "Gemini 2.5 Pro",
        "description": "Most capable, best for complex reasoning",
        "category": "gemini-2.5",
        "locations": [
            "us-central1", "us-east4", "us-west1", "us-west4", "us-south1",
            "europe-west1", "europe-west4",
            "europe-west9", "europe-north1",
            "asia-northeast1",
            "northamerica-northeast1",
        ],
        "max_output_tokens": 16384,
    },
}

# Default model when nothing is stored
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_LOCATION = "us-central1"


# ── Live Availability Cache ──────────────────────────────────────
#
# After probing, this holds only the models+regions that actually
# responded 200 from the Vertex AI API for this GCP project.

_availability_cache: dict[str, list[str]] = {}   # model → [verified regions]
_availability_ts: float = 0.0                     # epoch when last probed
_AVAILABILITY_TTL = 3600                          # re-probe every hour


def _get_credentials():
    """Load service account credentials (internal helper)."""
    import os
    from google.oauth2 import service_account
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not creds_path:
        return None
    try:
        return service_account.Credentials.from_service_account_file(
            creds_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    except Exception:
        return None


def _probe_single(model: str, region: str, token: str, project: str) -> bool:
    """Send a tiny generateContent request; return True if 200."""
    import requests
    url = (
        f"https://{region}-aiplatform.googleapis.com/v1/"
        f"projects/{project}/locations/{region}/"
        f"publishers/google/models/{model}:generateContent"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
        "generationConfig": {"maxOutputTokens": 5},
    }
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def probe_model_availability(force: bool = False) -> dict[str, list[str]]:
    """Probe every model×region and cache which ones actually work.

    Returns ``{model_name: [working_regions, …], …}``.
    Skips the probe if cached results are fresh (< TTL) unless *force*.
    When cache is empty and not forced, returns static registry immediately
    and schedules background probe to avoid blocking the event loop.
    """
    global _availability_cache, _availability_ts
    import os
    from google.auth.transport.requests import Request

    # Return cached if fresh
    if not force and _availability_cache and (time.time() - _availability_ts < _AVAILABILITY_TTL):
        return _availability_cache

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    creds = _get_credentials()
    if not project or not creds:
        # Can't probe — return static registry as fallback
        return {name: info["locations"] for name, info in MODEL_REGISTRY.items()}

    # Non-forced and no cache yet → return static + schedule background probe
    if not force and not _availability_cache:
        _schedule_background_probe()
        return {name: info["locations"] for name, info in MODEL_REGISTRY.items()}

    creds.refresh(Request())
    token = creds.token

    result: dict[str, list[str]] = {}
    for model_name, info in MODEL_REGISTRY.items():
        ok_regions: list[str] = []
        for region in info["locations"]:
            if _probe_single(model_name, region, token, project):
                ok_regions.append(region)
        if ok_regions:
            result[model_name] = ok_regions
        logger.info(
            f"Probe {model_name}: {len(ok_regions)}/{len(info['locations'])} regions OK"
        )

    _availability_cache = result
    _availability_ts = time.time()
    logger.info(f"Availability probe complete: {len(result)} models available")
    return result


_probe_scheduled = False


def _schedule_background_probe():
    """Run the full probe in a background thread (non-blocking)."""
    global _probe_scheduled
    if _probe_scheduled:
        return
    _probe_scheduled = True

    import threading

    def _run():
        global _probe_scheduled
        try:
            probe_model_availability(force=True)
        except Exception as e:
            logger.warning(f"Background probe failed: {e}")
        finally:
            _probe_scheduled = False

    threading.Thread(target=_run, daemon=True).start()


def get_verified_models() -> list[dict]:
    """Return only models that passed the live probe, sorted by category."""
    avail = probe_model_availability()
    result = []
    for name, regions in avail.items():
        info = MODEL_REGISTRY.get(name, {})
        if info:
            result.append({
                "name": name,
                "display": info["display"],
                "description": info["description"],
                "category": info["category"],
                "locations": regions,
                "max_output_tokens": info["max_output_tokens"],
            })
    result.sort(key=lambda m: (m["category"], m["name"]))
    return result


def get_verified_locations(model_name: str) -> list[str]:
    """Return verified (live-probed) locations for a model."""
    avail = probe_model_availability()
    return avail.get(model_name, [DEFAULT_LOCATION])


# ── Backward-compatible helpers (used by bot/ai.py, tests) ───────

def get_model_info(model_name: str) -> dict | None:
    """Get registry info for a model, or None if unknown."""
    return MODEL_REGISTRY.get(model_name)


def get_available_models() -> list[dict]:
    """Return all models with their display info, sorted by category.

    Prefers verified (probed) data when available, otherwise static.
    """
    return get_verified_models() or [
        {"name": n, **info} for n, info in MODEL_REGISTRY.items()
    ]


def get_locations_for_model(model_name: str) -> list[str]:
    """Return available locations for a model (probed if available)."""
    return get_verified_locations(model_name)


def get_current_model_name() -> str:
    """Return the currently active model name."""
    return _current_model_name or DEFAULT_MODEL


def get_current_location() -> str:
    """Return the currently active location."""
    return _current_location or DEFAULT_LOCATION


def _ensure_model(model_name: str | None = None,
                  location: str | None = None):
    """Lazy-init the Vertex AI Gemini model on first use.

    If model_name/location differ from the current model, re-init.
    """
    global _model, _current_model_name, _current_location

    # If already initialized and no explicit switch requested, return cached
    if _model is not None and model_name is None and location is None:
        return _model

    import os

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not project:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT is not set. "
            "Set it to your GCP project ID (e.g. 'semi-telematics')."
        )
    if not creds_path:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS is not set. "
            "Point it to your service-account JSON file."
        )

    target_model = model_name or os.getenv("VERTEX_AI_MODEL", DEFAULT_MODEL)
    target_location = location or os.getenv("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION)

    # Validate against registry
    info = MODEL_REGISTRY.get(target_model)
    if info and target_location not in info["locations"]:
        # Fall back to first available location for this model
        target_location = info["locations"][0]
        logger.warning(
            f"Location not available for {target_model}, "
            f"using {target_location}"
        )

    # Re-use cached model if same config
    if (_model is not None
            and _current_model_name == target_model
            and _current_location == target_location):
        return _model

    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel, GenerationConfig
    except ImportError:
        raise RuntimeError(
            "google-cloud-aiplatform package not installed. "
            "Run: pip install google-cloud-aiplatform"
        )

    vertexai.init(project=project, location=target_location)

    max_tokens = 4096
    if info:
        max_tokens = min(info.get("max_output_tokens", 4096), 8192)

    gen_config = GenerationConfig(
        temperature=0.3,
        max_output_tokens=max_tokens,
        top_p=0.8,
    )

    # Cap thinking budget for Gemini 2.5 models to avoid token waste
    if target_model.startswith("gemini-2.5"):
        try:
            proto = gen_config._raw_generation_config
            tc = proto.ThinkingConfig(thinking_budget=2048)
            proto.thinking_config = tc
            logger.info(f"Thinking budget set to 2048 for {target_model}")
        except Exception as e:
            logger.debug(f"Could not set thinking budget: {e}")

    _model = GenerativeModel(
        model_name=target_model,
        generation_config=gen_config,
    )
    _current_model_name = target_model
    _current_location = target_location
    logger.info(
        f"Vertex AI initialized: project={project}, "
        f"location={target_location}, model={target_model}"
    )
    return _model


def switch_model(model_name: str, location: str | None = None,
                 account_id: int | None = None):
    """Switch to a different model (and optionally location).

    If account_id is provided, the preference is saved per-account
    and only that account's cached model is updated.
    Otherwise, the global default model is updated.
    """
    if model_name not in MODEL_REGISTRY:
        raise ValueError(
            f"Unknown model: {model_name}. "
            f"Available: {', '.join(MODEL_REGISTRY.keys())}"
        )
    info = MODEL_REGISTRY[model_name]
    # Use verified locations if probed, otherwise fall back to static
    verified_locs = get_verified_locations(model_name)
    target_loc = location or (verified_locs[0] if verified_locs else info["locations"][0])
    if target_loc not in info["locations"]:
        raise ValueError(
            f"Location {target_loc} not available for {model_name}. "
            f"Available: {', '.join(info['locations'])}"
        )

    if account_id is not None:
        # Per-account: build model and cache it
        model_obj = _build_model(model_name, target_loc, info)
        _account_models[account_id] = (model_name, target_loc, model_obj)
    else:
        # Global: force re-init on next call
        global _model
        _model = None
        _ensure_model(model_name=model_name, location=target_loc)


async def save_account_model(account_id: int, model_name: str,
                             location: str):
    """Persist the model preference for an account in the database."""
    if _db:
        await _db.set_account_setting(account_id, "ai_model", model_name)
        await _db.set_account_setting(account_id, "ai_location", location)


async def load_account_model(account_id: int) -> tuple[str, str]:
    """Load persisted model preference for an account.

    Returns (model_name, location). Falls back to global defaults.
    """
    if _db:
        model = await _db.get_account_setting(account_id, "ai_model", "")
        loc = await _db.get_account_setting(account_id, "ai_location", "")
        if model and model in MODEL_REGISTRY:
            loc = loc or DEFAULT_LOCATION
            return model, loc
    return get_current_model_name(), get_current_location()


def _build_model(model_name: str, location: str, info: dict | None = None):
    """Create a GenerativeModel instance (does not touch globals)."""
    import os

    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel, GenerationConfig
    except ImportError:
        raise RuntimeError("google-cloud-aiplatform package not installed.")

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    vertexai.init(project=project, location=location)

    if info is None:
        info = MODEL_REGISTRY.get(model_name, {})
    max_tokens = min(info.get("max_output_tokens", 4096), 8192)

    gen_config = GenerationConfig(
        temperature=0.3,
        max_output_tokens=max_tokens,
        top_p=0.8,
    )

    if model_name.startswith("gemini-2.5"):
        try:
            proto = gen_config._raw_generation_config
            tc = proto.ThinkingConfig(thinking_budget=2048)
            proto.thinking_config = tc
        except Exception:
            pass

    return GenerativeModel(
        model_name=model_name,
        generation_config=gen_config,
    )


def get_model_for_account(account_id: int | None = None):
    """Get the model instance for an account (or global default).

    Returns (model_obj, model_name, location).
    """
    if account_id is not None and account_id in _account_models:
        name, loc, obj = _account_models[account_id]
        return obj, name, loc
    return _ensure_model(), get_current_model_name(), get_current_location()


async def ensure_account_model(account_id: int):
    """Load account's model preference from DB and cache the model.

    Call this before making AI requests to ensure the right model is used.
    """
    if account_id in _account_models:
        return  # Already cached
    model_name, location = await load_account_model(account_id)
    # Only cache if different from global default
    if model_name != get_current_model_name() or location != get_current_location():
        info = MODEL_REGISTRY.get(model_name)
        if info:
            try:
                model_obj = _build_model(model_name, location, info)
                _account_models[account_id] = (model_name, location, model_obj)
            except Exception as e:
                logger.warning(f"Failed to build model for account {account_id}: {e}")


def is_configured() -> bool:
    """Check if Vertex AI env vars are set (without initializing)."""
    import os
    return bool(
        os.getenv("GOOGLE_CLOUD_PROJECT", "")
        and os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    )


# ── System Prompts ───────────────────────────────────────────────

FLEET_ASSISTANT_SYSTEM = """\
You are a fleet telematics AI assistant inside a Telegram bot for \
semi-truck fleet management. You help fleet managers, owners, and \
drivers understand their vehicle data.

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

Not all fields are available for every vehicle — some Samsara gateways \
don't support certain sensor types. If a field is missing, it means the \
data is not available from the hardware, not that things are normal.

Rules:
- Be concise. Telegram messages should be short and scannable.
- Use simple language — the audience is truck drivers and fleet ops, \
  not engineers.
- Format output for Telegram HTML: use <b>bold</b> for emphasis, \
  keep paragraphs short.
- When listing vehicles, use bullet points (• Truck #NNN).
- Never make up data. If the provided fleet data doesn't contain \
  what the user asked for, say so honestly and explain which fields \
  are unavailable.
- When discussing faults/DTCs, explain in plain English what the \
  fault means, how severe it is, and what action to take.
- Amounts in USD, distances in miles, temperatures in °F.
- Do NOT include markdown formatting (no **, no ##). Use only HTML tags.
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
- Use Telegram HTML formatting (<b>bold</b> for severity).
- Audience: fleet managers and truck drivers, not engineers.
- If you recognize the SPN, name the actual component.
- If it's a critical fault (STOP light), emphasize urgency.
"""

FLEET_SUMMARY_SYSTEM = """\
You are a fleet intelligence AI. Given raw fleet data, produce a \
brief executive summary suitable for a fleet manager checking their \
phone in the morning. Include:
- Overall fleet health status (good / some issues / needs attention)
- Key metrics at a glance
- Any vehicles that need immediate attention
- Brief trend observations if data supports it

IMPORTANT — Language:
- Detect the language of the user's message or context.
- ALWAYS respond in the SAME language the user used.

Rules:
- 4-6 short paragraphs max.
- Telegram HTML formatting only (<b>bold</b>, no markdown).
- Prioritize actionable information over raw stats.
- Be direct — no filler phrases.
"""


# ── Core Generation ──────────────────────────────────────────────

async def generate(prompt: str, system: str = FLEET_ASSISTANT_SYSTEM,
                   context_data: dict | str | None = None,
                   user_id: int | None = None,
                   account_id: int | None = None) -> str:
    """Generate a response from Vertex AI Gemini.

    Args:
        prompt: The user's question or instruction.
        system: System prompt to set the AI's behavior.
        context_data: Fleet data to include as context (dict → JSON).
        user_id: Telegram user ID for conversation memory.
        account_id: Account ID for per-account model selection.

    Returns:
        The AI-generated text.
    """
    import asyncio

    model, cur_model_name, _ = get_model_for_account(account_id)

    # Cache check — skip for follow-up questions (with history)
    has_history = bool(user_id and user_id in _chat_histories)
    snap_h = _snapshot_hash(context_data) if not has_history else ""
    ck = _cache_key(prompt, snap_h, cur_model_name) if not has_history else ""
    if not has_history and ck:
        cached = _cache_get(ck)
        if cached is not None:
            logger.debug("Cache hit for generate()")
            if user_id is not None:
                _store_history(user_id, prompt, cached)
            return cached

    parts = [system, "\n\n"]
    if context_data:
        if isinstance(context_data, dict):
            data_str = json.dumps(context_data, separators=(',', ':'), default=str)
        else:
            data_str = str(context_data)
        if len(data_str) > 30000:
            data_str = data_str[:30000] + "\n... (truncated)"
        parts.append(f"Fleet data:\n```\n{data_str}\n```\n\n")

    # Include conversation history for follow-up context
    if user_id and user_id in _chat_histories:
        history = _chat_histories[user_id]
        parts.append("Previous conversation:\n")
        for entry in history:
            role = entry["role"]
            parts.append(f"{role}: {entry['text']}\n")
        parts.append("\n")

    parts.append(f"User question: {prompt}")
    full_prompt = "".join(parts)

    max_retries = 2
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            response = await asyncio.to_thread(
                model.generate_content, full_prompt
            )

            # Handle safety blocks — check if response was blocked
            if not response.candidates:
                block_reason = "Unknown"
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    block_reason = str(response.prompt_feedback.block_reason)
                logger.warning(f"Gemini response blocked: {block_reason}")
                return (
                    "I couldn't generate a response for that question. "
                    "Please try rephrasing it."
                )

            candidate = response.candidates[0]
            if candidate.finish_reason and candidate.finish_reason.name == "SAFETY":
                logger.warning("Gemini response blocked by safety filter")
                return (
                    "I couldn't generate a response due to content filters. "
                    "Please try rephrasing your question."
                )

            # Handle MAX_TOKENS — model ran out of output budget
            # Try to extract partial text; if empty, return a helpful message
            try:
                text = response.text.strip()
            except ValueError:
                # response.text throws when content has no parts
                fr = getattr(candidate, 'finish_reason', None)
                reason_name = getattr(fr, 'name', str(fr)) if fr else 'unknown'
                if reason_name == 'MAX_TOKENS':
                    # Try to get partial text from parts if any exist
                    parts = getattr(getattr(candidate, 'content', None), 'parts', [])
                    if parts:
                        text = ''.join(getattr(p, 'text', '') for p in parts).strip()
                    else:
                        logger.warning(f"Gemini response empty (finish_reason={reason_name})")
                        _capture_usage(response)
                        return (
                            "The AI ran out of space generating the response. "
                            "Try asking a more specific question."
                        )
                else:
                    logger.warning(f"Cannot get response text (finish_reason={reason_name})")
                    _capture_usage(response)
                    return (
                        "I couldn't generate a response for that question. "
                        "Please try rephrasing it."
                    )

            if not text:
                _capture_usage(response)
                return (
                    "The AI generated an empty response. "
                    "Try rephrasing or asking a more specific question."
                )
            # Clean up any markdown that slipped through
            text = text.replace("**", "").replace("##", "").replace("# ", "")

            # Capture token usage metadata
            _capture_usage(response)

            # Store in conversation history
            if user_id is not None:
                _store_history(user_id, prompt, text)

            # Store in cache (only first-time questions without history)
            if ck and not has_history:
                _cache_put(ck, text)

            return text
        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            # Retry on 429 rate limit errors
            if ('429' in err_str or 'resource exhausted' in err_str) and attempt < max_retries:
                wait = 2 ** attempt  # 1s, 2s
                logger.warning(f"Rate limited (attempt {attempt+1}/{max_retries+1}), retrying in {wait}s")
                await asyncio.sleep(wait)
                continue
            logger.error(f"Vertex AI generation failed: {e}")
            raise
    # Should not reach here, but just in case
    raise last_exc  # type: ignore[misc]


def _capture_usage(response):
    """Extract token usage metadata from a Vertex AI response."""
    global _last_usage
    try:
        meta = getattr(response, "usage_metadata", None)
        if meta:
            _last_usage = {
                "prompt_tokens": getattr(meta, "prompt_token_count", 0) or 0,
                "reply_tokens": getattr(meta, "candidates_token_count", 0) or 0,
                "total_tokens": getattr(meta, "total_token_count", 0) or 0,
            }
        else:
            _last_usage = None
    except Exception:
        _last_usage = None


def get_last_usage() -> dict | None:
    """Return token usage from the most recent API call, or None."""
    return _last_usage


def _store_history(user_id: int, question: str, answer: str):
    """Store the last N exchanges for conversation context."""
    if user_id not in _chat_histories:
        _chat_histories[user_id] = []
    history = _chat_histories[user_id]
    # Truncate question/answer to keep history compact
    history.append({"role": "User", "text": question[:300]})
    history.append({"role": "Assistant", "text": answer[:500]})
    # Keep only last N entries
    if len(history) > _MAX_HISTORY * 2:
        _chat_histories[user_id] = history[-_MAX_HISTORY * 2:]


def clear_history(user_id: int):
    """Clear conversation history for a user."""
    _chat_histories.pop(user_id, None)


async def diagnose_faults(vehicle_name: str,
                          dtcs: list[dict],
                          lights: dict | None = None,
                          account_id: int | None = None) -> str:
    """AI-powered fault code diagnosis for a specific vehicle.

    Args:
        vehicle_name: Truck name/number.
        dtcs: List of DTC dicts with spnId, fmiId, spnDescription, etc.
        lights: Check engine light states (stopIsOn, protectIsOn, etc.).
        account_id: Account ID for per-account model selection.

    Returns:
        Plain-English diagnosis text (HTML formatted).
    """
    context = {
        "truck": vehicle_name,
        "check_engine_lights": lights or {},
        "active_faults": [
            {
                "spn": dtc.get("spnId"),
                "fmi": dtc.get("fmiId"),
                "description": dtc.get("spnDescription", "Unknown"),
                "severity": dtc.get("fmiDescription", "Unknown"),
                "source": dtc.get("sourceAddressName", "Unknown"),
                "count": dtc.get("count", 1),
            }
            for dtc in dtcs[:10]
        ],
    }

    prompt = (
        f"Diagnose the active faults on Truck #{vehicle_name}. "
        f"There are {len(dtcs)} active fault code(s)."
    )
    return await generate(prompt, system=FAULT_DIAGNOSIS_SYSTEM,
                          context_data=context, account_id=account_id)


async def fleet_summary(fleet_data: dict,
                        account_id: int | None = None) -> str:
    """Generate an AI executive summary of fleet status.

    Args:
        fleet_data: Dict with keys like total_vehicles, faulted_count,
                    low_fuel_count, health_alerts, efficiency, etc.

    Returns:
        Executive summary text (HTML formatted).
    """
    prompt = "Generate a morning fleet status briefing from this data."
    return await generate(prompt, system=FLEET_SUMMARY_SYSTEM,
                          context_data=fleet_data, account_id=account_id)


async def ask_fleet(question: str, fleet_context: dict,
                    user_id: int | None = None,
                    account_id: int | None = None) -> str:
    """Answer a natural-language question about the fleet.

    Args:
        question: The user's question in plain English.
        fleet_context: Current fleet snapshot data.
        user_id: Telegram user ID for conversation memory.
        account_id: Account ID for per-account model selection.

    Returns:
        AI answer (HTML formatted).
    """
    return await generate(question, system=FLEET_ASSISTANT_SYSTEM,
                          context_data=fleet_context, user_id=user_id,
                          account_id=account_id)


# ── Function-Calling Agent ───────────────────────────────────────
#
# When ask_fleet_agent() is used, the AI can call back into Samsara
# to fetch data on demand, instead of relying only on the pre-built
# snapshot.  This makes it smarter for specific / drill-down questions.

# Tool declarations for Gemini function calling
FLEET_TOOLS = [
    {
        "name": "get_truck_faults",
        "description": (
            "Get active J1939/OBD fault codes (DTCs) and check engine light "
            "status for a specific truck by its name/number."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "truck_name": {
                    "type": "string",
                    "description": "The truck name or number, e.g. '101' or 'Truck 205'",
                },
            },
            "required": ["truck_name"],
        },
    },
    {
        "name": "get_truck_detail",
        "description": (
            "Get detailed info for a specific truck: VIN, make/model/year, "
            "fuel level, DEF level, GPS location, and fault summary."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "truck_name": {
                    "type": "string",
                    "description": "The truck name or number",
                },
            },
            "required": ["truck_name"],
        },
    },
    {
        "name": "get_driver_efficiency",
        "description": (
            "Get driver efficiency stats for the last N days: MPG, idle %, "
            "miles driven, eco-driving score, overspeed minutes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back (default 7)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_critical_faults",
        "description": (
            "Get all trucks with critical faults: STOP light, PROTECT light, "
            "EMISSIONS light, or severe FMI codes. Returns only critical vehicles."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_low_fuel_vehicles",
        "description": (
            "Get trucks with fuel level at or below a threshold percentage."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "integer",
                    "description": "Fuel percentage threshold (default 20)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_vehicle_health",
        "description": (
            "Get health diagnostics for all vehicles: battery voltage, "
            "coolant temperature, oil pressure, DEF level, engine RPM, "
            "seatbelt status, and health alerts (low battery, high coolant, etc)."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_fleet_weather",
        "description": (
            "Get ambient air temperature (°F) for each truck's current location. "
            "Useful for identifying trucks in extreme cold or heat conditions."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_fleet_efficiency",
        "description": (
            "Get engine hours, driving hours, idle hours, idle percentage, "
            "and miles driven per truck over the last N days. Also includes "
            "driver fuel efficiency (MPG) and eco-driving score when available."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back (default 7)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_truck_location",
        "description": (
            "Get the current GPS location, city, and speed for a specific truck."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "truck_name": {
                    "type": "string",
                    "description": "The truck name or number",
                },
            },
            "required": ["truck_name"],
        },
    },
    {
        "name": "get_geofences",
        "description": (
            "Get all geofence zones defined in Samsara: name, address, "
            "type (circle/polygon), and coordinates."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "count_fleet_stats",
        "description": (
            "Get quick fleet-wide counts: total active trucks, trucks with "
            "faults, trucks with critical faults, low fuel trucks, and "
            "trucks with health alerts. Fast overview without full details."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


async def _execute_tool_call(tool_name: str, tool_args: dict,
                             samsara_client) -> dict:
    """Execute a single tool call and return the result as a dict."""
    try:
        if tool_name == "get_truck_faults":
            truck = tool_args.get("truck_name", "")
            faulted, _ = await samsara_client.get_vehicles_with_faults()
            matches = [
                v for v in faulted
                if v.get("name", "").lower() == truck.lower()
            ]
            if not matches:
                return {"result": f"No active faults found for truck {truck}."}
            v = matches[0]
            return {
                "truck": v.get("name"),
                "fault_count": len(v.get("_dtcs", [])),
                "faults": [
                    {
                        "spn": d.get("spnId"),
                        "fmi": d.get("fmiId"),
                        "description": d.get("spnDescription", "?"),
                        "severity": d.get("fmiDescription", "?"),
                    }
                    for d in v.get("_dtcs", [])[:10]
                ],
                "check_engine_lights": v.get("_lights", {}),
            }

        elif tool_name == "get_truck_detail":
            truck = tool_args.get("truck_name", "")
            detail = await samsara_client.get_vehicle_detail(truck)
            if not detail:
                return {"result": f"Truck {truck} not found."}
            # Handle both single dict and list return
            v = detail[0] if isinstance(detail, list) else detail
            loc = v.get("location", {})
            return {
                "truck": v.get("name"),
                "vin": v.get("vin"),
                "make": v.get("make"),
                "model": v.get("model"),
                "year": v.get("year"),
                "fuel_pct": v.get("fuel", {}).get("value"),
                "def_pct": v.get("def_level", {}).get("value"),
                "city": loc.get("reverseGeo", {}).get("formattedLocation", ""),
                "speed_mph": round(loc.get("speed", 0) * 0.621371, 1) if loc.get("speed") else 0,
            }

        elif tool_name == "get_driver_efficiency":
            days = tool_args.get("days", 7)
            drivers = await samsara_client.get_driver_efficiency(days=days)
            return {
                "period_days": days,
                "drivers": [
                    {
                        "name": d["driver_name"],
                        "miles": d.get("_miles"),
                        "mpg": d.get("_mpg"),
                        "idle_pct": d.get("_idle_pct"),
                        "drive_hours": d.get("_drive_h"),
                        "green_pct": d.get("_green_pct"),
                        "overspeed_min": d.get("_overspeed_min"),
                    }
                    for d in drivers[:20]
                ],
            }

        elif tool_name == "get_critical_faults":
            critical = await samsara_client.get_critical_faults()
            return {
                "critical_count": len(critical),
                "vehicles": [
                    {
                        "truck": v.get("name"),
                        "lights": v.get("_lights", {}),
                        "fault_count": len(v.get("_dtcs", [])),
                        "top_faults": [
                            d.get("spnDescription", "?")
                            for d in v.get("_dtcs", [])[:3]
                        ],
                    }
                    for v in critical[:15]
                ],
            }

        elif tool_name == "get_low_fuel_vehicles":
            threshold = tool_args.get("threshold", 20)
            low = await samsara_client.get_low_fuel_vehicles(threshold=threshold)
            return {
                "threshold_pct": threshold,
                "count": len(low),
                "vehicles": [
                    {
                        "truck": v.get("name"),
                        "fuel_pct": v.get("_fuel_pct"),
                    }
                    for v in low[:20]
                ],
            }

        elif tool_name == "get_vehicle_health":
            health = await samsara_client.get_vehicle_health()
            return {
                "total_vehicles": len(health),
                "vehicles_with_alerts": sum(1 for v in health if v.get("_health_alerts")),
                "vehicles": [
                    {
                        "truck": v.get("name"),
                        "battery_v": v.get("_health", {}).get("battery_v"),
                        "coolant_c": v.get("_health", {}).get("coolant_c"),
                        "oil_psi": v.get("_health", {}).get("oil_psi"),
                        "def_pct": v.get("_health", {}).get("def_pct"),
                        "rpm": v.get("_health", {}).get("rpm"),
                        "engine_on": v.get("_health", {}).get("engine_on"),
                        "seatbelt": v.get("_health", {}).get("seatbelt"),
                        "alerts": v.get("_health_alerts", []),
                    }
                    for v in health[:30]
                ],
            }

        elif tool_name == "get_fleet_weather":
            weather = await samsara_client.get_fleet_weather()
            return {
                "truck_count": len(weather),
                "trucks": [
                    {
                        "truck": v.get("name"),
                        "temp_f": v.get("_weather", {}).get("temp_f"),
                        "temp_c": v.get("_weather", {}).get("temp_c"),
                        "city": v.get("location", {}).get("reverseGeo", {}).get(
                            "formattedLocation", ""),
                    }
                    for v in weather[:30]
                    if v.get("_weather", {}).get("temp_f") is not None
                ],
            }

        elif tool_name == "get_fleet_efficiency":
            days = tool_args.get("days", 7)
            eff = await samsara_client.get_fleet_efficiency(days=days)
            return {
                "period_days": days,
                "truck_count": len(eff),
                "trucks": [
                    {
                        "truck": v.get("name"),
                        "engine_hours": v.get("_engine_hours"),
                        "driving_hours": v.get("_driving_hours"),
                        "idle_hours": v.get("_idle_hours"),
                        "idle_pct": v.get("_idle_pct"),
                        "miles": v.get("_miles"),
                        "driver": v.get("_driver_name"),
                        "mpg": v.get("_mpg"),
                        "green_pct": v.get("_green_pct"),
                    }
                    for v in eff[:30]
                ],
            }

        elif tool_name == "get_truck_location":
            truck = tool_args.get("truck_name", "")
            detail = await samsara_client.get_vehicle_detail(truck)
            if not detail:
                return {"result": f"Truck {truck} not found."}
            v = detail[0] if isinstance(detail, list) else detail
            loc = v.get("location", {})
            geo = loc.get("reverseGeo", {})
            return {
                "truck": v.get("name"),
                "city": geo.get("formattedLocation", "Unknown"),
                "latitude": loc.get("latitude"),
                "longitude": loc.get("longitude"),
                "speed_mph": round(loc.get("speed", 0) * 0.621371, 1) if loc.get("speed") else 0,
                "heading": loc.get("heading"),
                "time": loc.get("time", ""),
            }

        elif tool_name == "get_geofences":
            fences = await samsara_client.get_geofences()
            return {
                "count": len(fences),
                "geofences": [
                    {
                        "name": f.get("name"),
                        "address": (f.get("formattedAddress")
                                    or f.get("address", {}).get("formattedAddress", "")),
                    }
                    for f in fences[:30]
                ],
            }

        elif tool_name == "count_fleet_stats":
            fleet = await samsara_client.get_fleet_overview()
            faulted = []
            critical = []
            for v in fleet:
                fc = v.get("fault_codes", {})
                j1939 = fc.get("j1939", {})
                dtcs = j1939.get("diagnosticTroubleCodes", [])
                cel = j1939.get("checkEngineLights", {})
                if dtcs:
                    faulted.append(v)
                if (cel.get("stopIsOn") or cel.get("protectIsOn")
                        or cel.get("emissionsIsOn")):
                    critical.append(v)
            low_fuel = [v for v in fleet
                        if (v.get("fuel", {}).get("value") or 100) <= 20]
            try:
                health = await samsara_client.get_vehicle_health()
                alerts = sum(1 for v in health if v.get("_health_alerts"))
            except Exception:
                alerts = 0
            return {
                "total_active_trucks": len(fleet),
                "trucks_with_faults": len(faulted),
                "trucks_critical": len(critical),
                "trucks_low_fuel": len(low_fuel),
                "trucks_with_health_alerts": alerts,
            }

        else:
            return {"error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        logger.error(f"Tool {tool_name} failed: {e}")
        return {"error": str(e)}


_cached_tools = None  # Lazy-built Tool objects for function calling


def _get_cached_tools():
    """Return cached Vertex AI Tool objects, building them once on first call."""
    global _cached_tools
    if _cached_tools is not None:
        return _cached_tools
    from vertexai.generative_models import Tool, FunctionDeclaration
    func_decls = [FunctionDeclaration(**td) for td in FLEET_TOOLS]
    _cached_tools = [Tool(function_declarations=func_decls)]
    return _cached_tools


async def ask_fleet_agent(question: str, fleet_context: dict,
                          samsara_client,
                          user_id: int | None = None,
                          account_id: int | None = None) -> dict:
    """Agent-mode: AI can call Samsara tools to answer questions.

    Uses Gemini function calling. The AI decides which tools to call
    based on the user's question.

    Returns dict: {"text": str, "tool_results": [{"tool": name, "data": dict}, ...]}
    Falls back to regular ask_fleet if function calling is unavailable.
    """
    import asyncio

    try:
        from vertexai.generative_models import (
            GenerativeModel, GenerationConfig, Tool, FunctionDeclaration,
            Part, Content,
        )
    except ImportError:
        text = await ask_fleet(question, fleet_context, user_id=user_id,
                               account_id=account_id)
        return {"text": text, "tool_results": []}

    model, cur_model_name, _ = get_model_for_account(account_id)

    # Cache check — skip for follow-up questions
    has_history = bool(user_id and user_id in _chat_histories)
    snap_h = _snapshot_hash(fleet_context) if not has_history else ""
    ck = _cache_key(question, snap_h, cur_model_name) if not has_history else ""
    if not has_history and ck:
        cached = _cache_get(ck)
        if cached is not None:
            logger.debug("Cache hit for ask_fleet_agent()")
            if user_id is not None:
                _store_history(user_id, question, cached)
            return {"text": cached, "tool_results": []}

    # Build tool declarations (cached after first build)
    tools = _get_cached_tools()

    # Build the prompt with context
    parts = [FLEET_ASSISTANT_SYSTEM, "\n\n"]
    if fleet_context:
        data_str = json.dumps(fleet_context, separators=(',', ':'), default=str)
        if len(data_str) > 30000:
            data_str = data_str[:30000] + "\n... (truncated)"
        parts.append(f"Fleet snapshot:\n```\n{data_str}\n```\n\n")

    parts.append(
        "You have access to tools that can fetch live data from Samsara. "
        "Use them when the user asks about specific trucks, faults, fuel, "
        "or driver efficiency. If the snapshot already has enough info, "
        "answer directly without calling tools.\n\n"
    )

    if user_id and user_id in _chat_histories:
        history = _chat_histories[user_id]
        parts.append("Previous conversation:\n")
        for entry in history:
            parts.append(f"{entry['role']}: {entry['text']}\n")
        parts.append("\n")

    parts.append(f"User question: {question}")
    full_prompt = "".join(parts)
    tool_results: list[dict] = []

    max_retries = 2
    last_exc = None

    for attempt in range(max_retries + 1):
        try:
            response = await asyncio.to_thread(
                model.generate_content, full_prompt, tools=tools,
            )

            # Process function calls (up to 3 rounds of tool use)
            for _round in range(3):
                if not response.candidates:
                    return {
                        "text": (
                            "I couldn't generate a response for that question. "
                            "Please try rephrasing it."
                        ),
                        "tool_results": tool_results,
                    }

                candidate = response.candidates[0]
                part = candidate.content.parts[0]

                # If the model returns text, we're done
                if part.text:
                    text = part.text.strip()
                    text = text.replace("**", "").replace("##", "").replace("# ", "")
                    _capture_usage(response)
                    if user_id is not None:
                        _store_history(user_id, question, text)
                    if ck and not has_history:
                        _cache_put(ck, text)
                    return {"text": text, "tool_results": tool_results}

                # If the model calls a function, execute it
                if part.function_call:
                    fc = part.function_call
                    tool_name = fc.name
                    tool_args = dict(fc.args) if fc.args else {}

                    logger.info(f"AI agent calling tool: {tool_name}({tool_args})")
                    result = await _execute_tool_call(
                        tool_name, tool_args, samsara_client,
                    )
                    tool_results.append({"tool": tool_name, "args": tool_args, "data": result})

                    # Feed the result back to the model
                    fn_response = Part.from_function_response(
                        name=tool_name,
                        response={"result": result},
                    )
                    response = await asyncio.to_thread(
                        model.generate_content,
                        [
                            Content(parts=[Part.from_text(full_prompt)], role="user"),
                            candidate.content,
                            Content(parts=[fn_response], role="function"),
                        ],
                        tools=tools,
                    )
                else:
                    break

            # Extract final text after tool-use rounds
            if response.candidates and response.candidates[0].content.parts:
                try:
                    text = response.text.strip()
                except ValueError:
                    parts_list = response.candidates[0].content.parts
                    text = ''.join(getattr(p, 'text', '') for p in parts_list).strip()
                text = text.replace("**", "").replace("##", "").replace("# ", "")
                _capture_usage(response)
                if user_id is not None:
                    _store_history(user_id, question, text)
                if ck and not has_history:
                    _cache_put(ck, text)
                return {"text": text, "tool_results": tool_results}

            # No usable text — fall back to simple generate
            text = await ask_fleet(question, fleet_context, user_id=user_id,
                                   account_id=account_id)
            return {"text": text, "tool_results": tool_results}

        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            # Retry on 429 rate limit errors with backoff
            if ('429' in err_str or 'resource exhausted' in err_str) and attempt < max_retries:
                wait = 2 ** attempt  # 1s, 2s
                logger.warning(
                    f"Agent rate limited (attempt {attempt+1}/{max_retries+1}), "
                    f"retrying in {wait}s"
                )
                await asyncio.sleep(wait)
                continue
            # Non-retryable error → fall back to simple generate
            logger.warning(f"Agent mode failed, falling back: {e}")
            text = await ask_fleet(question, fleet_context, user_id=user_id,
                                   account_id=account_id)
            return {"text": text, "tool_results": tool_results}

    # Exhausted retries — fall back
    logger.warning(f"Agent exhausted retries, falling back: {last_exc}")
    text = await ask_fleet(question, fleet_context, user_id=user_id,
                           account_id=account_id)
    return {"text": text, "tool_results": tool_results}
