"""Period-mileage engine (warehouse.get_period_mileage & the
single-vehicle variant).

The authoritative number is an odometer DELTA over stored end-of-day
readings — these tests pin the rule and every degraded shape:

  * normal range          → end reading − last reading before the range
  * boundary gap          → the baseline reaches back past silent days
  * mid-range join        → 'partial' flag, counted from first in-range
  * odometer reset        → 'reset' flag, clamped to summed daily miles
  * no usable readings    → the vehicle is OMITTED (omitted ≠ zero)
  * invalid range         → empty result, never an exception
"""

from __future__ import annotations

import os
import sys

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest_asyncio.fixture
async def tenant(pg_db):
    yield pg_db


async def _day(db, vid: str, name: str, day: str,
               odo: float | None, miles: float = 0.0, account_id: int = 1):
    await db._db.execute(
        "INSERT INTO vehicle_telemetry "
        "(account_id, vehicle_id, vehicle_name, granularity, bucket_start, "
        " miles, odometer_eod) VALUES (?, ?, ?, 'daily', ?, ?, ?)",
        (account_id, vid, name, day, miles, odo),
    )
    await db._db.commit()


class TestPeriodMileage:
    @pytest.mark.asyncio
    async def test_normal_delta(self, tenant):
        await _day(tenant, "v1", "132", "2026-07-01", 10_000, 0)
        await _day(tenant, "v1", "132", "2026-07-02", 10_250, 250)
        await _day(tenant, "v1", "132", "2026-07-03", 10_600, 350)
        rows = await tenant.get_period_mileage(1, "2026-07-02", "2026-07-03")
        assert len(rows) == 1
        r = rows[0]
        assert r["vehicle_name"] == "132"
        assert r["miles"] == 600.0          # 10600 − 10000 (day before range)
        assert r["flag"] == ""
        assert r["days_covered"] == 2

    @pytest.mark.asyncio
    async def test_baseline_reaches_back_over_gap(self, tenant):
        # Truck silent Jul 2-9; the Jul-1 reading is still the correct
        # baseline for a Jul-10..12 range (no readings = no driving drift).
        await _day(tenant, "v1", "132", "2026-07-01", 5_000)
        await _day(tenant, "v1", "132", "2026-07-10", 5_400, 400)
        await _day(tenant, "v1", "132", "2026-07-12", 5_500, 100)
        rows = await tenant.get_period_mileage(1, "2026-07-10", "2026-07-12")
        assert rows[0]["miles"] == 500.0
        assert rows[0]["flag"] == ""
        assert rows[0]["start_read_on"] == "2026-07-01"

    @pytest.mark.asyncio
    async def test_mid_range_join_flags_partial(self, tenant):
        # First-ever reading lands inside the range → partial coverage,
        # counted from that first reading, flagged so the UI can say so.
        await _day(tenant, "v2", "245", "2026-07-05", 90_000, 120)
        await _day(tenant, "v2", "245", "2026-07-08", 90_700, 300)
        rows = await tenant.get_period_mileage(1, "2026-07-01", "2026-07-10")
        r = rows[0]
        assert r["miles"] == 700.0
        assert r["flag"] == "partial"
        assert r["start_read_on"] == "2026-07-05"

    @pytest.mark.asyncio
    async def test_odometer_reset_clamps_to_daily_sum(self, tenant):
        # Device swap mid-range: odometer drops.  Never report negative —
        # clamp to the summed daily miles and flag it.
        await _day(tenant, "v3", "001", "2026-07-01", 200_000)
        await _day(tenant, "v3", "001", "2026-07-02", 200_300, 300)
        await _day(tenant, "v3", "001", "2026-07-03", 1_200, 150)  # reset
        rows = await tenant.get_period_mileage(1, "2026-07-02", "2026-07-03")
        r = rows[0]
        assert r["flag"] == "reset"
        assert r["miles"] == 450.0          # 300 + 150, not 1200−200000

    @pytest.mark.asyncio
    async def test_vehicle_without_readings_is_omitted(self, tenant):
        await _day(tenant, "v1", "132", "2026-07-01", 10_000)
        await _day(tenant, "v1", "132", "2026-07-03", 10_100, 100)
        # v9 exists only after the range end — no usable reading.
        await _day(tenant, "v9", "777", "2026-08-01", 50)
        rows = await tenant.get_period_mileage(1, "2026-07-01", "2026-07-03")
        assert [r["vehicle_id"] for r in rows] == ["v1"]

    @pytest.mark.asyncio
    async def test_sorted_by_miles_desc_and_account_scoped(self, tenant):
        await _day(tenant, "a", "100", "2026-07-01", 1_000)
        await _day(tenant, "a", "100", "2026-07-05", 1_200, 200)
        await _day(tenant, "b", "200", "2026-07-01", 8_000)
        await _day(tenant, "b", "200", "2026-07-05", 9_000, 1000)
        await _day(tenant, "z", "999", "2026-07-01", 4_000, 0, account_id=2)
        rows = await tenant.get_period_mileage(1, "2026-07-02", "2026-07-05")
        assert [r["vehicle_name"] for r in rows] == ["200", "100"]

    @pytest.mark.asyncio
    async def test_invalid_range_returns_empty(self, tenant):
        await _day(tenant, "v1", "132", "2026-07-01", 10_000)
        assert await tenant.get_period_mileage(1, "2026-07-05", "2026-07-01") == []
        assert await tenant.get_period_mileage(1, "bad", "2026-07-01") == []


class TestVehiclePeriodMileage:
    @pytest.mark.asyncio
    async def test_summary_plus_daily_bars(self, tenant):
        await tenant.upsert_vehicle_state(1, [
            {"vehicle_id": "v1", "vehicle_name": "132", "company_code": "A"},
        ])
        await _day(tenant, "v1", "132", "2026-07-01", 10_000, 0)
        await _day(tenant, "v1", "132", "2026-07-02", 10_250, 250)
        await _day(tenant, "v1", "132", "2026-07-03", 10_600, 350)
        out = await tenant.get_vehicle_period_mileage(
            1, "132", "2026-07-02", "2026-07-03")
        assert out is not None
        assert out["miles"] == 600.0
        assert out["days"] == [
            {"day": "2026-07-02", "miles": 250.0},
            {"day": "2026-07-03", "miles": 350.0},
        ]

    @pytest.mark.asyncio
    async def test_name_resolution_is_case_insensitive(self, tenant):
        await tenant.upsert_vehicle_state(1, [
            {"vehicle_id": "v1", "vehicle_name": "Truck-9", "company_code": ""},
        ])
        await _day(tenant, "v1", "Truck-9", "2026-07-01", 100)
        await _day(tenant, "v1", "Truck-9", "2026-07-02", 150, 50)
        out = await tenant.get_vehicle_period_mileage(
            1, "truck-9", "2026-07-02", "2026-07-02")
        assert out is not None and out["miles"] == 50.0

    @pytest.mark.asyncio
    async def test_unlinked_vehicle_returns_none(self, tenant):
        assert await tenant.get_vehicle_period_mileage(
            1, "no-such-truck", "2026-07-01", "2026-07-02") is None


class TestCatchupFlag:
    @pytest.mark.asyncio
    async def test_backlog_day_flags_catchup(self, tenant):
        # Feed silent Jul 2-3, then Jul 4 absorbs the backlog (+3,300
        # in one bucket — physically impossible in a day).  Total stays
        # the honest odometer delta; the flag says the shape is lumpy.
        await _day(tenant, "v1", "132", "2026-07-01", 700_000, 0)
        await _day(tenant, "v1", "132", "2026-07-04", 703_300, 3300)
        await _day(tenant, "v1", "132", "2026-07-05", 703_800, 500)
        rows = await tenant.get_period_mileage(1, "2026-07-02", "2026-07-05")
        r = rows[0]
        assert r["miles"] == 3800.0
        assert r["flag"] == "catchup"

    @pytest.mark.asyncio
    async def test_normal_heavy_day_stays_unflagged(self, tenant):
        # 1,200 mi in a day is heavy team driving, not a backlog.
        await _day(tenant, "v1", "132", "2026-07-01", 700_000, 0)
        await _day(tenant, "v1", "132", "2026-07-02", 701_200, 1200)
        rows = await tenant.get_period_mileage(1, "2026-07-02", "2026-07-02")
        assert rows[0]["flag"] == ""


class TestTieredFreshness:
    """The daily tier only lands at 00:05 UTC for YESTERDAY, so reading
    it alone made today invisible.  Both boundaries now prefer the
    5-minute snapshot when it is a LATER DAY, mirroring the tiering
    ``get_reading_as_of`` already used."""

    @staticmethod
    async def _snap(db, vid, captured_at, odo, account_id=1):
        await db.upsert_vehicle_state_snapshots(account_id, [
            {"vehicle_id": vid, "captured_at": captured_at,
             "odometer_mi": odo, "engine_hours": 10,
             "engine_state": "moving", "speed_mph": 55},
        ])

    @pytest.mark.asyncio
    async def test_snapshot_extends_past_the_daily_tier(self, tenant):
        # Daily stops at Jul 2; live snapshots continue into Jul 3.
        await _day(tenant, "v1", "132", "2026-07-01", 1_000, 0)
        await _day(tenant, "v1", "132", "2026-07-02", 1_200, 200)
        await self._snap(tenant, "v1", "2026-07-03T14:00:00+00:00", 1_500)
        rows = await tenant.get_period_mileage(1, "2026-07-02", "2026-07-03")
        assert len(rows) == 1
        r = rows[0]
        assert r["miles"] == 500.0            # 1500 (snapshot) - 1000 (Jul 1)
        assert r["end_read_on"] == "2026-07-03"

    @pytest.mark.asyncio
    async def test_daily_eod_wins_within_the_same_day(self, tenant):
        # A same-day snapshot is mid-day; the daily EOD is max-of-day and
        # therefore the later reading — it must not be overridden.
        await _day(tenant, "v1", "132", "2026-07-01", 1_000, 0)
        await _day(tenant, "v1", "132", "2026-07-03", 1_600, 600)
        await self._snap(tenant, "v1", "2026-07-03T12:00:00+00:00", 1_500)
        rows = await tenant.get_period_mileage(1, "2026-07-02", "2026-07-03")
        assert rows[0]["miles"] == 600.0      # 1600, not the 1500 snapshot

    @pytest.mark.asyncio
    async def test_snapshot_after_the_range_is_ignored(self, tenant):
        # Freshness must not leak driving from AFTER the window.
        await _day(tenant, "v1", "132", "2026-07-01", 1_000, 0)
        await _day(tenant, "v1", "132", "2026-07-02", 1_200, 200)
        await self._snap(tenant, "v1", "2026-07-05T09:00:00+00:00", 9_999)
        rows = await tenant.get_period_mileage(1, "2026-07-02", "2026-07-03")
        assert rows[0]["miles"] == 200.0
        assert rows[0]["end_read_on"] == "2026-07-02"

    @pytest.mark.asyncio
    async def test_snapshot_only_vehicle_still_reports(self, tenant):
        # A vehicle whose daily rows haven't been rolled up yet at all.
        await self._snap(tenant, "v9", "2026-07-02T08:00:00+00:00", 500)
        await self._snap(tenant, "v9", "2026-07-03T20:00:00+00:00", 640)
        rows = await tenant.get_period_mileage(1, "2026-07-03", "2026-07-03")
        assert len(rows) == 1
        assert rows[0]["miles"] == 140.0
