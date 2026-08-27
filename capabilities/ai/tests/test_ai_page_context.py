"""Copilot page context — `_render_page_context` folds "what the user is
viewing" into the prompt so "this truck" / "these rows" resolve.

Contract: it is a PROMPT HINT built purely from the request body — it never
touches tool authorization or vehicle scope (those come from the JWT).
These tests pin the rendering shape and the no-op / empty cases.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.ai.intelligence import _render_page_context, _build_agent_user_prompt


class TestRenderPageContext:
    def test_none_and_missing_yield_empty(self):
        assert _render_page_context(None) == ""
        assert _render_page_context({}) == ""
        assert _render_page_context({"page_context": None}) == ""
        assert _render_page_context({"page_context": {"label": "x"}}) == ""  # no feature

    def test_full_descriptor_renders_all_parts(self):
        out = _render_page_context({"page_context": {
            "feature": "maintenance",
            "label": "Maintenance",
            "focus": {"kind": "maintenance task", "id": 42, "label": "Truck 228"},
            "filters": {"company": "PTG", "status": "all", "empty": ""},
            "selectedIds": [1, 2, 3],
        }})
        assert "currently viewing" in out
        assert "Maintenance" in out
        assert "Truck 228 (maintenance task)" in out
        assert "PTG" in out                 # active filter kept
        assert "status" not in out          # 'all' dropped as non-informative
        assert "empty" not in out           # '' dropped
        assert "3 item(s)" in out
        assert '"this"' in out              # the pronoun-resolution instruction

    def test_minimal_descriptor_feature_only(self):
        out = _render_page_context({"page_context": {"feature": "loads", "label": "Loads"}})
        assert "Loads" in out
        assert "Focused on" not in out
        assert "Selected" not in out

    def test_folds_into_agent_prompt_after_profile(self):
        prompt = _build_agent_user_prompt(
            "why is this overdue?",
            None,
            {"name": "Al", "role": "owner",
             "page_context": {"feature": "maintenance", "label": "Maintenance",
                              "focus": {"kind": "task", "id": 9, "label": "Truck 228"}}},
            None,
        )
        # Profile then page-context then the question, in order.
        assert prompt.index("User profile") < prompt.index("currently viewing")
        assert prompt.index("currently viewing") < prompt.index("why is this overdue?")
        assert "Truck 228" in prompt

    def test_no_page_context_leaves_prompt_unchanged_shape(self):
        prompt = _build_agent_user_prompt(
            "status?", None, {"name": "Al", "role": "owner"}, None,
        )
        assert "currently viewing" not in prompt
        assert "User profile" in prompt
