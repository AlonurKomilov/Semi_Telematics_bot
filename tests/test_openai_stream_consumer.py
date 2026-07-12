"""``_consume_openai_stream`` — SSE assembly for the OpenAI-compat agent.

Streaming is what makes the live process timeline REAL for the Reasoning
tier: thinking deltas reach the chat as they're generated instead of a
canned phrase for the whole 30-60s round.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import json

from capabilities.ai.intelligence import _consume_openai_stream


def _sse(*objs, done=True):
    lines = [f"data: {json.dumps(o)}" for o in objs]
    if done:
        lines.append("data: [DONE]")
    return lines


def _delta(d, **chunk_extra):
    return {"choices": [{"delta": d}], **chunk_extra}


class TestConsume:
    def test_reasoning_content_deltas_stream_live_and_assemble(self):
        seen: list[str] = []
        msg, usage = _consume_openai_stream(_sse(
            _delta({"reasoning_content": "step one… "}),
            _delta({"reasoning_content": "step two."}),
            _delta({"content": "The answer."}),
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                      "total_tokens": 15}},
        ), on_thinking=seen.append)

        assert seen == ["step one… ", "step two."]
        assert msg["reasoning_content"] == "step one… step two."
        assert msg["content"] == "The answer."
        assert usage["total_tokens"] == 15

    def test_inline_think_content_routes_to_thinking_until_closed(self):
        seen: list[str] = []
        msg, _ = _consume_openai_stream(_sse(
            _delta({"content": "<think>odometer "}),
            _delta({"content": "math</think>"}),
            _delta({"content": "Truck 228 overdue."}),
        ), on_thinking=seen.append)

        assert seen == ["odometer ", "math"]          # tags stripped, live
        # Raw content preserved verbatim — the post-parse strip handles tags.
        assert msg["content"] == "<think>odometer math</think>Truck 228 overdue."

    def test_plain_answer_content_is_not_emitted_as_thinking(self):
        seen: list[str] = []
        msg, _ = _consume_openai_stream(_sse(
            _delta({"content": "Just an answer, "}),
            _delta({"content": "no thinking."}),
        ), on_thinking=seen.append)

        assert seen == []
        assert msg["content"] == "Just an answer, no thinking."

    def test_fragmented_tool_calls_merge_by_index(self):
        msg, _ = _consume_openai_stream(_sse(
            _delta({"tool_calls": [{"index": 0, "id": "call_1",
                                    "function": {"name": "get_maintenance_summary",
                                                 "arguments": ""}}]}),
            _delta({"tool_calls": [{"index": 0,
                                    "function": {"arguments": '{"vehi'}}]}),
            _delta({"tool_calls": [{"index": 0,
                                    "function": {"arguments": 'cle": "228"}'}}]}),
            _delta({"tool_calls": [{"index": 1, "id": "call_2",
                                    "function": {"name": "get_vehicle_info",
                                                 "arguments": "{}"}}]}),
        ))

        tcs = msg["tool_calls"]
        assert len(tcs) == 2
        assert tcs[0]["id"] == "call_1"
        assert json.loads(tcs[0]["function"]["arguments"]) == {"vehicle": "228"}
        assert tcs[1]["function"]["name"] == "get_vehicle_info"

    def test_r1_special_token_garbage_in_arguments_is_cleaned(self):
        """The exact production payload: R1 leaks raw special tokens into
        the streamed arguments tail; replaying them 400s the next round."""
        msg, _ = _consume_openai_stream(_sse(
            _delta({"tool_calls": [{"index": 0, "id": "call_x",
                                    "function": {"name": "get_maintenance_summary",
                                                 "arguments": "{}\n```<｜tool▁call▁end｜><｜tool▁calls▁end｜>"}}]}),
        ))
        assert msg["tool_calls"][0]["function"]["arguments"] == "{}"

    def test_garbage_with_real_args_keeps_the_object(self):
        msg, _ = _consume_openai_stream(_sse(
            _delta({"tool_calls": [{"index": 0, "id": "c",
                                    "function": {"name": "t",
                                                 "arguments": 'junk {"vehicle": "228"} <｜end｜>'}}]}),
        ))
        assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"vehicle": "228"}

    def test_garbage_lines_and_bytes_are_tolerated(self):
        msg, _ = _consume_openai_stream([
            b"",                                  # keepalive
            b": comment",
            b'data: {"choices": [{"delta": {"content": "ok"}}]}',
            "data: not-json",
        ])
        assert msg["content"] == "ok"
