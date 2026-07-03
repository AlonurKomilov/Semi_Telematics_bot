"""KPI feature — pure computation + grading + thresholds config.

The math and grades are pure functions over serialized loads (no DB), so
most of this file runs in milliseconds; the thresholds round-trip uses the
account_settings store.
"""

from __future__ import annotations

import pytest

from features.kpi.service import (
    DEFAULT_KPI_THRESHOLDS,
    compute_dispatcher_kpis,
    get_kpi_thresholds,
    grade,
    set_kpi_thresholds,
)


def _load(**kw) -> dict:
    return {
        "status": kw.get("status", "delivered"),
        "dispatcher_user_id": kw.get("dispatcher_user_id"),
        "dispatcher_name": kw.get("dispatcher_name", ""),
        "driver_user_id": kw.get("driver_user_id"),
        "driver_name": kw.get("driver_name", ""),
        "vehicle_unit": kw.get("vehicle_unit", ""),
        "total_rate": kw.get("total_rate"),
        "loaded_miles": kw.get("loaded_miles"),
        "empty_miles": kw.get("empty_miles"),
        "driver_pay": kw.get("driver_pay"),
        "other_costs": kw.get("other_costs"),
    }


T = DEFAULT_KPI_THRESHOLDS


def test_metrics_math():
    """revenue Σ, RPM = revenue / total miles, empty % = empty / total,
    gross = revenue − pay − costs, distinct trucks."""
    rows = compute_dispatcher_kpis([
        _load(dispatcher_user_id=5, dispatcher_name="Jasur",
              total_rate=2000, loaded_miles=800, empty_miles=200,
              driver_pay=600, vehicle_unit="240", driver_name="A"),
        _load(dispatcher_user_id=5, dispatcher_name="Jasur",
              total_rate=1000, loaded_miles=450, empty_miles=50,
              driver_pay=300, other_costs=100, vehicle_unit="241", driver_name="B"),
    ], T)
    assert len(rows) == 1
    m = rows[0]
    assert m["revenue"] == 3000.0
    assert m["total_miles"] == 1500.0
    assert m["rpm"] == 2.0                    # 3000 / 1500
    assert m["empty_pct"] == pytest.approx(16.7, abs=0.05)  # 250/1500
    assert m["gross"] == 2000.0               # 3000 − 900 − 100
    assert m["trucks"] == 2 and m["drivers"] == 2 and m["loads"] == 2
    assert m["gross_per_truck"] == 1000.0


def test_grouping_by_user_id_with_name_fallback():
    """Linked dispatchers group by user id; unlinked (TMS-synced) group by
    name so they rank before becoming 4truck users."""
    rows = compute_dispatcher_kpis([
        _load(dispatcher_user_id=5, dispatcher_name="Jasur", total_rate=100),
        _load(dispatcher_user_id=5, dispatcher_name="Jasur A.", total_rate=100),
        _load(dispatcher_name="Shaxzod", total_rate=100),
        _load(dispatcher_name="shaxzod", total_rate=100),   # case-insensitive
        _load(total_rate=100),                               # unassigned
    ], T)
    names = {r["dispatcher_name"]: r for r in rows}
    assert names["Jasur"]["loads"] == 2       # merged on user id
    assert names["Shaxzod"]["loads"] == 2     # merged on lowercased name
    assert names["(unassigned)"]["loads"] == 1


def test_canceled_loads_do_not_count():
    rows = compute_dispatcher_kpis([
        _load(dispatcher_name="X", total_rate=1000),
        _load(dispatcher_name="X", total_rate=9999, status="canceled"),
    ], T)
    assert rows[0]["revenue"] == 1000.0 and rows[0]["loads"] == 1


def test_grade_boundaries():
    # A — everything good.
    assert grade({"rpm": 2.5, "empty_pct": 8.0,
                  "gross_per_truck": 8000.0, "gross": 8000.0}, T) == "A"
    # B — nothing bad, not all good (rpm in the middle band).
    assert grade({"rpm": 2.15, "empty_pct": 8.0,
                  "gross_per_truck": 8000.0, "gross": 8000.0}, T) == "B"
    # C — exactly one bad (deadhead too high).
    assert grade({"rpm": 2.5, "empty_pct": 18.0,
                  "gross_per_truck": 8000.0, "gross": 8000.0}, T) == "C"
    # D — two bad.
    assert grade({"rpm": 1.9, "empty_pct": 18.0,
                  "gross_per_truck": 8000.0, "gross": 8000.0}, T) == "D"
    # D — negative gross trumps everything.
    assert grade({"rpm": 2.5, "empty_pct": 8.0,
                  "gross_per_truck": 8000.0, "gross": -50.0}, T) == "D"
    # Thin data (nothing computable) is neutral — B, not dramatic.
    assert grade({"rpm": None, "empty_pct": None,
                  "gross_per_truck": None, "gross": None}, T) == "B"


def test_grades_ride_computed_rows():
    rows = compute_dispatcher_kpis([
        _load(dispatcher_name="Good", total_rate=2500, loaded_miles=900,
              empty_miles=50, driver_pay=500, vehicle_unit="1"),
    ], T)
    # rpm 2500/950≈2.63 good; empty 5.3% good; gross/truck 2000 bad(<6500) → C
    assert rows[0]["grade"] == "C"


@pytest.mark.asyncio
async def test_thresholds_round_trip_and_validation(db):
    aid = (await db.create_account("KPI Co")).id
    merged = await set_kpi_thresholds(db, aid, {
        "rpm_good": 2.5,          # valid override
        "empty_pct_bad": "20",    # coercible string
        "nonsense_key": 99,       # ignored
        "rpm_bad": "not-a-number",  # ignored
    })
    assert merged["rpm_good"] == 2.5
    assert merged["empty_pct_bad"] == 20.0
    assert merged["rpm_bad"] == DEFAULT_KPI_THRESHOLDS["rpm_bad"]
    assert "nonsense_key" not in merged
    again = await get_kpi_thresholds(db, aid)
    assert again == merged
