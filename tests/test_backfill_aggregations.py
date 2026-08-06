"""Tests for the aggregation backfill chain.

Covers:

  * ``_aggregate_hour_window`` and ``_aggregate_day_window`` accept
    arbitrary windows — proves the live cron path and the backfill
    path share the same body.
  * ``backfill_aggregations`` is idempotent — re-running produces the
    same rows (UPSERT semantics).
  * ``backfill_aggregations`` populates the daily table from snapshot
    data so the calendar's median-velocity path activates immediately
    after a fresh history backfill.
  * Catalog default: ``history_backfill`` is now enabled-by-default so
    fresh connects auto-trigger.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio


# ── Catalog default ─────────────────────────────────────────────


def test_history_backfill_default_is_enabled():
    """If this changes, the auto-trigger-on-connect flow stops working
    because the route gates on ``feature_defaults[HISTORY_BACKFILL]
    .enabled``.  Locking the default here so a future toggle flip is
    impossible without updating both places."""
    from adapters.telematics import PROVIDER_CATALOG
    from adapters.telematics.protocol import Capability

    samsara = PROVIDER_CATALOG["samsara"]
    cfg = samsara.feature_defaults.get(Capability.HISTORY_BACKFILL)
    assert cfg is not None
    assert cfg.get("enabled") is True


# ── _aggregate_hour_window arbitrary-window contract ────────────


@pytest.mark.asyncio
async def test_aggregate_hour_window_accepts_arbitrary_hour():
    """The helper must accept any hour_start, not just 'just-closed
    hour'.  Mock the tenant DB and verify the SQL was bound to the
    requested window, not to ``now``."""
    from features.vehicles.warehouse.aggregator import _aggregate_hour_window

    tenant = MagicMock()
    cur = MagicMock()
    cur.fetchall = AsyncMock(return_value=[])
    tenant._db.execute = AsyncMock(return_value=cur)
    tenant.upsert_vehicle_telemetry_hourly = AsyncMock(return_value=0)

    historical_hour = datetime(2026, 5, 14, 7, 0, tzinfo=timezone.utc)
    await _aggregate_hour_window(tenant, 42, historical_hour)

    # Two execute calls (safety events + snapshot rollup) — both must
    # have been bound with the requested hour, not ``now``.  Checked by
    # membership rather than position: the queries also carry tuning
    # constants, and where those sit depends on the SQL's shape.
    calls = tenant._db.execute.call_args_list
    assert len(calls) >= 2
    for call in calls:
        params = call.args[1]
        assert 42 in params
        assert "2026-05-14T07:00:00+00:00" in params
        assert "2026-05-14T08:00:00+00:00" in params


@pytest.mark.asyncio
async def test_aggregate_day_window_accepts_arbitrary_day():
    from features.vehicles.warehouse.aggregator import _aggregate_day_window

    tenant = MagicMock()
    cur = MagicMock()
    cur.fetchall = AsyncMock(return_value=[])
    tenant._db.execute = AsyncMock(return_value=cur)
    tenant.upsert_vehicle_metrics_daily = AsyncMock(return_value=0)

    historical_day = datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc)
    await _aggregate_day_window(tenant, 42, historical_day)

    call = tenant._db.execute.call_args
    params = call.args[1]
    assert params[0] == 42
    # hour_utc bounds = [2026-05-14T00:00, 2026-05-15T00:00)
    assert params[1] == "2026-05-14T00:00:00"
    assert params[2] == "2026-05-15T00:00:00"


# ── _aggregate_week_window contract ─────────────────────────────


@pytest.mark.asyncio
async def test_aggregate_week_window_rolls_daily_into_weekly():
    """The weekly window reads the DAILY granularity over the whole 7-day
    span and upserts one weekly row per vehicle, carrying the latest EOD
    odometer in the week."""
    from features.vehicles.warehouse.aggregator import _aggregate_week_window

    tenant = MagicMock()
    cur = MagicMock()
    # (vehicle_id, miles, drive_min, idle_min, max_speed, avg_fuel,
    #  harsh, fault_eod, odometer_eod, engine_hours_eod)
    cur.fetchall = AsyncMock(return_value=[
        ("v1", 700.0, 300.0, 60.0, 70.0, 50.0, 3, 0, 12345.0, 800.0),
    ])
    tenant._db.execute = AsyncMock(return_value=cur)
    tenant.upsert_vehicle_metrics_weekly = AsyncMock(return_value=1)

    monday = datetime(2026, 5, 11, 0, 0, tzinfo=timezone.utc)  # an ISO Monday
    n = await _aggregate_week_window(tenant, 7, monday)
    assert n == 1

    # Read bound to the whole week [2026-05-11, 2026-05-18) on the daily grain.
    params = tenant._db.execute.call_args.args[1]
    assert params == (7, "2026-05-11", "2026-05-18")

    # The summed row was forwarded to the weekly upsert.
    rows = tenant.upsert_vehicle_metrics_weekly.call_args.args[1]
    assert rows[0]["vehicle_id"] == "v1"
    assert rows[0]["week_utc"] == "2026-05-11"
    assert rows[0]["miles"] == 700.0
    assert rows[0]["odometer_eod"] == 12345.0


# ── backfill_aggregations contract ──────────────────────────────


@pytest.mark.asyncio
async def test_backfill_aggregations_walks_each_hour_in_window(monkeypatch):
    """30-day backfill should call _aggregate_hour_window 721 times
    (30 days × 24 hours + the in-progress hour) and _aggregate_day_window
    30 times.  The trailing in-progress hour gets re-aggregated by the
    next live cron tick — UPSERT semantics make this safe."""
    from features.vehicles.warehouse import aggregator as ingestor

    tenant = MagicMock()
    monkeypatch.setattr(
        ingestor, "get_tenant_db", AsyncMock(return_value=tenant),
    )

    hour_calls: list = []
    day_calls: list = []

    async def fake_hour(tenant_, account_id, hour_start):
        hour_calls.append(hour_start)
        return 0

    async def fake_day(tenant_, account_id, day_start):
        day_calls.append(day_start)
        return 0

    monkeypatch.setattr(ingestor, "_aggregate_hour_window", fake_hour)
    monkeypatch.setattr(ingestor, "_aggregate_day_window", fake_day)

    result = await ingestor.backfill_aggregations(42, days=30)
    assert result["hours"] == 30 * 24 + 1
    assert result["days"] == 30
    assert len(hour_calls) == 30 * 24 + 1
    assert len(day_calls) == 30
    # Each call should be exactly 1 hour / 1 day apart, in chronological order.
    assert hour_calls == sorted(hour_calls)
    for prev, cur in zip(hour_calls, hour_calls[1:]):
        assert cur - prev == timedelta(hours=1)
    for prev, cur in zip(day_calls, day_calls[1:]):
        assert cur - prev == timedelta(days=1)


@pytest.mark.asyncio
async def test_backfill_aggregations_smaller_window(monkeypatch):
    """7-day backfill should call 7*24 + 1 = 169 hours, 7 days
    (the +1 is the in-progress hour, see test above)."""
    from features.vehicles.warehouse import aggregator as ingestor

    tenant = MagicMock()
    monkeypatch.setattr(
        ingestor, "get_tenant_db", AsyncMock(return_value=tenant),
    )
    monkeypatch.setattr(
        ingestor, "_aggregate_hour_window", AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        ingestor, "_aggregate_day_window", AsyncMock(return_value=0),
    )

    result = await ingestor.backfill_aggregations(42, days=7)
    assert result["hours"] == 7 * 24 + 1
    assert result["days"] == 7


@pytest.mark.asyncio
async def test_backfill_aggregations_continues_when_one_hour_fails(monkeypatch):
    """A per-hour aggregation failure should log + skip, not abort
    the whole 30-day backfill — partial data is still useful."""
    from features.vehicles.warehouse import aggregator as ingestor

    tenant = MagicMock()
    monkeypatch.setattr(
        ingestor, "get_tenant_db", AsyncMock(return_value=tenant),
    )

    call_count = {"n": 0}

    async def flaky_hour(*_a, **_k):
        call_count["n"] += 1
        if call_count["n"] == 5:
            raise RuntimeError("transient DB error")
        return 1

    monkeypatch.setattr(ingestor, "_aggregate_hour_window", flaky_hour)
    monkeypatch.setattr(
        ingestor, "_aggregate_day_window", AsyncMock(return_value=0),
    )

    result = await ingestor.backfill_aggregations(42, days=2)
    # 2*24 + 1 = 49 hours (the +1 is the in-progress hour); one raised,
    # the rest returned 1.
    assert result["hours"] == 49
    assert result["hourly_rows"] == 48


@pytest.mark.asyncio
async def test_backfill_aggregations_returns_zero_when_no_tenant(monkeypatch):
    from features.vehicles.warehouse import aggregator as ingestor

    monkeypatch.setattr(
        ingestor, "get_tenant_db", AsyncMock(return_value=None),
    )
    result = await ingestor.backfill_aggregations(99999, days=30)
    assert result == {"hours": 0, "days": 0, "hourly_rows": 0, "daily_rows": 0}


# ── M5 → aggregations chain ────────────────────────────────────


@pytest.mark.asyncio
async def test_m5_backfill_calls_aggregations_on_success(monkeypatch):
    """The M5 history backfill must chain into ``backfill_aggregations``
    so the calendar projection's median path has data the moment M5
    finishes (rather than waiting ~7 days for the cron to catch up)."""
    from capabilities.integrations.shared import history_backfill
    from features.vehicles.warehouse import aggregator as ingestor

    # Mock the M5 setup so it gets to the chain-into-aggregations
    # point: integration row exists, status connected, toggle on,
    # provider fetches succeed, tenant DB present, days_to_run is one
    # historical day.
    integration = MagicMock()
    integration.status = "connected"
    integration.feature_toggles = {"history_backfill": {"enabled": True}}

    platform_db = MagicMock()
    platform_db.get_account_integration = AsyncMock(return_value=integration)
    monkeypatch.setattr(
        history_backfill, "get_platform_db", lambda: platform_db,
    )

    tenant = MagicMock()
    tenant.vehicle_state_snapshot_has_day = AsyncMock(return_value=False)
    tenant.upsert_vehicle_state_snapshots = AsyncMock(return_value=0)
    monkeypatch.setattr(
        history_backfill, "get_tenant_db", AsyncMock(return_value=tenant),
    )

    provider = MagicMock()
    provider.get_stats_history = AsyncMock(return_value={})
    monkeypatch.setattr(
        history_backfill, "get_telematics_client",
        AsyncMock(return_value=provider),
    )

    # Skip the real throttle + cooldowns to keep the test fast.
    monkeypatch.setattr(
        history_backfill.samsara_backfill_throttle,
        "acquire", AsyncMock(),
    )
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.asyncio.sleep",
        AsyncMock(),
    )

    # The actual assertion — backfill_aggregations should be called
    # after the per-day loop completes.
    agg_calls: list = []

    async def fake_aggregations(account_id, *, days):
        agg_calls.append((account_id, days))
        return {"hours": days * 24, "days": days,
                "hourly_rows": 0, "daily_rows": 0}

    monkeypatch.setattr(ingestor, "backfill_aggregations", fake_aggregations)

    result = await history_backfill.backfill_vehicle_history(
        42, days=3, provider_id="samsara",
    )
    assert result.state == "completed"
    # The chain MUST have fired.
    assert agg_calls == [(42, 3)]


@pytest.mark.asyncio
async def test_m5_backfill_aggregations_failure_does_not_flip_state(monkeypatch):
    """If the aggregations chain raises, M5's overall state should
    still be 'completed' (snapshot data landed successfully — the
    aggregations failure just means the live cron will catch up over
    the next 7 days)."""
    from capabilities.integrations.shared import history_backfill
    from features.vehicles.warehouse import aggregator as ingestor

    integration = MagicMock()
    integration.status = "connected"
    integration.feature_toggles = {"history_backfill": {"enabled": True}}

    platform_db = MagicMock()
    platform_db.get_account_integration = AsyncMock(return_value=integration)
    monkeypatch.setattr(
        history_backfill, "get_platform_db", lambda: platform_db,
    )

    tenant = MagicMock()
    tenant.vehicle_state_snapshot_has_day = AsyncMock(return_value=False)
    tenant.upsert_vehicle_state_snapshots = AsyncMock(return_value=0)
    monkeypatch.setattr(
        history_backfill, "get_tenant_db", AsyncMock(return_value=tenant),
    )

    provider = MagicMock()
    provider.get_stats_history = AsyncMock(return_value={})
    monkeypatch.setattr(
        history_backfill, "get_telematics_client",
        AsyncMock(return_value=provider),
    )
    monkeypatch.setattr(
        history_backfill.samsara_backfill_throttle,
        "acquire", AsyncMock(),
    )
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.asyncio.sleep",
        AsyncMock(),
    )

    async def broken_aggregations(*_a, **_k):
        raise RuntimeError("DB blew up during aggregation")

    monkeypatch.setattr(ingestor, "backfill_aggregations", broken_aggregations)

    result = await history_backfill.backfill_vehicle_history(
        42, days=3, provider_id="samsara",
    )
    # State should still be completed — aggregation failure is non-fatal.
    assert result.state == "completed"
    # But the error must be captured for ops visibility.
    assert any("aggregations" in e for e in result.errors)


@pytest_asyncio.fixture
async def tenant(pg_db):
    """Real per-test Postgres — the self-healing tests exercise SQL."""
    yield pg_db

# ── self-healing roll-ups (2026-07-29) ────────────────────────────────
#
# Both live wrappers only ever processed the just-closed period, so ONE
# missed run left a permanent hole: production lost daily 07-27/28 to an
# ingest outage and the whole week of 07-20 to a single skipped Monday.

@pytest.mark.asyncio
async def test_daily_run_heals_a_missing_earlier_day(tenant, monkeypatch):
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    from features.vehicles.warehouse import aggregator as agg

    await tenant.upsert_vehicle_state(1, [
        {"vehicle_id": "v1", "vehicle_name": "204", "company_code": "A"},
    ])
    # Snapshots for two days: the day BEFORE yesterday (the hole) and
    # yesterday (the normal run).
    now = _dt(2026, 7, 29, 6, 0, tzinfo=_tz.utc)
    for day, odo in ((_dt(2026, 7, 27, 12, tzinfo=_tz.utc), 1_000),
                     (_dt(2026, 7, 28, 12, tzinfo=_tz.utc), 1_400)):
        await tenant.upsert_vehicle_state_snapshots(1, [
            {"vehicle_id": "v1", "captured_at": day.isoformat(),
             "odometer_mi": odo, "engine_hours": 10,
             "engine_state": "moving", "speed_mph": 55},
        ])
        await agg._aggregate_hour_window(tenant, 1, day)

    class _FrozenDT(_dt):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(agg, "datetime", _FrozenDT)

    async def _tdb(_acct):
        return tenant
    monkeypatch.setattr(agg, "get_tenant_db", _tdb)

    await agg.aggregate_metrics_daily(1)

    cur = await tenant._db.execute(
        "SELECT bucket_start FROM vehicle_telemetry WHERE account_id = ? "
        "AND granularity = 'daily' ORDER BY bucket_start", (1,))
    days = [str(dict(r)["bucket_start"])[:10] for r in await cur.fetchall()]
    # 07-28 is the normal "yesterday"; 07-27 is the healed hole.
    assert "2026-07-28" in days
    assert "2026-07-27" in days, "a missed day must be healed, not lost forever"


@pytest.mark.asyncio
async def test_daily_run_re_rolls_a_day_that_is_present_but_wrong(tenant, monkeypatch):
    """Healing holes was never enough — a day written WRONG stayed wrong.

    Production carried days whose stored total disagreed with the hours
    underneath them (an outage repaired later, the lost 23:00 bucket),
    and the old missing-only pass skipped every one of them because a
    row existed.
    """
    from datetime import datetime as _dt, timezone as _tz
    from features.vehicles.warehouse import aggregator as agg

    await tenant.upsert_vehicle_state(1, [
        {"vehicle_id": "v1", "vehicle_name": "204", "company_code": "A"},
    ])
    day = _dt(2026, 7, 27, 12, tzinfo=_tz.utc)
    await tenant.upsert_vehicle_state_snapshots(1, [
        {"vehicle_id": "v1", "captured_at": day.isoformat(),
         "odometer_mi": 1_000, "engine_hours": 10,
         "engine_state": "moving", "speed_mph": 55},
        # 25 miles in 30 minutes — physically plausible driving, so the
        # gap-aware step guard (steps must fit elapsed time x 90 mph)
        # counts it.  The original 120-in-30 fixture implied 240 mph and
        # was correctly dropped once the physics guard landed.
        {"vehicle_id": "v1", "captured_at": (day.replace(minute=30)).isoformat(),
         "odometer_mi": 1_025, "engine_hours": 12,
         "engine_state": "moving", "speed_mph": 55},
    ])
    await agg._aggregate_hour_window(tenant, 1, day)

    # A stale daily row claiming a total the hours do not support.
    await tenant.upsert_vehicle_metrics_daily(1, [
        {"vehicle_id": "v1", "day_utc": "2026-07-27", "miles": 9_999.0},
    ])

    now = _dt(2026, 7, 29, 6, 0, tzinfo=_tz.utc)

    class _FrozenDT(_dt):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(agg, "datetime", _FrozenDT)

    async def _tdb(_acct):
        return tenant
    monkeypatch.setattr(agg, "get_tenant_db", _tdb)

    await agg.aggregate_metrics_daily(1)

    cur = await tenant._db.execute(
        "SELECT miles FROM vehicle_telemetry WHERE account_id = ? "
        "AND granularity = 'daily' AND bucket_start = ?", (1, "2026-07-27"))
    miles = float(dict(await cur.fetchone())["miles"])
    assert miles == pytest.approx(25.0), (
        "a wrong day must be re-summed from its hours, not left alone"
    )


@pytest.mark.asyncio
async def test_duty_time_counts_without_a_working_odometer(tenant):
    """Drive minutes must not depend on the odometer feed.

    Counting duty inside the same query that computes distance made a
    working odometer a precondition for having driven at all — so a
    truck whose CAN bus reports engine state but no mileage sat at zero
    hours forever, however much it moved.
    """
    from datetime import datetime as _dt, timezone as _tz
    from features.vehicles.warehouse import aggregator as agg

    await tenant.upsert_vehicle_state(1, [
        {"vehicle_id": "v1", "vehicle_name": "301", "company_code": "A"},
    ])
    hour = _dt(2026, 7, 21, 9, tzinfo=_tz.utc)
    await tenant.upsert_vehicle_state_snapshots(1, [
        {"vehicle_id": "v1", "captured_at": hour.replace(minute=m).isoformat(),
         "odometer_mi": None, "engine_state": state, "speed_mph": speed}
        for m, state, speed in (
            (0, "moving", 60), (5, "moving", 58), (10, "idle", 0),
        )
    ])
    await agg._aggregate_hour_window(tenant, 1, hour)

    cur = await tenant._db.execute(
        "SELECT miles, drive_min, idle_min FROM vehicle_telemetry "
        "WHERE account_id = ? AND granularity = 'hourly' AND bucket_start = ?",
        (1, "2026-07-21T09:00:00"))
    row = dict(await cur.fetchone())
    # Gap-based: each sample's state lasts until the next sample.
    # moving@:00 -> :05 (5), moving@:05 -> :10 (5) = 10 drive minutes.
    assert float(row["drive_min"]) == pytest.approx(10.0)
    # idle@:10 is the LAST sample — the feed then goes quiet, so its
    # state may claim at most the gap cap, never the rest of the hour.
    assert float(row["idle_min"]) == pytest.approx(10.0)
    assert float(row["miles"]) == pytest.approx(0.0)        # honestly unknown


@pytest.mark.asyncio
async def test_duty_time_is_cadence_independent(tenant):
    """One hour holding 5-minute history AND 1-minute samples.

    The old math multiplied sample counts by an assumed interval, so
    the hour in which the sampling cadence changed — and every mixed
    replay after it — would have been wrong by up to 5x.  Gap-based
    duty asks each sample how long its state actually lasted.
    """
    from datetime import datetime as _dt, timezone as _tz
    from features.vehicles.warehouse import aggregator as agg

    await tenant.upsert_vehicle_state(1, [
        {"vehicle_id": "v1", "vehicle_name": "310", "company_code": "A"},
    ])
    hour = _dt(2026, 8, 3, 9, tzinfo=_tz.utc)
    rows = []
    # First half: legacy 5-minute spacing, moving (:00 :05 ... :25).
    for m in range(0, 30, 5):
        rows.append({"vehicle_id": "v1",
                     "captured_at": hour.replace(minute=m).isoformat(),
                     "odometer_mi": 5000 + m, "engine_state": "moving",
                     "speed_mph": 55})
    # Second half: new 1-minute spacing, idle (:30 :31 ... :59).
    for m in range(30, 60):
        rows.append({"vehicle_id": "v1",
                     "captured_at": hour.replace(minute=m).isoformat(),
                     "odometer_mi": 5030, "engine_state": "idle",
                     "speed_mph": 0})
    await tenant.upsert_vehicle_state_snapshots(1, rows)
    await agg._aggregate_hour_window(tenant, 1, hour)

    cur = await tenant._db.execute(
        "SELECT drive_min, idle_min FROM vehicle_telemetry "
        "WHERE account_id = ? AND granularity = 'hourly' AND bucket_start = ?",
        (1, "2026-08-03T09:00:00"))
    row = dict(await cur.fetchone())
    # Moving: 5+5+5+5+5 (to :25) + 5 (:25 -> :30) = 30 minutes.
    assert float(row["drive_min"]) == pytest.approx(30.0)
    # Idle: 29 one-minute gaps + the last sample's gap to the window
    # end (1 min) = 30 minutes.  Count-based math would have said 150.
    assert float(row["idle_min"]) == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_odometer_re_baseline_does_not_become_miles(tenant):
    """A gateway swap must not read as distance driven.

    One truck booked 24,352 miles in a single hour when its ECU
    re-baselined: the window's span counted the whole discarded reading
    as travel.  Miles now come from the steps between readings, so the
    jump is dropped while the surrounding real movement survives.
    """
    from datetime import datetime as _dt, timezone as _tz
    from features.vehicles.warehouse import aggregator as agg

    await tenant.upsert_vehicle_state(1, [
        {"vehicle_id": "v1", "vehicle_name": "233", "company_code": "A"},
    ])
    hour = _dt(2026, 7, 21, 15, tzinfo=_tz.utc)
    await tenant.upsert_vehicle_state_snapshots(1, [
        {"vehicle_id": "v1", "captured_at": hour.replace(minute=0).isoformat(),
         "odometer_mi": 282_050, "engine_state": "moving", "speed_mph": 60},
        {"vehicle_id": "v1", "captured_at": hour.replace(minute=5).isoformat(),
         "odometer_mi": 282_056, "engine_state": "moving", "speed_mph": 60},
        # ECU re-baselines to a fresh unit's reading.
        {"vehicle_id": "v1", "captured_at": hour.replace(minute=10).isoformat(),
         "odometer_mi": 306_408, "engine_state": "moving", "speed_mph": 60},
    ])
    await agg._aggregate_hour_window(tenant, 1, hour)

    cur = await tenant._db.execute(
        "SELECT miles, odometer_eod FROM vehicle_telemetry "
        "WHERE account_id = ? AND granularity = 'hourly' AND bucket_start = ?",
        (1, "2026-07-21T15:00:00"))
    row = dict(await cur.fetchone())
    assert float(row["miles"]) == pytest.approx(6.0), (
        "only the real 6 miles between the first two readings count"
    )
    # The odometer itself still banks the newest reading — the reading is
    # real, it is the DISTANCE that was fiction.
    assert float(row["odometer_eod"]) == pytest.approx(306_408)


async def test_step_plausibility_is_physics_not_a_magic_number(tenant):
    """Two identical 900-mile odometer steps; only their TIME context
    differs.  Across one minute it is a re-baseline artefact (dropped
    — the old flat 10k ceiling admitted every sub-10k jump: 179
    impossible hour rows).  Across a 14-hour provider silence
    (source_ts gap) it is a truck's real catch-up and counts."""
    from datetime import datetime as _dt, timezone as _tz
    from features.vehicles.warehouse import aggregator as agg

    hour = _dt(2026, 8, 3, 9, tzinfo=_tz.utc)

    # v_fake: odometer jumps 900 between two samples a minute apart.
    await tenant.upsert_vehicle_state_snapshots(61, [
        {"vehicle_id": "v_fake",
         "captured_at": hour.replace(minute=m).isoformat(),
         "odometer_mi": odo, "engine_state": "idle", "speed_mph": 0,
         "source_ts": hour.replace(minute=m).isoformat()}
        for m, odo in ((10, 1000), (11, 1900), (12, 1901))
    ])
    # v_real: same 900-mile step, but the provider was silent 14 hours
    # (source_ts gap) — the truck genuinely drove those miles.
    await tenant.upsert_vehicle_state_snapshots(61, [
        {"vehicle_id": "v_real",
         "captured_at": hour.replace(minute=10).isoformat(),
         "odometer_mi": 5000, "engine_state": "moving", "speed_mph": 50,
         "source_ts": "2026-08-02T19:10:00+00:00"},
        {"vehicle_id": "v_real",
         "captured_at": hour.replace(minute=11).isoformat(),
         "odometer_mi": 5900, "engine_state": "moving", "speed_mph": 50,
         "source_ts": hour.replace(minute=11).isoformat()},
    ])
    await agg._aggregate_hour_window(tenant, 61, hour)

    cur = await tenant._db.execute(
        "SELECT vehicle_id, miles FROM vehicle_telemetry "
        "WHERE account_id = ? AND granularity = 'hourly' "
        "AND bucket_start = ? ORDER BY vehicle_id",
        (61, "2026-08-03T09:00:00"))
    miles = {r[0]: float(r[1]) for r in await cur.fetchall()}
    assert miles["v_fake"] == pytest.approx(1.0)    # the 900 dropped, the 1 kept
    assert miles["v_real"] == pytest.approx(900.0)  # the catch-up counted
