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


def test_the_vision_fallback_ladder_names_registered_models():
    """vision.py keeps its own fallback list rather than reading the
    chains; it named the retired preview id longest of all."""
    import inspect
    from capabilities.ai import vision
    src = inspect.getsource(vision)
    named = set(re.findall(r'\("(gemini-[a-z0-9.\-]+)",\s*"[a-z0-9\-]+"\)', src))
    assert named, "could not find the _VISION_FALLBACK ladder in vision.py"
    unknown = named - set(reg.MODEL_REGISTRY)
    assert not unknown, f"vision fallback names unregistered models: {sorted(unknown)}"


def test_the_default_model_heads_the_fast_chain():
    """Auto routing sends lookups to FAST; the model the rest of the
    stack is tuned against must be the one it reaches first."""
    assert reg.TIER_FALLBACK_CHAINS[reg.TIER_FAST][0] == reg.DEFAULT_MODEL
