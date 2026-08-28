"""Driver-role isolation for the DND/shift-handoff delivery.

Background
----------
``get_shift_handoff_data`` returns four lists for a recipient: pending
alerts, resolved alerts, pending maintenance, and recent alert history.
The first two are scoped per-subscriber via ``alert_acknowledgments.sent_to``
(and ``send_alert`` already drops fleet rows for drivers before they are
written), but ``pending_maintenance`` and ``recent_history`` are pulled at
account scope and would leak the rest of the fleet's data into the morning
PDF/summary.

These tests pin the ``_filter_handoff_for_driver`` helper that the DND
delivery loop applies to those two lists for ``Role.DRIVER`` recipients.
"""

from __future__ import annotations

from capabilities.alerting.dnd import _filter_handoff_for_driver


class TestFilterHandoffForDriver:
    def test_keeps_only_driver_truck_rows(self):
        items = [
            {"vehicle_name": "107", "task": "oil"},
            {"vehicle_name": "213", "task": "tires"},
            {"vehicle_name": "Truck-107A", "task": "DEF"},
            {"vehicle_name": "571701", "task": "engine"},
        ]
        result = _filter_handoff_for_driver(items, ["107"])
        names = {r["vehicle_name"] for r in result}
        assert names == {"107", "Truck-107A"}

    def test_empty_truck_list_returns_empty(self):
        items = [{"vehicle_name": "107"}, {"vehicle_name": "213"}]
        assert _filter_handoff_for_driver(items, []) == []
        assert _filter_handoff_for_driver(items, [""]) == []

    def test_handles_missing_or_none_vehicle_name(self):
        items = [
            {"vehicle_name": None, "task": "x"},
            {"task": "y"},  # no vehicle_name key at all
            {"vehicle_name": "107"},
        ]
        result = _filter_handoff_for_driver(items, ["107"])
        assert result == [{"vehicle_name": "107"}]

    def test_multi_truck_assignment(self):
        items = [
            {"vehicle_name": "107"},
            {"vehicle_name": "213"},
            {"vehicle_name": "571701"},
        ]
        result = _filter_handoff_for_driver(items, ["107", "571701"])
        names = {r["vehicle_name"] for r in result}
        assert names == {"107", "571701"}

    def test_case_insensitive_match(self):
        items = [{"vehicle_name": "TRUCK-107"}, {"vehicle_name": "truck-213"}]
        result = _filter_handoff_for_driver(items, ["107"])
        assert len(result) == 1
        assert result[0]["vehicle_name"] == "TRUCK-107"

    def test_custom_name_key(self):
        items = [
            {"truck": "107", "x": 1},
            {"truck": "213", "x": 2},
        ]
        result = _filter_handoff_for_driver(items, ["107"], name_key="truck")
        assert len(result) == 1
        assert result[0]["truck"] == "107"
