"""Pillar accumulator + insufficient-data tests."""
from __future__ import annotations

from capabilities.scorecards.engine import (
    MIN_DRIVE_HOURS_FOR_RANKING,
    PILLAR_CAPS,
    score,
)
from capabilities.scorecards.rules.defs import (
    PILLAR_COMPLIANCE,
    PILLAR_EFFICIENCY,
    PILLAR_SAFETY,
    Rule,
)


def _signals(**over):
    """Build a minimum-viable signals dict; override fields per test."""
    base = {
        "drive_hours": 10.0,
        "miles": 500.0,
        "idle_hours": 1.0,
        "idle_pct": 10.0,
        "overspeed_min": 0.0,
        "green_pct": 75.0,
        "antic_pct": 50.0,
        "events": {"count": 0, "by_type": {}},
        "maintenance": {"pti_completed": 1, "pti_overdue": 0, "other_overdue": 0,
                        "vehicles_assigned": 1},
        "cameras": {"checks_total": 0, "checks_failing": 0,
                    "vehicles_with_failing_camera": 0},
        "fuel": {"vehicles_assigned": 1, "vehicles_with_entries": 1, "anomalous_entries": 0},
        "vehicle_health": {"vehicles_assigned": 1, "vehicles_clean": 1,
                           "vehicles_with_faults": 0, "critical_alerts": 0,
                           "minor_alerts": 0},
    }
    base.update(over)
    return base


def test_pillar_caps_sum_to_100():
    assert sum(PILLAR_CAPS.values()) == 100


def test_clean_driver_scores_full():
    """Driver with no events and complete data lands at the cap."""
    rules = [
        Rule(id="x.bonus", label="x", category="safety", pillar=PILLAR_SAFETY,
             kind="bonus", points=10, condition=lambda s: 1),
    ]
    sc = score("d1", "Alice", _signals(), rules)
    # Safety pillar got +10 above the cap → clamps to cap (50).
    assert sc.pillars[PILLAR_SAFETY].subtotal == 50
    # Without other rules, efficiency & compliance start at cap & untouched.
    assert sc.pillars[PILLAR_EFFICIENCY].subtotal == 25
    assert sc.pillars[PILLAR_COMPLIANCE].subtotal == 25
    assert sc.total == 100


def test_pillar_penalty_clamps_at_zero():
    """A penalty larger than the pillar cap can't drag below zero."""
    rules = [
        Rule(id="huge", label="x", category="safety", pillar=PILLAR_SAFETY,
             kind="penalty", points=-200, condition=lambda s: 1),
    ]
    sc = score("d1", "Alice", _signals(), rules)
    assert sc.pillars[PILLAR_SAFETY].subtotal == 0
    # Other pillars unaffected.
    assert sc.pillars[PILLAR_EFFICIENCY].subtotal == 25
    assert sc.pillars[PILLAR_COMPLIANCE].subtotal == 25
    assert sc.total == 50


def test_total_equals_sum_of_subtotals():
    rules = [
        Rule(id="s1", label="x", category="safety", pillar=PILLAR_SAFETY,
             kind="penalty", points=-10, condition=lambda s: 1),
        Rule(id="e1", label="x", category="efficiency", pillar=PILLAR_EFFICIENCY,
             kind="penalty", points=-5, condition=lambda s: 1),
    ]
    sc = score("d1", "Alice", _signals(), rules)
    assert sc.total == sum(p.subtotal for p in sc.pillars.values())


def test_insufficient_data_flag():
    rules: list[Rule] = []
    sc = score("d1", "Alice", _signals(drive_hours=2.0), rules)
    assert sc.insufficient_data is True
    sc2 = score("d1", "Alice", _signals(drive_hours=MIN_DRIVE_HOURS_FOR_RANKING), rules)
    assert sc2.insufficient_data is False


def test_legacy_aliases_present():
    """Backward-compat: top-level ``base/bonus_points/penalty_points`` exist."""
    rules = [
        Rule(id="b1", label="x", category="safety", pillar=PILLAR_SAFETY,
             kind="bonus", points=5, condition=lambda s: 1),
    ]
    sc = score("d1", "Alice", _signals(), rules)
    assert sc.base == 0
    assert sc.bonus_points == 5
    assert sc.penalty_points == 0
    assert len(sc.bonuses) == 1
    assert sc.bonuses[0].pillar == PILLAR_SAFETY
