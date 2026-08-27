"""The bot's AI surface — menu keyboards and the fleet snapshot.

Split from tests/test_ai.py. This half asserts interfaces.bot
(_ai_menu_kb, _ai_chat_kb, _parse_suggestions, _gather_vehicles_snapshot)
— the transport and its formatting, not the AI client, which now lives
in capabilities/ai/tests/test_client.py.

Stays in tests/ for now: interfaces/ suites have not been reorganised
into tests/interfaces/ yet, and inventing a home for one file ahead of
that decision would be guessing.
"""

import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

# No real credentials during tests
os.environ["GOOGLE_CLOUD_PROJECT"] = ""
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = ""
os.environ.setdefault("ENCRYPTION_KEY", "")


class TestAIKeyboard:
    """AI buttons in main menu and truck detail keyboards."""

    def _all_callbacks(self, markup):
        return [b.callback_data for r in markup.inline_keyboard for b in r if b.callback_data]

    def _all_labels(self, markup):
        return [b.text for r in markup.inline_keyboard for b in r]

    def test_main_menu_shows_ai_when_configured(self):
        from adapters.storage import Role
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "my-project", "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/sa.json"}):
            from interfaces.bot.keyboards import main_menu_kb
            kb = main_menu_kb(Role.OWNER, ["CO1"])
            callbacks = self._all_callbacks(kb)
            assert "cmd_ai" in callbacks

    def test_main_menu_hides_ai_when_not_configured(self):
        from adapters.storage import Role
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "", "GOOGLE_APPLICATION_CREDENTIALS": ""}):
            from interfaces.bot.keyboards import main_menu_kb
            kb = main_menu_kb(Role.OWNER, ["CO1"])
            callbacks = self._all_callbacks(kb)
            assert "cmd_ai" not in callbacks

    def test_truck_kb_shows_ai_diagnose_when_configured(self):
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "my-project", "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/sa.json"}):
            from interfaces.bot.keyboards import vehicle_kb
            kb = vehicle_kb(vehicle_name="101", company="CO1", show_faults=True)
            callbacks = self._all_callbacks(kb)
            assert "ai_diag_CO1_101" in callbacks
            labels = self._all_labels(kb)
            assert any("AI Diagnose" in l for l in labels)

    def test_truck_kb_hides_ai_diagnose_when_not_configured(self):
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "", "GOOGLE_APPLICATION_CREDENTIALS": ""}):
            from interfaces.bot.keyboards import vehicle_kb
            kb = vehicle_kb(vehicle_name="101", company="CO1", show_faults=True)
            callbacks = self._all_callbacks(kb)
            assert not any("ai_diag" in c for c in callbacks)

    def test_ai_menu_kb(self):
        from interfaces.bot.ai import _ai_menu_kb
        kb = _ai_menu_kb()
        callbacks = self._all_callbacks(kb)
        assert "ai_chat" in callbacks
        assert "ai_summary" in callbacks
        assert "cmd_menu" in callbacks

    def test_account_owner_does_NOT_see_the_model_button(self):
        """Model pinning left the account tier — this used to assert the
        opposite.

        Picking an individual model is not an account decision.  Users
        choose a TIER (Fast / Thinking / Reasoning / Auto) and the router
        resolves a model within it from live availability; a pinned
        ``ai_model`` short-circuits that, so a model going down fails
        instead of falling through.  ``is_management_role`` was never the
        right gate — an account owner should not have it either.
        """
        from interfaces.bot.ai import _ai_menu_kb
        from adapters.storage import Role
        kb = _ai_menu_kb(user_role=Role.OWNER, telegram_id=999_000_111)
        callbacks = self._all_callbacks(kb)
        assert "ai_models_text" not in callbacks
        assert "ai_models_vision" not in callbacks
        # The rest of the AI menu is untouched.
        assert "cmd_ai_alerts" in callbacks

    def test_system_owner_still_sees_the_model_button(self):
        """The override survives for whoever runs the platform.

        Kept rather than deleted because there is no operator surface to
        move it to, and pinning past a misbehaving provider is worth
        having.
        """
        from unittest.mock import patch
        from interfaces.bot.ai import _ai_menu_kb
        from adapters.storage import Role
        with patch("capabilities.permissions.roles.is_system_owner", return_value=True):
            kb = _ai_menu_kb(user_role=Role.OWNER, telegram_id=555)
        callbacks = self._all_callbacks(kb)
        assert "ai_models_text" in callbacks
        assert "ai_models_vision" in callbacks
        labels = self._all_labels(kb)
        assert any("Text:" in l for l in labels)
        assert any("Vision:" in l for l in labels)

    def test_ai_menu_kb_driver_no_model_button(self):
        from interfaces.bot.ai import _ai_menu_kb
        from adapters.storage import Role
        kb = _ai_menu_kb(user_role=Role.DRIVER)
        callbacks = self._all_callbacks(kb)
        assert "ai_models_text" not in callbacks
        assert "ai_models_vision" not in callbacks
        assert "cmd_ai_alerts" in callbacks

    def test_ai_back_kb(self):
        from interfaces.bot.ai import _ai_back_kb
        kb = _ai_back_kb()
        callbacks = self._all_callbacks(kb)
        assert "cmd_ai" in callbacks
        assert "cmd_menu" in callbacks

    def test_ai_chat_kb(self):
        from interfaces.bot.ai import _ai_chat_kb
        kb = _ai_chat_kb()
        callbacks = self._all_callbacks(kb)
        assert "ai_newchat" in callbacks
        assert "cmd_menu" in callbacks

    def test_build_chat_kb_with_suggestions(self):
        from interfaces.bot.ai import _build_chat_kb
        kb = _build_chat_kb(["Which truck needs fuel?", "Show faults"])
        callbacks = self._all_callbacks(kb)
        assert any(c.startswith("ai_sug_") for c in callbacks)
        assert "cmd_ai" in callbacks
        assert "cmd_menu" in callbacks
        labels = self._all_labels(kb)
        assert any("Which truck needs fuel?" in l for l in labels)

    def test_build_chat_kb_no_suggestions(self):
        from interfaces.bot.ai import _build_chat_kb
        kb = _build_chat_kb()
        callbacks = self._all_callbacks(kb)
        assert not any(c.startswith("ai_sug_") for c in callbacks)
        assert "cmd_ai" in callbacks

    def test_parse_suggestions(self):
        from interfaces.bot.ai import _parse_suggestions
        text = "Here are 3 trucks.\n>> Check truck 101\n>> Show fuel levels"
        clean, suggestions = _parse_suggestions(text)
        assert ">> Check truck 101" not in clean
        assert len(suggestions) == 2
        assert suggestions[0] == "Check truck 101"
        assert suggestions[1] == "Show fuel levels"

    def test_parse_suggestions_no_suggestions(self):
        from interfaces.bot.ai import _parse_suggestions
        text = "Here are 3 trucks with low fuel."
        clean, suggestions = _parse_suggestions(text)
        assert clean == text
        assert suggestions == []


# ══════════════════════════════════════════════════════════════════
# FLEET SNAPSHOT BUILDER
# ══════════════════════════════════════════════════════════════════

class TestFleetSnapshot:
    """_gather_vehicles_snapshot data shape."""

    @pytest.mark.asyncio
    async def test_snapshot_structure(self):
        from interfaces.bot.ai import _gather_vehicles_snapshot

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
        mock_client.get_vehicles_overview.return_value = mock_fleet
        mock_client.get_vehicle_health.return_value = mock_health
        mock_client.get_driver_efficiency.return_value = []
        mock_client.get_fleet_weather.return_value = []

        with patch("infra.services.get_client", return_value=mock_client), \
             patch("infra.services.get_tenant_db", new_callable=AsyncMock), \
             patch("features.vehicles.service.get_vehicles_overview",
                   new=AsyncMock(return_value=mock_fleet)), \
             patch("features.vehicles.warehouse.service.get_vehicle_health",
                   new=AsyncMock(return_value=mock_health)), \
             patch("features.events.service.get_events",
                   new=AsyncMock(return_value=[])):
            snapshot = await _gather_vehicles_snapshot(account_id=1)

        assert snapshot["total_vehicles"] == 2
        assert snapshot["faulted_count"] == 1
        assert snapshot["low_fuel_count"] == 1
        assert len(snapshot["vehicles"]) == 2
        assert snapshot["vehicles"][0]["name"] == "101"
        assert snapshot["vehicles"][0]["fuel_pct"] == 45
        assert len(snapshot["health_alerts"]) == 1

    @pytest.mark.asyncio
    async def test_snapshot_filters_by_truck(self):
        from interfaces.bot.ai import _gather_vehicles_snapshot

        mock_fleet = [
            {"id": "v1", "name": "101", "_org": "CO1",
             "location": {}, "fuel": {"value": 80}, "_dtcs": [], "_lights": {}},
            {"id": "v2", "name": "202", "_org": "CO1",
             "location": {}, "fuel": {"value": 50}, "_dtcs": [], "_lights": {}},
        ]

        mock_client = AsyncMock()
        mock_client.get_vehicles_overview.return_value = mock_fleet
        mock_client.get_vehicle_health.return_value = []
        mock_client.get_driver_efficiency.return_value = []
        mock_client.get_fleet_weather.return_value = []

        with patch("infra.services.get_client", return_value=mock_client), \
             patch("infra.services.get_tenant_db", new_callable=AsyncMock), \
             patch("features.vehicles.service.get_vehicles_overview",
                   new=AsyncMock(return_value=mock_fleet)), \
             patch("features.vehicles.warehouse.service.get_vehicle_health",
                   new=AsyncMock(return_value=[])), \
             patch("features.events.service.get_events",
                   new=AsyncMock(return_value=[])):
            snapshot = await _gather_vehicles_snapshot(
                account_id=1, vehicle_num="101"
            )

        assert snapshot["total_vehicles"] == 1
        assert snapshot["vehicles"][0]["name"] == "101"


# ══════════════════════════════════════════════════════════════════
# MODEL REGISTRY
# ══════════════════════════════════════════════════════════════════
