"""Agent tool-call loop depth is no longer a hardcoded ``range(4)`` / ``range(3)``
— it resolves from the model's registry (``max_tool_rounds``) with a per-provider
default.  Foundation for a future reasoning/autonomous tier that needs deeper
loops; behaviour-preserving today (no model sets the field, defaults unchanged).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.ai.intelligence import (
    _resolve_tool_rounds,
    _DEFAULT_TOOL_ROUNDS_ANTHROPIC,
    _DEFAULT_TOOL_ROUNDS_GEMINI,
)


def test_defaults_preserve_current_behavior():
    # The constants must stay at the values the loops were hardcoded to.
    assert _DEFAULT_TOOL_ROUNDS_ANTHROPIC == 4
    assert _DEFAULT_TOOL_ROUNDS_GEMINI == 3
    # No override → provider default is used unchanged.
    assert _resolve_tool_rounds({}, _DEFAULT_TOOL_ROUNDS_ANTHROPIC) == 4
    assert _resolve_tool_rounds({}, _DEFAULT_TOOL_ROUNDS_GEMINI) == 3


def test_registry_override_is_honored():
    assert _resolve_tool_rounds({"max_tool_rounds": 12}, 4) == 12
    assert _resolve_tool_rounds({"max_tool_rounds": "8"}, 3) == 8  # coerced


def test_floored_at_one_so_initial_call_always_runs():
    # A misconfigured 0 / negative can't disable the model call entirely.
    assert _resolve_tool_rounds({"max_tool_rounds": 0}, 4) == 1
    assert _resolve_tool_rounds({"max_tool_rounds": -5}, 4) == 1


def test_non_int_value_falls_back_to_default():
    assert _resolve_tool_rounds({"max_tool_rounds": "lots"}, 4) == 4
    assert _resolve_tool_rounds({"max_tool_rounds": None}, 3) == 3
