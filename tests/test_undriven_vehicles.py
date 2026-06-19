"""Integration test for ``warehouse.get_undriven_vehicles`` against real PG.

Exercises the actual SQL (not a fake DB): seeds ``vehicle_state`` (names +
company for the join) and ``vehicle_state_snapshot`` (movement history), then
verifies the movement-based "hasn't driven in N days" computation — last-moved
detection, the min_days cutoff, the name/company join, and days_stopped math.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from datetime import datetime, timedelta, timezone

import pytest


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.mark.asyncio
async def test_undriven_returns_stopped_excludes_driving(pg_db):
    db = pg_db
    acct = 1
    now = datetime.now(timezone.utc)

    # Current state → supplies vehicle_name + company for the join.
    await db.upsert_vehicle_state(acct, [
        {"vehicle_id": "v1", "vehicle_name": "B-1", "company_code": "B"},
        {"vehicle_id": "v2", "vehicle_name": "B-2", "company_code": "B"},
    ])

    # B-1: last actually moved 5 days ago, then parked but still reporting.
    # B-2: moved an hour ago.
    await db.upsert_vehicle_state_snapshots(acct, [
        {"vehicle_id": "v1", "captured_at": _iso(now - timedelta(days=5)), "speed_mph": 35.0},
        {"vehicle_id": "v1", "captured_at": _iso(now - timedelta(hours=1)), "speed_mph": 0.0},
        {"vehicle_id": "v2", "captured_at": _iso(now - timedelta(hours=1)), "speed_mph": 40.0},
    ])

    rows = await db.get_undriven_vehicles(acct, min_days=3)
    by_name = {r["vehicle_name"]: r for r in rows}

    assert "B-1" in by_name          # last moved 5d ago → undriven for >3d
    assert "B-2" not in by_name      # moved today → driving
    b1 = by_name["B-1"]
    assert b1["company_code"] == "B"                       # join worked
    assert b1["last_seen"]                                 # still reporting
    assert b1["days_stopped"] is not None
    assert 4.5 <= b1["days_stopped"] <= 5.5                # ~5 days stopped


@pytest.mark.asyncio
async def test_undriven_respects_min_days_cutoff(pg_db):
    db = pg_db
    acct = 2
    now = datetime.now(timezone.utc)

    await db.upsert_vehicle_state(acct, [
        {"vehicle_id": "v1", "vehicle_name": "C-1", "company_code": "C"},
    ])
    await db.upsert_vehicle_state_snapshots(acct, [
        {"vehicle_id": "v1", "captured_at": _iso(now - timedelta(days=5)), "speed_mph": 35.0},
        {"vehicle_id": "v1", "captured_at": _iso(now - timedelta(hours=1)), "speed_mph": 0.0},
    ])

    # Stopped only 5 days → must NOT appear when asking for 7+ days.
    rows = await db.get_undriven_vehicles(acct, min_days=7)
    assert all(r["vehicle_name"] != "C-1" for r in rows)

    # ...but DOES appear at min_days=3.
    rows3 = await db.get_undriven_vehicles(acct, min_days=3)
    assert any(r["vehicle_name"] == "C-1" for r in rows3)


@pytest.mark.asyncio
async def test_undriven_skips_offline_vehicles(pg_db):
    """A vehicle with no snapshots in the lookback window isn't reported as
    'stopped' — it's simply absent (offline / no signal), not undriven."""
    db = pg_db
    acct = 3
    now = datetime.now(timezone.utc)
    await db.upsert_vehicle_state(acct, [
        {"vehicle_id": "v9", "vehicle_name": "D-9", "company_code": "D"},
    ])
    # Only a very old snapshot, outside the default 30-day lookback.
    await db.upsert_vehicle_state_snapshots(acct, [
        {"vehicle_id": "v9", "captured_at": _iso(now - timedelta(days=60)), "speed_mph": 0.0},
    ])
    rows = await db.get_undriven_vehicles(acct, min_days=3)
    assert all(r["vehicle_name"] != "D-9" for r in rows)
