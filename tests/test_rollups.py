"""The Roll-up hub must resolve the vehicle telemetry cascade with stable
job ids + cadences, and the engine must fan a stage across active accounts.

The scheduler registers one job per stage straight from this registry, so a
wrong id here = a tier the Scheduler page/operator console can't find, and a
wrong cadence = a silently mis-scheduled roll-up (the kind of regression that
once froze all back-dated odometer history).  These guard both.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("ENCRYPTION_KEY", "")


def test_vehicle_cascade_registered_with_stable_ids_and_cadences():
    from capabilities.lifecycle.rollups import discover
    from capabilities.lifecycle.rollups.registry import all_cascades, all_stages

    discover()

    assert "vehicle" in {c.name for c in all_cascades()}

    stages = {s.job_id: s for s in all_stages()}
    # The ids the scheduler + operator console key on.
    assert {
        "warehouse_state_snapshot",
        "warehouse_telemetry_hourly",
        "warehouse_metrics_daily",
    } <= set(stages)

    # Cadences unchanged from the hand-wired scheduler (5 min · :05 hourly ·
    # 00:05 UTC — the UTC pin keeps "yesterday UTC" from resolving 2 days back).
    assert stages["warehouse_state_snapshot"].cadence == {"interval_min": 5}
    assert stages["warehouse_telemetry_hourly"].cadence == {"cron": "5 * * * *"}
    assert stages["warehouse_metrics_daily"].cadence == {
        "cron": "5 0 * * *",
        "tz": "UTC",
    }

    # Every stage carries a runnable aggregation.
    for stage in stages.values():
        assert callable(stage.run)


def test_run_stage_fans_out_across_active_accounts(monkeypatch):
    """The engine decides FOR WHOM (the shared active-account iterator), the
    stage decides WHAT (its run function) — so run_stage just delegates."""
    import capabilities.lifecycle._common as _common
    from capabilities.lifecycle.rollups.engine import run_stage
    from capabilities.lifecycle.rollups.registry import RollupStage

    seen: dict = {}

    async def fake_iter(fn):
        seen["fn"] = fn

    monkeypatch.setattr(_common, "for_each_active_account", fake_iter)

    async def my_run(account_id: int) -> int:
        return 0

    stage = RollupStage("x", {"interval_min": 5}, my_run, "x")
    asyncio.run(run_stage(stage))

    assert seen["fn"] is my_run
