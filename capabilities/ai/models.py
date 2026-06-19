"""Model state management, init, switching, and persistence.

Migrated 2026-06-03 from the deprecated ``vertexai.generative_models``
SDK to ``google-genai`` (1.67.0).  ``vertexai.generative_models``
reaches end-of-life on 2026-06-24 — see
https://cloud.google.com/vertex-ai/generative-ai/docs/deprecations/genai-vertexai-sdk

The legacy ``vertexai.init(project=…, location=…)`` mutated process-wide
singletons, so the first caller's region "won" and every subsequent
call to a different region silently used the wrong endpoint.  The new
SDK exposes a per-client ``genai.Client(vertexai=True, project=…,
location=…)`` — so each ``_GeminiCaller`` below owns its own client
and the global-init race is gone.
"""

from __future__ import annotations

import logging
from typing import Any

from capabilities.ai.registry import (
    MODEL_REGISTRY,
    DEFAULT_MODEL,
    DEFAULT_LOCATION,
    DEFAULT_VISION_MODEL,
    DEFAULT_VISION_LOCATION,
    DEFAULT_TIER,
    TIER_AUTO,
    TIER_CHOICES,
    TIER_FOR_CATEGORY,
    _is_openai_compat,
    is_vision_capable,
    get_tier_chain,
)

logger = logging.getLogger("bot.ai")


# ── google-genai wrapper ─────────────────────────────────────────


class _GeminiCaller:
    """Thin wrapper around ``google.genai.Client`` that preserves the
    legacy ``model.generate_content(contents, tools=…)`` surface the
    rest of the codebase calls.

    Holds (client, model_id, base_config) per-instance — no global
    state — so two concurrent calls to two different regions can't
    clobber each other the way ``vertexai.init()`` allowed.
    """

    __slots__ = ("_client", "_model_id", "_base_config")

    def __init__(self, client, model_id: str, base_config):
        self._client = client
        self._model_id = model_id
        self._base_config = base_config

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate_content(self, contents, tools=None):
        """Sync call into google-genai; safe to wrap with asyncio.to_thread."""
        if tools is None:
            config = self._base_config
        else:
            from google.genai import types as _gtypes
            cfg_dict = self._base_config.model_dump(exclude_unset=True)
            cfg_dict["tools"] = tools
            config = _gtypes.GenerateContentConfig(**cfg_dict)
        return self._client.models.generate_content(
            model=self._model_id,
            contents=contents,
            config=config,
        )

    def generate_content_stream(self, contents, tools=None):
        """Streaming variant of :meth:`generate_content`.

        Returns google-genai's sync streaming iterator (chunks).  Forces
        ``include_thoughts=True`` so reasoning summaries surface as ``thought``
        parts in the stream — this only produces output when the model is
        actually thinking (``thinking_budget != 0``), so it's a no-op for the
        Flash variants that run with budget 0.  Preserves any existing
        thinking budget / level.  Sync generator — wrap the consuming loop with
        ``asyncio.to_thread``.
        """
        from google.genai import types as _gtypes
        cfg_dict = self._base_config.model_dump(exclude_unset=True)
        if tools is not None:
            cfg_dict["tools"] = tools
        tc = cfg_dict.get("thinking_config")
        tc = {**tc, "include_thoughts": True} if isinstance(tc, dict) else {"include_thoughts": True}
        cfg_dict["thinking_config"] = _gtypes.ThinkingConfig(**tc)
        config = _gtypes.GenerateContentConfig(**cfg_dict)
        return self._client.models.generate_content_stream(
            model=self._model_id,
            contents=contents,
            config=config,
        )


def _build_gen_config(info: dict):
    """Build a ``GenerateContentConfig`` from a registry entry."""
    from google.genai import types as _gtypes
    max_tokens = min(info.get("max_output_tokens", 4096), 8192)
    kwargs: dict[str, Any] = {
        "temperature": 0.3,
        "max_output_tokens": max_tokens,
        "top_p": 0.8,
    }
    # Per-model thinking budget — replaces the old hardcoded
    # ``startswith("gemini-2.5-flash")`` check.  Flash variants set
    # budget=0 because thinking adds 30-45s per call (60-90s over two
    # agentic rounds) and blows the nginx/client timeouts.
    # ``gemini-2.5-pro`` requires thinking and rejects budget=0, so
    # leave it unset there.
    tb = info.get("thinking_budget")
    if tb is not None:
        kwargs["thinking_config"] = _gtypes.ThinkingConfig(thinking_budget=tb)
    return _gtypes.GenerateContentConfig(**kwargs)


# ── Mutable state ────────────────────────────────────────────────

_model: _GeminiCaller | None = None
_current_model_name: str = ""
_current_location: str = ""

_account_models: dict[int, tuple[str, str, Any]] = {}
_account_vision_models: dict[int, tuple[str, str, Any]] = {}
_user_models: dict[int, tuple[str, str, Any]] = {}  # per-user text model

_db = None


# ── Accessors ────────────────────────────────────────────────────


def set_db(db_instance):
    """Deprecated — kept for backward compat only.

    AI model persistence now looks up the per-tenant DB via
    ``get_tenant_db(account_id)`` so settings are always stored in the
    correct tenant DB regardless of single- vs. multi-tenant deployment.
    """


def get_account_model_name(account_id: int) -> str | None:
    entry = _account_models.get(account_id)
    return entry[0] if entry else None


def get_account_model_info(account_id: int) -> tuple[str, str, Any] | None:
    return _account_models.get(account_id)


def get_account_vision_model_name(account_id: int) -> str | None:
    entry = _account_vision_models.get(account_id)
    return entry[0] if entry else None


def get_account_vision_model_info(account_id: int) -> tuple[str, str, Any] | None:
    return _account_vision_models.get(account_id)


# ── Per-user model preference ─────────────────────────────────────

def get_user_model_name(user_id: int) -> str | None:
    entry = _user_models.get(user_id)
    return entry[0] if entry else None


def get_user_model_info(user_id: int) -> tuple[str, str, Any] | None:
    return _user_models.get(user_id)


def get_current_model_name() -> str:
    return _current_model_name or DEFAULT_MODEL


def get_current_location() -> str:
    return _current_location or DEFAULT_LOCATION


def get_locations_for_model(model_name: str) -> list[str]:
    info = MODEL_REGISTRY.get(model_name)
    if info and info.get("locations"):
        return info["locations"]
    return [DEFAULT_LOCATION]


def is_configured() -> bool:
    """Check if Vertex AI env vars are set (without initializing)."""
    import os
    return bool(
        os.getenv("GOOGLE_CLOUD_PROJECT", "")
        and os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    )


# ── Model lifecycle ──────────────────────────────────────────────


def _ensure_model(model_name: str | None = None,
                  location: str | None = None) -> _GeminiCaller:
    """Lazy-build the Gemini caller on first use."""
    global _model, _current_model_name, _current_location

    if _model is not None and model_name is None and location is None:
        return _model

    import os

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not project:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT is not set. "
            "Set it to your GCP project ID (e.g. '4truck')."
        )
    if not creds_path:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS is not set. "
            "Point it to your service-account JSON file."
        )

    target_model = model_name or os.getenv("VERTEX_AI_MODEL", DEFAULT_MODEL)
    target_location = location or os.getenv("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION)

    info = MODEL_REGISTRY.get(target_model)
    if info and target_location not in info["locations"]:
        target_location = info["locations"][0]
        logger.warning(
            f"Location not available for {target_model}, "
            f"using {target_location}"
        )

    if (_model is not None
            and _current_model_name == target_model
            and _current_location == target_location):
        return _model

    _model = _build_model(target_model, target_location, info)
    _current_model_name = target_model
    _current_location = target_location
    logger.info(
        f"google-genai initialized: project={project}, "
        f"location={target_location}, model={target_model}"
    )
    return _model


def _build_model(model_name: str, location: str,
                 info: dict | None = None) -> _GeminiCaller:
    """Create a ``_GeminiCaller`` (its own client; no global mutation)."""
    import os

    try:
        from google import genai
    except ImportError:
        raise RuntimeError(
            "google-genai package not installed.  Run: pip install google-genai"
        )

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    if info is None:
        info = MODEL_REGISTRY.get(model_name, {})

    client = genai.Client(vertexai=True, project=project, location=location)
    config = _build_gen_config(info)
    return _GeminiCaller(client, model_name, config)


# ── Switching ────────────────────────────────────────────────────


def switch_model(model_name: str, location: str | None = None,
                 account_id: int | None = None):
    """Switch to a different model (and optionally location)."""
    if model_name not in MODEL_REGISTRY:
        raise ValueError(
            f"Unknown model: {model_name}. "
            f"Use the model selector to pick a valid model."
        )
    info = MODEL_REGISTRY[model_name]
    target_loc = location or info["locations"][0]
    if target_loc not in info["locations"]:
        raise ValueError(
            f"Location {target_loc} not available for {model_name}. "
            f"Available: {', '.join(info['locations'])}"
        )

    if account_id is not None:
        if _is_openai_compat(model_name):
            _account_models[account_id] = (model_name, target_loc, None)
        else:
            model_obj = _build_model(model_name, target_loc, info)
            _account_models[account_id] = (model_name, target_loc, model_obj)
    else:
        global _model
        _model = None
        if not _is_openai_compat(model_name):
            _ensure_model(model_name=model_name, location=target_loc)
        global _current_model_name, _current_location
        _current_model_name = model_name
        _current_location = target_loc


def switch_vision_model(model_name: str, location: str | None = None,
                        account_id: int | None = None):
    """Switch the vision model for an account."""
    if model_name not in MODEL_REGISTRY:
        raise ValueError(f"Unknown model: {model_name}")
    if not is_vision_capable(model_name):
        raise ValueError(
            f"{model_name} does not support vision. "
            f"Only Gemini models can be used for vision tasks."
        )
    info = MODEL_REGISTRY[model_name]
    target_loc = location or info["locations"][0]
    if target_loc not in info["locations"]:
        raise ValueError(
            f"Location {target_loc} not available for {model_name}."
        )
    if account_id is not None:
        model_obj = _build_model(model_name, target_loc, info)
        _account_vision_models[account_id] = (model_name, target_loc, model_obj)


# ── DB persistence ───────────────────────────────────────────────
# All settings are stored in the *per-tenant* DB, not the platform DB,
# so they are correctly isolated per account in multi-tenant deployments.


async def _get_tenant(account_id: int):
    """Return the tenant DB for *account_id*, or None on failure."""
    try:
        from infra.services import get_tenant_db
        return await get_tenant_db(account_id)
    except Exception:
        return None


async def save_account_model(account_id: int, model_name: str,
                             location: str):
    tenant = await _get_tenant(account_id)
    if tenant:
        await tenant.set_account_setting(account_id, "ai_model", model_name)
        await tenant.set_account_setting(account_id, "ai_location", location)


async def load_account_model(account_id: int) -> tuple[str, str]:
    tenant = await _get_tenant(account_id)
    if tenant:
        model = await tenant.get_account_setting(account_id, "ai_model", "")
        loc = await tenant.get_account_setting(account_id, "ai_location", "")
        if model and model in MODEL_REGISTRY:
            loc = loc or DEFAULT_LOCATION
            return model, loc
    return get_current_model_name(), get_current_location()


async def save_account_vision_model(account_id: int, model_name: str,
                                    location: str):
    tenant = await _get_tenant(account_id)
    if tenant:
        await tenant.set_account_setting(account_id, "ai_vision_model", model_name)
        await tenant.set_account_setting(account_id, "ai_vision_location", location)


# ── Per-user model persistence ────────────────────────────────────

async def save_user_model(account_id: int, user_id: int,
                          model_name: str, location: str):
    """Persist user's model preference via account_settings (keyed by user_id)."""
    tenant = await _get_tenant(account_id)
    if tenant:
        await tenant.set_account_setting(
            account_id, f"user:{user_id}:ai_model", model_name)
        await tenant.set_account_setting(
            account_id, f"user:{user_id}:ai_location", location)


async def load_user_model(account_id: int, user_id: int) -> tuple[str, str] | None:
    """Load user's model preference. Returns None if not set."""
    tenant = await _get_tenant(account_id)
    if tenant:
        model = await tenant.get_account_setting(
            account_id, f"user:{user_id}:ai_model", "")
        loc = await tenant.get_account_setting(
            account_id, f"user:{user_id}:ai_location", "")
        if model and model in MODEL_REGISTRY:
            return model, loc or DEFAULT_LOCATION
    return None


def switch_user_model(user_id: int, model_name: str,
                      location: str | None = None):
    """Switch model for a specific user (in-memory cache)."""
    if model_name not in MODEL_REGISTRY:
        raise ValueError(f"Unknown model: {model_name}")
    info = MODEL_REGISTRY[model_name]
    target_loc = location or info["locations"][0]
    if _is_openai_compat(model_name):
        _user_models[user_id] = (model_name, target_loc, None)
    else:
        model_obj = _build_model(model_name, target_loc, info)
        _user_models[user_id] = (model_name, target_loc, model_obj)


async def ensure_user_model(account_id: int, user_id: int):
    """Load user's model preference from DB and cache it."""
    if user_id in _user_models:
        return
    result = await load_user_model(account_id, user_id)
    if result:
        model_name, location = result
        try:
            switch_user_model(user_id, model_name, location)
        except Exception as e:
            logger.warning(f"Failed to build user model for user {user_id}: {e}")


# ── Tier persistence + resolution ────────────────────────────────
#
# Users pick a tier (Fast / Thinking / Reasoning) in the dashboard
# instead of an individual model.  Resolution flow:
#
#   1. Read the user's stored tier (per-account_settings, keyed by
#      user_id).  None → fall through to account default.
#   2. Read the account default tier.  None → DEFAULT_TIER ("fast").
#   3. Walk the tier's fallback chain.  First model the live probe
#      reports as available wins.  No probe data yet (cold start) →
#      first in the static chain.
#
# The router we already shipped (per (account, role, prompt_category)
# scoring with ε-greedy) operates *within* the tier's candidate
# pool — so the tier is the user-facing knob and the router is the
# system-side smartening on top of it.

_user_tiers: dict[int, str] = {}        # user_id → tier
_account_tiers: dict[int, str] = {}     # account_id → tier


async def save_user_tier(account_id: int, user_id: int, tier: str):
    """Persist user's tier preference (per-account-settings).

    Accepts ``"auto"`` in addition to the three real tiers — auto
    isn't a real tier (it resolves to one at request time), but it's
    a valid stored preference value.
    """
    if tier not in TIER_CHOICES:
        raise ValueError(f"Unknown tier: {tier}")
    tenant = await _get_tenant(account_id)
    if tenant:
        await tenant.set_account_setting(
            account_id, f"user:{user_id}:ai_tier", tier,
        )
    _user_tiers[user_id] = tier


async def load_user_tier(account_id: int, user_id: int) -> str | None:
    """Return the user's stored tier (including ``"auto"``), or None."""
    tenant = await _get_tenant(account_id)
    if tenant:
        tier = await tenant.get_account_setting(
            account_id, f"user:{user_id}:ai_tier", "",
        )
        if tier in TIER_CHOICES:
            return tier
    return None


async def save_account_tier(account_id: int, tier: str):
    """Persist the account-default tier (used when no user pref set)."""
    if tier not in TIER_CHOICES:
        raise ValueError(f"Unknown tier: {tier}")
    tenant = await _get_tenant(account_id)
    if tenant:
        await tenant.set_account_setting(account_id, "ai_tier", tier)
    _account_tiers[account_id] = tier


async def load_account_tier(account_id: int) -> str | None:
    """Return the account's default tier (including ``"auto"``), or None."""
    tenant = await _get_tenant(account_id)
    if tenant:
        tier = await tenant.get_account_setting(account_id, "ai_tier", "")
        if tier in TIER_CHOICES:
            return tier
    return None


async def resolve_tier(account_id: int | None,
                       user_id: int | None) -> str:
    """User pref → account default → DEFAULT_TIER (Fast).

    Reads in-memory cache first; falls back to DB for cold start.
    """
    if user_id is not None and user_id in _user_tiers:
        return _user_tiers[user_id]
    if account_id is not None and account_id in _account_tiers:
        return _account_tiers[account_id]
    if user_id is not None and account_id is not None:
        t = await load_user_tier(account_id, user_id)
        if t:
            _user_tiers[user_id] = t
            return t
    if account_id is not None:
        t = await load_account_tier(account_id)
        if t:
            _account_tiers[account_id] = t
            return t
    return DEFAULT_TIER


async def pick_model_for_tier(tier: str) -> str:
    """Pick the first probe-available model in a tier.

    Falls back to the static first entry when the live probe hasn't
    run yet (cold start) — the probe's result is the more accurate
    signal but isn't always present.
    """
    chain = get_tier_chain(tier)
    if not chain:
        return DEFAULT_MODEL
    try:
        from capabilities.ai.probing import probe_model_availability
        availability = probe_model_availability()
        for m in chain:
            if availability.get(m):
                return m
    except Exception as e:
        logger.debug("Probe lookup failed; using static head: %s", e)
    return chain[0]


async def ensure_user_tier(account_id: int, user_id: int):
    """Warm the user's tier into the in-memory cache + resolve a model.

    Called from the API ``ensure_account_model`` shim so the chat
    endpoint sees the user's choice without an extra round-trip.
    No-op model-warm for ``"auto"`` — the model gets picked per
    request from the prompt-derived tier in ``resolve_tier_for_request``.
    """
    tier = await resolve_tier(account_id, user_id)
    _user_tiers.setdefault(user_id, tier)
    if tier == TIER_AUTO:
        # Don't pre-pick a model — the per-request resolver does that.
        return
    # Resolve the tier's current best model and switch the per-user
    # cache so generate() picks it up.  Cheap — no network call when
    # the model is already in the per-user cache.
    model_name = await pick_model_for_tier(tier)
    if model_name in MODEL_REGISTRY:
        info = MODEL_REGISTRY[model_name]
        loc = info["locations"][0]
        try:
            switch_user_model(user_id, model_name, loc)
        except Exception as e:
            logger.debug("Tier-resolved switch_user_model failed: %s", e)


def auto_resolve_tier(prompt: str) -> str:
    """Map a user prompt to one of the three real tiers.

    Uses the existing prompt classifier (lookup / analysis / comparison
    / summary / troubleshooting / other) and the TIER_FOR_CATEGORY
    table from the registry.  Pure function — no DB, no LLM call.
    """
    from capabilities.ai.usage import classify_prompt
    category = classify_prompt(prompt)
    return TIER_FOR_CATEGORY.get(category, DEFAULT_TIER)


async def resolve_tier_for_request(
    prompt: str,
    account_id: int | None,
    user_id: int | None,
) -> str:
    """Return the actual tier to use for a request.

    Reads the user's stored choice; when that's ``"auto"``, classifies
    the prompt and returns the auto-mapped real tier instead.  The
    stored choice is never returned as ``"auto"`` — callers always
    get one of the three real tiers ready to feed into
    ``pick_model_for_tier``.

    Side-effect: when the resolved tier differs from what's currently
    cached in ``_user_models`` for this user, hot-swap the cached
    model so ``generate()`` / ``ask_agent()`` see the right pick on
    the next call without an extra round-trip.
    """
    stored = await resolve_tier(account_id, user_id)
    real_tier = auto_resolve_tier(prompt) if stored == TIER_AUTO else stored

    # When auto-mode is on, the in-memory user-model cache may point
    # at a model from a previous prompt's resolved tier.  Re-pick the
    # model from the newly-resolved tier and stage it before the call.
    if stored == TIER_AUTO and user_id is not None:
        model_name = await pick_model_for_tier(real_tier)
        if model_name in MODEL_REGISTRY:
            info = MODEL_REGISTRY[model_name]
            loc = info["locations"][0]
            try:
                switch_user_model(user_id, model_name, loc)
            except Exception as e:
                logger.debug(
                    "Auto-tier switch_user_model failed (tier=%s, model=%s): %s",
                    real_tier, model_name, e,
                )
    return real_tier


async def switch_tier(tier: str, account_id: int,
                      user_id: int | None = None):
    """Switch the tier for a user (or the account default).

    Persists the choice + warms the in-memory model cache.  The
    actual model picked is the first probe-available entry from
    the tier's fallback chain; the router then reorders by score
    inside that chain on each call.

    Accepts ``"auto"`` — persisted as the user choice, but no model
    is pre-cached because the auto-resolution runs per-request.  The
    return value in that case is the empty string, signalling to the
    caller that the resolved model will be determined on first
    request after the switch.
    """
    if tier not in TIER_CHOICES:
        raise ValueError(f"Unknown tier: {tier}")
    if user_id is not None:
        await save_user_tier(account_id, user_id, tier)
    else:
        await save_account_tier(account_id, tier)
    if tier == TIER_AUTO:
        # Auto-mode: don't pre-pick a model.  The next request will
        # classify the prompt and pick from the right tier on the fly.
        # Drop any stale cached pick from a previous explicit-tier
        # choice so we don't accidentally serve from the wrong tier
        # before the next request.
        if user_id is not None:
            _user_models.pop(user_id, None)
        return ""
    # Warm the model cache so the next generate() call uses it.
    model_name = await pick_model_for_tier(tier)
    info = MODEL_REGISTRY.get(model_name)
    if not info:
        return model_name
    loc = info["locations"][0]
    if user_id is not None:
        switch_user_model(user_id, model_name, loc)
    else:
        if _is_openai_compat(model_name):
            _account_models[account_id] = (model_name, loc, None)
        else:
            model_obj = _build_model(model_name, loc, info)
            _account_models[account_id] = (model_name, loc, model_obj)
    return model_name


def get_user_tier(user_id: int) -> str | None:
    """Return the cached tier for this user, or None."""
    return _user_tiers.get(user_id)


def get_account_tier(account_id: int) -> str | None:
    """Return the cached account-default tier, or None."""
    return _account_tiers.get(account_id)


async def load_account_vision_model(account_id: int) -> tuple[str, str]:
    tenant = await _get_tenant(account_id)
    if tenant:
        model = await tenant.get_account_setting(account_id, "ai_vision_model", "")
        loc = await tenant.get_account_setting(account_id, "ai_vision_location", "")
        if model and model in MODEL_REGISTRY and is_vision_capable(model):
            loc = loc or DEFAULT_VISION_LOCATION
            return model, loc
    return DEFAULT_VISION_MODEL, DEFAULT_VISION_LOCATION


async def ensure_account_vision_model(account_id: int):
    """Load account's vision model preference from DB and cache it."""
    if account_id in _account_vision_models:
        return
    model_name, location = await load_account_vision_model(account_id)
    info = MODEL_REGISTRY.get(model_name)
    if info:
        try:
            model_obj = _build_model(model_name, location, info)
            _account_vision_models[account_id] = (model_name, location, model_obj)
        except Exception as e:
            logger.warning(f"Failed to build vision model for account {account_id}: {e}")


def get_model_for_account(account_id: int | None = None):
    """Get (model_obj, model_name, location) for an account (or global)."""
    if account_id is not None and account_id in _account_models:
        name, loc, obj = _account_models[account_id]
        return obj, name, loc
    return _ensure_model(), get_current_model_name(), get_current_location()


def get_model_for_user(user_id: int | None = None,
                       account_id: int | None = None):
    """Get (model_obj, model_name, location) — user pref → account → global."""
    if user_id is not None and user_id in _user_models:
        name, loc, obj = _user_models[user_id]
        return obj, name, loc
    return get_model_for_account(account_id)


async def ensure_account_model(account_id: int):
    """Load account's model preference from DB and cache the model."""
    if account_id not in _account_models:
        model_name, location = await load_account_model(account_id)
        if model_name != get_current_model_name() or location != get_current_location():
            info = MODEL_REGISTRY.get(model_name)
            if info:
                try:
                    if _is_openai_compat(model_name):
                        _account_models[account_id] = (model_name, location, None)
                    else:
                        model_obj = _build_model(model_name, location, info)
                        _account_models[account_id] = (model_name, location, model_obj)
                except Exception as e:
                    logger.warning(f"Failed to build model for account {account_id}: {e}")
    await ensure_account_vision_model(account_id)
