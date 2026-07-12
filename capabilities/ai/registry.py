"""Static model catalog, pricing, URL builders, and credentials."""

from __future__ import annotations

import logging

logger = logging.getLogger("bot.ai")

# ── Model Registry ───────────────────────────────────────────────
#
# Static catalog of models with ALL known Vertex AI regions.  The
# live health-check (probe_model_availability) tests which of these
# the current GCP project actually has access to, and the bot only
# shows the verified subset to users.

MODEL_REGISTRY: dict[str, dict] = {
    # ── Gemini (native Vertex AI SDK) ─────────────────────────
    "gemini-2.5-flash": {
        "display": "Gemini 2.5 Flash",
        "description": "Smart & fast, great balance of speed/quality",
        "category": "gemini",
        "api_type": "gemini",
        "locations": [
            "us-central1", "us-east4", "us-west1", "us-west4", "us-south1",
            "europe-west1", "europe-west2", "europe-west3", "europe-west4",
            "europe-west9", "europe-north1",
            "asia-northeast1", "asia-northeast3", "asia-southeast1",
            "australia-southeast1",
            "northamerica-northeast1",
        ],
        "max_output_tokens": 16384,
        # Flash variants accept thinking_budget=0; thinking adds 30-45s
        # per call in the agentic loop (2 rounds = 60-90s) and blows
        # the nginx/client timeouts.  ``gemini-2.5-pro`` REJECTS budget=0
        # with a 400 — leave it unset on Pro entries.
        "thinking_budget": 0,
    },
    "gemini-2.5-pro": {
        "display": "Gemini 2.5 Pro",
        "description": "Most capable, best for complex reasoning",
        "category": "gemini",
        "api_type": "gemini",
        "locations": [
            "us-central1", "us-east4", "us-west1", "us-west4", "us-south1",
            "europe-west1", "europe-west4",
            "europe-west9", "europe-north1",
            "asia-northeast1",
            "northamerica-northeast1",
        ],
        "max_output_tokens": 16384,
    },
    # ── MaaS models (OpenAI-compatible endpoint, serverless) ─
    "deepseek-r1": {
        "display": "DeepSeek R1",
        "description": "Deep reasoning model, strong on analysis",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "deepseek-ai/deepseek-r1-0528-maas",
        "locations": ["us-central1"],
        "max_output_tokens": 8192,
    },
    "deepseek-v3.2": {
        "display": "DeepSeek V3.2",
        "description": "Fast general-purpose, hybrid thinking mode",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "deepseek-ai/deepseek-v3.2-maas",
        "locations": ["global"],
        "max_output_tokens": 8192,
        # Hybrid model: thinking mode is OFF by default on the API.
        # This model serves the Thinking tier, so opt in explicitly —
        # the vLLM-style toggle passed through the OpenAI-compat body.
        # If the endpoint ever rejects the field, the router's error
        # scoring downweights the model and the chain moves on.
        "extra_body": {"chat_template_kwargs": {"thinking": True}},
    },
    "deepseek-v3.1": {
        "display": "DeepSeek V3.1",
        "description": "Hybrid inference — thinking + non-thinking modes",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "deepseek-ai/deepseek-v3.1-maas",
        "locations": ["us-west2"],
        "max_output_tokens": 8192,
        # Same Thinking-tier opt-in as deepseek-v3.2 — see that entry.
        "extra_body": {"chat_template_kwargs": {"thinking": True}},
    },
    "deepseek-ocr": {
        "display": "DeepSeek OCR",
        "description": "DeepSeek OCR — vision-text compression & analysis",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "deepseek-ai/deepseek-ocr-maas",
        "locations": ["global"],
        "max_output_tokens": 8192,
    },
    # NOTE: a legacy ``kimi-k2`` entry used to live here pointing at the
    # *same* ``moonshotai/kimi-k2-thinking-maas`` endpoint as
    # ``kimi-k2-thinking`` below — a registry-level duplicate that
    # made cost telemetry, the probe, and the cloud-monitoring map
    # all conflate the two.  Dropped: ``kimi-k2-thinking`` is the
    # canonical entry.  Users with the legacy key saved in
    # ``account_models`` / ``user_models`` will fall through to the
    # account default on next resolve (registry lookup returns None,
    # caller handles that path).  When the non-thinking Kimi K2.6
    # base is added, it'll get its own ``kimi-k2.6`` key.
    "qwen3-next": {
        "display": "Qwen3-Next 80B",
        "description": "Alibaba Qwen3-Next instruct — efficient 80B MoE",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "qwen/qwen3-next-80b-a3b-instruct-maas",
        "locations": ["global"],
        "max_output_tokens": 8192,
    },
    "minimax-m2": {
        "display": "MiniMax M2",
        "description": "MiniMax M2 — versatile general model",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "minimaxai/minimax-m2-maas",
        "locations": ["global"],
        "max_output_tokens": 8192,
    },
    "glm-5": {
        "display": "GLM 5",
        "description": "Zhipu GLM 5 — large-scale general model",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "zai-org/glm-5-maas",
        "locations": ["global"],
        "max_output_tokens": 8192,
    },
    "glm-4.7": {
        "display": "GLM 4.7",
        "description": "Zhipu GLM 4.7 — efficient general model",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "zai-org/glm-4.7-maas",
        "locations": ["global"],
        "max_output_tokens": 8192,
    },
    "gpt-oss-120b": {
        "display": "GPT OSS 120B",
        "description": "OpenAI open-source 120B model",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "openai/gpt-oss-120b-maas",
        "locations": ["global"],
        "max_output_tokens": 8192,
    },
    "gpt-oss-20b": {
        "display": "GPT OSS 20B",
        "description": "OpenAI open-source 20B — fast & cheap",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "openai/gpt-oss-20b-maas",
        "locations": ["global"],
        "max_output_tokens": 8192,
    },
    "llama-4-scout": {
        "display": "Llama 4 Scout",
        "description": "Meta Llama 4 Scout — fast 17B MoE model",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "meta/llama-4-scout-17b-16e-instruct-maas",
        "locations": ["us-east5"],
        "max_output_tokens": 8192,
    },
    "llama-4-maverick": {
        "display": "Llama 4 Maverick",
        "description": "Meta Llama 4 Maverick — powerful 17B x 128E MoE",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "meta/llama-4-maverick-17b-128e-instruct-maas",
        "locations": ["us-east5"],
        "max_output_tokens": 8192,
    },
    "llama-3.3": {
        "display": "Llama 3.3 70B",
        "description": "Meta Llama 3.3 70B — proven & reliable",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "meta/llama-3.3-70b-instruct-maas",
        "locations": ["us-central1"],
        "max_output_tokens": 8192,
    },
    # ── Gemini 3.x (native Vertex AI SDK, global only) ───────
    "gemini-3.1-flash-lite-preview": {
        "display": "Gemini 3.1 Flash-Lite",
        "description": "Next-gen Gemini — ultra fast & cheap (preview)",
        "category": "gemini",
        "api_type": "gemini",
        "locations": ["global"],
        "max_output_tokens": 16384,
    },
    "gemini-3.1-pro-preview": {
        "display": "Gemini 3.1 Pro",
        "description": "Next-gen Gemini Pro — most capable (preview)",
        "category": "gemini",
        "api_type": "gemini",
        "locations": ["global"],
        "max_output_tokens": 16384,
    },
    # ── Claude (Anthropic rawPredict on Vertex AI) ───────────
    # Requires per-base-model quota on the GCP project.  When quota
    # is 0, the live probe filter hides these from the picker so a
    # user can't tap a model that everyone gets 429 from.  Request
    # quota via https://console.cloud.google.com/vertex-ai/quotas
    "claude-sonnet-4.6": {
        "display": "Claude Sonnet 4.6",
        "description": "Anthropic Claude Sonnet 4.6 — balanced reasoning",
        "category": "anthropic",
        "api_type": "anthropic",
        "anthropic_model_id": "claude-sonnet-4-6",
        "locations": ["global"],
        "max_output_tokens": 8192,
        # Extended thinking ON — this model serves the Thinking tier, so
        # it must actually think.  The budget is carved out of
        # max_tokens; the caller bumps max_tokens to budget + headroom
        # and switches to the thinking-mode constraints (temperature=1,
        # no top_p).  Remove this key to run it as a plain model.
        "anthropic_thinking_budget": 4096,
    },
    # ── Gemini 3.5 Flash (newer than 2.5, drop-in upgrade) ────
    "gemini-3.5-flash": {
        "display": "Gemini 3.5 Flash",
        "description": "Latest Gemini Flash — faster & smarter than 2.5",
        "category": "gemini",
        "api_type": "gemini",
        "locations": ["global"],
        "max_output_tokens": 16384,
        # Same rationale as gemini-2.5-flash — Flash class disables thinking
        # so agentic tool loops stay under the request-deadline budget.
        "thinking_budget": 0,
    },
    # ── Reasoning MaaS additions (verified working in this project) ──
    "qwen3-next-thinking": {
        "display": "Qwen3-Next Thinking",
        "description": "Qwen 80B reasoning model — step-by-step thinking",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "qwen/qwen3-next-80b-a3b-thinking-maas",
        "locations": ["global"],
        "max_output_tokens": 16384,
    },
    "kimi-k2-thinking": {
        "display": "Kimi K2 Thinking",
        "description": "Moonshot Kimi K2 reasoning model — long-context agent",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "moonshotai/kimi-k2-thinking-maas",
        "locations": ["global"],
        "max_output_tokens": 16384,
    },
    "grok-4.20-reasoning": {
        "display": "Grok 4.20 Reasoning",
        "description": "xAI Grok 4.20 — low hallucination, 2M context, agent-grade",
        "category": "maas",
        "api_type": "openai_compat",
        "maas_model_id": "xai/grok-4.20-reasoning",
        "locations": ["global"],
        "max_output_tokens": 16384,
    },
}

# ── Pricing (USD per 1M tokens) ─────────────────────────────────
# Vertex AI pricing includes separate rates for thinking tokens.
# For Gemini: thinking is NOT included in reply (candidates_token_count).
# For MaaS (OpenAI-compat): reasoning IS included in completion_tokens.
#   "thinking" rates are set explicitly for all reasoning models.
#   MaaS reasoning = output rate on Vertex AI (as of 2026-Q2).

MODEL_PRICING: dict[str, dict[str, float]] = {
    # Gemini (thinking separate from output in API)
    "gemini-2.5-flash":            {"input": 0.15, "output": 0.60, "thinking": 0.70},
    "gemini-2.5-pro":              {"input": 1.25, "output": 10.00, "thinking": 3.75},
    "gemini-3.1-flash-lite-preview": {"input": 0.10, "output": 0.40, "thinking": 0.40},
    "gemini-3.1-pro-preview":      {"input": 1.25, "output": 10.00, "thinking": 3.75},
    "gemini-3.5-flash":            {"input": 0.15, "output": 0.60, "thinking": 0.70},
    # MaaS reasoning models (thinking included in completion_tokens)
    "deepseek-r1":                 {"input": 0.80, "output": 2.17, "thinking": 2.17},
    "deepseek-v3.2":               {"input": 0.30, "output": 0.88, "thinking": 0.88},
    "deepseek-v3.1":               {"input": 0.30, "output": 0.88, "thinking": 0.88},
    "kimi-k2-thinking":            {"input": 0.60, "output": 2.40, "thinking": 2.40},
    "qwen3-next-thinking":         {"input": 0.14, "output": 0.55, "thinking": 0.55},
    "grok-4.20-reasoning":         {"input": 3.00, "output": 15.00, "thinking": 15.00},
    # MaaS non-reasoning models
    "deepseek-ocr":                {"input": 0.30, "output": 0.88},
    "qwen3-next":                  {"input": 0.14, "output": 0.27},
    "minimax-m2":                  {"input": 0.36, "output": 1.10},
    "glm-5":                       {"input": 0.50, "output": 2.00},
    "glm-4.7":                     {"input": 0.14, "output": 0.55},
    "gpt-oss-120b":                {"input": 0.30, "output": 0.50},
    "gpt-oss-20b":                 {"input": 0.10, "output": 0.30},
    "llama-4-scout":               {"input": 0.14, "output": 0.27},
    "llama-4-maverick":            {"input": 0.30, "output": 0.50},
    "llama-3.3":                   {"input": 0.20, "output": 0.20},
    # Partner models
    "claude-sonnet-4.6":           {"input": 3.00, "output": 15.00, "thinking": 15.00},
}

_COST_MARGIN = 0.30    # 30 % business margin

_TYPICAL_INPUT_TOKENS = 2000
_TYPICAL_OUTPUT_TOKENS = 1000
_TYPICAL_THINKING_TOKENS = 4000  # thinking-heavy models


def estimate_cost(model: str, prompt_tokens: int,
                  reply_tokens: int,
                  total_tokens: int = 0,
                  thinking_tokens: int = 0) -> float:
    """Return estimated cost in USD including thinking tokens + margin.

    Two patterns of thinking-token accounting:

    • **Gemini** (native SDK): ``reply_tokens`` = candidates only (no
      thinking).  ``total_tokens`` includes everything.
      *thinking = total − prompt − reply*.

    • **MaaS / OpenAI-compat** (DeepSeek-R1, Kimi-K2, …):
      ``reply_tokens`` = completion_tokens which *already includes*
      reasoning.  ``total_tokens = prompt + completion``.  The explicit
      ``thinking_tokens`` from ``completion_tokens_details`` tells us
      how many are reasoning.  We subtract them from reply to avoid
      double-counting.

    If neither derivation applies, all output is priced at the output rate.
    """
    pricing = MODEL_PRICING.get(model, {"input": 1.0, "output": 2.0})
    factor = 1.0 + _COST_MARGIN
    thinking_rate = pricing.get("thinking", pricing["output"])

    # Determine thinking vs text-output split
    derived = max(0, total_tokens - prompt_tokens - reply_tokens)

    if derived > 0:
        # Gemini path: thinking is separate from reply in the API
        thinking = derived
        text_reply = reply_tokens
    elif thinking_tokens > 0:
        # MaaS path: reasoning is inside reply_tokens
        thinking = thinking_tokens
        text_reply = max(0, reply_tokens - thinking_tokens)
    else:
        thinking = 0
        text_reply = reply_tokens

    cost = (
        prompt_tokens * pricing["input"] / 1_000_000
        + text_reply * pricing["output"] / 1_000_000
        + thinking * thinking_rate / 1_000_000
    ) * factor
    return round(cost, 6)


def estimate_request_cost(model: str) -> float:
    """Estimate cost of a typical request for display in the model selector."""
    pricing = MODEL_PRICING.get(model, {})
    thinking = _TYPICAL_THINKING_TOKENS if "thinking" in pricing else 0
    total = _TYPICAL_INPUT_TOKENS + _TYPICAL_OUTPUT_TOKENS + thinking
    return estimate_cost(
        model, _TYPICAL_INPUT_TOKENS, _TYPICAL_OUTPUT_TOKENS, total,
    )


# ── Defaults ─────────────────────────────────────────────────────

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_LOCATION = "us-central1"
DEFAULT_VISION_MODEL = "gemini-2.5-flash"
DEFAULT_VISION_LOCATION = "us-central1"

# ── Model tiers ─────────────────────────────────────────────────
#
# Users pick a tier (Fast / Thinking / Reasoning), not a specific
# model — picker UX flagged the old per-model dropdown as expert-
# unfriendly.  The router picks the best available model in the
# chosen tier (live-probe filtered, scored per-account-role-category
# by the e-greedy router landed in e3a8f93 + 9531c5e), and the
# fallback cascade stays *within* the tier so a Reasoning user
# never silently drops to a Fast model.
#
# Each tier's list is ordered best-to-worst — the router uses this
# order as the cold-start ranking until per-account telemetry
# accumulates and overrides it.

TIER_FAST: str = "fast"
TIER_THINKING: str = "thinking"
TIER_REASONING: str = "reasoning"
TIERS: tuple[str, ...] = (TIER_FAST, TIER_THINKING, TIER_REASONING)

# ``auto`` is a stored-preference value, NOT a real tier — it lives
# alongside ``TIERS`` but isn't in ``TIER_FALLBACK_CHAINS`` because
# auto-mode resolves to one of the three real tiers per-request.
# Kept out of ``TIERS`` so existing code that iterates over real
# tiers (probe filtering, tier-chain walks, fallback ordering) stays
# correct without auto-resolution leaking in.
TIER_AUTO: str = "auto"
TIER_CHOICES: tuple[str, ...] = TIERS + (TIER_AUTO,)

TIER_DISPLAY: dict[str, dict[str, str]] = {
    TIER_FAST: {
        "label": "Fast",
        "icon": "⚡",
        "description": "Quick lookups and day-to-day chat",
    },
    TIER_THINKING: {
        "label": "Thinking",
        "icon": "🧠",
        "description": "Multi-step questions, diagnosis, briefings",
    },
    TIER_REASONING: {
        "label": "Reasoning",
        "icon": "🔬",
        "description": "Deep analysis and root-cause investigation",
    },
    TIER_AUTO: {
        "label": "Auto",
        "icon": "✨",
        "description": "Picks the right tier for each question",
    },
}

# Prompt category → tier mapping for auto-mode.  The classifier in
# capabilities/ai/usage.py (classify_prompt) returns one of six
# buckets; this table picks the tier most likely to do that bucket
# well.  Defaults err toward ``fast`` when the category is unclear
# so the user pays for Thinking only when the question genuinely
# benefits from it.
#
# - lookup        → fast       (just retrieve a fact)
# - analysis      → thinking   ("why is X happening" needs depth)
# - comparison    → thinking   (multi-entity needs structure)
# - summary       → thinking   (briefings benefit from quality)
# - troubleshooting → reasoning (root-cause diagnosis is exactly what
#                     the deep chain-of-thought models are for; they
#                     gained tool access with the openai-compat agent
#                     loop, so this is an upgrade, not a blindfold)
# - other         → fast       (default to cheap when unsure)
#
# Adding a new category to the classifier?  Add a row here too —
# missing entries fall through to ``fast`` per the dict default.
TIER_FOR_CATEGORY: dict[str, str] = {
    "lookup":          TIER_FAST,
    "analysis":        TIER_THINKING,
    "comparison":      TIER_THINKING,
    "summary":         TIER_THINKING,
    "troubleshooting": TIER_REASONING,
    "other":           TIER_FAST,
}

TIER_FALLBACK_CHAINS: dict[str, list[str]] = {
    TIER_FAST: [
        "gemini-2.5-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite-preview",
        "gpt-oss-20b",
        "llama-4-scout",
        "llama-3.3",
    ],
    TIER_THINKING: [
        "gemini-3.1-pro-preview",
        "gemini-2.5-pro",
        "claude-sonnet-4.6",
        "gpt-oss-120b",
        "llama-4-maverick",
        "deepseek-v3.2",
        "deepseek-v3.1",
        "glm-5",
        "glm-4.7",
        "minimax-m2",
        "qwen3-next",
    ],
    TIER_REASONING: [
        "deepseek-r1",
        "qwen3-next-thinking",
        "kimi-k2-thinking",
        "grok-4.20-reasoning",
    ],
}

DEFAULT_TIER: str = TIER_FAST


# ── Query helpers ────────────────────────────────────────────────


def _is_openai_compat(model_name: str) -> bool:
    """Return True for non-Gemini API types (openai_compat, anthropic, mistral_raw)."""
    info = MODEL_REGISTRY.get(model_name, {})
    return info.get("api_type") != "gemini"


# Sampling temperature was hardcoded to 0.3 at every rawPredict/MaaS call site.
# Centralizing it here lets a model override sampling via the registry
# (``"temperature": <float>``) — e.g. a reasoning model that wants different (or
# no) sampling — without editing the generation paths.  Unset → the default,
# so behaviour is unchanged for every model today.
DEFAULT_TEMPERATURE = 0.3


def model_temperature(model_info: dict | None) -> float:
    """Sampling temperature for a model: its registry ``temperature`` or the
    global :data:`DEFAULT_TEMPERATURE`.  A missing or non-numeric value falls
    back to the default."""
    try:
        if model_info and model_info.get("temperature") is not None:
            return float(model_info["temperature"])
    except (TypeError, ValueError):
        pass
    return DEFAULT_TEMPERATURE


def is_vision_capable(model_name: str) -> bool:
    """Check whether a model supports multimodal vision (image input)."""
    info = MODEL_REGISTRY.get(model_name, {})
    return info.get("api_type") == "gemini"


# Model-name prefix → maker display name.  Used by the dashboard model
# picker to group models by who built them (Google, Anthropic, OpenAI…)
# instead of the legacy ``category`` which only distinguished
# gemini-native vs MaaS API shape.  Order matters: more-specific
# prefixes must come before shorter ones that could match.
_MAKER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("gemini-",     "Google"),
    ("claude-",     "Anthropic"),
    ("gpt-oss",     "OpenAI"),
    ("llama-",      "Meta"),
    ("deepseek-",   "DeepSeek"),
    ("qwen3-",      "Alibaba"),
    ("qwen-",       "Alibaba"),
    ("kimi-",       "Moonshot"),
    ("mistral-",    "Mistral"),
    ("codestral-",  "Mistral"),
    ("grok-",       "xAI"),
    ("minimax-",    "MiniMax"),
    ("glm-",        "Zhipu"),
)


def get_model_maker(model_name: str) -> str:
    """Return the upstream maker / provider name for *model_name*.

    Used for UI grouping; falls back to ``"Other"`` for unrecognised
    prefixes so a newly added model still renders somewhere instead of
    disappearing.  Adding a new prefix here is enough — registry rows
    don't need a separate ``maker`` field that could drift.
    """
    for prefix, maker in _MAKER_PREFIXES:
        if model_name.startswith(prefix):
            return maker
    return "Other"


# Reverse lookup: model_name → tier.  Built from TIER_FALLBACK_CHAINS so
# the tier name lives in one place — adding a model to a chain
# automatically registers its tier here.
_MODEL_TIER_INDEX: dict[str, str] = {
    model: tier
    for tier, models in TIER_FALLBACK_CHAINS.items()
    for model in models
}


def get_model_tier(model_name: str) -> str | None:
    """Return the tier ('fast' / 'thinking' / 'reasoning') for a model.

    None when the model isn't in any tier chain — e.g. vision-only
    models (DeepSeek OCR) or models added to MODEL_REGISTRY without
    being slotted into TIER_FALLBACK_CHAINS.
    """
    return _MODEL_TIER_INDEX.get(model_name)


def get_model_tier_label(model_name: str) -> str | None:
    """User-facing tier label ("Fast" / "Thinking" / "Reasoning") for a model.

    This is what customer chat bubbles show as per-answer attribution —
    users pick a tier in the picker, so the answer is attributed in the
    same vocabulary.  The raw model id ("deepseek-r1") is an
    implementation detail that stays server-side: ai_usage rows carry
    it for the operator console and router analytics.

    None when the model isn't slotted into any tier chain.
    """
    tier = _MODEL_TIER_INDEX.get(model_name)
    if tier is None:
        return None
    return TIER_DISPLAY.get(tier, {}).get("label")


def get_tier_chain(tier: str) -> list[str]:
    """Return the ordered fallback list for a tier, or empty if unknown."""
    return list(TIER_FALLBACK_CHAINS.get(tier, []))


def get_model_info(model_name: str) -> dict | None:
    """Get registry info for a model, or None if unknown."""
    return MODEL_REGISTRY.get(model_name)


def get_locations_for_model(model_name: str) -> list[str]:
    """Return available locations for a model, or [DEFAULT_LOCATION] if unknown."""
    info = MODEL_REGISTRY.get(model_name)
    if info and info.get("locations"):
        return info["locations"]
    return [DEFAULT_LOCATION]


def _probe_filtered_registry() -> dict[str, dict]:
    """Return MODEL_REGISTRY entries, filtered by the live probe cache.

    Why filter
    ──────────
    The registry is a *static* catalog of every model the bot knows
    how to talk to.  The *live* set — which the project actually has
    quota for, has enabled, has billing for — is a subset.  Before
    this filter, the model picker showed every entry in the registry,
    so a user could tap (say) Claude Opus 4.7, see "this model is
    available", select it, and then hit a 429 because the project's
    Anthropic quota was never provisioned.

    Behaviour:
      * Probe cache populated → include only models with ≥1 working
        region, and narrow each entry's ``locations`` to the working
        subset (so a region-specific outage doesn't leak through).
      * Probe cache empty (first call, cache TTL just expired) →
        return the full registry as a safe default.  The first call
        also schedules a background probe; subsequent calls within
        the TTL (1h) hit the populated cache and get filtered.
    """
    # Lazy import: probing imports registry, so importing it at module
    # load time would create a cycle.
    from capabilities.ai.probing import probe_model_availability
    available = probe_model_availability(force=False)
    if not available:
        # No cache yet — be permissive while the background probe runs.
        return dict(MODEL_REGISTRY)
    filtered: dict[str, dict] = {}
    for name, info in MODEL_REGISTRY.items():
        working_regions = available.get(name)
        if not working_regions:
            continue
        filtered[name] = {**info, "locations": working_regions}
    return filtered


def get_available_models() -> list[dict]:
    """Return registered models the project actually has access to,
    with display info, sorted by category."""
    result = []
    for name, info in _probe_filtered_registry().items():
        result.append({
            "name": name,
            "display": info["display"],
            "description": info["description"],
            "category": info["category"],
            "locations": info["locations"],
            "max_output_tokens": info["max_output_tokens"],
            "est_cost": estimate_request_cost(name),
        })
    result.sort(key=lambda m: (m["category"], m["name"]))
    return result


def get_vision_models() -> list[dict]:
    """Return only vision-capable models the project has access to."""
    result = []
    for name, info in _probe_filtered_registry().items():
        if info.get("api_type") != "gemini":
            continue
        result.append({
            "name": name,
            "display": info["display"],
            "description": info["description"],
            "category": info["category"],
            "locations": info["locations"],
            "max_output_tokens": info["max_output_tokens"],
            "est_cost": estimate_request_cost(name),
        })
    result.sort(key=lambda m: m["name"])
    return result


def get_text_models() -> list[dict]:
    """Return all models (for text/chat tasks), sorted by category."""
    return get_available_models()


# ── URL Builders ─────────────────────────────────────────────────


def _maas_base_url(location: str, project: str) -> str:
    """Build the OpenAI-compat chat/completions URL."""
    if location == "global":
        host = "aiplatform.googleapis.com"
    else:
        host = f"{location}-aiplatform.googleapis.com"
    return (
        f"https://{host}/v1/"
        f"projects/{project}/locations/{location}/"
        f"endpoints/openapi/chat/completions"
    )


def _anthropic_url(location: str, project: str, model_id: str) -> str:
    """Build the Anthropic rawPredict URL on Vertex AI."""
    if location == "global":
        host = "aiplatform.googleapis.com"
    else:
        host = f"{location}-aiplatform.googleapis.com"
    return (
        f"https://{host}/v1/projects/{project}/locations/{location}/"
        f"publishers/anthropic/models/{model_id}:rawPredict"
    )


def _mistral_url(location: str, project: str,
                 publisher: str, model_id: str) -> str:
    """Build the Mistral rawPredict URL on Vertex AI."""
    host = f"{location}-aiplatform.googleapis.com"
    return (
        f"https://{host}/v1/projects/{project}/locations/{location}/"
        f"publishers/{publisher}/models/{model_id}:rawPredict"
    )


# ── Credentials ──────────────────────────────────────────────────


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


# ── Cloud Monitoring (real Vertex AI usage) ──────────────────────

# Map Cloud Monitoring model IDs → our short model names
_CLOUD_MODEL_MAP: dict[str, str] = {
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-pro-001": "gemini-2.5-pro",
    "gemini-3.1-flash-lite-preview": "gemini-3.1-flash-lite-preview",
    "gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
    "gemini-3.5-flash": "gemini-3.5-flash",
    "deepseek-r1-0528-maas": "deepseek-r1",
    "deepseek-v3.2-maas": "deepseek-v3.2",
    "deepseek-v3.1-maas": "deepseek-v3.1",
    "deepseek-ocr-maas": "deepseek-ocr",
    # Kimi K2 only — Vertex returns ``kimi-k2-thinking-maas`` as the
    # cloud model_user_id for both chat and reasoning calls (they
    # share an endpoint).  Maps to our single ``kimi-k2-thinking``
    # registry entry.
    "kimi-k2-thinking-maas": "kimi-k2-thinking",
    "qwen3-next-80b-a3b-instruct-maas": "qwen3-next",
    "qwen3-next-80b-a3b-thinking-maas": "qwen3-next-thinking",
    "grok-4.20-reasoning": "grok-4.20-reasoning",
    "minimax-m2-maas": "minimax-m2",
    "glm-5-maas": "glm-5",
    "glm-4.7-maas": "glm-4.7",
    "gpt-oss-120b-maas": "gpt-oss-120b",
    "gpt-oss-20b-maas": "gpt-oss-20b",
    "llama-4-scout-17b-16e-instruct-maas": "llama-4-scout",
    "llama-4-maverick-17b-128e-instruct-maas": "llama-4-maverick",
    "llama-3.3-70b-instruct-maas": "llama-3.3",
    "claude-sonnet-4-6": "claude-sonnet-4.6",
}


def _resolve_cloud_model(cloud_id: str) -> str:
    """Map a Cloud Monitoring model_user_id to our registry key."""
    if cloud_id in MODEL_PRICING:
        return cloud_id
    if cloud_id in _CLOUD_MODEL_MAP:
        return _CLOUD_MODEL_MAP[cloud_id]
    # Try suffix matching (e.g. "deepseek-ai/deepseek-r1-0528-maas")
    short = cloud_id.rsplit("/", 1)[-1] if "/" in cloud_id else cloud_id
    return _CLOUD_MODEL_MAP.get(short, cloud_id)


async def get_vertex_ai_cloud_usage(days: int = 90) -> dict | None:
    """Query real Vertex AI usage from Cloud Monitoring.

    Returns dict with by_model breakdown, totals, and cost with margin,
    or None if the API is unavailable.
    """
    import asyncio
    import os
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    try:
        from google.cloud import monitoring_v3
    except ImportError:
        logger.debug("google-cloud-monitoring not installed")
        return None

    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    if not creds_path or not project_id:
        return None

    def _query():
        client = monitoring_v3.MetricServiceClient()
        project_name = f"projects/{project_id}"
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)
        interval = monitoring_v3.TimeInterval({
            "start_time": {"seconds": int(start.timestamp())},
            "end_time": {"seconds": int(now.timestamp())},
        })

        # Token counts by model (input/output)
        token_data = defaultdict(lambda: {"input": 0, "output": 0})
        results = client.list_time_series(request={
            "name": project_name,
            "filter": 'metric.type = "aiplatform.googleapis.com/publisher/online_serving/token_count"',
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
        })
        for ts in results:
            cloud_model = ts.resource.labels.get("model_user_id", "unknown")
            model = _resolve_cloud_model(cloud_model)
            tok_type = ts.metric.labels.get("type", "")
            total_val = sum(
                p.value.int64_value or int(p.value.double_value)
                for p in ts.points
            )
            if tok_type in ("input", "output"):
                token_data[model][tok_type] += total_val

        # Request counts by model
        request_data = defaultdict(lambda: {"ok": 0, "errors": 0})
        results = client.list_time_series(request={
            "name": project_name,
            "filter": 'metric.type = "aiplatform.googleapis.com/publisher/online_serving/model_invocation_count"',
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
        })
        for ts in results:
            cloud_model = ts.resource.labels.get("model_user_id", "unknown")
            model = _resolve_cloud_model(cloud_model)
            status = ts.metric.labels.get("response_code", "200")
            total_val = sum(
                p.value.int64_value or int(p.value.double_value)
                for p in ts.points
            )
            if status == "200":
                request_data[model]["ok"] += total_val
            else:
                request_data[model]["errors"] += total_val

        # Build result
        all_models = set(list(token_data.keys()) + list(request_data.keys()))
        factor = 1.0 + _COST_MARGIN
        by_model = {}
        grand = {"requests": 0, "errors": 0, "input": 0, "output": 0,
                 "base_cost": 0.0, "cost": 0.0}

        for model in sorted(all_models):
            tok = token_data.get(model, {"input": 0, "output": 0})
            req = request_data.get(model, {"ok": 0, "errors": 0})
            pricing = MODEL_PRICING.get(model, {"input": 1.0, "output": 2.0})
            base = (tok["input"] * pricing["input"] / 1_000_000
                    + tok["output"] * pricing["output"] / 1_000_000)
            cost = round(base * factor, 6)

            by_model[model] = {
                "requests": req["ok"],
                "errors": req["errors"],
                "input": tok["input"],
                "output": tok["output"],
                "base_cost": round(base, 6),
                "cost": cost,
            }
            grand["requests"] += req["ok"]
            grand["errors"] += req["errors"]
            grand["input"] += tok["input"]
            grand["output"] += tok["output"]
            grand["base_cost"] += base
            grand["cost"] += cost

        grand["base_cost"] = round(grand["base_cost"], 4)
        grand["cost"] = round(grand["cost"], 4)

        return {"days": days, "totals": grand, "by_model": by_model}

    try:
        return await asyncio.to_thread(_query)
    except Exception as e:
        logger.warning(f"Cloud Monitoring query failed: {e}")
        return None
