"""The Gemini agent loop remembers what its earlier tools returned.

Every round rebuilt the conversation as exactly three messages — the
user's question, the model's LATEST function call, and that call's
response.  Nothing accumulated.  So on a two-tool question ("compare
truck 231's fuel with its maintenance") the first tool's result was
gone from context by the time the model wrote the answer: it had
already decided to call a second tool BECAUSE it had seen the first
one, and then the evidence was taken away.

The Anthropic loop appends to ``messages`` and always did.  This pins
the Gemini path to the same contract.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from types import SimpleNamespace

import pytest

import capabilities.ai.intelligence as intel


class _Part:
    """A google-genai Part stand-in; ``.text`` raises on non-text parts."""

    def __init__(self, text=None, function_call=None):
        self._text = text
        self.function_call = function_call
        self.thought = False

    @property
    def text(self):
        if self._text is None:
            raise ValueError("part has no text")
        return self._text


def _call_turn(name):
    return SimpleNamespace(candidates=[SimpleNamespace(
        content=SimpleNamespace(
            parts=[_Part(function_call=SimpleNamespace(name=name, args={}))],
            role="model"))], usage_metadata=None)


def _answer_turn(text):
    return SimpleNamespace(candidates=[SimpleNamespace(
        content=SimpleNamespace(parts=[_Part(text=text)], role="model"))],
        usage_metadata=None, text=text)


class _ScriptedModel:
    """Answers with a scripted turn each call, recording what it was sent."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.seen: list = []

    def generate_content(self, contents, tools=None):
        self.seen.append(contents)
        return self._turns.pop(0)


@pytest.fixture
def _gemini(monkeypatch):
    """Drive the real loop against a scripted model."""
    pytest.importorskip("google.genai")

    async def _no_perm_check(*a, **kw):
        return None

    async def _tool(tool_name, tool_args, client, **kw):
        return {"ok": True, "tool": tool_name, "value": f"{tool_name}-result"}

    async def _no_tools(**kw):
        return None

    async def _record(**kw):
        return None

    monkeypatch.setattr(intel, "_check_tool_permission", _no_perm_check)
    monkeypatch.setattr(intel, "_execute_tool", _tool)
    monkeypatch.setattr(intel, "_get_cached_tools", _no_tools)
    monkeypatch.setattr(intel, "_STREAM_TOKENS", False)
    import capabilities.ai.usage as usage
    monkeypatch.setattr(usage, "record_call_attempt", _record)

    def _install(turns):
        model = _ScriptedModel(turns)
        monkeypatch.setattr(
            intel, "get_model_for_user",
            lambda uid, aid: (model, "gemini-2.5-flash", None))
        return model

    return _install


def _function_response_names(contents) -> list[str]:
    """Every tool whose RESPONSE is present in one request's contents."""
    out = []
    for c in contents if isinstance(contents, list) else []:
        for p in getattr(c, "parts", []) or []:
            fr = getattr(p, "function_response", None)
            if fr is not None:
                out.append(fr.name)
    return out


@pytest.mark.asyncio
async def test_the_second_round_still_carries_the_first_tools_result(_gemini):
    model = _gemini([
        _call_turn("get_fuel_summary"),
        _call_turn("get_maintenance_summary"),
        _answer_turn("Truck 231 burned 40 gal and is overdue for an oil change."),
    ])

    out = await intel.ask_agent("compare 231's fuel and maintenance", {}, None)

    assert out["text"].startswith("Truck 231")
    assert [t["tool"] for t in out["tool_results"]] == [
        "get_fuel_summary", "get_maintenance_summary",
    ]
    # Three model calls: initial, after tool 1, after tool 2.
    assert len(model.seen) == 3
    # The LAST request — the one the answer was written from — must
    # carry BOTH results, not just the most recent.
    assert _function_response_names(model.seen[-1]) == [
        "get_fuel_summary", "get_maintenance_summary",
    ]


@pytest.mark.asyncio
async def test_the_question_is_never_repeated_as_the_history_grows(_gemini):
    """Accumulating must append turns, not re-send the opening prompt."""
    model = _gemini([
        _call_turn("get_fuel_summary"),
        _call_turn("get_maintenance_summary"),
        _answer_turn("done"),
    ])
    await intel.ask_agent("q", {}, None)

    def _has_prompt_text(content) -> bool:
        for part in getattr(content, "parts", None) or []:
            try:
                if part.text:
                    return True
            except (ValueError, AttributeError):
                continue
        return False

    last = model.seen[-1]
    user_turns = [c for c in last
                  if getattr(c, "role", "") == "user" and _has_prompt_text(c)]
    assert len(user_turns) == 1, "the opening question appears more than once"


@pytest.mark.asyncio
async def test_a_single_tool_question_is_unchanged(_gemini):
    model = _gemini([
        _call_turn("get_fuel_summary"),
        _answer_turn("40 gallons."),
    ])
    out = await intel.ask_agent("fuel for 231?", {}, None)
    assert out["text"] == "40 gallons."
    assert _function_response_names(model.seen[-1]) == ["get_fuel_summary"]
