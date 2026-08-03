"""The staleness contract — world-time, propagated and never minted.

``source_ts`` is when the provider last saw the world move.  Write
times advance every tick whether or not the world does — that is how a
truck parked since May stayed indistinguishable from one reporting this
minute, and how a 43-hour outage read as normal data for weeks.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from capabilities.data_lifecycle.staleness import (
    data_age_minutes,
    freshest,
    is_stale,
)

_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


class TestHelpers:
    def test_age_of_a_known_time(self):
        assert data_age_minutes("2026-08-02T11:30:00Z", now=_NOW) == pytest.approx(30.0)
        assert data_age_minutes("2026-08-02T11:30:00+00:00", now=_NOW) == pytest.approx(30.0)

    def test_unknown_age_is_none_and_stale(self):
        # NULL / garbage cannot be proven fresh — and these helpers
        # exist for callers deciding whether to trust a number.
        for bad in (None, "", "not-a-time"):
            assert data_age_minutes(bad, now=_NOW) is None
            assert is_stale(bad, 999999, now=_NOW)

    def test_sla_boundary(self):
        assert not is_stale("2026-08-02T11:30:00Z", 31, now=_NOW)
        assert is_stale("2026-08-02T11:30:00Z", 29, now=_NOW)

    def test_freshest_mixes_suffix_styles_and_keeps_originals(self):
        newest = freshest(
            "2026-08-02T10:00:00Z",
            "2026-08-02T11:00:00+00:00",
            "",
            None,
        )
        assert newest == "2026-08-02T11:00:00+00:00"
        assert freshest(None, "") is None


@pytest.mark.asyncio
async def test_source_ts_propagates_snapshot_to_hourly_to_daily(pg_db):
    """The cascade carries the freshest sample time upward, minting
    nothing — and the samples' resolved identity (``registry_id``)
    rides the exact same path.  A tier row without identity is
    invisible to every registry-joined consumer, which is how a
    perfectly current warehouse once *looked* two weeks stale."""
    from capabilities.warehouse.telemetry import aggregator as agg

    acct = 46
    hour = datetime(2026, 7, 20, 9, tzinfo=timezone.utc)
    await pg_db.upsert_vehicle_state_snapshots(acct, [
        {"vehicle_id": "v1",
         "captured_at": hour.replace(minute=m).isoformat(),
         "odometer_mi": 1000 + m, "engine_state": "moving", "speed_mph": 55,
         "source_ts": f"2026-07-20T08:{40 + m // 5}:00Z",
         "registry_id": 37}
        for m in (0, 5, 10)
    ])
    await agg._aggregate_hour_window(pg_db, acct, hour)

    cur = await pg_db._db.execute(
        "SELECT source_ts, registry_id FROM vehicle_telemetry WHERE account_id = ? "
        "AND granularity = 'hourly' AND bucket_start = ?",
        (acct, "2026-07-20T09:00:00"))
    hourly_ts, hourly_rid = await cur.fetchone()
    assert hourly_ts == "2026-07-20T08:42:00Z"   # the freshest sample, verbatim
    assert hourly_rid == 37

    await agg._aggregate_day_window(pg_db, acct, hour.replace(hour=0))
    cur = await pg_db._db.execute(
        "SELECT source_ts, registry_id FROM vehicle_telemetry WHERE account_id = ? "
        "AND granularity = 'daily' AND bucket_start = ?",
        (acct, "2026-07-20"))
    daily_ts, daily_rid = await cur.fetchone()
    assert daily_ts == "2026-07-20T08:42:00Z"
    assert daily_rid == 37

    # 2026-07-20 is a Monday — roll the same day into its week bucket.
    await agg._aggregate_week_window(pg_db, acct, hour.replace(hour=0))
    cur = await pg_db._db.execute(
        "SELECT source_ts, registry_id FROM vehicle_telemetry WHERE account_id = ? "
        "AND granularity = 'weekly' AND bucket_start = ?",
        (acct, "2026-07-20"))
    weekly_ts, weekly_rid = await cur.fetchone()
    assert weekly_ts == "2026-07-20T08:42:00Z"
    assert weekly_rid == 37


@pytest.mark.asyncio
async def test_replay_without_sources_keeps_the_banked_world_time(pg_db):
    """Re-running a bucket whose snapshots aged out must not erase the
    age it honestly knew — same COALESCE rule the odometer bank uses."""
    acct = 47
    await pg_db.upsert_vehicle_telemetry_hourly(acct, [{
        "vehicle_id": "v1", "hour_utc": "2026-07-20T09:00:00",
        "miles": 10.0, "source_ts": "2026-07-20T08:42:00Z",
    }])
    await pg_db.upsert_vehicle_telemetry_hourly(acct, [{
        "vehicle_id": "v1", "hour_utc": "2026-07-20T09:00:00",
        "miles": 10.0, "source_ts": None,
    }])
    cur = await pg_db._db.execute(
        "SELECT source_ts FROM vehicle_telemetry WHERE account_id = ? "
        "AND granularity = 'hourly' AND bucket_start = ?",
        (acct, "2026-07-20T09:00:00"))
    assert (await cur.fetchone())[0] == "2026-07-20T08:42:00Z"


@pytest.mark.asyncio
async def test_every_contract_table_carries_the_column(pg_db):
    """Guard: a table registered to the contract cannot silently lack it."""
    cur = await pg_db._db.execute(
        "SELECT table_name FROM information_schema.columns "
        "WHERE column_name = 'source_ts' AND table_schema = 'public'"
    )
    have = {r[0] for r in await cur.fetchall()}
    required = {
        "vehicle_state", "vehicle_state_snapshot", "vehicle_telemetry",
        "vehicle_health_snapshot", "vehicle_fault_snapshot",
        "vehicle_fault_detail", "safety_event_log",
        "driver_efficiency_daily", "geofence_definitions",
        "aggregate_weather_snapshot", "aggregate_efficiency_snapshot",
    }
    missing = required - have
    assert not missing, f"tables missing source_ts: {sorted(missing)}"


@pytest.mark.asyncio
async def test_vehicle_timeline_view_agrees_with_its_tables(pg_db):
    """The view is a catalog, not a copy — it must never disagree with
    the tables underneath, and its grain vocabulary must be the clean
    one (live/minute/hour/day/week) even though storage keeps legacy
    values."""
    acct = 49
    await pg_db.upsert_vehicle_state(acct, [
        {"vehicle_id": "v1", "vehicle_name": "401", "company_code": "PTG",
         "speed_mph": 55.0, "captured_at": "2026-08-03T10:00:00Z"},
    ])
    await pg_db.upsert_vehicle_state_snapshots(acct, [
        {"vehicle_id": "v1", "captured_at": "2026-08-03T10:01:00+00:00",
         "odometer_mi": 900.0, "engine_state": "moving", "speed_mph": 55.0},
    ])
    await pg_db.upsert_vehicle_metrics_daily(acct, [
        {"vehicle_id": "v1", "day_utc": "2026-08-02", "miles": 123.0},
    ])

    cur = await pg_db._db.execute(
        "SELECT grain, kind, COUNT(*) FROM vehicle_timeline "
        "WHERE account_id = ? GROUP BY grain, kind ORDER BY grain",
        (acct,))
    got = {(r[0], r[1]): r[2] for r in await cur.fetchall()}
    assert got == {
        ("day", "aggregate"): 1,
        ("live", "sample"): 1,
        ("minute", "sample"): 1,
    }

    # The aggregate row's numbers ride through unchanged.
    cur = await pg_db._db.execute(
        "SELECT miles FROM vehicle_timeline "
        "WHERE account_id = ? AND grain = 'day'", (acct,))
    assert float((await cur.fetchone())[0]) == 123.0
    # Sample columns stay honest on aggregate rows: no fake position.
    cur = await pg_db._db.execute(
        "SELECT lat, speed_mph FROM vehicle_timeline "
        "WHERE account_id = ? AND grain = 'day'", (acct,))
    lat, speed = await cur.fetchone()
    assert lat is None and speed is None
