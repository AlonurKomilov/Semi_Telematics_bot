"""The bot's pulse, readable from any process.

Every in-process alerter (watchdog, capacity alerts, error reporter)
dies WITH the bot — a 20-hour outage on 2026-08-04 went completely
unalerted because the thing that would have warned was the thing that
was down.  The capacity sampler's minute row doubles as a
cross-process heartbeat: the API's ``/health`` reports it, and an
EXTERNAL watcher keyword-matching ``"status":"ok"`` catches a dead
bot from infrastructure that cannot die with ours.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from capabilities.platform.capacity.sampler import bot_heartbeat_age_min


@pytest.mark.asyncio
async def test_no_rows_means_unknown_not_fresh(pg_db):
    assert await bot_heartbeat_age_min(pg_db) is None


@pytest.mark.asyncio
async def test_fresh_pulse_reads_fresh_and_stale_reads_old(pg_db):
    now = datetime.now(timezone.utc)
    await pg_db.insert_system_metrics_minute(
        {"ts": (now - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M"),
         "cpu_pct": 10.0})
    age = await bot_heartbeat_age_min(pg_db)
    assert age == pytest.approx(90, abs=2)

    await pg_db.insert_system_metrics_minute(
        {"ts": now.strftime("%Y-%m-%dT%H:%M"), "cpu_pct": 10.0})
    age = await bot_heartbeat_age_min(pg_db)
    assert age is not None and age <= 2
