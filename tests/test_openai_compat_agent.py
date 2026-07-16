"""The OpenAI-compat function-calling loop — Reasoning-tier models get
real tool access (verified live: DeepSeek R1 / Qwen thinking / Kimi K2 all
emit ``tool_calls`` on the Vertex MaaS endpoint).

Loop mechanics pinned here with a stubbed endpoint: execute → feed back →
final answer; permission gate; graceful chat-only fallback when the
endpoint rejects the request.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("ENCRYPTION_KEY", "")

import capabilities.ai.intelligence as intel


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeCreds:
    token = "fake-token"

    def refresh(self, _request):
        pass


def _tool_call_round(name="get_maintenance_summary", args="{}"):
    return {
        "choices": [{"message": {
            "role": "assistant",
            "content": "",
            "reasoning_content": "I should check maintenance data.",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": name, "arguments": args},
            }],
        }}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                  "completion_tokens_details": {"reasoning_tokens": 15},
                  "total_tokens": 135},
    }


def _final_round(text="Truck 228 is overdue by 780 miles."):
    return {
        "choices": [{"message": {
            "role": "assistant",
            "content": text,
            "reasoning_content": "The tool says 1 overdue.",
        }}],
        "usage": {"prompt_tokens": 200, "completion_tokens": 40,
                  "total_tokens": 240},
    }


@pytest.fixture()
def _agent_env(monkeypatch):
    """Stub everything external: creds, HTTP, tools list, executor, perms."""
    import requests
    from capabilities.ai import registry as reg

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr(reg, "_get_credentials", lambda: _FakeCreds())
    monkeypatch.setattr(intel, "_chat_histories", {})

    async def _tools(**kw):
        return [{"type": "function", "function": {
            "name": "get_maintenance_summary", "description": "d",
            "parameters": {"type": "object", "properties": {}}}}]
    monkeypatch.setattr(intel, "_get_openai_tools", _tools)

    calls: dict = {"posted": [], "executed": []}

    async def _perm_ok(*a, **kw):
        return None
    monkeypatch.setattr(intel, "_check_tool_permission", _perm_ok)

    async def _exec(tool_name, tool_args, client, **kw):
        calls["executed"].append((tool_name, tool_args))
        return {"total_overdue": 1, "overdue_tasks": [{"vehicle": "228"}]}
    monkeypatch.setattr(intel, "_execute_tool", _exec)

    def _fake_post(url, headers=None, json=None, timeout=None):
        calls["posted"].append(json)
        return _FakeResp(calls["responses"].pop(0))
    monkeypatch.setattr(requests, "post", _fake_post)

    return calls


MODEL_INFO = {
    "maas_model_id": "deepseek-ai/deepseek-r1-0528-maas",
    "locations": ["us-central1"],
    "max_output_tokens": 8192,
}


@pytest.mark.asyncio
async def test_tool_round_then_answer(_agent_env):
    _agent_env["responses"] = [_tool_call_round(), _final_round()]

    result = await intel._run_openai_compat_agent(
        "Any overdue maintenance tasks?", {"fleet": "ctx"}, None,
        model_name="deepseek-r1", model_info=MODEL_INFO,
        user_id=901001, account_id=901, db=object(),
        language="en", user_context={"role": "owner"},
        event_callback=None,
    )

    assert result["text"] == "Truck 228 is overdue by 780 miles."
    assert _agent_env["executed"] == [("get_maintenance_summary", {})]
    assert result["tool_results"][0]["data"]["total_overdue"] == 1
    # Reasoning accumulated across BOTH rounds.
    assert "check maintenance data" in result["reasoning"]
    assert "tool says 1 overdue" in result["reasoning"]
    # Usage summed across rounds, reasoning tokens included.
    assert result["usage"]["prompt_tokens"] == 300
    assert result["usage"]["thinking_tokens"] == 15
    # Second request echoed the assistant tool_calls + a tool message.
    second = _agent_env["posted"][1]["messages"]
    assert second[-2]["tool_calls"][0]["id"] == "call_1"
    assert second[-1]["role"] == "tool"
    assert second[-1]["tool_call_id"] == "call_1"
    assert "total_overdue" in second[-1]["content"]


@pytest.mark.asyncio
async def test_permission_blocked_tool_feeds_denial_back(_agent_env, monkeypatch):
    denial = {"error": "You don't have permission for maintenance data."}

    async def _perm_block(*a, **kw):
        return denial
    monkeypatch.setattr(intel, "_check_tool_permission", _perm_block)
    _agent_env["responses"] = [_tool_call_round(), _final_round("No access.")]

    result = await intel._run_openai_compat_agent(
        "Any overdue tasks?", {}, None,
        model_name="deepseek-r1", model_info=MODEL_INFO,
        user_id=901002, account_id=901, db=object(),
        language="en", user_context={"role": "driver"},
        event_callback=None,
    )

    assert _agent_env["executed"] == []                       # never executed
    assert result["tool_results"][0]["data"] == denial        # denial surfaced
    tool_msg = _agent_env["posted"][1]["messages"][-1]
    assert json.loads(tool_msg["content"]) == denial          # model was told


@pytest.mark.asyncio
async def test_endpoint_rejection_falls_back_to_chat_only(_agent_env, monkeypatch):
    import requests

    def _boom(*a, **kw):
        raise RuntimeError("400: unknown field 'tools'")
    monkeypatch.setattr(requests, "post", _boom)

    async def _fake_ask_ai(*a, **kw):
        return "snapshot-only answer", {"prompt_tokens": 1, "reply_tokens": 1,
                                        "thinking_tokens": 0, "total_tokens": 2}
    monkeypatch.setattr(intel, "ask_ai", _fake_ask_ai)

    result = await intel._run_openai_compat_agent(
        "Any overdue tasks?", {}, None,
        model_name="deepseek-r1", model_info=MODEL_INFO,
        user_id=901003, account_id=901, db=object(),
        language="en", user_context={"role": "owner"},
        event_callback=None,
    )

    assert result["text"] == "snapshot-only answer"
    assert result["tool_results"] == []


def test_parse_reply_strips_orphan_think_close():
    """R1 sometimes opens reasoning IMPLICITLY and only emits the closing
    </think> at the boundary — the monologue plus a literal '</think>'
    leaked into the visible answer.  The head must move to reasoning."""
    from capabilities.ai.generation import _parse_openai_compat_reply
    data = {"choices": [{"message": {
        "content": "We ponder deeply.\nMore pondering.\n</think>\nThe answer is 42.",
    }}]}
    text, usage, reasoning = _parse_openai_compat_reply(data)
    assert text == "The answer is 42."
    assert "ponder" in reasoning and "</think>" not in text


def test_parse_reply_paired_think_still_works():
    from capabilities.ai.generation import _parse_openai_compat_reply
    data = {"choices": [{"message": {
        "content": "<think>hmm</think>Final.",
        "reasoning_content": "",
    }}]}
    text, _, reasoning = _parse_openai_compat_reply(data)
    assert text == "Final." and reasoning == "hmm"


def test_current_datetime_line_anchors_now():
    from capabilities.ai.usage import current_datetime_line
    line = current_datetime_line("America/New_York")
    assert line.startswith("- Current date/time: ") and "America/New_York" in line
    assert "20" in line  # a year is present
    # Invalid tz falls back to UTC instead of raising.
    assert "UTC" in current_datetime_line("Not/AZone")


def test_extract_text_tool_calls_rescues_tools_block():
    """qwen/GLM-style models sometimes emit the tool call as TEXT —
    <tools>{"name": …}</tools> — instead of the native channel; the raw
    JSON block became the user-visible answer and the tool never ran."""
    from capabilities.ai.intelligence import _extract_text_tool_calls
    text = ('<tools>\n{"name": "get_fuel_cost_summary", "arguments": '
            '{"start_date": "2026-07-01", "end_date": "2026-07-16"}}\n</tools>')
    calls = _extract_text_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "get_fuel_cost_summary"
    import json as _json
    assert _json.loads(calls[0]["function"]["arguments"])["start_date"] == "2026-07-01"
    # <tool_call> variant + garbage tolerance
    assert _extract_text_tool_calls('<tool_call>{"name": "x", "arguments": {}}</tool_call>')
    assert _extract_text_tool_calls("no tools here") == []
    assert _extract_text_tool_calls("<tools>not json</tools>") == []
