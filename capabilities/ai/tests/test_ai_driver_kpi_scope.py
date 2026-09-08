"""Driver KPI tools honour Team Management's Vehicle Access.

``get_driver_efficiency`` and ``get_driver_scorecard`` sat in no scope set,
so the gate passed them for every restricted caller and the handlers read
the whole account.  The REST scorecards endpoints filter by company; the
AI is now narrowed by the same scope every other tool uses — the service's
own ``vehicle_nums`` filter, which admits a driver when a truck they drove
is in scope and returns nobody for ``[]``.

The sharpest case, pinned first: a DRIVER holds ``can_view_scorecards``
by default, and omitting ``driver_name`` used to mean every colleague.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

import features.drivers.ai_tool as M
from capabilities.ai.intelligence import _check_tool_permission
from capabilities.ai.tools.registry import execute_tool
from capabilities.permissions.roles import SCOPE_AWARE_TOOLS


def _rows():
    return [
        {"driver_name": "Ana", "_miles": 100, "_vehicle_summaries": [{"vehicle": {"name": "229"}}]},
        {"driver_name": "Ben", "_miles": 200, "_vehicle_summaries": [{"vehicle": {"name": "341"}}]},
    ]


class _Recorder:
    """Stands in for the service and records the scope it was handed."""
    def __init__(self):
        self.calls: list = []

    async def __call__(self, account_id, days=7, company=None, vehicle_nums=None):
        self.calls.append(vehicle_nums)
        if vehicle_nums is None:
            return _rows()
        allowed = {v.lower() for v in vehicle_nums}
        return [r for r in _rows()
                if any((vs.get("vehicle") or {}).get("name", "").lower() in allowed
                       for vs in r["_vehicle_summaries"])]


@pytest.mark.asyncio
async def test_scoped_driver_is_still_offered_the_scorecard():
    """Closing the leak must not delete the feature.  Classifying the tool
    account-wide would have hidden it from every restricted caller (the
    registry dropped ALL account-wide tools), turning "leaky" into "gone"
    for the driver asking about their own scorecard."""
    from capabilities.ai.tools.registry import filter_tools_for_role
    names = {d["name"] for d in await filter_tools_for_role("driver", scoped=True)}
    assert "get_driver_scorecard" in names
    assert "get_drivers_list" not in names     # not scope-aware: still hidden


def test_both_tools_are_scope_aware():
    """The classification is what makes the orchestrator inject the scope."""
    assert "get_driver_efficiency" in SCOPE_AWARE_TOOLS
    assert "get_driver_scorecard" in SCOPE_AWARE_TOOLS


@pytest.mark.asyncio
class TestScorecardScope:
    async def test_driver_omitting_the_name_gets_only_their_trucks(self, monkeypatch):
        svc = _Recorder(); monkeypatch.setattr(M, "_svc_drv_eff", svc)
        ctx = {"role": "driver", "scoped_vehicle_nums": ["229"]}
        # The gate still admits it (scope-aware tools are allowed)…
        assert await _check_tool_permission("get_driver_scorecard", {}, "driver", ctx) is None
        # …and the executor injects the scope, which reaches the service.
        res = await execute_tool("get_driver_scorecard", {}, None, account_id=1,
                                 db=None, scope_vehicles=["229"])
        assert svc.calls == [["229"]]
        assert [d["name"] for d in res["drivers"]] == ["Ana"]

    async def test_unrestricted_caller_still_sees_everyone(self, monkeypatch):
        svc = _Recorder(); monkeypatch.setattr(M, "_svc_drv_eff", svc)
        res = await execute_tool("get_driver_scorecard", {}, None, account_id=1, db=None)
        assert svc.calls == [None]
        assert {d["name"] for d in res["drivers"]} == {"Ana", "Ben"}

    async def test_empty_scope_fails_closed(self, monkeypatch):
        svc = _Recorder(); monkeypatch.setattr(M, "_svc_drv_eff", svc)
        res = await execute_tool("get_driver_scorecard", {}, None, account_id=1,
                                 db=None, scope_vehicles=[])
        assert svc.calls == [[]]
        assert res["drivers"] == []

    async def test_name_filter_applies_inside_the_scope(self, monkeypatch):
        """Naming a driver OUTSIDE your scope finds nothing — the name
        filter narrows the scoped set, it cannot widen it."""
        svc = _Recorder(); monkeypatch.setattr(M, "_svc_drv_eff", svc)
        res = await execute_tool("get_driver_scorecard", {"driver_name": "Ben"}, None,
                                 account_id=1, db=None, scope_vehicles=["229"])
        assert res["drivers"] == []


@pytest.mark.asyncio
class TestEfficiencyScope:
    async def test_scoped_caller_is_narrowed(self, monkeypatch):
        svc = _Recorder(); monkeypatch.setattr(M, "_svc_drv_eff", svc)
        res = await execute_tool("get_driver_efficiency", {}, None, account_id=1,
                                 db=None, scope_vehicles=["341"])
        assert svc.calls == [["341"]]
        assert [d["name"] for d in res["drivers"]] == ["Ben"]

    async def test_model_cannot_supply_the_scope_itself(self, monkeypatch):
        """``_scope_vehicles`` is a server channel.  A model-supplied value
        is stripped and replaced by the caller's real scope."""
        svc = _Recorder(); monkeypatch.setattr(M, "_svc_drv_eff", svc)
        await execute_tool("get_driver_efficiency", {"_scope_vehicles": ["341"]}, None,
                           account_id=1, db=None, scope_vehicles=["229"])
        assert svc.calls == [["229"]]
