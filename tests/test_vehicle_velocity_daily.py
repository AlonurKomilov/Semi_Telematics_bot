"""Tests for ``compute_vehicle_velocity_daily``.

The helper backs the calendar projection's "projected_due_date" for
mileage-tracked tasks.  These tests cover:

  * Median of drive-day miles (robust to weekly cycles).
  * Operational-day filter (excludes idle / weekend days).
  * Coverage guard: skips vehicles with too few observed days.
  * Drive-day guard: skips vehicles whose median sample is too small.
  * Provenance fields the dashboard tooltip surfaces.
  * Per-account isolation.
  * Empty / boundary inputs.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from adapters.storage import Database
from adapters.storage.warehouse import (
    VELOCITY_DRIVE_DAY_MIN_MILES,
    VELOCITY_MIN_COVERAGE_DAYS,
    VELOCITY_MIN_DRIVE_DAYS,
)


# ── Helpers ───────────────────────────────────────────────────────


async def _seed_daily(db: Database, account_id: int, vehicle_id: str,
                      miles_by_offset: dict[int, float]) -> None:
    """Insert vehicle_metrics_daily rows for ``vehicle_id``.

    ``miles_by_offset`` maps days-ago → miles for that day.  Day 0 is
    today; day 7 is a week ago.  Tests use this to compose specific
    weekly patterns without writing INSERTs by hand.
    """
    today = date.today()
    rows = [
        {
            "vehicle_id": vehicle_id,
            "day_utc": (today - timedelta(days=offset)).isoformat(),
            "miles": miles,
        }
        for offset, miles in miles_by_offset.items()
    ]
    await db.upsert_vehicle_state_day(account_id, rows)


# ── Median over drive days ────────────────────────────────────────


@pytest.mark.asyncio
async def test_median_excludes_weekend_zeros(seeded_db):
    """Mon-Fri = 300 mi, Sat-Sun = 0.  Median over drive days only
    is 300, not 214 (the mean including zeros)."""
    db = seeded_db["db"]
    account = seeded_db["account"]

    # 14 days of data: two weeks of [300, 300, 300, 300, 300, 0, 0]
    pattern = {}
    for week in range(2):
        for day_in_week in range(7):
            offset = week * 7 + day_in_week
            pattern[offset] = 0.0 if day_in_week >= 5 else 300.0

    await _seed_daily(db, account.id, "v-1", pattern)
    result = await db.compute_vehicle_velocity_daily(
        account.id, window_days=30,
    )
    assert "v-1" in result
    assert result["v-1"]["velocity"] == 300.0
    assert result["v-1"]["window_days"] == 30
    assert result["v-1"]["days_observed"] == 14
    assert result["v-1"]["drive_days"] == 10  # 5 drive days × 2 weeks


@pytest.mark.asyncio
async def test_median_is_robust_to_one_outlier(seeded_db):
    """One huge day (500 mi delivery push) shouldn't shift the median
    the way it shifts the mean.  Median = 200, mean = 233."""
    db = seeded_db["db"]
    account = seeded_db["account"]

    miles = [200, 200, 200, 200, 200, 200, 200, 500]  # 8 days
    pattern = {i: miles[i] for i in range(len(miles))}
    await _seed_daily(db, account.id, "v-1", pattern)

    result = await db.compute_vehicle_velocity_daily(account.id, window_days=30)
    assert result["v-1"]["velocity"] == 200.0


@pytest.mark.asyncio
async def test_drive_day_threshold_excludes_low_miles_days(seeded_db):
    """A 4-mile day (parking-lot crawl) shouldn't count as a drive
    day.  Threshold is 5 mi inclusive."""
    db = seeded_db["db"]
    account = seeded_db["account"]

    # 9 days: 3 below the 5-mi threshold (excluded), 6 at ~150 mi.
    pattern = {
        0: 4.0, 1: 4.0, 2: 4.0,
        3: 150.0, 4: 150.0, 5: 150.0,
        6: 150.0, 7: 150.0, 8: 150.0,
    }
    await _seed_daily(db, account.id, "v-1", pattern)
    result = await db.compute_vehicle_velocity_daily(account.id, window_days=30)
    assert result["v-1"]["drive_days"] == 6
    assert result["v-1"]["velocity"] == 150.0


# ── Coverage guard ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skips_vehicle_with_fewer_observed_days_than_threshold(seeded_db):
    """A vehicle with only 4 days of data shouldn't surface a
    velocity — too cold a start to trust the projection."""
    db = seeded_db["db"]
    account = seeded_db["account"]

    # 4 days, all drive days at 200 mi.  Coverage threshold is 7.
    pattern = {0: 200.0, 1: 200.0, 2: 200.0, 3: 200.0}
    await _seed_daily(db, account.id, "v-1", pattern)
    result = await db.compute_vehicle_velocity_daily(account.id, window_days=30)
    assert "v-1" not in result


@pytest.mark.asyncio
async def test_skips_vehicle_with_too_few_drive_days(seeded_db):
    """A vehicle with 14 days of data but only 2 drive days (rest
    parked) has too small a sample for a reliable median."""
    db = seeded_db["db"]
    account = seeded_db["account"]

    pattern = {}
    for offset in range(14):
        # Only 2 days have meaningful miles.
        pattern[offset] = 200.0 if offset < 2 else 0.0
    await _seed_daily(db, account.id, "v-1", pattern)
    result = await db.compute_vehicle_velocity_daily(account.id, window_days=30)
    assert "v-1" not in result


# ── Per-account isolation ────────────────────────────────────────


@pytest.mark.asyncio
async def test_account_a_does_not_see_account_b_velocity(seeded_db):
    db = seeded_db["db"]
    account_a = seeded_db["account"]
    account_b = await db.create_account("Other Fleet Co")

    pattern_a = {i: 100.0 for i in range(14)}
    pattern_b = {i: 500.0 for i in range(14)}
    await _seed_daily(db, account_a.id, "v-shared", pattern_a)
    await _seed_daily(db, account_b.id, "v-shared", pattern_b)

    a_result = await db.compute_vehicle_velocity_daily(account_a.id, window_days=30)
    b_result = await db.compute_vehicle_velocity_daily(account_b.id, window_days=30)
    assert a_result["v-shared"]["velocity"] == 100.0
    assert b_result["v-shared"]["velocity"] == 500.0


# ── Window honoured ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_window_days_filters_rows_outside_the_window(seeded_db):
    """Rows older than ``window_days`` must not influence the median.
    Otherwise the helper has the same "window is fiction" bug the
    snapshot-based helper had."""
    db = seeded_db["db"]
    account = seeded_db["account"]

    # 14 recent days at 100 mi + 14 ancient days (40-53 days ago) at 1000 mi.
    pattern = {**{i: 100.0 for i in range(14)},
               **{i + 40: 1000.0 for i in range(14)}}
    await _seed_daily(db, account.id, "v-1", pattern)
    result = await db.compute_vehicle_velocity_daily(account.id, window_days=30)
    # If the window filter works, only the 14 recent (100 mi) days
    # are seen.  Median = 100, not somewhere between 100 and 1000.
    assert result["v-1"]["velocity"] == 100.0
    assert result["v-1"]["days_observed"] == 14


# ── Empty / boundary inputs ──────────────────────────────────────


@pytest.mark.asyncio
async def test_returns_empty_when_no_rows_exist(seeded_db):
    db = seeded_db["db"]
    account = seeded_db["account"]
    result = await db.compute_vehicle_velocity_daily(account.id, window_days=30)
    assert result == {}


@pytest.mark.asyncio
async def test_excludes_vehicle_with_only_zero_mile_days(seeded_db):
    """A garaged spare truck has 30 days of zero-mile rows.  It
    should be absent from the result (no drive days = no velocity)."""
    db = seeded_db["db"]
    account = seeded_db["account"]

    pattern = {i: 0.0 for i in range(20)}
    await _seed_daily(db, account.id, "v-spare", pattern)
    result = await db.compute_vehicle_velocity_daily(account.id, window_days=30)
    assert "v-spare" not in result


# ── Provenance ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provenance_fields_match_inputs(seeded_db):
    db = seeded_db["db"]
    account = seeded_db["account"]

    pattern = {0: 100.0, 1: 200.0, 2: 300.0, 3: 0.0, 4: 150.0,
               5: 200.0, 6: 250.0, 7: 0.0, 8: 180.0, 9: 220.0}
    await _seed_daily(db, account.id, "v-1", pattern)

    result = await db.compute_vehicle_velocity_daily(account.id, window_days=14)
    v = result["v-1"]
    assert v["window_days"] == 14
    assert v["days_observed"] == 10  # all 10 inserted
    assert v["drive_days"] == 8       # exclude the two 0-mi days
    assert v["min_drive_mi"] == 100.0
    assert v["max_drive_mi"] == 300.0


# ── Constants ────────────────────────────────────────────────────


def test_drive_day_threshold_is_5_miles():
    """If this changes, update the test_drive_day_threshold_excludes
    case above accordingly."""
    assert VELOCITY_DRIVE_DAY_MIN_MILES == 5.0


def test_coverage_thresholds_meaningful():
    """Min coverage must be more permissive than the snapshot-based
    helper's 3-day span guard, but strict enough to require at least
    a full week of data."""
    assert VELOCITY_MIN_COVERAGE_DAYS >= 7
    assert VELOCITY_MIN_DRIVE_DAYS >= 3
