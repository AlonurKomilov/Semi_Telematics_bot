"""The registry, the tier chains, the price list and the cloud-id map
name the same models.

Four tables describe one catalogue, and nothing tied them together:
a model id retired by Google (``gemini-3.1-flash-lite-preview``) sat
in the FAST chain, the vision fallback ladder, the price list and the
cloud map for weeks after it went 404 on every project — the chain
skipped over it on each request, the picker hid it, and no test went
red.  These pin the joins so the next retirement is a failing test,
not a silent hole in a fallback ladder.
"""
from __future__ import annotations

import re

from capabilities.ai import registry as reg


def _chain_ids() -> set[str]:
    return {m for chain in reg.TIER_FALLBACK_CHAINS.values() for m in chain}


def test_every_chain_entry_is_a_registered_model():
    unknown = _chain_ids() - set(reg.MODEL_REGISTRY)
    assert not unknown, (
        f"tier chains name models the registry does not have: {sorted(unknown)} — "
        f"a retired id here is a hole the chain silently steps over")


def test_every_registered_model_has_a_price():
    """Cost analytics fall to a $1/$2 default for a model without a
    row — wrong in either direction, and invisible."""
    unpriced = set(reg.MODEL_REGISTRY) - set(reg.MODEL_PRICING)
    assert not unpriced, f"no MODEL_PRICING row for: {sorted(unpriced)}"


def test_every_price_row_is_for_a_registered_model():
    """The other direction: a price for a model nobody can pick is a
    retired id that was half-removed."""
    orphan = set(reg.MODEL_PRICING) - set(reg.MODEL_REGISTRY)
    assert not orphan, f"MODEL_PRICING rows for unregistered models: {sorted(orphan)}"


def test_cloud_map_targets_are_registered_models():
    bad = {k: v for k, v in reg._CLOUD_MODEL_MAP.items() if v not in reg.MODEL_REGISTRY}
    assert not bad, f"_CLOUD_MODEL_MAP points at unregistered models: {bad}"


def test_non_gemini_models_declare_their_wire_id():
    """The MaaS endpoint is addressed by the publisher's id, Anthropic
    by its own; a registry key alone reaches neither."""
    for name, info in reg.MODEL_REGISTRY.items():
        api = info.get("api_type", "gemini")
        if api == "openai_compat":
            assert info.get("maas_model_id"), f"{name}: openai_compat without maas_model_id"
        elif api == "anthropic":
            assert info.get("anthropic_model_id"), f"{name}: anthropic without anthropic_model_id"


def test_the_vision_ladder_names_registered_models_in_regions_they_serve():
    """vision.py used to keep two private ladders; one named a retired id
    for weeks.  The ladder is registry data now, and each rung must be a
    model we have, at a region that model actually lists."""
    for name, loc in reg.VISION_FALLBACK_CHAIN:
        assert name in reg.MODEL_REGISTRY, f"vision ladder names unregistered {name}"
        assert loc in reg.MODEL_REGISTRY[name]["locations"], (
            f"{name} is asked at {loc}, which its registry entry does not list")


def test_the_default_vision_model_heads_the_ladder():
    assert reg.VISION_FALLBACK_CHAIN[0] == (reg.DEFAULT_VISION_MODEL, reg.DEFAULT_VISION_LOCATION)


def test_vision_reads_its_ladder_from_the_registry_not_a_private_copy():
    """A second list is how the retirement slipped through."""
    import inspect
    from capabilities.ai import vision
    src = inspect.getsource(vision)
    assert "_VISION_FALLBACK = [" not in src
    assert "VISION_FALLBACK_CHAIN" in src


def test_a_vision_attempt_does_not_switch_the_chat_model(monkeypatch):
    """Both ladders used to call _ensure_model, which REPLACES the
    process-wide current model whenever the name or region differs — a
    camera frame that fell back to 2.5 Pro left every chat on the worker
    answering from 2.5 Pro.  Vision builds and keeps its own callers."""
    from capabilities.ai import models, vision

    built = []
    monkeypatch.setattr(models, "_build_model",
                        lambda name, loc, info=None: built.append((name, loc)) or object())
    monkeypatch.setattr(vision, "_vision_callers", {})
    before = (models._current_model_name, models._model)
    vision._vision_caller("gemini-3.1-pro-preview", "global")
    vision._vision_caller("gemini-3.1-pro-preview", "global")      # cached, not rebuilt
    assert built == [("gemini-3.1-pro-preview", "global")]
    assert (models._current_model_name, models._model) == before


def test_vision_attempts_put_the_account_pin_first_and_never_repeat_it():
    from capabilities.ai import models, vision
    models._account_vision_models[999_001] = ("gemini-2.5-flash", "us-central1", None)
    try:
        attempts = vision._vision_attempts(999_001)
    finally:
        models._account_vision_models.pop(999_001, None)
    assert attempts[0] == ("gemini-2.5-flash", "us-central1")
    assert [n for n, _ in attempts].count("gemini-2.5-flash") == 1
    assert vision._vision_attempts(None)[0] == (reg.DEFAULT_VISION_MODEL, reg.DEFAULT_VISION_LOCATION)


def test_the_default_model_heads_the_fast_chain():
    """Auto routing sends lookups to FAST; the model the rest of the
    stack is tuned against must be the one it reaches first."""
    assert reg.TIER_FALLBACK_CHAINS[reg.TIER_FAST][0] == reg.DEFAULT_MODEL
