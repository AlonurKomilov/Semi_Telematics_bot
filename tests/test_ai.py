"""Tests for the AI integration — client, fleet assistant, keyboards."""

import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

# No real API key during tests
os.environ["GOOGLE_AI_API_KEY"] = ""
os.environ.setdefault("ENCRYPTION_KEY", "")


# ══════════════════════════════════════════════════════════════════
# AI CLIENT MODULE
# ══════════════════════════════════════════════════════════════════

class TestAIClientConfig:
    """ai_client configuration checks."""

    def test_is_configured_false_when_no_key(self):
        import ai_client
        with patch.dict(os.environ, {"GOOGLE_AI_API_KEY": ""}):
            assert ai_client.is_configured() is False

    def test_is_configured_true_when_key_set(self):
        import ai_client
        with patch.dict(os.environ, {"GOOGLE_AI_API_KEY": "test-key-123"}):
            assert ai_client.is_configured() is True

    def test_ensure_model_raises_without_key(self):
        import ai_client
        ai_client._model = None  # Reset
        with patch.dict(os.environ, {"GOOGLE_AI_API_KEY": ""}):
            with pytest.raises(RuntimeError, match="GOOGLE_AI_API_KEY"):
                ai_client._ensure_model()

    def test_system_prompts_not_empty(self):
        import ai_client
        assert len(ai_client.FLEET_ASSISTANT_SYSTEM) > 100
        assert len(ai_client.FAULT_DIAGNOSIS_SYSTEM) > 100
        assert len(ai_client.FLEET_SUMMARY_SYSTEM) > 100

    def test_system_prompts_mention_html(self):
        """Prompts should instruct the model to use HTML formatting."""
        import ai_client
        assert "HTML" in ai_client.FLEET_ASSISTANT_SYSTEM
        assert "HTML" in ai_client.FAULT_DIAGNOSIS_SYSTEM
        assert "HTML" in ai_client.FLEET_SUMMARY_SYSTEM


class TestAIClientGenerate:
    """ai_client.generate with mocked Gemini SDK."""

    @pytest.mark.asyncio
    async def test_generate_calls_model(self):
        import ai_client

        mock_response = MagicMock()
        mock_response.text = "Test response from AI"

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        ai_client._model = mock_model

        result = await ai_client.generate("test question")
        assert result == "Test response from AI"
        mock_model.generate_content.assert_called_once()

        # Reset
        ai_client._model = None

    @pytest.mark.asyncio
    async def test_generate_includes_context_data(self):
        import ai_client

        mock_response = MagicMock()
        mock_response.text = "Answer with context"

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        ai_client._model = mock_model

        result = await ai_client.generate(
            "test", context_data={"vehicles": 5}
        )
        assert result == "Answer with context"

        # Check the prompt included the context
        call_args = mock_model.generate_content.call_args[0][0]
        assert "vehicles" in call_args

        ai_client._model = None

    @pytest.mark.asyncio
    async def test_generate_strips_markdown(self):
        import ai_client

        mock_response = MagicMock()
        mock_response.text = "**Bold** and ## Header"

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        ai_client._model = mock_model

        result = await ai_client.generate("test")
        assert "**" not in result
        assert "##" not in result

        ai_client._model = None

    @pytest.mark.asyncio
    async def test_generate_truncates_large_data(self):
        import ai_client

        mock_response = MagicMock()
        mock_response.text = "OK"

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        ai_client._model = mock_model

        large_data = "x" * 50000
        await ai_client.generate("test", context_data=large_data)

        call_args = mock_model.generate_content.call_args[0][0]
        assert "truncated" in call_args

        ai_client._model = None


class TestAIDiagnose:
    """ai_client.diagnose_faults."""

    @pytest.mark.asyncio
    async def test_diagnose_formats_dtcs(self):
        import ai_client

        mock_response = MagicMock()
        mock_response.text = "Engine coolant sensor failure"

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        ai_client._model = mock_model

        dtcs = [
            {"spnId": 110, "fmiId": 4, "spnDescription": "Engine Coolant Temp",
             "fmiDescription": "Voltage below normal", "sourceAddressName": "ECU"},
        ]
        result = await ai_client.diagnose_faults("101", dtcs, {"stopIsOn": True})
        assert "coolant" in result.lower()

        # Verify context included truck name and DTC details
        call_args = mock_model.generate_content.call_args[0][0]
        assert "101" in call_args
        assert "110" in call_args

        ai_client._model = None

    @pytest.mark.asyncio
    async def test_diagnose_caps_dtc_count(self):
        """Should cap at 10 DTCs to stay within token limits."""
        import ai_client

        mock_response = MagicMock()
        mock_response.text = "Multiple faults"

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        ai_client._model = mock_model

        dtcs = [
            {"spnId": i, "fmiId": 1, "spnDescription": f"Fault {i}"}
            for i in range(20)
        ]
        await ai_client.diagnose_faults("102", dtcs)

        call_args = mock_model.generate_content.call_args[0][0]
        # Only 10 faults should be in context
        assert "Fault 15" not in call_args

        ai_client._model = None


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
        with patch.dict(os.environ, {"GOOGLE_AI_API_KEY": "test-key"}):
            from bot.keyboards import main_menu_kb
            kb = main_menu_kb(Role.OWNER, ["CO1"])
            callbacks = self._all_callbacks(kb)
            assert "cmd_ai" in callbacks

    def test_main_menu_hides_ai_when_not_configured(self):
        from database import Role
        with patch.dict(os.environ, {"GOOGLE_AI_API_KEY": ""}):
            from bot.keyboards import main_menu_kb
            kb = main_menu_kb(Role.OWNER, ["CO1"])
            callbacks = self._all_callbacks(kb)
            assert "cmd_ai" not in callbacks

    def test_truck_kb_shows_ai_diagnose_when_configured(self):
        with patch.dict(os.environ, {"GOOGLE_AI_API_KEY": "test-key"}):
            from bot.keyboards import truck_kb
            kb = truck_kb(truck_name="101", company="CO1", show_faults=True)
            callbacks = self._all_callbacks(kb)
            assert "ai_diag_CO1_101" in callbacks
            labels = self._all_labels(kb)
            assert any("AI Diagnose" in l for l in labels)

    def test_truck_kb_hides_ai_diagnose_when_not_configured(self):
        with patch.dict(os.environ, {"GOOGLE_AI_API_KEY": ""}):
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
        assert "cmd_ai_usage" in callbacks
        assert "cmd_menu" in callbacks

    def test_ai_back_kb(self):
        from bot.ai import _ai_back_kb
        kb = _ai_back_kb()
        callbacks = self._all_callbacks(kb)
        assert "cmd_ai" in callbacks
        assert "cmd_menu" in callbacks


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
                "fault_codes": {"j1939": {
                    "diagnosticTroubleCodes": [
                        {"spnId": 110, "spnDescription": "Coolant Temp",
                         "fmiDescription": "Normal"}
                    ],
                    "checkEngineLights": {},
                }},
            },
            {
                "id": "v2", "name": "202", "_org": "CO1",
                "location": {}, "fuel": {"value": 15},
                "fault_codes": {"j1939": {
                    "diagnosticTroubleCodes": [],
                    "checkEngineLights": {},
                }},
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

        with patch("bot.ai.get_client", return_value=mock_client):
            snapshot = await _gather_fleet_snapshot(account_id=1)

        assert snapshot["total_vehicles"] == 2
        assert snapshot["stats"]["faulted"] == 1
        assert snapshot["stats"]["low_fuel"] == 1
        assert len(snapshot["vehicles"]) == 2
        assert snapshot["vehicles"][0]["name"] == "101"
        assert snapshot["vehicles"][0]["fuel_pct"] == 45
        # Health data merged into vehicle entries
        assert snapshot["vehicles"][1].get("battery_v") == 11.5

    @pytest.mark.asyncio
    async def test_snapshot_filters_by_truck(self):
        from bot.ai import _gather_fleet_snapshot

        mock_fleet = [
            {"id": "v1", "name": "101", "_org": "CO1",
             "location": {}, "fuel": {"value": 80},
             "fault_codes": {"j1939": {"diagnosticTroubleCodes": [], "checkEngineLights": {}}}},
            {"id": "v2", "name": "202", "_org": "CO1",
             "location": {}, "fuel": {"value": 50},
             "fault_codes": {"j1939": {"diagnosticTroubleCodes": [], "checkEngineLights": {}}}},
        ]

        mock_client = AsyncMock()
        mock_client.get_fleet_overview.return_value = mock_fleet
        mock_client.get_vehicle_health.return_value = []

        with patch("bot.ai.get_client", return_value=mock_client):
            snapshot = await _gather_fleet_snapshot(
                account_id=1, truck_num="101"
            )

        assert snapshot["total_vehicles"] == 1
        assert snapshot["vehicles"][0]["name"] == "101"
