"""``resolve_tier_for_request`` must stage a model from the user's stored
tier IN THE REQUEST PATH — for explicit tiers, not just auto.

The production bug this pins: the tier choice persists in the DB, but the
model pick lives in the per-process ``_user_models`` cache.  ``switch_tier``
(picker click) and ``ensure_user_tier`` (GET /ai/tier) warm only the worker
that served them — under multi-worker gunicorn the next POST /chat/stream
usually lands on a DIFFERENT worker whose cache is cold, and
``get_model_for_user`` silently fell back to the account default.  Net
effect: the picker said "Reasoning" while a Fast model answered.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

import capabilities.ai.models as m
from capabilities.ai.registry import get_model_tier


@pytest.fixture(autouse=True)
def _clean_caches(monkeypatch):
    """Isolate each test from module-level cache state (and vice versa)."""
    monkeypatch.setattr(m, "_user_tiers", {})
    monkeypatch.setattr(m, "_account_tiers", {})
    monkeypatch.setattr(m, "_user_models", {})


def _no_stored_tier(monkeypatch):
    async def _none(*a, **kw):
        return None
    monkeypatch.setattr(m, "load_user_tier", _none)
    monkeypatch.setattr(m, "load_account_tier", _none)


@pytest.mark.asyncio
async def test_cold_start_explicit_tier_stages_a_tier_model(monkeypatch):
    """Fresh worker (empty caches), user's DB row says "reasoning" →
    the request path itself must stage a Reasoning-tier model.
    """
    async def _stored(*a, **kw):
        return "reasoning"
    monkeypatch.setattr(m, "load_user_tier", _stored)

    tier = await m.resolve_tier_for_request("any question", account_id=1, user_id=7)

    assert tier == "reasoning"
    assert 7 in m._user_models
    staged_model = m._user_models[7][0]
    assert get_model_tier(staged_model) == "reasoning"


@pytest.mark.asyncio
async def test_wrong_tier_cached_model_is_restaged(monkeypatch):
    """Cache holds a Fast pick (e.g. from before the user switched tiers on
    another worker) while the stored tier is "reasoning" → re-stage.
    """
    async def _stored(*a, **kw):
        return "reasoning"
    monkeypatch.setattr(m, "load_user_tier", _stored)
    m._user_models[7] = ("gemini-2.5-flash", "us-central1", None)

    await m.resolve_tier_for_request("any question", account_id=1, user_id=7)

    assert get_model_tier(m._user_models[7][0]) == "reasoning"


@pytest.mark.asyncio
async def test_matching_cached_model_is_not_restaged(monkeypatch):
    """Cached pick already in the right tier → leave it alone (the scorer's
    per-account reorder within the tier must not be clobbered every turn).
    """
    async def _stored(*a, **kw):
        return "reasoning"
    monkeypatch.setattr(m, "load_user_tier", _stored)
    sentinel = ("kimi-k2-thinking", "us-central1", None)  # reasoning-tier, non-head
    m._user_models[7] = sentinel

    await m.resolve_tier_for_request("any question", account_id=1, user_id=7)

    assert m._user_models[7] is sentinel


@pytest.mark.asyncio
async def test_stale_worker_tier_cache_is_overridden_by_db(monkeypatch):
    """The picker's PUT warms only one worker; every other worker's
    ``_user_tiers`` entry is stale until restart.  The DB must win: a
    user who switched Thinking → Reasoning gets Reasoning on EVERY
    worker's next request, staged model included.  (Production bug:
    picker said Reasoning, a stale worker staged gemini-3.1-pro and the
    bubble said "Thinking".)
    """
    async def _stored(*a, **kw):
        return "reasoning"                       # what the DB says now
    monkeypatch.setattr(m, "load_user_tier", _stored)
    m._user_tiers[7] = "thinking"                # stale worker-local cache
    m._user_models[7] = ("gemini-3.1-pro-preview", "global", None)

    tier = await m.resolve_tier_for_request("q", account_id=1, user_id=7)

    assert tier == "reasoning"
    assert m._user_tiers[7] == "reasoning"       # cache refreshed
    assert get_model_tier(m._user_models[7][0]) == "reasoning"


@pytest.mark.asyncio
async def test_db_failure_degrades_to_cached_tier(monkeypatch):
    async def _boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(m, "load_user_tier", _boom)
    monkeypatch.setattr(m, "load_account_tier", _boom)
    m._user_tiers[7] = "thinking"

    tier = await m.resolve_tier_for_request("q", account_id=1, user_id=7)

    assert tier == "thinking"                    # cache is the fallback


@pytest.mark.asyncio
async def test_no_stored_pref_never_stages(monkeypatch):
    """No tier row anywhere → DEFAULT_TIER by fallthrough, and the user-model
    cache stays untouched so a legacy account-default model keeps serving.
    """
    _no_stored_tier(monkeypatch)

    tier = await m.resolve_tier_for_request("any question", account_id=1, user_id=7)

    assert tier == m.DEFAULT_TIER
    assert 7 not in m._user_models


@pytest.mark.asyncio
async def test_auto_pref_still_stages_from_classified_tier(monkeypatch):
    """Stored "auto" keeps working: the prompt-classified tier gets staged.

    The classifier is pinned to "reasoning" because that chain's head is an
    openai-compat model — stageable without the Vertex credentials this test
    environment doesn't have (gemini heads raise inside ``_build_model`` and
    the staging try/except would swallow it, failing the assert spuriously).
    """
    async def _stored(*a, **kw):
        return "auto"
    monkeypatch.setattr(m, "load_user_tier", _stored)
    monkeypatch.setattr(m, "auto_resolve_tier", lambda prompt: "reasoning")

    tier = await m.resolve_tier_for_request(
        "why is truck 12 burning more fuel than the rest of the fleet?",
        account_id=1, user_id=7,
    )

    assert tier == "reasoning"
    assert 7 in m._user_models
    assert get_model_tier(m._user_models[7][0]) == "reasoning"
