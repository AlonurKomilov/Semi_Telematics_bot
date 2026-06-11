"""Driver isolation for AI tools with an OPTIONAL vehicle_name param.

``get_recent_work_orders`` and ``get_recent_inspections`` run account-wide
when vehicle_name is omitted.  They are in VEHICLE_SPECIFIC_TOOLS (not
ACCOUNT_WIDE_TOOLS) because drivers may query their own vehicle — but the
isolation guard used to fire only when a vehicle was actually named, so a
driver omitting vehicle_name got the whole fleet's work orders and every
other driver's inspections.  The guard must fail closed: no vehicle named
by a driver → blocked, with the assigned vehicles echoed back so the model
can retry correctly scoped.
"""

import os

os.environ["GOOGLE_CLOUD_PROJECT"] = ""
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = ""
os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.ai.intelligence import _check_tool_permission

DRIVER_CTX = {"vehicle_nums": ["T-101"]}

OPTIONAL_VEHICLE_TOOLS = ["get_recent_work_orders", "get_recent_inspections"]


class TestDriverOmittedVehicleName:
    def test_driver_blocked_without_vehicle_name(self):
        for tool in OPTIONAL_VEHICLE_TOOLS:
            result = _check_tool_permission(tool, {}, "driver", DRIVER_CTX)
            assert result is not None, f"{tool} must be blocked for drivers without vehicle_name"
            assert "Access denied" in result["error"]
            assert "T-101" in result["error"]  # model can retry with the right scope

    def test_driver_blocked_with_blank_vehicle_name(self):
        for tool in OPTIONAL_VEHICLE_TOOLS:
            result = _check_tool_permission(tool, {"vehicle_name": "  "}, "driver", DRIVER_CTX)
            assert result is not None

    def test_driver_allowed_for_own_vehicle(self):
        for tool in OPTIONAL_VEHICLE_TOOLS:
            result = _check_tool_permission(tool, {"vehicle_name": "T-101"}, "driver", DRIVER_CTX)
            assert result is None, f"{tool} must stay usable for the driver's own vehicle"

    def test_driver_blocked_for_other_vehicle(self):
        for tool in OPTIONAL_VEHICLE_TOOLS:
            result = _check_tool_permission(tool, {"vehicle_name": "T-999"}, "driver", DRIVER_CTX)
            assert result is not None
            assert "Access denied" in result["error"]

    def test_owner_allowed_account_wide(self):
        for tool in OPTIONAL_VEHICLE_TOOLS:
            result = _check_tool_permission(tool, {}, "owner", {})
            assert result is None

    def test_driver_still_blocked_on_account_wide_tools(self):
        result = _check_tool_permission("get_alert_history", {}, "driver", DRIVER_CTX)
        assert result is not None
        assert "fleet-wide" in result["error"]
