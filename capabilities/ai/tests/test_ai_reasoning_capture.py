"""Thinking/Reasoning honesty — the tier label must match what the model
actually did.

Three pieces pinned here:
  1. ``_apply_anthropic_thinking`` — the registry-declared budget turns on
     Claude extended thinking WITH the API's hard constraints (temperature=1,
     no top_p, max_tokens > budget).  Without it a Thinking-tier Claude ran
     as a plain model.
  2. ``_parse_anthropic_reply`` — block-type-aware: with thinking on,
     ``content[0]`` is a thinking block and the old ``content[0]["text"]``
     read returned an EMPTY answer.
  3. ``_parse_openai_compat_reply`` — captures the chain-of-thought
     (``reasoning_content`` field or inline ``<think>`` tags) instead of
     discarding it, and strips the tags from the answer.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.ai.generation import (
    _apply_anthropic_thinking,
    _parse_anthropic_reply,
    _parse_openai_compat_reply,
)


class TestAnthropicThinkingBody:
    def _base(self):
        return {
            "anthropic_version": "vertex-2023-10-16",
            "system": "s",
            "messages": [],
            "max_tokens": 8192,
            "temperature": 0.3,
            "top_p": 0.8,
        }

    def test_budget_enables_thinking_with_api_constraints(self):
        body = _apply_anthropic_thinking(self._base(), 4096)
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 4096}
        assert body["temperature"] == 1          # API requires 1 with thinking
        assert "top_p" not in body               # API rejects top_p with thinking
        assert body["max_tokens"] >= 4096 + 2048  # budget carved out of max_tokens

    def test_small_max_tokens_is_raised_above_budget(self):
        body = self._base()
        body["max_tokens"] = 1024
        out = _apply_anthropic_thinking(body, 4096)
        assert out["max_tokens"] == 4096 + 2048

    def test_no_budget_is_a_noop(self):
        body = _apply_anthropic_thinking(self._base(), None)
        assert "thinking" not in body
        assert body["temperature"] == 0.3
        assert body["top_p"] == 0.8


class TestAnthropicReplyParse:
    def test_thinking_plus_text_blocks(self):
        data = {
            "content": [
                {"type": "thinking", "thinking": "check the odometer delta"},
                {"type": "text", "text": "Truck 228 is overdue."},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
        text, usage, reasoning = _parse_anthropic_reply(data)
        assert text == "Truck 228 is overdue."      # NOT the empty thinking block
        assert reasoning == "check the odometer delta"
        assert usage == {"prompt_tokens": 10, "reply_tokens": 20,
                         "thinking_tokens": 0, "total_tokens": 30}

    def test_plain_text_only_still_works(self):
        data = {"content": [{"type": "text", "text": "hi"}]}
        text, usage, reasoning = _parse_anthropic_reply(data)
        assert (text, usage, reasoning) == ("hi", None, "")


class TestOpenAICompatReplyParse:
    def _resp(self, message):
        return {"choices": [{"message": message}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7,
                          "completion_tokens_details": {"reasoning_tokens": 30},
                          "total_tokens": 42}}

    def test_reasoning_content_field(self):
        text, usage, reasoning = _parse_openai_compat_reply(
            self._resp({"content": "answer", "reasoning_content": "step 1… step 2…"}),
        )
        assert text == "answer"
        assert reasoning == "step 1… step 2…"
        assert usage["thinking_tokens"] == 30

    def test_inline_think_tags_are_captured_and_stripped(self):
        text, _, reasoning = _parse_openai_compat_reply(
            self._resp({"content": "<think>odometer math here</think>\nTruck 228 overdue."}),
        )
        assert text == "Truck 228 overdue."
        assert "<think>" not in text
        assert reasoning == "odometer math here"

    def test_both_sources_concatenate(self):
        text, _, reasoning = _parse_openai_compat_reply(
            self._resp({"content": "<think>inline</think>ans",
                        "reasoning_content": "field"}),
        )
        assert text == "ans"
        assert reasoning == "field\n\ninline"

    def test_no_reasoning_is_empty_not_none(self):
        text, _, reasoning = _parse_openai_compat_reply(
            self._resp({"content": "plain"}),
        )
        assert (text, reasoning) == ("plain", "")
