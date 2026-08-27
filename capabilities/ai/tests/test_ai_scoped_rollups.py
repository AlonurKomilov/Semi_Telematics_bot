"""Company-scoped fleet rollups — Phase 1 plumbing + first scope-aware tool.

For a company/vehicle-restricted user, account-wide tools that have been made
*scope-aware* (listed in ``SCOPE_AWARE_TOOLS``) are no longer blocked: the
orchestrator injects the caller's allowed vehicles as ``_scope_vehicles`` and
the tool returns only that subset.  Account-wide tools NOT yet scope-aware stay
blocked (safe default).  ``get_parked_vehicles`` is the first upgraded tool.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.ai.intelligence import _check_tool_permission
from features.parking.ai_tool import get_parked_vehicles
from capabilities.alerting.ai_tool import get_alert_history
from features.vehicles import ai_tool as _veh_mod
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
        # get_parked_vehicles is account-wide AND scope-aware → not blocked.
        assert await _check_tool_permission("get_parked_vehicles", {}, "fleet", ctx) is None

    async def test_non_scope_aware_account_wide_tool_still_blocked(self):
        ctx = {"role": "fleet", "scoped_vehicle_nums": ["B-1"]}
        blocked = await _check_tool_permission("get_drivers_list", {}, "fleet", ctx)
        assert blocked is not None
        assert "account-wide" in blocked["error"]

    async def test_unrestricted_user_unaffected(self):
        ctx = {"role": "fleet", "scoped_vehicle_nums": None}
        assert await _check_tool_permission("get_parked_vehicles", {}, "fleet", ctx) is None
        assert await _check_tool_permission("get_account_stats", {}, "fleet", ctx) is None


# ── Tool: get_parked_vehicles filters to the injected scope ────────────────────


@pytest.mark.asyncio
class TestIdleVehiclesScopeFilter:
    async def test_filters_to_allowed_vehicles(self):
        db = _FakeParkingDB(_events())
        res = await get_parked_vehicles(
            {"_scope_vehicles": ["B-1"], "min_days": 1}, None, account_id=1, db=db,
        )
        assert res["count"] == 1
        assert res["vehicles"][0]["vehicle"] == "B-1"

    async def test_no_scope_returns_all(self):
        db = _FakeParkingDB(_events())
        res = await get_parked_vehicles({"min_days": 1}, None, account_id=1, db=db)
        assert res["count"] == 2

    async def test_empty_scope_fails_closed(self):
        db = _FakeParkingDB(_events())
        res = await get_parked_vehicles(
            {"_scope_vehicles": [], "min_days": 1}, None, account_id=1, db=db,
        )
        assert res["count"] == 0


# ── execute_tool injects the scope for scope-aware tools ─────────────────────


@pytest.mark.asyncio
class TestExecuteToolInjection:
    async def test_scope_injected_causes_filtering(self):
        db = _FakeParkingDB(_events())
        res = await execute_tool(
            "get_parked_vehicles", {"min_days": 1}, None,
            account_id=1, db=db, scope_vehicles=["B-1"],
        )
        assert res["count"] == 1  # injection filtered out A-3

    async def test_no_scope_no_filtering(self):
        db = _FakeParkingDB(_events())
        res = await execute_tool(
            "get_parked_vehicles", {"min_days": 1}, None,
            account_id=1, db=db,  # scope_vehicles defaults to None
        )
        assert res["count"] == 2


# ── Batch 2: the four scope-aware tools, gate + per-tool filtering ───────────

_SCOPE_AWARE = ["get_parked_vehicles", "get_undriven_vehicles",
                "get_rolling_stopped", "search_vehicles",
                "get_alert_history", "get_maintenance_summary", "get_weather",
                "get_driver_hos_status", "get_low_fuel_vehicles",
                "get_fuel_cost_summary", "get_vehicle_health", "get_account_stats",
                "get_efficiency_summary", "get_events_summary"]
# Only account-wide tool still not scope-aware: the driver roster has no
# vehicle field, so scoping it needs a driver→company mapping (follow-up).
_NOT_YET = ["get_drivers_list"]


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
    """Stands in for the alert-history read the AI tool performs.

    The tool moved to the PAGED reader
    (``get_active_alert_history_for_account_paged``) and this fake was
    left speaking the retired ``get_alert_history`` — so both tests died
    on AttributeError rather than on anything they were written to
    check.  ``calls`` records the kwargs so the next signature drift
    shows up as a readable assertion instead of a missing attribute.
    """

    def __init__(self, rows):
        self._rows = rows
        self.calls: list[dict] = []

    async def get_active_alert_history_for_account_paged(
        self, account_id, **kw,
    ):
        self.calls.append({"account_id": account_id, **kw})
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


# ── Rollup tools: aggregation must respect scope (re-aggregate over subset) ──


@pytest.mark.asyncio
class TestRollupScopeFilter:
    async def test_events_summary_aggregates_only_scoped_vehicles(self, monkeypatch):
        import features.events.ai_tool as _events_mod

        async def fake_events(account_id, days=7):
            return [
                {"vehicle_name": "B-1", "event_name": "harsh_brake",
                 "driver_name": "X", "g_force": 1.0},
                {"vehicle_name": "A-3", "event_name": "crash",
                 "driver_name": "Y", "g_force": 2.0},
            ]

        monkeypatch.setattr(_events_mod, "_svc_events", fake_events)
        res = await _events_mod.get_events_summary(
            {"_scope_vehicles": ["B-1"]}, None, account_id=1, db=None,
        )
        assert res["total_events"] == 1                  # only B-1 counted
        assert "crash" not in res["events_by_type"]      # A-3's event excluded
        assert all(e["vehicle"] == "B-1" for e in res["most_severe"])

    async def test_events_summary_unscoped_counts_all(self, monkeypatch):
        import features.events.ai_tool as _events_mod

        async def fake_events(account_id, days=7):
            return [
                {"vehicle_name": "B-1", "event_name": "harsh_brake", "g_force": 1.0},
                {"vehicle_name": "A-3", "event_name": "crash", "g_force": 2.0},
            ]

        monkeypatch.setattr(_events_mod, "_svc_events", fake_events)
        res = await _events_mod.get_events_summary({}, None, account_id=1, db=None)
        assert res["total_events"] == 2


# ── get_undriven_vehicles (movement-based "not driven in N days") ────────────


class _FakeUndrivenDB:
    def __init__(self, rows):
        self._rows = rows

    async def get_undriven_vehicles(self, account_id, **kw):
        return list(self._rows)


@pytest.mark.asyncio
class TestUndrivenVehicles:
    async def test_filters_to_scope(self):
        from features.vehicles.ai_tool import get_undriven_vehicles
        db = _FakeUndrivenDB([
            {"vehicle_name": "B-1", "company_code": "B", "days_stopped": 5},
            {"vehicle_name": "A-3", "company_code": "A", "days_stopped": 9},
        ])
        res = await get_undriven_vehicles(
            {"_scope_vehicles": ["B-1"], "min_days": 3}, None, account_id=1, db=db,
        )
        assert res["count"] == 1
        assert res["vehicles"][0]["vehicle"] == "B-1"

    async def test_unscoped_returns_all(self):
        from features.vehicles.ai_tool import get_undriven_vehicles
        db = _FakeUndrivenDB([
            {"vehicle_name": "B-1", "days_stopped": 5},
            {"vehicle_name": "A-3", "days_stopped": 9},
        ])
        res = await get_undriven_vehicles({"min_days": 1}, None, account_id=1, db=db)
        assert res["count"] == 2

    async def test_missing_warehouse_method_errs_gracefully(self):
        from features.vehicles.ai_tool import get_undriven_vehicles

        class _NoMethodDB:
            pass

        res = await get_undriven_vehicles({"min_days": 1}, None, account_id=1, db=_NoMethodDB())
        assert "error" in res
