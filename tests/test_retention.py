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

from capabilities.data_lifecycle.retention import discover
from capabilities.data_lifecycle.retention.registry import (
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
    assert by["vehicle.timeline_minute"] == 7
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
    assert "vehicle.timeline_minute" in tenant
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
    from capabilities.data_lifecycle.retention.engine import prune_tenant_targets
    discover()
    calls: dict[str, int] = {}

    class FakeTenantDB:
        async def prune_vehicle_state_minutes(self, _a, *, days_keep):
            calls["snapshot"] = days_keep
            return 3
        async def prune_vehicle_state_hour(self, _a, *, days_keep):
            calls["hourly"] = days_keep
            return 0
        async def prune_vehicle_state_day(self, _a, *, days_keep):
            calls["daily"] = days_keep
            return 1
        async def prune_vehicle_fault_log(self, _a, *, days_keep):
            calls["fault"] = days_keep
            return 0
        async def prune_driver_efficiency_day(self, _a, *, days_keep):
            calls["driver_eff"] = days_keep
            return 0
        async def prune_score_events(self, _a, *, keep_days):
            calls["score"] = keep_days
            return 0
        async def prune_safety_event_log(self, _a, *, days_keep):
            calls["safety"] = days_keep
            return 0

    deleted = await prune_tenant_targets(FakeTenantDB(), account_id=1)
    assert calls["snapshot"] == 7
    assert calls["hourly"] == 90
    assert calls["daily"] == 730
    assert calls["fault"] == 365
    assert calls["driver_eff"] == 730
    assert calls["score"] == 90
    assert calls["safety"] == 1095
    # The engine now returns {target_key: rows_deleted} so the job can
    # aggregate per-target totals for the run telemetry.
    assert deleted["vehicle.timeline_minute"] == 3
    assert deleted["vehicle.metrics_daily"] == 1
    assert sum(deleted.values()) == 4


@pytest.mark.asyncio
async def test_record_and_get_latest_retention_runs(pg_db):
    """Run telemetry round-trips, and the newest run per target wins."""
    db = pg_db
    await db.record_retention_runs("2026-06-20T02:00:00+00:00", [
        {"target_key": "vehicle.timeline_minute", "scope": "tenant",
         "keep_days": 7, "rows_deleted": 10, "accounts": 3},
    ])
    await db.record_retention_runs("2026-06-21T02:00:00+00:00", [
        {"target_key": "vehicle.timeline_minute", "scope": "tenant",
         "keep_days": 7, "rows_deleted": 25, "accounts": 4},
        {"target_key": "email.delivery_events", "scope": "platform",
         "keep_days": 14, "rows_deleted": 2, "accounts": 0},
    ])
    latest = {r["target_key"]: r for r in await db.get_latest_retention_runs()}
    assert latest["vehicle.timeline_minute"]["rows_deleted"] == 25   # newest run wins
    assert latest["vehicle.timeline_minute"]["ran_at"] == "2026-06-21T02:00:00+00:00"
    assert latest["email.delivery_events"]["rows_deleted"] == 2


@pytest.mark.asyncio
async def test_record_and_get_scheduler_jobs(pg_db):
    """The scheduler-job snapshot round-trips and full-replaces."""
    db = pg_db
    await db.record_scheduler_jobs([
        {"job_id": "data_retention", "trigger": "cron[hour='2', minute='0']",
         "next_run_at": "2026-06-22T02:00:00+00:00",
         "category": "Storage & data", "description": "Prune retention targets"},
        {"job_id": "warehouse_vehicle_state", "trigger": "interval[0:01:00]",
         "next_run_at": "2026-06-21T12:00:00+00:00",
         "category": "Telematics", "description": "Pull live state"},
    ])
    jobs = {j["job_id"]: j for j in await db.get_scheduler_jobs()}
    assert jobs["data_retention"]["trigger"] == "cron[hour='2', minute='0']"
    assert jobs["data_retention"]["category"] == "Storage & data"
    assert jobs["warehouse_vehicle_state"]["next_run_at"] == "2026-06-21T12:00:00+00:00"

    # A fresh snapshot fully replaces the prior one (removed jobs drop out).
    await db.record_scheduler_jobs([
        {"job_id": "data_retention", "trigger": "cron[hour='2', minute='0']",
         "next_run_at": "2026-06-23T02:00:00+00:00", "description": "Prune retention targets"},
    ])
    jobs2 = {j["job_id"]: j for j in await db.get_scheduler_jobs()}
    assert set(jobs2) == {"data_retention"}
    assert jobs2["data_retention"]["next_run_at"] == "2026-06-23T02:00:00+00:00"
