"""Tool-result envelope: the ``execute_tool`` boundary stamps a flat ``ok``
discriminator on every result, so the agent loop has a uniform success/failure
signal without knowing each tool's bespoke shape.  Behaviour-preserving: all
existing keys are kept; only one boolean is added.

This is foundation for a future multi-round / autonomous mode (retry-vs-replan
decisions key off ``ok``).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.ai.tools.registry import (
    execute_tool,
    register_tool,
    tool_ok,
    tool_error,
    _stamp_ok,
)


# ── tool_ok / tool_error helpers ─────────────────────────────────────────────


def test_tool_ok_builds_success_envelope():
    assert tool_ok() == {"ok": True}
    assert tool_ok({"count": 2}) == {"ok": True, "count": 2}
    assert tool_ok(count=3, vehicles=[]) == {"ok": True, "count": 3, "vehicles": []}


def test_tool_error_builds_failure_envelope():
    res = tool_error("boom", code=500)
    assert res == {"ok": False, "error": "boom", "code": 500}


# ── _stamp_ok is non-destructive ─────────────────────────────────────────────


def test_stamp_ok_marks_success_and_preserves_keys():
    out = _stamp_ok({"count": 2, "vehicles": ["B-1", "B-2"]})
    assert out["ok"] is True
    assert out["count"] == 2                      # existing keys preserved
    assert out["vehicles"] == ["B-1", "B-2"]


def test_stamp_ok_marks_error_results_not_ok():
    out = _stamp_ok({"error": "no such vehicle"})
    assert out["ok"] is False
    assert out["error"] == "no such vehicle"      # original key preserved


def test_stamp_ok_passes_through_explicit_envelope():
    explicit = {"ok": False, "error": "already wrapped"}
    assert _stamp_ok(explicit) is explicit        # untouched


def test_stamp_ok_wraps_non_dict_returns():
    assert _stamp_ok("hello") == {"ok": True, "data": "hello"}
    assert _stamp_ok(None) == {"ok": True, "data": None}


# ── execute_tool stamps every return path ────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_tool_unknown_tool_is_not_ok():
    res = await execute_tool("does_not_exist", {}, None, account_id=1)
    assert res["ok"] is False
    assert "Unknown tool" in res["error"]


@pytest.mark.asyncio
async def test_execute_tool_handler_exception_is_not_ok():
    @register_tool({"name": "_boom_tool", "description": "raises",
                    "parameters": {"type": "object", "properties": {}}})
    async def _boom(tool_args, samsara_client, account_id=None, db=None):
        raise RuntimeError("kaboom")

    res = await execute_tool("_boom_tool", {}, None, account_id=1)
    assert res["ok"] is False
    assert "kaboom" in res["error"]


@pytest.mark.asyncio
async def test_execute_tool_success_is_ok_and_keeps_shape():
    @register_tool({"name": "_count_tool", "description": "counts",
                    "parameters": {"type": "object", "properties": {}}})
    async def _count(tool_args, samsara_client, account_id=None, db=None):
        return {"count": 2, "vehicles": ["B-1", "B-2"]}

    res = await execute_tool("_count_tool", {}, None, account_id=1)
    assert res["ok"] is True
    assert res["count"] == 2                       # legacy consumers unaffected
    assert res["vehicles"] == ["B-1", "B-2"]


@pytest.mark.asyncio
async def test_execute_tool_respects_tool_authored_envelope():
    @register_tool({"name": "_envelope_tool", "description": "speaks envelope",
                    "parameters": {"type": "object", "properties": {}}})
    async def _env(tool_args, samsara_client, account_id=None, db=None):
        return tool_error("explicit failure", count=0)

    res = await execute_tool("_envelope_tool", {}, None, account_id=1)
    assert res["ok"] is False                      # not re-derived to True
    assert res["error"] == "explicit failure"
    assert res["count"] == 0
