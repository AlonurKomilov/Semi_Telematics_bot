"""The AI client itself — config, generation, diagnosis, model registry.

Split from tests/test_ai.py, which held two unrelated subjects under one
"AI integration" heading: roughly 300 lines asserting capabilities.ai
and 245 asserting interfaces.bot.ai. Neither was incidental to the
other, so no single owner could be picked — the bot half is a TRANSPORT
question (keyboards, snapshot formatting), this half is the client.
"""

import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

# No real credentials during tests
os.environ["GOOGLE_CLOUD_PROJECT"] = ""
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = ""
os.environ.setdefault("ENCRYPTION_KEY", "")


class TestAIClientConfig:
    """ai configuration checks."""

    def test_is_configured_false_when_no_key(self):
        import capabilities.ai as ai
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "", "GOOGLE_APPLICATION_CREDENTIALS": ""}):
            assert ai.is_configured() is False

    def test_is_configured_true_when_key_set(self):
        import capabilities.ai as ai
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "my-project", "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/sa.json"}):
            assert ai.is_configured() is True

    def test_ensure_model_raises_without_project(self):
        import capabilities.ai as ai
        ai.models._model = None  # Reset
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "", "GOOGLE_APPLICATION_CREDENTIALS": ""}):
            with pytest.raises(RuntimeError, match="GOOGLE_CLOUD_PROJECT"):
                ai._ensure_model()

    def test_system_prompts_not_empty(self):
        import capabilities.ai as ai
        assert len(ai.ASSISTANT_SYSTEM) > 100
        assert len(ai.FAULT_DIAGNOSIS_SYSTEM) > 100
        assert len(ai.SUMMARY_SYSTEM) > 100

    def test_system_prompts_mention_html(self):
        """Prompts should instruct the model to use HTML formatting."""
        import capabilities.ai as ai
        assert "HTML" in ai.ASSISTANT_SYSTEM
        assert "HTML" in ai.FAULT_DIAGNOSIS_SYSTEM
        assert "HTML" in ai.SUMMARY_SYSTEM


class TestAIClientGenerate:
    """ai.generate with mocked Gemini SDK."""

    @pytest.mark.asyncio
    async def test_generate_calls_model(self):
        import capabilities.ai as ai

        mock_candidate = MagicMock()
        mock_candidate.finish_reason = MagicMock()
        mock_candidate.finish_reason.name = "STOP"

        mock_response = MagicMock()
        mock_response.text = "Test response from AI"
        mock_response.candidates = [mock_candidate]
        mock_response.prompt_feedback = None

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        ai.models._model = mock_model

        text, usage = await ai.generate("test question")
        assert text == "Test response from AI"
        mock_model.generate_content.assert_called_once()

        # Reset
        ai.models._model = None

    @pytest.mark.asyncio
    async def test_generate_includes_context_data(self):
        import capabilities.ai as ai

        mock_candidate = MagicMock()
        mock_candidate.finish_reason = MagicMock()
        mock_candidate.finish_reason.name = "STOP"

        mock_response = MagicMock()
        mock_response.text = "Answer with context"
        mock_response.candidates = [mock_candidate]
        mock_response.prompt_feedback = None

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        ai.models._model = mock_model

        text, _usage = await ai.generate(
            "test", context_data={"vehicles": 5}
        )
        assert text == "Answer with context"

        # Check the prompt included the context
        call_args = mock_model.generate_content.call_args[0][0]
        assert "vehicles" in call_args

        ai.models._model = None

    @pytest.mark.asyncio
    async def test_generate_strips_markdown(self):
        import capabilities.ai as ai

        mock_candidate = MagicMock()
        mock_candidate.finish_reason = MagicMock()
        mock_candidate.finish_reason.name = "STOP"

        mock_response = MagicMock()
        mock_response.text = "**Bold** and ## Header"
        mock_response.candidates = [mock_candidate]
        mock_response.prompt_feedback = None

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        ai.models._model = mock_model

        text, _usage = await ai.generate("test")
        assert "**" not in text
        assert "##" not in text

        ai.models._model = None

    @pytest.mark.asyncio
    async def test_generate_truncates_large_data(self):
        import capabilities.ai as ai

        mock_candidate = MagicMock()
        mock_candidate.finish_reason = MagicMock()
        mock_candidate.finish_reason.name = "STOP"

        mock_response = MagicMock()
        mock_response.text = "OK"
        mock_response.candidates = [mock_candidate]
        mock_response.prompt_feedback = None

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        ai.models._model = mock_model

        large_data = "x" * 50000
        await ai.generate("test", context_data=large_data)

        call_args = mock_model.generate_content.call_args[0][0]
        assert "truncated" in call_args

        ai.models._model = None

    @pytest.mark.asyncio
    async def test_generate_handles_safety_block(self):
        """Should return a friendly message when response is blocked."""
        import capabilities.ai as ai

        # Clear cache to avoid hits from previous tests
        ai._response_cache.clear()

        mock_response = MagicMock()
        mock_response.candidates = []
        mock_response.prompt_feedback = MagicMock()
        mock_response.prompt_feedback.block_reason = "SAFETY"

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        ai.models._model = mock_model

        text, _usage = await ai.generate("test safety block")
        assert "couldn't generate" in text.lower()

        ai.models._model = None


class TestAIDiagnose:
    """ai.diagnose_faults."""

    @pytest.mark.asyncio
    async def test_diagnose_formats_dtcs(self):
        import capabilities.ai as ai

        mock_candidate = MagicMock()
        mock_candidate.finish_reason = MagicMock()
        mock_candidate.finish_reason.name = "STOP"

        mock_response = MagicMock()
        mock_response.text = "Engine coolant sensor failure"
        mock_response.candidates = [mock_candidate]
        mock_response.prompt_feedback = None

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        ai.models._model = mock_model

        dtcs = [
            {"spnId": 110, "fmiId": 4, "spnDescription": "Engine Coolant Temp",
             "fmiDescription": "Voltage below normal", "sourceAddressName": "ECU"},
        ]
        text, _usage = await ai.diagnose_faults("101", dtcs, {"stopIsOn": True})
        assert "coolant" in text.lower()

        # Verify context included truck name and DTC details
        call_args = mock_model.generate_content.call_args[0][0]
        assert "101" in call_args
        assert "110" in call_args

        ai.models._model = None

    @pytest.mark.asyncio
    async def test_diagnose_caps_dtc_count(self):
        """Should cap at 10 DTCs to stay within token limits."""
        import capabilities.ai as ai

        mock_candidate = MagicMock()
        mock_candidate.finish_reason = MagicMock()
        mock_candidate.finish_reason.name = "STOP"

        mock_response = MagicMock()
        mock_response.text = "Multiple faults"
        mock_response.candidates = [mock_candidate]
        mock_response.prompt_feedback = None

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        ai.models._model = mock_model

        dtcs = [
            {"spnId": i, "fmiId": 1, "spnDescription": f"Fault {i}"}
            for i in range(20)
        ]
        await ai.diagnose_faults("102", dtcs)

        call_args = mock_model.generate_content.call_args[0][0]
        # Only 10 faults should be in context
        assert "Fault 15" not in call_args

        ai.models._model = None


# ══════════════════════════════════════════════════════════════════
# AI KEYBOARD INTEGRATION
# ══════════════════════════════════════════════════════════════════


class TestModelRegistry:
    """Model registry and switching logic."""

    def test_registry_has_models(self):
        import capabilities.ai as ai
        assert len(ai.MODEL_REGISTRY) >= 2

    def test_all_models_have_required_fields(self):
        import capabilities.ai as ai
        for name, info in ai.MODEL_REGISTRY.items():
            assert "display" in info, f"{name} missing display"
            assert "description" in info, f"{name} missing description"
            assert "category" in info, f"{name} missing category"
            assert "locations" in info, f"{name} missing locations"
            assert len(info["locations"]) > 0, f"{name} has no locations"
            assert "max_output_tokens" in info, f"{name} missing max_output_tokens"

    def test_all_locations_are_valid_gcp_regions(self):
        import capabilities.ai as ai
        for name, info in ai.MODEL_REGISTRY.items():
            for loc in info["locations"]:
                # GCP regions follow pattern: area-direction-number
                # "global" is valid for MaaS endpoints
                assert "-" in loc or loc == "global", f"{name}: invalid location {loc}"

    def test_default_model_in_registry(self):
        import capabilities.ai as ai
        assert ai.DEFAULT_MODEL in ai.MODEL_REGISTRY

    def test_default_location_available_for_default_model(self):
        import capabilities.ai as ai
        info = ai.MODEL_REGISTRY[ai.DEFAULT_MODEL]
        assert ai.DEFAULT_LOCATION in info["locations"]

    def test_get_model_info_returns_dict(self):
        import capabilities.ai as ai
        info = ai.get_model_info("gemini-2.5-flash")
        assert info is not None
        assert info["display"] == "Gemini 2.5 Flash"

    def test_get_model_info_unknown_returns_none(self):
        import capabilities.ai as ai
        assert ai.get_model_info("nonexistent-model") is None

    def test_get_available_models_sorted(self):
        import capabilities.ai as ai
        models = ai.get_available_models()
        assert len(models) >= 2
        # Should be sorted by category
        cats = [m["category"] for m in models]
        assert cats == sorted(cats)

    def test_get_locations_for_model(self):
        import capabilities.ai as ai
        locs = ai.get_locations_for_model("gemini-2.5-flash")
        assert "us-central1" in locs

    def test_get_locations_for_unknown_model(self):
        import capabilities.ai as ai
        locs = ai.get_locations_for_model("nonexistent")
        assert locs == [ai.DEFAULT_LOCATION]

    def test_switch_model_validates(self):
        import capabilities.ai as ai
        with pytest.raises(ValueError, match="Unknown model"):
            ai.switch_model("fake-model-xyz")

    def test_switch_model_validates_location(self):
        import capabilities.ai as ai
        with pytest.raises(ValueError, match="not available"):
            ai.switch_model("gemini-2.5-flash", "fake-location-1")
