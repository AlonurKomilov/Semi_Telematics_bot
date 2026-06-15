"""Tests for per-photo PTI AI vision review.

Covers the pure helpers (prompt build + response parse) directly, and
``review_photo`` with the Gemini call stubbed — no network, no Vertex
credentials needed.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from features.inspections import ai_review


# ── Response parsing ────────────────────────────────────────────────


class TestParseResponse:
    def test_parses_well_formed_block(self):
        text = (
            "VERDICT: likely_defect\n"
            "CONFIDENCE: high\n"
            "SUMMARY: The mudflap is torn and hanging loose."
        )
        r = ai_review._parse_response(text)
        assert r["verdict"] == "likely_defect"
        assert r["confidence"] == "high"
        assert "torn" in r["summary"]

    def test_tolerates_trailing_note_after_verdict(self):
        text = "VERDICT: ok — looks serviceable\nCONFIDENCE: medium\nSUMMARY: Fine."
        r = ai_review._parse_response(text)
        assert r["verdict"] == "ok"
        assert r["confidence"] == "medium"

    def test_unknown_verdict_falls_back_to_unclear(self):
        text = "VERDICT: banana\nCONFIDENCE: low\nSUMMARY: ?"
        r = ai_review._parse_response(text)
        assert r["verdict"] == "unclear"

    def test_freeform_response_still_yields_summary(self):
        # Model ignored the format entirely.
        text = "The tire looks a bit worn but probably OK."
        r = ai_review._parse_response(text)
        assert r["verdict"] == "unclear"
        assert r["summary"].startswith("The tire")

    def test_empty_response(self):
        r = ai_review._parse_response("")
        assert r["verdict"] == "unclear"
        assert r["summary"]


# ── Prompt building ─────────────────────────────────────────────────


class TestBuildPrompt:
    def test_includes_component_label(self):
        p = ai_review._build_prompt("Mudflaps", "exterior", "ok")
        assert "Mudflaps" in p
        assert "exterior" in p
        # Reported status echoed so the model can weigh agreement.
        assert "ok" in p

    def test_omits_pending_status(self):
        p = ai_review._build_prompt("Tires", "", "pending")
        # 'pending' is not a real driver assessment — shouldn't be
        # framed as one in the prompt.
        assert "marked this item as 'pending'" not in p


# ── review_photo (stubbed model) ────────────────────────────────────


class TestReviewPhoto:
    async def test_happy_path(self, monkeypatch):
        async def _fake_vision(prompt, image_bytes, system="", account_id=None):
            assert image_bytes == b"\x89PNG-bytes"
            return "VERDICT: ok\nCONFIDENCE: high\nSUMMARY: Looks serviceable."

        monkeypatch.setattr(
            "capabilities.ai.vision.generate_with_vision", _fake_vision,
        )
        out = await ai_review.review_photo(
            b"\x89PNG-bytes",
            item_label="Mudflaps",
            category="exterior",
            reported_status="ok",
            account_id=1,
        )
        assert out["verdict"] == "ok"
        assert out["confidence"] == "high"
        assert "model" in out

    async def test_empty_image_raises(self):
        with pytest.raises(ValueError):
            await ai_review.review_photo(b"", item_label="Tires")

    def test_serialize_roundtrips(self):
        import json
        blob = ai_review.serialize({"verdict": "ok", "summary": "fine"})
        assert json.loads(blob)["verdict"] == "ok"
