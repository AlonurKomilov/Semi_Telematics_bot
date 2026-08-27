"""Sampling temperature is no longer hardcoded to 0.3 at four rawPredict/MaaS
call sites — it resolves from the model's registry (``temperature``) with a
global default.  Foundation for a reasoning model that wants different sampling;
behaviour-preserving today (no model sets the field, default stays 0.3).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.ai.registry import DEFAULT_TEMPERATURE, model_temperature


def test_default_is_unchanged():
    assert DEFAULT_TEMPERATURE == 0.3
    assert model_temperature({}) == 0.3            # no override → 0.3
    assert model_temperature(None) == 0.3


def test_registry_override_is_honored():
    assert model_temperature({"temperature": 0.0}) == 0.0   # reasoning: deterministic
    assert model_temperature({"temperature": 1.0}) == 1.0
    assert model_temperature({"temperature": "0.7"}) == 0.7  # coerced


def test_non_numeric_falls_back_to_default():
    assert model_temperature({"temperature": "hot"}) == 0.3
    assert model_temperature({"temperature": None}) == 0.3


def test_generation_functions_accept_temperature_kwarg():
    # Signature contract: a future tier can pass a per-model temperature through.
    import inspect
    from capabilities.ai.generation import (
        _generate_openai_compat,
        _generate_anthropic,
        _generate_mistral_raw,
    )
    for fn in (_generate_openai_compat, _generate_anthropic, _generate_mistral_raw):
        params = inspect.signature(fn).parameters
        assert "temperature" in params, fn.__name__
        assert params["temperature"].default == DEFAULT_TEMPERATURE, fn.__name__
