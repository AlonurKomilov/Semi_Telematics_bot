"""Unit tests for curve evaluators (Audit Option C)."""
from __future__ import annotations

import pytest

from capabilities.scoring.curves import (
    LINEAR_PCT,
    LINEAR_PER_HOUR,
    LINEAR_PER_KMI,
    evaluate_curve,
)


# ── per-1000-mi ────────────────────────────────────────────────────

def test_per_kmi_zero_miles_returns_zero():
    """Zero exposure → no penalty even with raw events present."""
    pts = evaluate_curve(LINEAR_PER_KMI, raw_value=5, miles=0, drive_hours=10,
                         x_zero=0, x_max=10, y_max=-20)
    assert pts == 0.0


def test_per_kmi_at_anchor_max_yields_y_max():
    # 10 events / 1000 mi → exactly at x_max → y_max
    pts = evaluate_curve(LINEAR_PER_KMI, raw_value=10, miles=1000, drive_hours=10,
                         x_zero=0, x_max=10, y_max=-20)
    assert pts == pytest.approx(-20.0)


def test_per_kmi_clamps_above_x_max():
    pts = evaluate_curve(LINEAR_PER_KMI, raw_value=50, miles=1000, drive_hours=10,
                         x_zero=0, x_max=10, y_max=-20)
    assert pts == pytest.approx(-20.0)


def test_per_kmi_monotonic_in_raw():
    a = evaluate_curve(LINEAR_PER_KMI, raw_value=2, miles=1000, drive_hours=10,
                       x_zero=0, x_max=10, y_max=-20)
    b = evaluate_curve(LINEAR_PER_KMI, raw_value=5, miles=1000, drive_hours=10,
                       x_zero=0, x_max=10, y_max=-20)
    c = evaluate_curve(LINEAR_PER_KMI, raw_value=8, miles=1000, drive_hours=10,
                       x_zero=0, x_max=10, y_max=-20)
    assert a > b > c  # all negative; more events ⇒ deeper penalty


# ── per-hour ────────────────────────────────────────────────────────

def test_per_hour_zero_drive_returns_zero():
    pts = evaluate_curve(LINEAR_PER_HOUR, raw_value=5, miles=100, drive_hours=0,
                         x_zero=0, x_max=10, y_max=-25)
    assert pts == 0.0


def test_per_hour_at_anchor_max():
    pts = evaluate_curve(LINEAR_PER_HOUR, raw_value=10, miles=100, drive_hours=1,
                         x_zero=0, x_max=10, y_max=-25)
    assert pts == pytest.approx(-25.0)


# ── pct ─────────────────────────────────────────────────────────────

def test_pct_below_x_zero_yields_zero():
    pts = evaluate_curve(LINEAR_PCT, raw_value=10, miles=100, drive_hours=10,
                         x_zero=20, x_max=80, y_max=-10)
    assert pts == 0.0


def test_pct_clamps_above_x_max():
    pts = evaluate_curve(LINEAR_PCT, raw_value=120, miles=100, drive_hours=10,
                         x_zero=20, x_max=80, y_max=-10)
    assert pts == pytest.approx(-10.0)
