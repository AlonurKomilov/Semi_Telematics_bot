"""Every registered tool schema must convert cleanly to ALL THREE provider
tool formats (Gemini FunctionDeclaration, Anthropic, OpenAI chat-completions).

Regression guard for the outage where registry metadata (``writes`` /
``risk`` / ``scope``) was splatted into google-genai's FunctionDeclaration
(pydantic, extra="forbid") — ONE extra key broke every Gemini-tier request
at tool-build time, while the projecting Anthropic/OpenAI converters kept
their tiers alive.  Symptom: "No response received" on Fast/Thinking/Auto
with zero ai_usage telemetry, Reasoning fine.

If this test fails after registering a new tool, the schema is leaking
non-API keys into a converter — fix the converter to PROJECT fields,
never splat.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

import capabilities.ai.tools  # noqa: F401  (side effect: registers every tool)
from capabilities.ai.tools import (
    get_all_tool_schemas,
    get_cached_vertex_tools,
    get_anthropic_tools,
    get_openai_tools,
)


def test_registry_is_populated():
    assert len(get_all_tool_schemas()) > 10


async def test_gemini_function_declarations_build():
    """The converter that actually broke: google-genai pydantic models."""
    pytest.importorskip("google.genai")
    tools = await get_cached_vertex_tools(role=None)
    decls = tools[0].function_declarations
    assert len(decls) == len(get_all_tool_schemas())
    # Write tools carry the most metadata — make sure they made it through.
    names = {d.name for d in decls}
    assert "create_maintenance_task" in names
    assert "acknowledge_alerts" in names


async def test_anthropic_tools_build():
    tools = await get_anthropic_tools(role=None)
    assert len(tools) == len(get_all_tool_schemas())
    for td in tools:
        assert set(td) == {"name", "description", "input_schema"}


async def test_openai_tools_build():
    tools = await get_openai_tools(role=None)
    assert len(tools) == len(get_all_tool_schemas())
    for td in tools:
        assert set(td) == {"type", "function"}
        assert set(td["function"]) == {"name", "description", "parameters"}


def test_blank_responses_are_never_cached():
    """The second half of the outage: a blank generation cached under the
    question key served instant empty answers to every retry."""
    from capabilities.ai.cache import _cache_put, _cache_get
    _cache_put("k-blank", "")
    _cache_put("k-space", "   \n")
    assert _cache_get("k-blank") is None
    assert _cache_get("k-space") is None
    _cache_put("k-real", "answer")
    assert _cache_get("k-real") == "answer"
