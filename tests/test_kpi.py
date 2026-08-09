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


def test_line_items_and_layover_hit_dispatcher_gross():
    """Extra pay/costs on loads reduce that dispatcher's gross; an
    off-load layover (driver sat — dispatch found no load) charges the
    attributed dispatcher even with no load to carry it."""
    rows = compute_dispatcher_kpis(
        [{
            "dispatcher_user_id": 7, "dispatcher_name": "Jasur",
            "status": "delivered", "total_rate": 3000.0,
            "loaded_miles": 900.0, "empty_miles": 100.0,
            "driver_pay": 800.0, "other_costs": 0.0,
            "extra_driver_pay": 150.0, "extra_costs": 87.5,
            "vehicle_unit": "T1", "driver_user_id": 1,
        }],
        DEFAULT_KPI_THRESHOLDS,
        off_load_items=[
            {"dispatcher_user_id": 7, "bucket": "driver_pay", "amount": 300.0},
            # Unattributed layover lands on "(unassigned)", never guessed.
            {"dispatcher_user_id": None, "bucket": "driver_pay", "amount": 99.0},
        ],
    )
    jasur = next(r for r in rows if r["dispatcher_user_id"] == 7)
    assert jasur["gross"] == round(3000 - 800 - 150 - 87.5 - 300, 2)
    unassigned = next(r for r in rows if r["dispatcher_name"] == "(unassigned)")
    assert unassigned["driver_pay"] == 99.0


# ── Company scope ────────────────────────────────────────────────────
#
# The gap that let a leak ship: every test above exercises the PURE
# function, and none covered what happens when the caller is restricted
# to a subset of companies.  Loads were company-filtered; the off-load
# line items merged onto them were not.

def _off(dispatcher_user_id, amount, bucket="other_costs") -> dict:
    return {
        "dispatcher_user_id": dispatcher_user_id,
        "bucket": bucket,
        "amount": amount,
    }


def test_off_load_items_can_create_a_dispatcher_row():
    """The mechanism behind the leak, stated so the fix is meaningful.

    ``groups.setdefault`` means an off-load item for a dispatcher with NO
    loads in the visible set mints a row of its own.  That is CORRECT for
    an unrestricted caller — a dispatcher whose only activity was layover
    is real — and it is exactly what must not reach a restricted one.
    """
    rows = compute_dispatcher_kpis([], T, off_load_items=[_off(77, 500.0)])
    assert len(rows) == 1
    assert rows[0]["dispatcher_user_id"] == 77
    assert rows[0]["other_costs"] == 500.0
    assert rows[0]["revenue"] == 0            # costs, no revenue


def test_off_load_costs_move_the_grade():
    """Why the pollution mattered.

    These costs feed ``gross``, which feeds ``gross_per_truck`` — and
    THAT is the graded metric, not gross itself.  ``vehicle_unit`` is
    load-bearing in this fixture: with no truck the per-truck value is
    None, which grading treats as NEUTRAL, and the whole effect vanishes.
    (The first version of this test omitted it and passed while proving
    nothing.)  With one truck, 9,000 of another company's layover takes
    this dispatcher from A to C.
    """
    load = _load(dispatcher_user_id=1, total_rate=10_000,
                 loaded_miles=4_000, empty_miles=200, vehicle_unit="T1")
    clean = compute_dispatcher_kpis([load], T)[0]
    fouled = compute_dispatcher_kpis(
        [load], T, off_load_items=[_off(1, 9_000.0)],
    )[0]
    assert clean["trucks"] == 1, "no truck => gross_per_truck is neutral"
    assert fouled["gross"] < clean["gross"]
    assert clean["grade"] == "A" and fouled["grade"] == "C"


@pytest.mark.asyncio
async def test_restricted_caller_gets_no_off_load_items(monkeypatch):
    """THE regression guard.

    An off-load row carries no company — ``load_id IS NULL`` and
    ``load_line_items`` has no company column — so under a company
    restriction it cannot be attributed.  The service must not fetch it:
    including it leaked a dispatcher row from an invisible company and
    charged unverifiable costs into the visible ones.
    """
    from features.kpi import service as kpi_service

    fetched: list[str] = []

    async def _loads(account_id, **kw):
        return []

    async def _off(account_id, **kw):
        fetched.append("off")
        return [_off_item()]

    def _off_item():
        return {"dispatcher_user_id": 99, "bucket": "other_costs",
                "amount": 1_000.0}

    async def _tenant(_account_id):
        return None

    monkeypatch.setattr(kpi_service.loads_service, "get_loads", _loads)
    monkeypatch.setattr(
        kpi_service.loads_service, "get_off_load_line_items", _off)
    monkeypatch.setattr(kpi_service, "get_tenant_db", _tenant)

    restricted = await kpi_service.get_dispatcher_kpis(
        1, days=30, company_codes=["ACME"],
    )
    assert fetched == [], "off-load items were fetched for a scoped caller"
    assert restricted["dispatchers"] == [], (
        "a dispatcher row reached a company-restricted caller from an "
        "off-load item that carries no company"
    )

    unrestricted = await kpi_service.get_dispatcher_kpis(
        1, days=30, company_codes=None,
    )
    assert fetched == ["off"], "unrestricted callers must keep the full picture"
    assert len(unrestricted["dispatchers"]) == 1
