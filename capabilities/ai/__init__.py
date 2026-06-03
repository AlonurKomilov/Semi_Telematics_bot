"""Vertex AI Gemini client for telematics intelligence.

Uses the ``google-cloud-aiplatform`` (Vertex AI) SDK so all charges
go through your Google Cloud billing account (= Cloud credits).
"""

# Explicit __all__ so ``from ai import *`` includes private names that
# tests and other internal code reference directly.
__all__ = [
    # registry
    "MODEL_REGISTRY", "MODEL_PRICING",
    "DEFAULT_MODEL", "DEFAULT_LOCATION",
    "DEFAULT_VISION_MODEL", "DEFAULT_VISION_LOCATION",
    "_FALLBACK_CHAIN", "_COST_MARGIN",
    "_TYPICAL_INPUT_TOKENS", "_TYPICAL_OUTPUT_TOKENS",
    "estimate_cost", "estimate_request_cost",
    "_is_openai_compat", "is_vision_capable",
    "get_model_info", "get_locations_for_model",
    "get_available_models", "get_vision_models", "get_text_models",
    "_maas_base_url", "_anthropic_url", "_mistral_url", "_get_credentials",
    "get_vertex_ai_cloud_usage",
    # cache
    "_response_cache", "_CACHE_TTL", "_CACHE_MAX",
    "_cache_key", "_snapshot_hash", "_cache_get", "_cache_put",
    # chat
    "_chat_histories", "_MAX_HISTORY", "_MAX_CHAT_USERS",
    "_store_history", "clear_history",
    # models
    "_account_models", "_account_vision_models",
    "set_db",
    "get_account_model_name", "get_account_model_info",
    "get_account_vision_model_name", "get_account_vision_model_info",
    "get_user_model_name", "get_user_model_info",
    "get_current_model_name", "get_current_location",
    "is_configured",
    "_ensure_model", "_build_model",
    "switch_model", "switch_vision_model",
    "switch_user_model",
    "save_account_model", "load_account_model",
    "save_account_vision_model", "load_account_vision_model",
    "save_user_model", "load_user_model",
    "ensure_account_model", "ensure_account_vision_model",
    "ensure_user_model",
    "get_model_for_account", "get_model_for_user",
    # probing
    "probe_model_availability",
    # generation
    "generate",
    "_generate_with_model", "_generate_openai_compat",
    "_generate_anthropic", "_generate_mistral_raw",
    "_is_rate_limit_error",
    "_capture_usage",
    "ASSISTANT_SYSTEM", "FAULT_DIAGNOSIS_SYSTEM", "SUMMARY_SYSTEM",
    # intelligence
    "diagnose_faults", "generate_summary", "ask_ai", "ask_agent",
    "ask_agent_stream",
    "build_context",
    "AI_TOOLS",
    # vision
    "analyze_camera_image", "generate_with_vision", "CAMERA_CHECK_SYSTEM",
]

# ── registry (static data, no deps) ─────────────────────────────
from capabilities.ai.registry import (                                   # noqa: F401
    MODEL_REGISTRY,
    MODEL_PRICING,
    DEFAULT_MODEL,
    DEFAULT_LOCATION,
    DEFAULT_VISION_MODEL,
    DEFAULT_VISION_LOCATION,
    _FALLBACK_CHAIN,
    _COST_MARGIN,
    _TYPICAL_INPUT_TOKENS,
    _TYPICAL_OUTPUT_TOKENS,
    estimate_cost,
    estimate_request_cost,
    _is_openai_compat,
    is_vision_capable,
    get_model_maker,
    get_model_info,
    get_locations_for_model,
    get_available_models,
    get_vision_models,
    get_text_models,
    _maas_base_url,
    _anthropic_url,
    _mistral_url,
    _get_credentials,
    get_vertex_ai_cloud_usage,
)

# ── cache (no deps) ─────────────────────────────────────────────
from capabilities.ai.cache import (                                       # noqa: F401
    _response_cache,
    _CACHE_TTL,
    _CACHE_MAX,
    _cache_key,
    _snapshot_hash,
    _cache_get,
    _cache_put,
)

# ── chat (no deps) ──────────────────────────────────────────────
from capabilities.ai.chat import (                                        # noqa: F401
    _chat_histories,
    _MAX_HISTORY,
    _MAX_CHAT_USERS,
    _store_history,
    clear_history,
)

# ── models (depends: registry) ──────────────────────────────────
from capabilities.ai.models import (                                      # noqa: F401
    _account_models,
    _account_vision_models,
    set_db,
    get_account_model_name,
    get_account_model_info,
    get_account_vision_model_name,
    get_account_vision_model_info,
    get_current_model_name,
    get_current_location,
    is_configured,
    _ensure_model,
    _build_model,
    switch_model,
    switch_vision_model,
    save_account_model,
    load_account_model,
    save_account_vision_model,
    load_account_vision_model,
    save_user_model,
    load_user_model,
    ensure_account_model,
    ensure_account_vision_model,
    ensure_user_model,
    get_model_for_account,
    get_model_for_user,
    get_user_model_name,
    get_user_model_info,
    switch_user_model,
)

# ── probing (depends: registry) ─────────────────────────────────
from capabilities.ai.probing import (                                     # noqa: F401
    probe_model_availability,
)

# ── generation (depends: registry, cache, chat, models) ─────────
from capabilities.ai.generation import (                                  # noqa: F401
    generate,
    _generate_with_model,
    _generate_openai_compat,
    _generate_anthropic,
    _generate_mistral_raw,
    _is_rate_limit_error,
    _capture_usage,
    ASSISTANT_SYSTEM,
    FAULT_DIAGNOSIS_SYSTEM,
    SUMMARY_SYSTEM,
)

# ── intelligence (depends: generation) ─────────────────────────
from capabilities.ai.intelligence import (                               # noqa: F401
    diagnose_faults,
    generate_summary,
    ask_ai,
    ask_agent,
    ask_agent_stream,
    build_context,
)

# ── tools registry ───────────────────────────────────────────────
from capabilities.ai.tools import AI_TOOLS                               # noqa: F401

# ── vision (depends: models, generation) ─────────────────────────
from capabilities.ai.vision import (                                      # noqa: F401
    analyze_camera_image,
    generate_with_vision,
    CAMERA_CHECK_SYSTEM,
)


# ── Proxy for mutable state ─────────────────────────────────────
# _model lives in ai.models and is reassigned via `global`.
# We expose it via __getattr__ so reads like ``ai._model`` always
# reflect the current value in ai.models.

def __getattr__(name: str):
    if name == "_model":
        from capabilities.ai import models as _m
        return _m._model
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
