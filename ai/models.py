"""Model state management, init, switching, and persistence."""

from __future__ import annotations

import logging
from typing import Any

from ai.registry import (
    MODEL_REGISTRY,
    DEFAULT_MODEL,
    DEFAULT_LOCATION,
    DEFAULT_VISION_MODEL,
    DEFAULT_VISION_LOCATION,
    _is_openai_compat,
    is_vision_capable,
)

logger = logging.getLogger("bot.ai")

# ── Mutable state ────────────────────────────────────────────────

_model = None
_current_model_name: str = ""
_current_location: str = ""
_vertexai_inited: dict[str, bool] = {}

_account_models: dict[int, tuple[str, str, Any]] = {}
_account_vision_models: dict[int, tuple[str, str, Any]] = {}

_db = None


# ── Accessors ────────────────────────────────────────────────────


def set_db(db_instance):
    """Set the database reference for account model persistence."""
    global _db
    _db = db_instance


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
                  location: str | None = None):
    """Lazy-init the Vertex AI Gemini model on first use."""
    global _model, _current_model_name, _current_location

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

    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel, GenerationConfig
    except ImportError:
        raise RuntimeError(
            "google-cloud-aiplatform package not installed. "
            "Run: pip install google-cloud-aiplatform"
        )

    if target_location not in _vertexai_inited:
        vertexai.init(project=project, location=target_location)
        _vertexai_inited[target_location] = True

    max_tokens = 4096
    if info:
        max_tokens = min(info.get("max_output_tokens", 4096), 8192)

    gen_config = GenerationConfig(
        temperature=0.3,
        max_output_tokens=max_tokens,
        top_p=0.8,
    )

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


def _build_model(model_name: str, location: str, info: dict | None = None):
    """Create a GenerativeModel instance (does not touch globals)."""
    import os

    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel, GenerationConfig
    except ImportError:
        raise RuntimeError("google-cloud-aiplatform package not installed.")

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    if location not in _vertexai_inited:
        vertexai.init(project=project, location=location)
        _vertexai_inited[location] = True

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


async def save_account_model(account_id: int, model_name: str,
                             location: str):
    if _db:
        await _db.set_account_setting(account_id, "ai_model", model_name)
        await _db.set_account_setting(account_id, "ai_location", location)


async def load_account_model(account_id: int) -> tuple[str, str]:
    if _db:
        model = await _db.get_account_setting(account_id, "ai_model", "")
        loc = await _db.get_account_setting(account_id, "ai_location", "")
        if model and model in MODEL_REGISTRY:
            loc = loc or DEFAULT_LOCATION
            return model, loc
    return get_current_model_name(), get_current_location()


async def save_account_vision_model(account_id: int, model_name: str,
                                    location: str):
    if _db:
        await _db.set_account_setting(account_id, "ai_vision_model", model_name)
        await _db.set_account_setting(account_id, "ai_vision_location", location)


async def load_account_vision_model(account_id: int) -> tuple[str, str]:
    if _db:
        model = await _db.get_account_setting(account_id, "ai_vision_model", "")
        loc = await _db.get_account_setting(account_id, "ai_vision_location", "")
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
