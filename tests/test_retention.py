"""Retention hub tests.

Behavior-preservation is the contract: the windows the hub resolves must
equal the legacy hardcoded prune windows, and the engine must call the
existing storage prune methods with those windows.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from capabilities.retention import discover
from capabilities.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
    resolve,
)


async def _noop(_days: int) -> int:
    return 0


def test_windows_match_legacy_prune():
    """The migrated policies must reproduce the old hardcoded windows."""
    discover()
    by = {r.target.key: r.keep_days for r in resolve()}
    assert by["vehicle.timeline_5min"] == 7
    assert by["vehicle.timeline_hourly"] == 90
    assert by["vehicle.metrics_daily"] == 730
    assert by["scorecards.score_history"] == 90
    assert by["email.delivery_events"] == 14


def test_growing_table_target_windows():
    """The feature-owned growing-table targets resolve to their chosen windows."""
    discover()
    by = {r.target.key: r.keep_days for r in resolve()}
    assert by["driver.efficiency_daily"] == 730   # match the vehicle daily tier
    assert by["safety_events"] == 1095            # 3y — FMCSA/litigation lookback
    assert by["vehicle.faults"] == 365            # cleared-fault diagnostic history


def test_scope_partitions_targets():
    discover()
    tenant = {r.target.key for r in resolve(scope="tenant")}
    platform = {r.target.key for r in resolve(scope="platform")}
    assert "vehicle.timeline_5min" in tenant
    assert "email.delivery_events" in platform
    assert "email.delivery_events" not in tenant


def test_keep_days_is_max_across_needs():
    register_target(RetentionTarget("_t_max", "test", "tenant",
                                    lambda _db, _a, d: _noop(d)))
    register_need(RetentionNeed("featA", "_t_max", 30, "a"))
    register_need(RetentionNeed("featB", "_t_max", 90, "b"))
    r = next(x for x in resolve() if x.target.key == "_t_max")
    assert r.keep_days == 90  # the hungriest consumer wins


def test_unclaimed_target_never_pruned():
    register_target(RetentionTarget("_t_unclaimed", "test", "tenant",
                                    lambda _db, _a, d: _noop(d)))
    assert "_t_unclaimed" not in {r.target.key for r in resolve()}


@pytest.mark.asyncio
async def test_engine_calls_existing_methods_with_resolved_windows():
    """The tenant pass must invoke the real storage prune methods with the
    resolved keep-days (behavior-preserving)."""
    from capabilities.retention.engine import prune_tenant_targets
    discover()
    calls: dict[str, int] = {}

    class FakeTenantDB:
        async def prune_vehicle_state_snapshots(self, _a, *, days_keep):
            calls["snapshot"] = days_keep
            return 3
        async def prune_vehicle_telemetry_hourly(self, _a, *, days_keep):
            calls["hourly"] = days_keep
            return 0
        async def prune_vehicle_metrics_daily(self, _a, *, days_keep):
            calls["daily"] = days_keep
            return 1
        async def prune_score_events(self, _a, *, keep_days):
            calls["score"] = keep_days
            return 0

    deleted = await prune_tenant_targets(FakeTenantDB(), account_id=1)
    assert calls["snapshot"] == 7
    assert calls["hourly"] == 90
    assert calls["daily"] == 730
    assert calls["score"] == 90
    assert deleted >= 4  # 3 + 1 from snapshot + daily
