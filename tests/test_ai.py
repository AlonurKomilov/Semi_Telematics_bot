"""Tests for the AI integration — client, fleet assistant, keyboards."""

import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

# No real credentials during tests
os.environ["GOOGLE_CLOUD_PROJECT"] = ""
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = ""
os.environ.setdefault("ENCRYPTION_KEY", "")


# ══════════════════════════════════════════════════════════════════
# AI CLIENT MODULE
# ══════════════════════════════════════════════════════════════════

class TestAIClientConfig:
    """ai configuration checks."""

    def test_is_configured_false_when_no_key(self):
        import ai
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "", "GOOGLE_APPLICATION_CREDENTIALS": ""}):
            assert ai.is_configured() is False

    def test_is_configured_true_when_key_set(self):
        import ai
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "my-project", "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/sa.json"}):
            assert ai.is_configured() is True

    def test_ensure_model_raises_without_project(self):
        import ai
        import ai.models
        ai.models._model = None  # Reset
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "", "GOOGLE_APPLICATION_CREDENTIALS": ""}):
            with pytest.raises(RuntimeError, match="GOOGLE_CLOUD_PROJECT"):
                ai._ensure_model()

    def test_system_prompts_not_empty(self):
        import ai
        assert len(ai.FLEET_ASSISTANT_SYSTEM) > 100
        assert len(ai.FAULT_DIAGNOSIS_SYSTEM) > 100
        assert len(ai.FLEET_SUMMARY_SYSTEM) > 100

    def test_system_prompts_mention_html(self):
        """Prompts should instruct the model to use HTML formatting."""
        import ai
        assert "HTML" in ai.FLEET_ASSISTANT_SYSTEM
        assert "HTML" in ai.FAULT_DIAGNOSIS_SYSTEM
        assert "HTML" in ai.FLEET_SUMMARY_SYSTEM


class TestAIClientGenerate:
    """ai.generate with mocked Gemini SDK."""

    @pytest.mark.asyncio
    async def test_generate_calls_model(self):
        import ai

        mock_candidate = MagicMock()
        mock_candidate.finish_reason = MagicMock()
        mock_candidate.finish_reason.name = "STOP"

        mock_response = MagicMock()
        mock_response.text = "Test response from AI"
        mock_response.candidates = [mock_candidate]
        mock_response.prompt_feedback = None

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        import ai.models
        ai.models._model = mock_model

        result = await ai.generate("test question")
        assert result == "Test response from AI"
        mock_model.generate_content.assert_called_once()

        # Reset
        ai.models._model = None

    @pytest.mark.asyncio
    async def test_generate_includes_context_data(self):
        import ai

        mock_candidate = MagicMock()
        mock_candidate.finish_reason = MagicMock()
        mock_candidate.finish_reason.name = "STOP"

        mock_response = MagicMock()
        mock_response.text = "Answer with context"
        mock_response.candidates = [mock_candidate]
        mock_response.prompt_feedback = None

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        import ai.models
        ai.models._model = mock_model

        result = await ai.generate(
            "test", context_data={"vehicles": 5}
        )
        assert result == "Answer with context"

        # Check the prompt included the context
        call_args = mock_model.generate_content.call_args[0][0]
        assert "vehicles" in call_args

        ai.models._model = None

    @pytest.mark.asyncio
    async def test_generate_strips_markdown(self):
        import ai

        mock_candidate = MagicMock()
        mock_candidate.finish_reason = MagicMock()
        mock_candidate.finish_reason.name = "STOP"

        mock_response = MagicMock()
        mock_response.text = "**Bold** and ## Header"
        mock_response.candidates = [mock_candidate]
        mock_response.prompt_feedback = None

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        import ai.models
        ai.models._model = mock_model

        result = await ai.generate("test")
        assert "**" not in result
        assert "##" not in result

        ai.models._model = None

    @pytest.mark.asyncio
    async def test_generate_truncates_large_data(self):
        import ai

        mock_candidate = MagicMock()
        mock_candidate.finish_reason = MagicMock()
        mock_candidate.finish_reason.name = "STOP"

        mock_response = MagicMock()
        mock_response.text = "OK"
        mock_response.candidates = [mock_candidate]
        mock_response.prompt_feedback = None

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        import ai.models
        ai.models._model = mock_model

        large_data = "x" * 50000
        await ai.generate("test", context_data=large_data)

        call_args = mock_model.generate_content.call_args[0][0]
        assert "truncated" in call_args

        ai.models._model = None

    @pytest.mark.asyncio
    async def test_generate_handles_safety_block(self):
        """Should return a friendly message when response is blocked."""
        import ai

        # Clear cache to avoid hits from previous tests
        ai._response_cache.clear()

        mock_response = MagicMock()
        mock_response.candidates = []
        mock_response.prompt_feedback = MagicMock()
        mock_response.prompt_feedback.block_reason = "SAFETY"

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        import ai.models
        ai.models._model = mock_model

        result = await ai.generate("test safety block")
        assert "couldn't generate" in result.lower()

        ai.models._model = None


class TestAIDiagnose:
    """ai.diagnose_faults."""

    @pytest.mark.asyncio
    async def test_diagnose_formats_dtcs(self):
        import ai

        mock_candidate = MagicMock()
        mock_candidate.finish_reason = MagicMock()
        mock_candidate.finish_reason.name = "STOP"

        mock_response = MagicMock()
        mock_response.text = "Engine coolant sensor failure"
        mock_response.candidates = [mock_candidate]
        mock_response.prompt_feedback = None

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        import ai.models
        ai.models._model = mock_model

        dtcs = [
            {"spnId": 110, "fmiId": 4, "spnDescription": "Engine Coolant Temp",
             "fmiDescription": "Voltage below normal", "sourceAddressName": "ECU"},
        ]
        result = await ai.diagnose_faults("101", dtcs, {"stopIsOn": True})
        assert "coolant" in result.lower()

        # Verify context included truck name and DTC details
        call_args = mock_model.generate_content.call_args[0][0]
        assert "101" in call_args
        assert "110" in call_args

        ai.models._model = None

    @pytest.mark.asyncio
    async def test_diagnose_caps_dtc_count(self):
        """Should cap at 10 DTCs to stay within token limits."""
        import ai

        mock_candidate = MagicMock()
        mock_candidate.finish_reason = MagicMock()
        mock_candidate.finish_reason.name = "STOP"

        mock_response = MagicMock()
        mock_response.text = "Multiple faults"
        mock_response.candidates = [mock_candidate]
        mock_response.prompt_feedback = None

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        import ai.models
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

class TestAIKeyboard:
    """AI buttons in main menu and truck detail keyboards."""

    def _all_callbacks(self, markup):
        return [b.callback_data for r in markup.inline_keyboard for b in r if b.callback_data]

    def _all_labels(self, markup):
        return [b.text for r in markup.inline_keyboard for b in r]

    def test_main_menu_shows_ai_when_configured(self):
        from database import Role
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "my-project", "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/sa.json"}):
            from bot.keyboards import main_menu_kb
            kb = main_menu_kb(Role.OWNER, ["CO1"])
            callbacks = self._all_callbacks(kb)
            assert "cmd_ai" in callbacks

    def test_main_menu_hides_ai_when_not_configured(self):
        from database import Role
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "", "GOOGLE_APPLICATION_CREDENTIALS": ""}):
            from bot.keyboards import main_menu_kb
            kb = main_menu_kb(Role.OWNER, ["CO1"])
            callbacks = self._all_callbacks(kb)
            assert "cmd_ai" not in callbacks

    def test_truck_kb_shows_ai_diagnose_when_configured(self):
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "my-project", "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/sa.json"}):
            from bot.keyboards import truck_kb
            kb = truck_kb(truck_name="101", company="CO1", show_faults=True)
            callbacks = self._all_callbacks(kb)
            assert "ai_diag_CO1_101" in callbacks
            labels = self._all_labels(kb)
            assert any("AI Diagnose" in l for l in labels)

    def test_truck_kb_hides_ai_diagnose_when_not_configured(self):
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "", "GOOGLE_APPLICATION_CREDENTIALS": ""}):
            from bot.keyboards import truck_kb
            kb = truck_kb(truck_name="101", company="CO1", show_faults=True)
            callbacks = self._all_callbacks(kb)
            assert not any("ai_diag" in c for c in callbacks)

    def test_ai_menu_kb(self):
        from bot.ai import _ai_menu_kb
        kb = _ai_menu_kb()
        callbacks = self._all_callbacks(kb)
        assert "ai_chat" in callbacks
        assert "ai_summary" in callbacks
        assert "cmd_menu" in callbacks

    def test_ai_menu_kb_owner_sees_model_button(self):
        from bot.ai import _ai_menu_kb
        from database import Role
        kb = _ai_menu_kb(user_role=Role.OWNER)
        callbacks = self._all_callbacks(kb)
        assert "ai_models_text" in callbacks
        assert "ai_models_vision" in callbacks
        assert "cmd_ai_alerts" in callbacks
        labels = self._all_labels(kb)
        assert any("Text:" in l for l in labels)
        assert any("Vision:" in l for l in labels)

    def test_ai_menu_kb_driver_no_model_button(self):
        from bot.ai import _ai_menu_kb
        from database import Role
        kb = _ai_menu_kb(user_role=Role.DRIVER)
        callbacks = self._all_callbacks(kb)
        assert "ai_models_text" not in callbacks
        assert "ai_models_vision" not in callbacks
        assert "cmd_ai_alerts" in callbacks

    def test_ai_back_kb(self):
        from bot.ai import _ai_back_kb
        kb = _ai_back_kb()
        callbacks = self._all_callbacks(kb)
        assert "cmd_ai" in callbacks
        assert "cmd_menu" in callbacks

    def test_ai_chat_kb(self):
        from bot.ai import _ai_chat_kb
        kb = _ai_chat_kb()
        callbacks = self._all_callbacks(kb)
        assert "ai_newchat" in callbacks
        assert "cmd_menu" in callbacks

    def test_build_chat_kb_with_suggestions(self):
        from bot.ai import _build_chat_kb
        kb = _build_chat_kb(["Which truck needs fuel?", "Show faults"])
        callbacks = self._all_callbacks(kb)
        assert any(c.startswith("ai_sug_") for c in callbacks)
        assert "cmd_ai" in callbacks
        assert "cmd_menu" in callbacks
        labels = self._all_labels(kb)
        assert any("Which truck needs fuel?" in l for l in labels)

    def test_build_chat_kb_no_suggestions(self):
        from bot.ai import _build_chat_kb
        kb = _build_chat_kb()
        callbacks = self._all_callbacks(kb)
        assert not any(c.startswith("ai_sug_") for c in callbacks)
        assert "cmd_ai" in callbacks

    def test_parse_suggestions(self):
        from bot.ai import _parse_suggestions
        text = "Here are 3 trucks.\n>> Check truck 101\n>> Show fuel levels"
        clean, suggestions = _parse_suggestions(text)
        assert ">> Check truck 101" not in clean
        assert len(suggestions) == 2
        assert suggestions[0] == "Check truck 101"
        assert suggestions[1] == "Show fuel levels"

    def test_parse_suggestions_no_suggestions(self):
        from bot.ai import _parse_suggestions
        text = "Here are 3 trucks with low fuel."
        clean, suggestions = _parse_suggestions(text)
        assert clean == text
        assert suggestions == []


# ══════════════════════════════════════════════════════════════════
# FLEET SNAPSHOT BUILDER
# ══════════════════════════════════════════════════════════════════

class TestFleetSnapshot:
    """_gather_fleet_snapshot data shape."""

    @pytest.mark.asyncio
    async def test_snapshot_structure(self):
        from bot.ai import _gather_fleet_snapshot

        mock_fleet = [
            {
                "id": "v1", "name": "101", "_org": "CO1",
                "location": {"reverseGeo": {"formattedLocation": "Chicago, IL"}},
                "fuel": {"value": 45},
                "_dtcs": [{"spnId": 110, "spnDescription": "Coolant Temp",
                           "fmiDescription": "Normal"}],
                "_lights": {},
            },
            {
                "id": "v2", "name": "202", "_org": "CO1",
                "location": {}, "fuel": {"value": 15},
                "_dtcs": [], "_lights": {},
            },
        ]

        mock_health = [
            {
                "id": "v2", "name": "202", "_org": "CO1",
                "_health_alerts": ["low_battery"],
                "_health": {"battery_v": 11.5},
            },
        ]

        mock_client = AsyncMock()
        mock_client.get_fleet_overview.return_value = mock_fleet
        mock_client.get_vehicle_health.return_value = mock_health
        mock_client.get_driver_efficiency.return_value = []
        mock_client.get_fleet_weather.return_value = []

        with patch("bot.ai.get_client", return_value=mock_client):
            snapshot = await _gather_fleet_snapshot(account_id=1)

        assert snapshot["total_vehicles"] == 2
        assert snapshot["faulted_count"] == 1
        assert snapshot["low_fuel_count"] == 1
        assert len(snapshot["vehicles"]) == 2
        assert snapshot["vehicles"][0]["name"] == "101"
        assert snapshot["vehicles"][0]["fuel_pct"] == 45
        assert len(snapshot["health_alerts"]) == 1

    @pytest.mark.asyncio
    async def test_snapshot_filters_by_truck(self):
        from bot.ai import _gather_fleet_snapshot

        mock_fleet = [
            {"id": "v1", "name": "101", "_org": "CO1",
             "location": {}, "fuel": {"value": 80}, "_dtcs": [], "_lights": {}},
            {"id": "v2", "name": "202", "_org": "CO1",
             "location": {}, "fuel": {"value": 50}, "_dtcs": [], "_lights": {}},
        ]

        mock_client = AsyncMock()
        mock_client.get_fleet_overview.return_value = mock_fleet
        mock_client.get_vehicle_health.return_value = []
        mock_client.get_driver_efficiency.return_value = []
        mock_client.get_fleet_weather.return_value = []

        with patch("bot.ai.get_client", return_value=mock_client):
            snapshot = await _gather_fleet_snapshot(
                account_id=1, truck_num="101"
            )

        assert snapshot["total_vehicles"] == 1
        assert snapshot["vehicles"][0]["name"] == "101"


# ══════════════════════════════════════════════════════════════════
# MODEL REGISTRY
# ══════════════════════════════════════════════════════════════════

class TestModelRegistry:
    """Model registry and switching logic."""

    def test_registry_has_models(self):
        import ai
        assert len(ai.MODEL_REGISTRY) >= 2

    def test_all_models_have_required_fields(self):
        import ai
        for name, info in ai.MODEL_REGISTRY.items():
            assert "display" in info, f"{name} missing display"
            assert "description" in info, f"{name} missing description"
            assert "category" in info, f"{name} missing category"
            assert "locations" in info, f"{name} missing locations"
            assert len(info["locations"]) > 0, f"{name} has no locations"
            assert "max_output_tokens" in info, f"{name} missing max_output_tokens"

    def test_all_locations_are_valid_gcp_regions(self):
        import ai
        for name, info in ai.MODEL_REGISTRY.items():
            for loc in info["locations"]:
                # GCP regions follow pattern: area-direction-number
                # "global" is valid for MaaS endpoints
                assert "-" in loc or loc == "global", f"{name}: invalid location {loc}"

    def test_default_model_in_registry(self):
        import ai
        assert ai.DEFAULT_MODEL in ai.MODEL_REGISTRY

    def test_default_location_available_for_default_model(self):
        import ai
        info = ai.MODEL_REGISTRY[ai.DEFAULT_MODEL]
        assert ai.DEFAULT_LOCATION in info["locations"]

    def test_get_model_info_returns_dict(self):
        import ai
        info = ai.get_model_info("gemini-2.5-flash")
        assert info is not None
        assert info["display"] == "Gemini 2.5 Flash"

    def test_get_model_info_unknown_returns_none(self):
        import ai
        assert ai.get_model_info("nonexistent-model") is None

    def test_get_available_models_sorted(self):
        import ai
        models = ai.get_available_models()
        assert len(models) >= 2
        # Should be sorted by category
        cats = [m["category"] for m in models]
        assert cats == sorted(cats)

    def test_get_locations_for_model(self):
        import ai
        locs = ai.get_locations_for_model("gemini-2.5-flash")
        assert "us-central1" in locs

    def test_get_locations_for_unknown_model(self):
        import ai
        locs = ai.get_locations_for_model("nonexistent")
        assert locs == [ai.DEFAULT_LOCATION]

    def test_switch_model_validates(self):
        import ai
        with pytest.raises(ValueError, match="Unknown model"):
            ai.switch_model("fake-model-xyz")

    def test_switch_model_validates_location(self):
        import ai
        with pytest.raises(ValueError, match="not available"):
            ai.switch_model("gemini-2.5-flash", "fake-location-1")
