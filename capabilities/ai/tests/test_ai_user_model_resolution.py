"""The chat-only path must answer with the USER's staged model.

Pins the production bug where a Reasoning-tier user's question was
answered by the account's Flash default: ``resolve_tier_for_request``
stages the tier's model in ``_user_models``, the agent's non-Gemini
branch degrades to ``ask_ai`` → ``_generate_impl`` — which resolved the
model as account → global only, skipping the user entry entirely.  The
bubble label (read from the staged model) said "Reasoning" while Flash
wrote the answer, with no reasoning to show.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

import capabilities.ai.generation as g
import capabilities.ai.models as m


@pytest.mark.asyncio
async def test_ask_ai_uses_the_user_staged_model(monkeypatch):
    called: dict = {}

    async def _fake_openai_compat(model_id, location, system, prompt,
                                  max_tokens=4096, temperature=0.3,
                                  extra_body=None):
        called["model_id"] = model_id
        called["extra_body"] = extra_body
        return "stub answer", {"prompt_tokens": 1, "reply_tokens": 1,
                               "thinking_tokens": 0, "total_tokens": 2}

    monkeypatch.setattr(g, "_generate_openai_compat", _fake_openai_compat)
    monkeypatch.setattr(m, "_user_models", {})
    monkeypatch.setattr(m, "_account_models", {}, raising=False)
    monkeypatch.setattr(g, "_account_models", m._account_models, raising=False)
    monkeypatch.setattr(g, "_chat_histories", {})

    # Stage the Reasoning pick for this user — openai-compat, no build.
    m.switch_user_model(555001, "deepseek-r1", "us-central1")

    text, usage = await g.generate(
        "any overdue tasks?", context_data={"x": 1},
        user_id=555001, account_id=424242, action="question",
    )

    assert text == "stub answer"
    # THE assertion: the user's staged model answered — not the global
    # default (gemini flash), which wouldn't hit the openai-compat stub.
    assert called["model_id"] == "deepseek-r1-0528-maas" or "deepseek" in called["model_id"]


@pytest.mark.asyncio
async def test_extra_body_reaches_the_request(monkeypatch):
    """Registry-declared extra_body (DeepSeek hybrid thinking toggle)
    must ride along on the actual call."""
    called: dict = {}

    async def _fake_openai_compat(model_id, location, system, prompt,
                                  max_tokens=4096, temperature=0.3,
                                  extra_body=None):
        called["extra_body"] = extra_body
        return "ok", None

    monkeypatch.setattr(g, "_generate_openai_compat", _fake_openai_compat)
    monkeypatch.setattr(m, "_user_models", {})
    monkeypatch.setattr(g, "_chat_histories", {})

    m.switch_user_model(555002, "deepseek-v3.2", "global")

    await g.generate(
        "hello", user_id=555002, account_id=424243, action="question",
    )

    assert called["extra_body"] == {"chat_template_kwargs": {"thinking": True}}
