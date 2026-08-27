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

import pytest

from capabilities.ai.intelligence import _check_tool_permission

DRIVER_CTX = {"vehicle_nums": ["T-101"]}

OPTIONAL_VEHICLE_TOOLS = ["get_recent_work_orders", "get_recent_inspections"]


# The gate is account-aware + async; these driver-isolation cases pass no
# account_id, so they exercise the role-default fallback (unchanged behavior).
@pytest.mark.asyncio
class TestDriverOmittedVehicleName:
    async def test_driver_blocked_without_vehicle_name(self):
        for tool in OPTIONAL_VEHICLE_TOOLS:
            result = await _check_tool_permission(tool, {}, "driver", DRIVER_CTX)
            assert result is not None, f"{tool} must be blocked for drivers without vehicle_name"
            assert "Access denied" in result["error"]
            assert "T-101" in result["error"]  # model can retry with the right scope

    async def test_driver_blocked_with_blank_vehicle_name(self):
        for tool in OPTIONAL_VEHICLE_TOOLS:
            result = await _check_tool_permission(tool, {"vehicle_name": "  "}, "driver", DRIVER_CTX)
            assert result is not None

    async def test_driver_allowed_for_own_vehicle(self):
        for tool in OPTIONAL_VEHICLE_TOOLS:
            result = await _check_tool_permission(tool, {"vehicle_name": "T-101"}, "driver", DRIVER_CTX)
            assert result is None, f"{tool} must stay usable for the driver's own vehicle"

    async def test_driver_blocked_for_other_vehicle(self):
        for tool in OPTIONAL_VEHICLE_TOOLS:
            result = await _check_tool_permission(tool, {"vehicle_name": "T-999"}, "driver", DRIVER_CTX)
            assert result is not None
            assert "Access denied" in result["error"]

    async def test_owner_allowed_account_wide(self):
        for tool in OPTIONAL_VEHICLE_TOOLS:
            result = await _check_tool_permission(tool, {}, "owner", {})
            assert result is None

    async def test_driver_scope_aware_tool_allowed_role_denied_tool_blocked(self):
        # get_alert_history is now SCOPE-AWARE: a driver is allowed it and the
        # results are filtered to their assigned vehicle (better than the old
        # blanket block — drivers have can_alerts_vehicle for their own truck).
        assert await _check_tool_permission(
            "get_alert_history", {}, "driver", DRIVER_CTX,
        ) is None
        # A fleet-only tool the driver has NO permission for is still denied.
        denied = await _check_tool_permission(
            "get_drivers_list", {}, "driver", DRIVER_CTX,
        )
        assert denied is not None
        assert "cannot use" in denied["error"]
