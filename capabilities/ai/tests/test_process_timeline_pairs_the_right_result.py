"""A refused tool must not hand its payload to the next tool's step.

``ask_agent_stream`` builds the visible "N steps" timeline by zipping
tool STEPS against ``tool_results`` by index. A refused call appends a
result and emits no step — so the raw zip slid every later pairing by
one, and a user was shown "Checking fuel" with an Access-denied result
attached to it.

Worse than an undocumented bug: the function's own docstring asserted
the zip was exact, so the next reader had no reason to check.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

import capabilities.ai.intelligence as intel


async def _collect(stream):
    return [ev async for ev in stream]


def _fake_ask_agent(script, result):
    async def _fake(question, ctx, client, event_callback=None, **kw):
        for ev in script:
            if event_callback:
                await event_callback(ev)
        return result
    return _fake


@pytest.mark.asyncio
async def test_a_refusal_does_not_slide_the_pairing(monkeypatch):
    """The model asked for two tools; the first was refused.

    Only one step exists, and it must carry the SECOND tool's result.
    """
    script = [
        # No event for the refused call — the dispatcher skips it.
        {"type": "tool", "name": "get_fuel_cost_summary",
         "label": "Reviewing fuel"},
    ]
    result = {
        "text": "Fuel spend was $4,120.",
        "usage": None,
        "tool_results": [
            {"tool": "get_camera_frame", "args": {},
             "data": {"error": "Access denied: cameras"}, "blocked": True},
            {"tool": "get_fuel_cost_summary", "args": {},
             "data": {"total_cost_dollars": 4120}, "blocked": False},
        ],
    }
    monkeypatch.setattr(intel, "ask_agent", _fake_ask_agent(script, result))

    done = (await _collect(
        intel.ask_agent_stream("q", {}, None, user_id=1, account_id=1)))[-1]
    steps = done["process"]

    assert len(steps) == 1
    assert steps[0]["label"] == "Reviewing fuel"
    assert "4120" in steps[0]["result"], (
        "the fuel step wore the refusal's payload"
    )
    assert "Access denied" not in steps[0]["result"]


@pytest.mark.asyncio
async def test_two_refusals_in_a_row_still_pair_correctly(monkeypatch):
    script = [
        {"type": "tool", "name": "get_vehicle_info", "label": "Checking truck"},
    ]
    result = {
        "text": "ok", "usage": None,
        "tool_results": [
            {"tool": "a", "args": {}, "data": {"error": "denied"}, "blocked": True},
            {"tool": "b", "args": {}, "data": {"error": "denied"}, "blocked": True},
            {"tool": "get_vehicle_info", "args": {},
             "data": {"odometer": 219111}, "blocked": False},
        ],
    }
    monkeypatch.setattr(intel, "ask_agent", _fake_ask_agent(script, result))

    steps = (await _collect(
        intel.ask_agent_stream("q", {}, None, user_id=2, account_id=1)))[-1]["process"]
    assert len(steps) == 1
    assert "219111" in steps[0]["result"]


@pytest.mark.asyncio
async def test_a_turn_with_no_refusal_is_unchanged(monkeypatch):
    """The common case must keep pairing exactly as before."""
    script = [
        {"type": "tool", "name": "one", "label": "One"},
        {"type": "tool", "name": "two", "label": "Two"},
    ]
    result = {
        "text": "ok", "usage": None,
        "tool_results": [
            {"tool": "one", "args": {}, "data": {"n": 1}, "blocked": False},
            {"tool": "two", "args": {}, "data": {"n": 2}, "blocked": False},
        ],
    }
    monkeypatch.setattr(intel, "ask_agent", _fake_ask_agent(script, result))

    steps = (await _collect(
        intel.ask_agent_stream("q", {}, None, user_id=3, account_id=1)))[-1]["process"]
    assert [s["label"] for s in steps] == ["One", "Two"]
    assert '"n": 1' in steps[0]["result"] or "'n': 1" in steps[0]["result"]
    assert '"n": 2' in steps[1]["result"] or "'n': 2" in steps[1]["result"]


@pytest.mark.asyncio
async def test_results_from_before_the_marker_still_pair(monkeypatch):
    """A result dict with no ``blocked`` key at all — anything built by
    a path that predates the marker — must behave as not-blocked rather
    than vanishing from the timeline."""
    script = [{"type": "tool", "name": "one", "label": "One"}]
    result = {
        "text": "ok", "usage": None,
        "tool_results": [{"tool": "one", "args": {}, "data": {"n": 1}}],
    }
    monkeypatch.setattr(intel, "ask_agent", _fake_ask_agent(script, result))

    steps = (await _collect(
        intel.ask_agent_stream("q", {}, None, user_id=4, account_id=1)))[-1]["process"]
    assert len(steps) == 1
    assert "n" in steps[0]["result"]
