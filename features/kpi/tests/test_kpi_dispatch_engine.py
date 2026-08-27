"""The incentive engine reproduces a REAL settlement sheet to the cent.

The customer's manager provided Period 6 (11/03–11/30) of their actual
dispatcher-KPI Excel: six trucks, one dispatcher, a $1,332.03 payout.
This file recomputes every row from raw inputs and must match every
column — Adjusted Gross Target, RPM, KPI-%, KPI-$ and the confirmed
total.  If the engine cannot reproduce a settlement their Excel already
paid, the engine is wrong, whatever its unit tests say.

The sheet also decided three semantics no spec stated (each is asserted
separately below so a regression names the rule it broke):

  * RPM excludes extras, the %-base includes them — provable from the
    Donat row, where only (22,680 − 350)/10,222 gives the sheet's 2.18;
  * tier comparison uses ROUNDED rpm — provable from the ISAIAH row,
    where raw 1.9997 would miss the ≥2.0 tier the sheet paid;
  * money rounds half-up — provable from the first row, where banker's
    rounding answers 340.12 against the sheet's 340.13.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from features.kpi.dispatch.engine import (  # noqa: E402
    adjusted_target, compute_rpm, compute_truck_row, confirmed_dollars,
    matched_rule, money, payout_total, resolve_pct, validate_exception,
)

# The customer's ladder, as config rows.  Reconstructed from their tier
# table with CD/OO dropped (owner: driver type is not a KPI input):
#   below the bar but strong RPM        → 1%
#   bar met, any RPM                    → 1%
#   bar met and RPM ≥ 2.0               → 1.5%
#   bar met and RPM ≥ 2.5               → 2.0%
# plus the removal rule: under $7,000/wk AND under 1.9 RPM → 0%.
CONFIG = {
    "model": "ladder",
    "exception_cap_pct": 2.0,
    "floor_weekly_gross": 7000.0,
    "floor_rpm": 1.9,
}
TIERS = [
    {"min_rpm": 2.0, "pct": 1.0},
    {"requires_target": True, "pct": 1.0},
    {"requires_target": True, "min_rpm": 2.0, "pct": 1.5},
    {"requires_target": True, "min_rpm": 2.5, "pct": 2.0},
]

# Period 6 rows: (unit, base_gross, extras, miles, weekly_target,
#                 active_days, expected_target, expected_rpm,
#                 expected_pct, expected_dollars)
# base_gross = the sheet's Total gross minus its extras column, because
# the sheet's RPM only reproduces from the extras-free figure.
SHEET = [
    ("OSY 200", 22_575.0, 100.0, 11_110, 8_000, 19,
     21_714.29, 2.03, 1.5, 340.13),
    ("OSY 237", 15_200.0, 0.0, 7_601, 8_000, 13,
     14_857.14, 2.00, 1.5, 228.00),
    ("OSY 211", 22_330.0, 350.0, 10_222, 7_000, 18,
     18_000.00, 2.18, 1.5, 340.20),
    ("CFT 201", 23_800.0, 0.0, 13_015, 8_000, 20,
     22_857.14, 1.83, 1.0, 238.00),
    ("CFT 569640", 11_000.0, 0.0, 5_960, 7_000, 14,
     14_000.00, 1.85, 0.0, 0.00),
    ("CFT 301", 6_670.0, 0.0, 3_590, 7_000, 6,
     6_000.00, 1.86, 1.0, 66.70),
]


class TestGoldenSheet:
    @pytest.mark.parametrize(
        "unit,base,extras,miles,target,days,exp_target,exp_rpm,exp_pct,exp_usd",
        SHEET, ids=[r[0] for r in SHEET],
    )
    def test_row(self, unit, base, extras, miles, target, days,
                 exp_target, exp_rpm, exp_pct, exp_usd):
        row = compute_truck_row(
            CONFIG, TIERS,
            base_gross=base, extras=extras, miles=miles,
            weekly_target=target, active_days=days,
        )
        assert row["adjusted_target"] == exp_target, unit
        assert row["rpm"] == exp_rpm, unit
        assert row["pct"] == exp_pct, unit
        assert row["kpi_dollars"] == exp_usd, unit

    def test_the_payout_total_with_kevins_exception(self):
        """The sheet's bottom line.  CFT 201 (Kevin) computed 1% ($238)
        and was CONFIRMED at 1.5% ($357) with reason "New MC" — a manual
        exception, legal because 1.5 ≤ the 2% cap.  Every other row pays
        as computed.  Sum: $1,332.03, the figure in the sheet's corner.
        """
        confirmed = []
        for (unit, base, extras, miles, target, days, *_rest) in SHEET:
            row = compute_truck_row(
                CONFIG, TIERS,
                base_gross=base, extras=extras, miles=miles,
                weekly_target=target, active_days=days,
            )
            override = 1.5 if unit == "CFT 201" else None
            confirmed.append(confirmed_dollars(row, CONFIG, override))
        assert confirmed[3] == 357.00          # Kevin, post-exception
        assert payout_total(confirmed) == 1_332.03


class TestVerifiedSemantics:
    """One test per sheet-derived rule, so a break names its rule."""

    def test_rpm_excludes_extras_but_the_base_includes_them(self):
        # Donat: only (22,680 − 350) / 10,222 = 2.1845 → 2.18 matches the
        # sheet; 22,680/10,222 = 2.2187 → 2.22 does not.  Yet his KPI-$
        # is 340.20 = 22,680 × 1.5% — the bonus IS in the %-base.
        assert compute_rpm(22_330.0, 10_222) == 2.18
        row = compute_truck_row(
            CONFIG, TIERS, base_gross=22_330.0, extras=350.0,
            miles=10_222, weekly_target=7_000, active_days=18,
        )
        assert row["kpi_gross"] == 22_680.00
        assert row["kpi_dollars"] == 340.20

    def test_tier_comparison_sees_rounded_rpm(self):
        # ISAIAH: 15,200 / 7,601 = 1.99973…  The sheet displays 2.00 and
        # PAID the ≥2.0 tier.  Comparing raw would demote him to 1%.
        assert compute_rpm(15_200.0, 7_601) == 2.00
        pct = resolve_pct(
            CONFIG, TIERS,
            weekly_eq=weekly_eq_of(15_200.0, 13), rpm=2.00, target_met=True,
        )
        assert pct == 1.5

    def test_money_rounds_half_up_not_bankers(self):
        # 22,675 × 1.5% = 340.125.  round() says 340.12; the sheet says
        # 340.13.  Settlements round half-up.
        assert money(340.125) == 340.13
        assert round(340.125, 2) == 340.12     # the trap, documented

    def test_proration_matches_the_sheet_to_the_cent(self):
        assert adjusted_target(8_000, 19) == 21_714.29
        assert adjusted_target(7_000, 18) == 18_000.00
        assert adjusted_target(7_000, 6) == 6_000.00

    def test_the_floor_needs_BOTH_conditions(self):
        # CFT 301 is above the gross floor on weekly-equivalent terms
        # (7,781.67) despite weak RPM — not removed, pays 1%.  Gadi is
        # below both — removed.
        row = compute_truck_row(
            CONFIG, TIERS, base_gross=6_670.0, extras=0.0, miles=3_590,
            weekly_target=7_000, active_days=6,
        )
        assert row["pct"] == 1.0
        gadi = compute_truck_row(
            CONFIG, TIERS, base_gross=11_000.0, extras=0.0, miles=5_960,
            weekly_target=7_000, active_days=14,
        )
        assert gadi["pct"] == 0.0

    def test_exception_above_the_cap_is_refused_not_clamped(self):
        with pytest.raises(ValueError, match="policy cap"):
            validate_exception(2.5, CONFIG)
        validate_exception(2.0, CONFIG)        # at the cap: allowed

    def test_zero_active_days_pays_nothing(self):
        row = compute_truck_row(
            CONFIG, TIERS, base_gross=10_000.0, extras=0.0, miles=5_000,
            weekly_target=8_000, active_days=0,
        )
        assert row["pct"] == 0.0
        assert row["kpi_dollars"] == 0.0


def weekly_eq_of(gross: float, days: int) -> float:
    from features.kpi.dispatch.engine import weekly_equivalent
    return weekly_equivalent(gross, days)


class TestOtherModels:
    """The customer picks the model; all three must work."""

    def test_hybrid_matches_on_both_ranges(self):
        cfg = {"model": "hybrid"}
        tiers = [
            {"gross_min": 0, "gross_max": 7_999,
             "rpm_min": 1.0, "rpm_max": 2.0, "pct": 1.0},
            {"gross_min": 8_000, "gross_max": 9_000,
             "rpm_min": 2.2, "rpm_max": 2.3, "pct": 1.5},
        ]
        assert resolve_pct(cfg, tiers, weekly_eq=8_500, rpm=2.25,
                           target_met=True) == 1.5
        # In range on gross, out on RPM → no match, not a partial one.
        assert resolve_pct(cfg, tiers, weekly_eq=8_500, rpm=2.1,
                           target_met=True) == 0.0

    @pytest.mark.parametrize("rule,expected", [
        ("lower", 1.0), ("higher", 1.5), ("add", 2.5),
    ])
    def test_fixed_combine_rules(self, rule, expected):
        cfg = {"model": "fixed", "combine_rule": rule}
        tiers = [
            {"axis": "rpm", "min": 2.2, "pct": 1.5},
            {"axis": "gross", "min": 8_000, "pct": 1.0},
        ]
        assert resolve_pct(cfg, tiers, weekly_eq=9_000, rpm=2.3,
                           target_met=True) == expected


from features.kpi.dispatch import engine  # noqa: E402


class TestNextTierGap:
    """The shortfall explanation: smallest ADDITIONAL gross that lifts a
    ladder row to a higher percent — the number a 0% row argues with."""

    LADDER = [
        {"min_rpm": 2.0, "pct": 1.0},
        {"requires_target": True, "min_rpm": 2.0, "pct": 1.0},
        {"requires_target": True, "min_rpm": 2.2, "pct": 1.5},
        {"requires_target": True, "min_rpm": 2.5, "pct": 2.0},
    ]

    def test_the_50_dollar_gap(self):
        # $7,950 at RPM 2.81 vs an $8,000 7-day target: $50 to 1.5%.
        g = engine.next_tier_gap(
            {"model": "ladder"}, self.LADDER,
            base_gross=7_950, extras=0, miles=2_826.39,
            weekly_target=8_000, active_days=7, current_pct=1.0)
        assert g == {"pct": 1.5, "gap": 50.0, "dollars_at": 120.0,
                     "min_rpm": 2.2}

    def test_no_rpm_tier_reports_null_condition(self):
        # A target-only tier: the gap is pure gross, valid at any miles
        # — min_rpm None tells the UI to skip the miles caveat.
        g = engine.next_tier_gap(
            {"model": "ladder"}, [{"requires_target": True, "pct": 1.0}],
            base_gross=5_000, extras=0, miles=2_000,
            weekly_target=8_000, active_days=7, current_pct=0.0)
        assert g == {"pct": 1.0, "gap": 3_000.0, "dollars_at": 80.0,
                     "min_rpm": None}

    def test_rpm_condition_converts_to_gross(self):
        # Target met but RPM 2.1 < 2.2: the binding condition is
        # min_rpm x miles, not the target.
        g = engine.next_tier_gap(
            {"model": "ladder"}, self.LADDER,
            base_gross=8_400, extras=0, miles=4_000,
            weekly_target=8_000, active_days=7, current_pct=1.0)
        assert g["pct"] == 1.5
        assert g["gap"] == 2.2 * 4_000 - 8_400  # 400.0

    def test_topped_out_and_non_ladder_return_none(self):
        assert engine.next_tier_gap(
            {"model": "ladder"}, self.LADDER,
            base_gross=20_000, extras=0, miles=5_000,
            weekly_target=8_000, active_days=7, current_pct=2.0) is None
        assert engine.next_tier_gap(
            {"model": "hybrid"}, self.LADDER,
            base_gross=5_000, extras=0, miles=2_000,
            weekly_target=8_000, active_days=7, current_pct=0.0) is None

    def test_no_target_and_zero_miles_are_not_gaps(self):
        assert engine.next_tier_gap(
            {"model": "ladder"}, self.LADDER,
            base_gross=5_000, extras=0, miles=2_000,
            weekly_target=None, active_days=7, current_pct=0.0) is None
        # zero miles: RPM tiers unreachable; target-only rows absent here.
        g = engine.next_tier_gap(
            {"model": "ladder"}, self.LADDER,
            base_gross=200, extras=0, miles=0,
            weekly_target=8_000, active_days=3, current_pct=0.0)
        assert g is None


class TestMatchedRule:
    """matched_rule shares resolve_pct's resolver, so the explanation
    can never disagree with the price — these pin the contract."""

    def test_names_the_winning_ladder_tier(self):
        # OSY 200's inputs: bar met, RPM 2.03 → the 1.5% tier wins.
        rule = matched_rule(
            CONFIG, TIERS, weekly_eq=8_353.95, rpm=2.03, target_met=True)
        assert rule == {
            "model": "ladder", "pct": 1.5,
            "min_weekly_gross": None, "min_rpm": 2.0,
            "requires_target": True,
        }

    def test_zero_rows_return_none(self):
        # CFT 569640: floored (both under) → None; the zero path owns it.
        assert matched_rule(
            CONFIG, TIERS, weekly_eq=5_500.0, rpm=1.85, target_met=False,
        ) is None

    def test_agrees_with_resolve_pct_on_every_golden_row(self):
        for unit, base, extras, miles, target, days, *_ in SHEET:
            row = compute_truck_row(
                CONFIG, TIERS, base_gross=base, extras=extras, miles=miles,
                weekly_target=target, active_days=days)
            rule = matched_rule(
                CONFIG, TIERS, weekly_eq=row["weekly_equivalent"],
                rpm=row["rpm"], target_met=row["target_met"])
            if row["pct"] > 0:
                assert rule is not None and rule["pct"] == row["pct"], unit
            else:
                assert rule is None, unit

    def test_fixed_model_describes_the_combination(self):
        cfg = {"model": "fixed", "combine_rule": "lower"}
        tiers = [
            {"axis": "rpm", "min": 2.0, "pct": 1.5},
            {"axis": "gross", "min": 8_000.0, "pct": 1.0},
        ]
        rule = matched_rule(
            cfg, tiers, weekly_eq=9_000.0, rpm=2.1, target_met=True)
        assert rule == {
            "model": "fixed", "pct": 1.0, "combine_rule": "lower",
            "rpm_pct": 1.5, "gross_pct": 1.0,
        }
