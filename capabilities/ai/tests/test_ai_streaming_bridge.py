"""Unit test for the Gemini streaming bridge (``_gemini_streamed_call``).

We can't hit live Vertex here, so we feed the bridge fake google-genai stream
chunks (thought / text / function_call parts) and assert two things:
  1. it emits live ``thinking`` and ``delta`` events in order, and
  2. it returns a response-shaped object the agent loop can consume unchanged
     (merged full-text single part, function_call parts preserved for re-feed,
     usage_metadata carried through).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from types import SimpleNamespace

import pytest

from capabilities.ai.intelligence import _gemini_streamed_call


class _FakePart:
    """Mimics a google-genai Part: ``.text`` raises on a non-text part (as the
    real SDK does for thought-signature-only parts), and carries ``.thought``
    / ``.function_call``."""

    def __init__(self, text=None, thought=False, function_call=None):
        self._text = text
        self.thought = thought
        self.function_call = function_call

    @property
    def text(self):
        if self._text is None:
            raise ValueError("part has no text")
        return self._text


def _chunk(parts, usage=None):
    return SimpleNamespace(
        usage_metadata=usage,
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))],
    )


class _FakeModel:
    def __init__(self, chunks):
        self._chunks = chunks

    def generate_content_stream(self, contents, tools=None):
        return iter(self._chunks)


@pytest.mark.asyncio
async def test_answer_turn_streams_thinking_then_deltas():
    usage = SimpleNamespace(
        prompt_token_count=10, candidates_token_count=3,
        thoughts_token_count=5, total_token_count=18,
    )
    model = _FakeModel([
        _chunk([_FakePart(text="Let me check the data.", thought=True)]),
        _chunk([_FakePart(text="Hello ")]),
        _chunk([_FakePart(text="world.")], usage=usage),
    ])
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    resp = await _gemini_streamed_call(model, "prompt", None, emit)

    # Live events: reasoning first, then two answer chunks, in order.
    assert events == [
        {"type": "thinking", "text": "Let me check the data."},
        {"type": "delta", "text": "Hello "},
        {"type": "delta", "text": "world."},
    ]
    # Synthesized response: full merged answer, single text part, usage carried.
    assert resp.text == "Hello world."
    assert resp.usage_metadata is usage
    text_parts = [p for p in resp.candidates[0].content.parts if getattr(p, "text", None)]
    assert len(text_parts) == 1
    assert text_parts[0].text == "Hello world."


@pytest.mark.asyncio
async def test_tool_turn_preserves_function_call_and_emits_no_delta():
    # Real google-genai Part — the synthesized Content re-feeds it verbatim,
    # so it must be a genuine Part (pydantic-validated), not a stand-in.
    from google.genai import types as gt
    fc_part = gt.Part(function_call=gt.FunctionCall(name="get_parked_vehicles", args={}))
    model = _FakeModel([
        _chunk([_FakePart(text="I should look up parking.", thought=True)]),
        _chunk([fc_part]),
    ])
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    resp = await _gemini_streamed_call(model, "prompt", None, emit)

    # Reasoning streamed; no answer deltas on a tool-deciding turn.
    assert {"type": "thinking", "text": "I should look up parking."} in events
    assert all(e["type"] != "delta" for e in events)
    # No answer text; the function_call part is preserved for re-feeding.
    assert resp.text == ""
    parts = resp.candidates[0].content.parts
    assert fc_part in parts
    assert any(getattr(p, "function_call", None) for p in parts)


@pytest.mark.asyncio
async def test_no_thoughts_no_thinking_events():
    # Flash-style: budget 0 → no thought parts → only answer deltas.
    model = _FakeModel([_chunk([_FakePart(text="All 95 vehicles are off.")])])
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    resp = await _gemini_streamed_call(model, "prompt", None, emit)
    assert all(e["type"] != "thinking" for e in events)
    assert resp.text == "All 95 vehicles are off."
