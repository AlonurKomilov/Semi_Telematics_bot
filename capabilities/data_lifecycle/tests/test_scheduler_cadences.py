"""Scheduler cadence contracts — the bugs that only bite on a date.

Three defects lived here, all invisible until a specific moment
arrived: a weekly roll-up that fired the wrong DAY, an hourly one that
would lose a bucket at DST, and a one-second misfire grace that dropped
any job whose moment passed while the loop was busy.  Each is pinned
here because none of them announce themselves in a log.
"""

from __future__ import annotations

from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger

_REF = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)   # a Sunday


def _stages():
    from capabilities.data_lifecycle.rollups import discover
    from capabilities.data_lifecycle.rollups.registry import all_stages
    discover()
    return {s.job_id: s for s in all_stages()}


def test_weekly_rollup_fires_on_monday():
    """APScheduler numbers crontab days from Monday=0, so the numeric
    "1" every reader takes for Monday actually means Tuesday."""
    stage = _stages()["warehouse_metrics_weekly"]
    trigger = CronTrigger.from_crontab(
        stage.cadence["cron"], timezone=stage.cadence.get("tz"),
    )
    fire = trigger.get_next_fire_time(None, _REF)
    assert fire.strftime("%A") == "Monday", (
        f"weekly roll-up fires {fire.strftime('%A')}, not Monday"
    )


def test_every_cron_stage_is_utc_pinned():
    """The server's local zone observes DST; UTC does not.  An unpinned
    hourly cron loses one bucket each autumn and skips one each spring,
    permanently — the hourly tier cannot heal a hole once its 5-minute
    snapshots age out."""
    unpinned = [
        job_id for job_id, s in _stages().items()
        if "cron" in s.cadence and s.cadence.get("tz") != "UTC"
    ]
    assert not unpinned, f"cron stages not pinned to UTC: {unpinned}"


def test_ingest_datasets_declare_a_positive_cadence():
    from capabilities.data_lifecycle.ingest import all_datasets, discover
    discover()
    for d in all_datasets():
        if "cron" in d.cadence:
            assert d.cadence.get("tz") == "UTC", d.key
        else:
            assert d.cadence["interval_min"] > 0, d.key


def test_scheduler_grants_a_real_misfire_grace():
    """One second of grace means a deploy, a GC pause or a busy host
    silently drops the run instead of running it late."""
    import re
    from pathlib import Path

    src = Path("run.py").read_text()
    match = re.search(r'"misfire_grace_time"\s*:\s*(\d+)', src)
    assert match, "no misfire grace configured — jobs are dropped, not delayed"
    grace = int(match.group(1))
    assert grace >= 60, f"misfire grace {grace}s is too tight for a restart"


def test_vehicle_stream_declares_stream_and_grain():
    """The two words stay separate: one stream name, grain as a label —
    the naming rule that stops interval-in-the-name from coming back."""
    stages = _stages()
    grains = {s.grain for s in stages.values() if s.stream == "vehicle.timeline"}
    assert grains == {"minute", "hour", "day", "week"}
    for s in stages.values():
        assert s.stream, f"{s.job_id} declares no stream"
        assert s.grain, f"{s.job_id} declares no grain"


def test_minute_snapshot_rides_a_wall_aligned_cron():
    """An interval trigger fires N seconds from process BOOT, so every
    restart mints a new second-offset and writes the deploy history
    into the minute grid (production accumulated :06/:36/:38/:16/:52
    offsets, one per restart).  Wall-clock cron pins the fire — and
    therefore the slot label — to the top of the minute."""
    stage = _stages()["warehouse_state_snapshot"]
    assert stage.cadence.get("cron") == "* * * * *", stage.cadence
    assert stage.cadence.get("tz") == "UTC"
