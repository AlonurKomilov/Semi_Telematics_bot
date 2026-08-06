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


class TestTimeGrid:
    """Sample labels sit ON the grid; the honest moment rides in
    source_ts.  Both minute-tier writers must share ONE flooring rule —
    two local rules is how the grid forked into :00 backfill rows and
    :13 live rows in the first place."""

    def test_label_floors_to_the_minute(self):
        from capabilities.data_lifecycle.timegrid import floor_to_slot
        ts = datetime(2026, 8, 3, 7, 26, 13, tzinfo=timezone.utc)
        assert floor_to_slot(ts) == "2026-08-03T07:26:00+00:00"

    def test_both_minute_writers_agree(self):
        from capabilities.data_lifecycle.timegrid import floor_to_slot
        from capabilities.integrations.shared import history_backfill as hb
        ts = datetime(2026, 8, 3, 7, 59, 59, tzinfo=timezone.utc)
        assert hb._floor_to_slot(ts) == floor_to_slot(ts, hb.SLOT_SECONDS)


@pytest.mark.asyncio
async def test_source_ts_propagates_snapshot_to_hourly_to_daily(pg_db):
    """The cascade carries the freshest sample time upward, minting
    nothing — and the samples' resolved identity (``registry_id``)
    rides the exact same path.  A tier row without identity is
    invisible to every registry-joined consumer, which is how a
    perfectly current warehouse once *looked* two weeks stale."""
    from features.vehicles.warehouse import aggregator as agg

    acct = 46
    hour = datetime(2026, 7, 20, 9, tzinfo=timezone.utc)
    gauges = {0: (12.9, 92.0), 5: (13.4, 85.0), 10: (13.1, 88.0)}
    await pg_db.upsert_vehicle_state_minutes(acct, [
        {"vehicle_id": "v1",
         "captured_at": hour.replace(minute=m).isoformat(),
         "odometer_mi": 1000 + m, "engine_state": "moving", "speed_mph": 55,
         "source_ts": f"2026-07-20T08:{40 + m // 5}:00Z",
         "registry_id": 37,
         "battery_v": gauges[m][0], "coolant_c": gauges[m][1]}
        for m in (0, 5, 10)
    ])
    await agg._aggregate_hour_window(pg_db, acct, hour)

    def tier_cols(table):
        return ("SELECT source_ts, registry_id, battery_min_v, "
                f"coolant_max_c, oil_min_psi FROM {table} "
                "WHERE account_id = ? AND bucket_start = ?")
    cur = await pg_db._db.execute(tier_cols("vehicle_state_hour"), (acct, "2026-07-20T09:00:00"))
    hourly_ts, hourly_rid, batt_min, cool_max, oil_min = await cur.fetchone()
    assert hourly_ts == "2026-07-20T08:42:00Z"   # the freshest sample, verbatim
    assert hourly_rid == 37
    # Gauges climb the ladder: MIN catches the battery's low, MAX the
    # coolant's peak, and a gauge that never reported stays NULL.
    assert batt_min == pytest.approx(12.9)
    assert cool_max == pytest.approx(92.0)
    assert oil_min is None

    await agg._aggregate_day_window(pg_db, acct, hour.replace(hour=0))
    cur = await pg_db._db.execute(tier_cols("vehicle_state_day"), (acct, "2026-07-20"))
    daily_ts, daily_rid, batt_min, cool_max, oil_min = await cur.fetchone()
    assert daily_ts == "2026-07-20T08:42:00Z"
    assert daily_rid == 37
    assert batt_min == pytest.approx(12.9)
    assert cool_max == pytest.approx(92.0)

    # 2026-07-20 is a Monday — roll the same day into its week bucket.
    await agg._aggregate_week_window(pg_db, acct, hour.replace(hour=0))
    cur = await pg_db._db.execute(tier_cols("vehicle_state_week"), (acct, "2026-07-20"))
    weekly_ts, weekly_rid, batt_min, cool_max, oil_min = await cur.fetchone()
    assert weekly_ts == "2026-07-20T08:42:00Z"
    assert weekly_rid == 37
    assert batt_min == pytest.approx(12.9)
    assert cool_max == pytest.approx(92.0)


@pytest.mark.asyncio
async def test_replay_without_sources_keeps_the_banked_world_time(pg_db):
    """Re-running a bucket whose snapshots aged out must not erase the
    age it honestly knew — same COALESCE rule the odometer bank uses."""
    acct = 47
    await pg_db.upsert_vehicle_state_hour(acct, [{
        "vehicle_id": "v1", "hour_utc": "2026-07-20T09:00:00",
        "miles": 10.0, "source_ts": "2026-07-20T08:42:00Z",
    }])
    await pg_db.upsert_vehicle_state_hour(acct, [{
        "vehicle_id": "v1", "hour_utc": "2026-07-20T09:00:00",
        "miles": 10.0, "source_ts": None,
    }])
    cur = await pg_db._db.execute(
        "SELECT source_ts FROM vehicle_state_hour WHERE account_id = ? "
        "AND bucket_start = ?",
        (acct, "2026-07-20T09:00:00"))
    assert (await cur.fetchone())[0] == "2026-07-20T08:42:00Z"


@pytest.mark.asyncio
async def test_live_reader_carries_source_ts_to_the_snapshot_copy(pg_db):
    """The minute tier's source_ts arrives from the live row THROUGH
    ``get_vehicle_state`` — a reader that omits the column starves the
    whole cascade silently (12,160 NULL minute rows in one day before
    this was caught)."""
    acct = 50
    await pg_db.upsert_vehicle_state(acct, [
        {"vehicle_id": "v1", "vehicle_name": "401", "company_code": "PTG",
         "speed_mph": 10.0, "captured_at": "2026-08-03T10:00:05Z",
         "source_ts": "2026-08-03T09:59:58Z"},
    ])
    rows = await pg_db.get_vehicle_state(acct)
    assert rows and rows[0].get("source_ts") == "2026-08-03T09:59:58Z"


@pytest.mark.asyncio
async def test_asset_grain_surfaces_agree_with_storage(pg_db):
    """The ten ``<stream>_<grain>`` views are the warehouse's official
    read surface (migration 185): readers address clean per-grain
    names; the physical layer stays free to change behind them.  One
    minute sample must feed BOTH streams' minute surface; one tier row
    must split by stream (miles on state, gauges on health)."""
    acct = 52
    await pg_db.upsert_vehicle_state_minutes(acct, [
        {"vehicle_id": "v1", "captured_at": "2026-08-03T10:01:00+00:00",
         "odometer_mi": 900.0, "engine_state": "moving", "speed_mph": 55.0,
         "battery_v": 13.2, "coolant_c": 88.0},
    ])
    await pg_db.upsert_vehicle_state_day(acct, [
        {"vehicle_id": "v1", "day_utc": "2026-08-02", "miles": 123.0,
         "battery_min_v": 12.8, "coolant_max_c": 93.0},
    ])

    for stream in ("vehicle_state", "vehicle_health"):
        for grain in ("live", "minute", "hour", "day", "week"):
            cur = await pg_db._db.execute(
                "SELECT to_regclass(?)", (f"warehouse.{stream}_{grain}",))
            assert (await cur.fetchone())[0] is not None, f"{stream}_{grain}"

    cur = await pg_db._db.execute(
        "SELECT speed_mph FROM warehouse.vehicle_state_minute "
        "WHERE account_id = ?", (acct,))
    assert float((await cur.fetchone())[0]) == 55.0
    cur = await pg_db._db.execute(
        "SELECT battery_v FROM warehouse.vehicle_health_minute "
        "WHERE account_id = ?", (acct,))
    assert float((await cur.fetchone())[0]) == pytest.approx(13.2)
    cur = await pg_db._db.execute(
        "SELECT miles FROM warehouse.vehicle_state_day "
        "WHERE account_id = ?", (acct,))
    assert float((await cur.fetchone())[0]) == 123.0
    cur = await pg_db._db.execute(
        "SELECT battery_min_v, coolant_max_c FROM warehouse.vehicle_health_day "
        "WHERE account_id = ?", (acct,))
    batt_min, cool_max = await cur.fetchone()
    assert batt_min == pytest.approx(12.8)
    assert cool_max == pytest.approx(93.0)


@pytest.mark.asyncio
async def test_secondary_writers_survive_their_conflict_path(pg_db):
    """health / faults / weather upserts, run twice so the second pass
    takes ON CONFLICT DO UPDATE.  These three shipped with an ambiguous
    unqualified ``source_ts`` in that branch — Postgres rejects the
    whole statement at parse time, and with no test on this path all
    three feeds silently froze for a day (found 2026-08-03)."""
    acct = 51
    for ts in ("2026-08-03T09:00:00Z", "2026-08-03T09:05:00Z"):
        await pg_db.upsert_vehicle_health_snapshots(acct, [
            {"vehicle_id": "v1", "vehicle_name": "401", "company_code": "PTG",
             "alert_count": 1, "raw": {"battery_v": 13.2},
             "captured_at": ts, "source_ts": ts},
        ])
        await pg_db.upsert_vehicle_fault_snapshot(acct, [
            {"vehicle_id": "v1", "vehicle_name": "401", "company_code": "PTG",
             "dtc_count": 2, "raw": {}, "captured_at": ts, "source_ts": ts},
        ])
        await pg_db.upsert_aggregate_weather_snapshots(acct, [
            {"vehicle_id": "v1", "vehicle_name": "401", "company_code": "PTG",
             "temp_f": 88.0, "raw": {}, "captured_at": ts, "source_ts": ts},
        ])
    for table in ("vehicle_health_snapshot", "vehicle_fault_snapshot",
                  "weather_snapshot"):
        cur = await pg_db._db.execute(
            f"SELECT source_ts FROM {table} WHERE account_id = ?", (acct,))
        row = await cur.fetchone()
        assert row and row[0] == "2026-08-03T09:05:00Z", table


@pytest.mark.asyncio
async def test_no_warehouse_table_shadows_in_public(pg_db):
    """The pool search_path is ``public,warehouse`` — public FIRST, so
    unqualified CREATEs keep landing in public.  The price of that
    order: a stray unqualified ``CREATE TABLE IF NOT EXISTS`` of a
    warehouse name would recreate it in public and silently hijack
    every read.  A fresh install must end with the family living in
    the warehouse schema and NOTHING shadowing it in public."""
    old_and_new = [
        "vehicle_state_live", "vehicle_state_minute",
        "vehicle_state_hour", "vehicle_state_day", "vehicle_state_week",
        "vehicle_health_snapshot", "vehicle_fault_snapshot",
        "vehicle_fault_detail", "safety_event_log", "geofence_definitions",
        "ingest_runs", "driver_efficiency", "weather_snapshot",
        "efficiency_snapshot", "ingest_orphans", "vehicle_timeline",
        "vehicle_state", "vehicle_state_snapshot", "vehicle_telemetry",
        "driver_efficiency_daily", "aggregate_weather_snapshot",
        "aggregate_efficiency_snapshot", "warehouse_ingest_orphans",
    ]
    for name in old_and_new:
        cur = await pg_db._db.execute(
            "SELECT to_regclass(?)", (f"public.{name}",))
        assert (await cur.fetchone())[0] is None, (
            f"public.{name} exists — it shadows the warehouse schema"
        )
    for name in ["vehicle_state_live", "vehicle_state_hour", "vehicle_state_day",
                 "vehicle_state_week", "driver_efficiency",
                 "weather_snapshot", "efficiency_snapshot", "ingest_orphans",
                 "vehicle_timeline"]:
        cur = await pg_db._db.execute(
            "SELECT to_regclass(?)", (f"warehouse.{name}",))
        assert (await cur.fetchone())[0] is not None, f"warehouse.{name} missing"


@pytest.mark.asyncio
async def test_catalog_comments_describe_the_family(pg_db):
    """The DB itself says which tables are warehouse and who owns them
    — names accreted across eras and can't (renames are wire surgery).
    The sync is registry-driven, so a new dataset self-describes."""
    from capabilities.data_lifecycle.catalog import sync_table_comments

    synced = await sync_table_comments(pg_db)
    assert synced >= 10   # 9 datasets' tables + build output + ledgers

    async def comment_of(table: str) -> str:
        cur = await pg_db._db.execute(
            "SELECT obj_description(?::regclass, 'pg_class')", (table,))
        return (await cur.fetchone())[0] or ""

    assert "vehicles.state (owner: vehicles)" in await comment_of("vehicle_state_live")
    assert "grain minute" in await comment_of("vehicle_state_minute")
    assert "grain hour" in await comment_of("vehicle_state_hour")
    assert "grain week" in await comment_of("vehicle_state_week")
    assert "events.safety (owner: events)" in await comment_of("safety_event_log")
    # Operational feature tables are NOT the warehouse — no family tag.
    assert "warehouse" not in await comment_of("vehicles")


@pytest.mark.asyncio
async def test_every_contract_table_carries_the_column(pg_db):
    """Guard: a table registered to the contract cannot silently lack it."""
    cur = await pg_db._db.execute(
        "SELECT table_name FROM information_schema.columns "
        "WHERE column_name = 'source_ts' AND table_schema IN ('public', 'warehouse')"
    )
    have = {r[0] for r in await cur.fetchall()}
    required = {
        "vehicle_state_live", "vehicle_state_minute",
        "vehicle_state_hour", "vehicle_state_day", "vehicle_state_week",
        "vehicle_health_snapshot", "vehicle_fault_snapshot",
        "vehicle_fault_detail", "safety_event_log",
        "driver_efficiency", "geofence_definitions",
        "weather_snapshot", "efficiency_snapshot",
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
    await pg_db.upsert_vehicle_state_minutes(acct, [
        {"vehicle_id": "v1", "captured_at": "2026-08-03T10:01:00+00:00",
         "odometer_mi": 900.0, "engine_state": "moving", "speed_mph": 55.0,
         "battery_v": 13.2},
    ])
    await pg_db.upsert_vehicle_state_day(acct, [
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
    # Gauge columns are kind-split the same way: the minute sample
    # exposes the instantaneous reading, never the aggregate stats.
    cur = await pg_db._db.execute(
        "SELECT battery_v, battery_min_v FROM vehicle_timeline "
        "WHERE account_id = ? AND grain = 'minute'", (acct,))
    batt_v, batt_min = await cur.fetchone()
    assert batt_v == pytest.approx(13.2) and batt_min is None
