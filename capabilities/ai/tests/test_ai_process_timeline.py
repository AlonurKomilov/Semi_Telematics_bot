"""The process timeline — an ordered step log (thinking → tool → …) the
chat renders as "N steps", assembled in ``ask_agent_stream`` from the same
event stream all three agent loops (Gemini / Anthropic / OpenAI-compat)
already emit through one callback.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("ENCRYPTION_KEY", "")

import capabilities.ai.intelligence as intel


async def _collect(stream):
    events = []
    async for ev in stream:
        events.append(ev)
    return events


def _fake_ask_agent(script, result):
    """ask_agent stand-in: fires callback events in order, returns result."""
    async def _fake(question, ctx, client, event_callback=None, **kw):
        for ev in script:
            if event_callback:
                await event_callback(ev)
        return result
    return _fake


@pytest.mark.asyncio
async def test_steps_keep_occurrence_order_and_zip_results(monkeypatch):
    script = [
        {"type": "thinking", "text": "I should check maintenance."},
        {"type": "tool", "name": "get_maintenance_summary", "label": "Reviewing maintenance"},
        {"type": "thinking", "text": "One overdue — verify the vehicle."},
        {"type": "tool", "name": "get_vehicle_info", "label": "Checking vehicle"},
    ]
    result = {
        "text": "Truck 228 is overdue.",
        "usage": None,
        "tool_results": [
            {"tool": "get_maintenance_summary", "args": {},
             "data": {"total_overdue": 1}},
            {"tool": "get_vehicle_info", "args": {"vehicle_name": "228"},
             "data": {"odometer": 219111}},
        ],
        "reasoning": "ignored — thinking events exist",
    }
    monkeypatch.setattr(intel, "ask_agent", _fake_ask_agent(script, result))

    events = await _collect(intel.ask_agent_stream("q", {}, None, user_id=1, account_id=1))
    done = events[-1]
    assert done["type"] == "done"
    steps = done["process"]

    assert [s["type"] for s in steps] == ["thinking", "tool", "thinking", "tool"]
    assert steps[1]["label"] == "Reviewing maintenance"
    assert "total_overdue" in steps[1]["result"]         # digest zipped by order
    assert "odometer" in steps[3]["result"]
    assert "228" in steps[3]["args"]


@pytest.mark.asyncio
async def test_consecutive_thinking_chunks_coalesce(monkeypatch):
    script = [
        {"type": "thinking", "text": "Gemini streams "},
        {"type": "thinking", "text": "tiny fragments."},
        {"type": "tool", "name": "t", "label": "T"},
    ]
    result = {"text": "a", "usage": None, "tool_results": [
        {"tool": "t", "args": {}, "data": {}}]}
    monkeypatch.setattr(intel, "ask_agent", _fake_ask_agent(script, result))

    done = (await _collect(intel.ask_agent_stream("q", {}, None, user_id=2, account_id=1)))[-1]
    steps = done["process"]
    assert len(steps) == 2                                # not 3
    assert steps[0]["text"] == "Gemini streams tiny fragments."


@pytest.mark.asyncio
async def test_chat_only_reasoning_becomes_a_step(monkeypatch):
    """No thinking events (whole-blob path) → the result's reasoning is
    surfaced as the timeline's first step so the log is never empty."""
    result = {"text": "a", "usage": None, "tool_results": [],
              "reasoning": "We are given fleet data…"}
    monkeypatch.setattr(intel, "ask_agent", _fake_ask_agent([], result))

    done = (await _collect(intel.ask_agent_stream("q", {}, None, user_id=3, account_id=1)))[-1]
    assert done["process"] == [
        {"type": "thinking", "text": "We are given fleet data…"},
    ]


@pytest.mark.asyncio
async def test_huge_tool_result_is_digested(monkeypatch):
    script = [{"type": "tool", "name": "big", "label": "Big"}]
    result = {"text": "a", "usage": None, "tool_results": [
        {"tool": "big", "args": {}, "data": {"rows": ["x" * 50] * 200}}]}
    monkeypatch.setattr(intel, "ask_agent", _fake_ask_agent(script, result))

    done = (await _collect(intel.ask_agent_stream("q", {}, None, user_id=4, account_id=1)))[-1]
    assert len(done["process"][0]["result"]) <= 401       # 400 + ellipsis


@pytest.mark.asyncio
async def test_artifacts_collected_from_tool_results(monkeypatch):
    """A tool that attaches `artifacts` to its result surfaces them on the
    done event (flattened, in tool order); malformed entries are skipped."""
    script = [{"type": "tool", "name": "get_maintenance_summary", "label": "M"}]
    result = {"text": "ok", "usage": None, "tool_results": [
        {"tool": "get_maintenance_summary", "args": {}, "data": {
            "total_overdue": 1,
            "artifacts": [
                {"type": "table", "title": "Open", "columns": [], "rows": []},
                {"no_type": "skipped"},          # malformed → dropped
            ],
        }},
        {"tool": "other", "args": {}, "data": {"x": 1}},   # no artifacts
    ]}
    monkeypatch.setattr(intel, "ask_agent", _fake_ask_agent(script, result))

    done = (await _collect(intel.ask_agent_stream("q", {}, None, user_id=6, account_id=1)))[-1]
    assert [a["type"] for a in done["artifacts"]] == ["table"]
    assert done["artifacts"][0]["title"] == "Open"


@pytest.mark.asyncio
async def test_no_artifacts_yields_empty_list(monkeypatch):
    result = {"text": "ok", "usage": None, "tool_results": [
        {"tool": "t", "args": {}, "data": {"x": 1}}]}
    monkeypatch.setattr(intel, "ask_agent", _fake_ask_agent(
        [{"type": "tool", "name": "t", "label": "T"}], result))
    done = (await _collect(intel.ask_agent_stream("q", {}, None, user_id=7, account_id=1)))[-1]
    assert done["artifacts"] == []


@pytest.mark.asyncio
async def test_tool_steps_carry_elapsed_ms(monkeypatch):
    """Each tool step gets a server-measured wall-clock duration — the
    "12s / 8s" the timeline shows.  A tool step's time is the gap from
    its event to the next; the last is closed at finish."""
    async def _fake(question, ctx, client, event_callback=None, **kw):
        import asyncio
        if event_callback:
            await event_callback({"type": "tool", "name": "t1", "label": "One"})
            await asyncio.sleep(0.02)   # t1 "runs"
            await event_callback({"type": "tool", "name": "t2", "label": "Two"})
            await asyncio.sleep(0.02)   # t2 "runs"
        return {"text": "done", "usage": None, "tool_results": [
            {"tool": "t1", "args": {}, "data": {}},
            {"tool": "t2", "args": {}, "data": {}}]}
    monkeypatch.setattr(intel, "ask_agent", _fake)

    done = (await _collect(intel.ask_agent_stream("q", {}, None, user_id=5, account_id=1)))[-1]
    steps = [s for s in done["process"] if s["type"] == "tool"]
    assert len(steps) == 2
    # Both closed with a non-negative, sane duration (each ~20ms).
    assert all("elapsed_ms" in s and s["elapsed_ms"] >= 0 for s in steps)
    assert steps[0]["elapsed_ms"] < 5000 and steps[1]["elapsed_ms"] < 5000
