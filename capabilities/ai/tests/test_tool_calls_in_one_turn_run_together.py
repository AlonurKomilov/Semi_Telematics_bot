"""Tools the model asked for in ONE turn run together.

Anthropic and the OpenAI-compat providers both return several
``tool_use`` / ``tool_calls`` entries in a single turn — the provider's
way of saying "these are independent, I want them all".  Both loops
then awaited them one at a time, so three independent reads cost three
round trips of latency in series.

Order is load-bearing and survives the change: ``ask_agent_stream``
zips the process timeline's tool steps against ``tool_results`` BY
POSITION, so the answers must come back in the order the model asked,
whatever order they finish in.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

import capabilities.ai.intelligence as intel


def _calls(*names):
    return [(f"id-{n}", n, {"vehicle_name": n}) for n in names]


async def _dispatch(calls, **kw):
    return await intel._dispatch_tool_calls(
        calls, provider="test", samsara_client=None, account_id=1, db=None,
        user_role="owner", user_context={}, **kw,
    )


@pytest.fixture
def _allow_all(monkeypatch):
    async def _ok(*a, **kw):
        return None
    monkeypatch.setattr(intel, "_check_tool_permission", _ok)


@pytest.mark.asyncio
async def test_two_tools_in_one_turn_actually_overlap(_allow_all, monkeypatch):
    """The proof is a barrier: if these ran in series the first would
    wait for a partner that has not been started yet and time out."""
    both_started = asyncio.Event()
    running: set[str] = set()

    async def _tool(name, args, client, **kw):
        running.add(name)
        if len(running) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=3)
        return {"ok": True, "tool": name}

    monkeypatch.setattr(intel, "_execute_tool", _tool)
    out = await _dispatch(_calls("a", "b"), event_callback=None)

    assert [r["data"].get("ok") for r in out] == [True, True], (
        "a tool timed out waiting for its partner — they ran in series")


@pytest.mark.asyncio
async def test_answers_come_back_in_the_order_the_model_asked(_allow_all,
                                                              monkeypatch):
    """The slow tool is asked for FIRST; it must still be reported first."""
    async def _tool(name, args, client, **kw):
        if name == "slow":
            await asyncio.sleep(0.05)
        return {"tool": name}

    monkeypatch.setattr(intel, "_execute_tool", _tool)
    out = await _dispatch(_calls("slow", "fast"), event_callback=None)

    assert [r["tool"] for r in out] == ["slow", "fast"]
    assert [r["id"] for r in out] == ["id-slow", "id-fast"]


@pytest.mark.asyncio
async def test_one_tool_failing_does_not_take_the_others_down(_allow_all,
                                                              monkeypatch):
    async def _tool(name, args, client, **kw):
        if name == "boom":
            raise RuntimeError("provider said no")
        return {"tool": name}

    monkeypatch.setattr(intel, "_execute_tool", _tool)
    out = await _dispatch(_calls("boom", "fine"), event_callback=None)

    assert "provider said no" in out[0]["data"]["error"]
    assert out[1]["data"] == {"tool": "fine"}


@pytest.mark.asyncio
async def test_a_blocked_tool_keeps_its_slot_and_never_runs(monkeypatch):
    ran: list[str] = []

    async def _check(tool_name, *a, **kw):
        if tool_name == "denied":
            return {"error": "Access denied: denied"}
        return None

    async def _tool(name, args, client, **kw):
        ran.append(name)
        return {"tool": name}

    monkeypatch.setattr(intel, "_check_tool_permission", _check)
    monkeypatch.setattr(intel, "_execute_tool", _tool)
    out = await _dispatch(_calls("denied", "allowed"), event_callback=None)

    assert ran == ["allowed"]
    assert out[0]["blocked"] is True
    assert "Access denied" in out[0]["data"]["error"]
    assert out[1]["blocked"] is False


@pytest.mark.asyncio
async def test_progress_events_fire_in_call_order_and_skip_blocked(monkeypatch):
    events: list[str] = []

    async def _check(tool_name, *a, **kw):
        return {"error": "no"} if tool_name == "denied" else None

    async def _tool(name, args, client, **kw):
        # Finish out of order on purpose — events must not follow this.
        await asyncio.sleep(0.03 if name == "first" else 0)
        return {"tool": name}

    async def _emit(ev):
        events.append(ev["name"])

    monkeypatch.setattr(intel, "_check_tool_permission", _check)
    monkeypatch.setattr(intel, "_execute_tool", _tool)
    await _dispatch(_calls("first", "denied", "second"), event_callback=_emit)

    assert events == ["first", "second"]
