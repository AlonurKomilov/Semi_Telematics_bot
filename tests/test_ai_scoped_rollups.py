"""Company-scoped fleet rollups — Phase 1 plumbing + first scope-aware tool.

For a company/vehicle-restricted user, account-wide tools that have been made
*scope-aware* (listed in ``SCOPE_AWARE_TOOLS``) are no longer blocked: the
orchestrator injects the caller's allowed vehicles as ``_scope_vehicles`` and
the tool returns only that subset.  Account-wide tools NOT yet scope-aware stay
blocked (safe default).  ``get_idle_vehicles`` is the first upgraded tool.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.ai.intelligence import _check_tool_permission
from capabilities.ai.tools.idle import get_idle_vehicles
from capabilities.ai.tools.alert_history import get_alert_history
from capabilities.ai.tools import vehicle as _veh_mod
from capabilities.ai.tools.registry import execute_tool


class _FakeParkingDB:
    def __init__(self, events):
        self._events = events

    async def get_active_parking_events(self, account_id, attention_only=True):
        return list(self._events)


def _events():
    return [
        {"vehicle_name": "B-1", "company_code": "B", "duration_hours": 48},
        {"vehicle_name": "A-3", "company_code": "A", "duration_hours": 72},
    ]


# ── Gate: scope-aware account-wide tools allowed; others still blocked ────────


@pytest.mark.asyncio
class TestGateAllowsScopeAwareTools:
    async def test_scope_aware_tool_allowed_for_scoped_user(self):
        ctx = {"role": "fleet", "scoped_vehicle_nums": ["B-1"]}
        # get_idle_vehicles is account-wide AND scope-aware → not blocked.
        assert await _check_tool_permission("get_idle_vehicles", {}, "fleet", ctx) is None

    async def test_non_scope_aware_account_wide_tool_still_blocked(self):
        ctx = {"role": "fleet", "scoped_vehicle_nums": ["B-1"]}
        blocked = await _check_tool_permission("get_account_stats", {}, "fleet", ctx)
        assert blocked is not None
        assert "account-wide" in blocked["error"]

    async def test_unrestricted_user_unaffected(self):
        ctx = {"role": "fleet", "scoped_vehicle_nums": None}
        assert await _check_tool_permission("get_idle_vehicles", {}, "fleet", ctx) is None
        assert await _check_tool_permission("get_account_stats", {}, "fleet", ctx) is None


# ── Tool: get_idle_vehicles filters to the injected scope ────────────────────


@pytest.mark.asyncio
class TestIdleVehiclesScopeFilter:
    async def test_filters_to_allowed_vehicles(self):
        db = _FakeParkingDB(_events())
        res = await get_idle_vehicles(
            {"_scope_vehicles": ["B-1"], "min_days": 1}, None, account_id=1, db=db,
        )
        assert res["count"] == 1
        assert res["vehicles"][0]["vehicle"] == "B-1"

    async def test_no_scope_returns_all(self):
        db = _FakeParkingDB(_events())
        res = await get_idle_vehicles({"min_days": 1}, None, account_id=1, db=db)
        assert res["count"] == 2

    async def test_empty_scope_fails_closed(self):
        db = _FakeParkingDB(_events())
        res = await get_idle_vehicles(
            {"_scope_vehicles": [], "min_days": 1}, None, account_id=1, db=db,
        )
        assert res["count"] == 0


# ── execute_tool injects the scope for scope-aware tools ─────────────────────


@pytest.mark.asyncio
class TestExecuteToolInjection:
    async def test_scope_injected_causes_filtering(self):
        db = _FakeParkingDB(_events())
        res = await execute_tool(
            "get_idle_vehicles", {"min_days": 1}, None,
            account_id=1, db=db, scope_vehicles=["B-1"],
        )
        assert res["count"] == 1  # injection filtered out A-3

    async def test_no_scope_no_filtering(self):
        db = _FakeParkingDB(_events())
        res = await execute_tool(
            "get_idle_vehicles", {"min_days": 1}, None,
            account_id=1, db=db,  # scope_vehicles defaults to None
        )
        assert res["count"] == 2


# ── Batch 2: the four scope-aware tools, gate + per-tool filtering ───────────

_SCOPE_AWARE = ["get_idle_vehicles", "get_rolling_stopped",
                "search_vehicles", "get_alert_history"]
_NOT_YET = ["get_drivers_list", "get_driver_hos_status",
            "get_maintenance_summary", "get_weather"]


@pytest.mark.asyncio
class TestGateBatch2:
    async def test_all_scope_aware_tools_allowed_for_scoped_user(self):
        # owner has every req-perm, so this isolates the scope branch.
        ctx = {"role": "owner", "scoped_vehicle_nums": ["B-1"]}
        for tool in _SCOPE_AWARE:
            assert await _check_tool_permission(tool, {}, "owner", ctx) is None, tool

    async def test_not_yet_scope_aware_still_blocked(self):
        ctx = {"role": "owner", "scoped_vehicle_nums": ["B-1"]}
        for tool in _NOT_YET:
            blocked = await _check_tool_permission(tool, {}, "owner", ctx)
            assert blocked is not None, tool
            assert "account-wide" in blocked["error"], tool


class _FakeAlertDB:
    def __init__(self, rows):
        self._rows = rows

    async def get_alert_history(self, account_id, **kw):
        return list(self._rows)


def _fleet():
    return [
        {"id": "1", "name": "B-1", "location": {"speed": 0}},
        {"id": "2", "name": "A-3", "location": {"speed": 0}},
    ]


@pytest.mark.asyncio
class TestBatch2ToolFilters:
    async def test_rolling_stopped_filters_to_scope(self, monkeypatch):
        async def fake_fleet(account_id):
            return _fleet()

        async def fake_states(account_id):
            return []

        monkeypatch.setattr(_veh_mod, "_svc_vehicles", fake_fleet)
        monkeypatch.setattr(_veh_mod, "_svc_engine_states", fake_states)
        res = await _veh_mod.get_rolling_stopped(
            {"_scope_vehicles": ["B-1"]}, None, account_id=1,
        )
        assert res["total"] == 1
        assert all(v["vehicle"] == "B-1" for v in res["off_vehicles"])

    async def test_search_vehicles_filters_to_scope(self, monkeypatch):
        async def fake_fleet(account_id):
            return _fleet()

        monkeypatch.setattr(_veh_mod, "_svc_vehicles", fake_fleet)
        res = await _veh_mod.search_vehicles(
            {"_scope_vehicles": ["B-1"]}, None, account_id=1,
        )
        assert res["matched"] == 1
        assert res["vehicles"][0]["vehicle"] == "B-1"

    async def test_alert_history_filters_to_scope(self):
        db = _FakeAlertDB([
            {"id": 1, "vehicle_name": "B-1", "alert_type": "fault"},
            {"id": 2, "vehicle_name": "A-3", "alert_type": "fault"},
        ])
        res = await get_alert_history(
            {"_scope_vehicles": ["B-1"]}, None, account_id=1, db=db,
        )
        assert res["count"] == 1
        assert res["alerts"][0]["vehicle"] == "B-1"

    async def test_alert_history_unscoped_returns_all(self):
        db = _FakeAlertDB([
            {"id": 1, "vehicle_name": "B-1"},
            {"id": 2, "vehicle_name": "A-3"},
        ])
        res = await get_alert_history({}, None, account_id=1, db=db)
        assert res["count"] == 2
