"""Tests for the bot AI streaming groundwork.

Covers the contract the bot's cmd_ai_answer loop depends on:
- ask_agent_stream yields {"type":"tool",...} per tool call then one
  {"type":"done", reply/suggestions/tool_results/usage}.
- _format_ai_progress renders the live checklist.
- _edit_active is best-effort (never raises).
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("DATABASE_URL", "postgresql://x")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── _format_ai_progress (pure) ──────────────────────────────────────


class TestProgressRender:
    def test_empty(self):
        from interfaces.bot.ai import _format_ai_progress
        out = _format_ai_progress([])
        assert "Working on it" in out

    def test_checklist_marks_done_and_current(self):
        from interfaces.bot.ai import _format_ai_progress
        out = _format_ai_progress(["Checking fuel", "Checking maintenance"])
        # First is done (✓), last is current (⚙️ …)
        assert "✓ Checking fuel" in out
        assert "⚙️ Checking maintenance…" in out

    def test_escapes_special_chars_in_labels(self):
        from interfaces.bot.ai import _format_ai_progress
        # escape_html preserves allowed formatting tags (<b>, <i>, …) by
        # design but escapes bare specials like & — proving the label is
        # run through the escaper before going into HTML parse_mode.
        out = _format_ai_progress(["fuel & oil"])
        assert "fuel &amp; oil" in out


# ── ask_agent_stream contract (what the bot loop consumes) ──────────


class TestStreamContract:
    async def test_yields_tools_then_done(self, monkeypatch):
        import capabilities.ai.intelligence as intel

        async def fake_ask_agent(
            question, vehicle_context, samsara_client,
            *, event_callback=None, **kw,
        ):
            if event_callback:
                await event_callback({"type": "tool", "name": "get_fuel", "label": "Checking fuel"})
                await event_callback({"type": "tool", "name": "get_maint", "label": "Checking maintenance"})
            return {
                "text": "Everything looks fine.",
                "tool_results": [{"data": {}}, {"data": {}}],
                "usage": {"total_tokens": 7},
            }

        monkeypatch.setattr(intel, "ask_agent", fake_ask_agent)

        events = []
        async for ev in intel.ask_agent_stream("how is fuel?", {}, None):
            events.append(ev)

        types = [e["type"] for e in events]
        assert types == ["tool", "tool", "done"]
        assert events[0]["label"] == "Checking fuel"
        done = events[-1]
        assert done["reply"]                       # parsed reply present
        assert "suggestions" in done               # parser ran
        assert len(done["tool_results"]) == 2
        assert done["usage"]["total_tokens"] == 7

    async def test_error_event_on_failure(self, monkeypatch):
        import capabilities.ai.intelligence as intel

        async def boom(question, vehicle_context, samsara_client, *, event_callback=None, **kw):
            raise RuntimeError("model exploded")

        monkeypatch.setattr(intel, "ask_agent", boom)

        events = [ev async for ev in intel.ask_agent_stream("q", {}, None)]
        assert events[-1]["type"] == "error"
        assert "model exploded" in events[-1]["message"]


# ── _edit_active is best-effort (never raises) ──────────────────────


class TestEditActiveBestEffort:
    async def test_swallows_edit_errors(self):
        from interfaces.bot.helpers import _edit_active

        class _FakeBot:
            async def edit_message_text(self, **kw):
                raise RuntimeError("rate limited")

        class _FakeMsg:
            message_id = 5
            class chat:  # noqa: N801
                id = 1

        class _FakeQuery:
            message = _FakeMsg()

        class _FakeUpdate:
            callback_query = _FakeQuery()

        class _FakeCtx:
            bot = _FakeBot()
            user_data: dict = {}

        # Must NOT raise even though edit_message_text blows up.
        await _edit_active(_FakeUpdate(), _FakeCtx(), "progress")
