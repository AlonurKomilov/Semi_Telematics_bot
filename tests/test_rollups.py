"""The Roll-up hub must resolve the vehicle telemetry cascade with stable
job ids + cadences, and the engine must fan a stage across active accounts.

The scheduler registers one job per stage straight from this registry, so a
wrong id here = a tier the Scheduler page/operator console can't find, and a
wrong cadence = a silently mis-scheduled roll-up (the kind of regression that
once froze all back-dated odometer history).  These guard both.
"""

from __future__ import annotations

import pytest

import asyncio
import os

os.environ.setdefault("ENCRYPTION_KEY", "")


def test_vehicle_cascade_registered_with_stable_ids_and_cadences():
    from capabilities.data_lifecycle.rollups import discover
    from capabilities.data_lifecycle.rollups.registry import all_cascades, all_stages

    discover()

    assert "vehicle" in {c.name for c in all_cascades()}

    stages = {s.job_id: s for s in all_stages()}
    # The ids the scheduler + operator console key on.
    assert {
        "warehouse_state_snapshot",
        "warehouse_telemetry_hourly",
        "warehouse_metrics_daily",
        "warehouse_metrics_weekly",
    } <= set(stages)

    # The full cadence contract: every stage wall-aligned cron, UTC-pinned
    # (the pin keeps "yesterday UTC" from resolving 2 days back at 00:05,
    # and DST from eating an hourly bucket).  The minute snapshot rides
    # cron too — an interval fires from process boot and mints a new
    # second-offset in the minute grid at every restart.
    assert stages["warehouse_state_snapshot"].cadence == {
        "cron": "* * * * *", "tz": "UTC",
    }
    assert stages["warehouse_telemetry_hourly"].cadence == {
        "cron": "5 * * * *", "tz": "UTC",
    }
    assert stages["warehouse_metrics_daily"].cadence == {
        "cron": "5 0 * * *", "tz": "UTC",
    }
    assert stages["warehouse_metrics_weekly"].cadence == {
        "cron": "10 0 * * mon", "tz": "UTC",
    }

    # Every stage carries a runnable aggregation.
    for stage in stages.values():
        assert callable(stage.run)


def test_run_stage_fans_out_across_active_accounts(monkeypatch):
    """The engine decides FOR WHOM (the shared active-account iterator), the
    stage decides WHAT (its run function) — so run_stage just delegates."""
    import capabilities.data_lifecycle._common as _common
    from capabilities.data_lifecycle.rollups.engine import run_stage
    from capabilities.data_lifecycle.rollups.registry import RollupStage

    seen: dict = {}

    async def fake_iter(fn):
        seen["fn"] = fn

    monkeypatch.setattr(_common, "for_each_active_account", fake_iter)

    async def my_run(account_id: int) -> int:
        return 0

    stage = RollupStage("x", {"interval_min": 5}, my_run, "x")
    asyncio.run(run_stage(stage))

    assert seen["fn"] is my_run


@pytest.mark.asyncio
async def test_reroll_hook_is_late_bound_and_discoverable(monkeypatch):
    """The cascade's reroll must (a) exist after a plain discover() —
    the cold-worker path the backfill takes — and (b) resolve the
    aggregator's CURRENT function at call time, so monkeypatches and
    hot-fixes take effect.  A bound-at-registration reference silently
    ignored both (review finding, 2026-08-06)."""
    from capabilities.data_lifecycle.rollups import discover
    from capabilities.data_lifecycle.rollups.registry import get_cascade
    import features.vehicles.warehouse.aggregator as agg

    discover()
    cascade = get_cascade("vehicle")
    assert cascade is not None and cascade.reroll is not None

    called = {}

    async def fake(account_id, *, days):
        called["args"] = (account_id, days)
        return {"hours": 0, "days": 0, "hourly_rows": 0, "daily_rows": 0}

    monkeypatch.setattr(agg, "backfill_aggregations", fake)
    out = await cascade.reroll(7, days=3)
    assert called["args"] == (7, 3) and out["hours"] == 0
