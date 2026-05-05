"""Smooth curve evaluators for scoring rules (audit Option C).

Replaces brittle step thresholds (``green_pct >= 80 → +5``) with linear
interpolation between two anchor points.  Each evaluator returns a
*signed* point delta which the engine then caps against the rule's
pillar budget.

All evaluators are pure functions of (signals, rule) — no I/O, no
clock — so they can be exhaustively unit-tested.
"""

from __future__ import annotations

from typing import Callable

from .normalize import per_hour, per_kmi


# Curve kinds — code-defined per rule (not editable).  The DB only stores
# the three numeric anchors so admins can re-tune the slope without
# changing the rule's signal source.
LINEAR_PER_KMI  = "linear_per_kmi"
LINEAR_PER_HOUR = "linear_per_hour"
LINEAR_PCT      = "linear_pct"   # signal already a 0-100 percentage


_VALID_KINDS = {LINEAR_PER_KMI, LINEAR_PER_HOUR, LINEAR_PCT}


def _interp(x: float, x_zero: float, x_max: float, y_max: float) -> float:
    """Linearly interpolate y between (x_zero, 0) and (x_max, y_max).

    Behaviour:
        x ≤ x_zero → 0
        x_zero < x < x_max → linear ramp
        x ≥ x_max → y_max  (clamped, no overshoot)

    Works for both rising bonuses (y_max > 0) and falling penalties
    (y_max < 0) — the clamp uses ``min``/``max`` so the sign of y_max
    determines direction.
    """
    if x_max == x_zero:
        # Degenerate config — treat as flat threshold.
        return float(y_max) if x >= x_max else 0.0
    if x <= x_zero:
        return 0.0
    if x >= x_max:
        return float(y_max)
    frac = (x - x_zero) / (x_max - x_zero)
    return frac * float(y_max)


def evaluate_curve(
    kind: str,
    *,
    raw_value: float,
    miles: float,
    drive_hours: float,
    x_zero: float,
    x_max: float,
    y_max: float,
) -> float:
    """Compute the signed point contribution for a curve-driven rule.

    *raw_value* is what the rule's `curve_value_fn` extracted from the
    signals dict (e.g. count of harsh-brake events, or a green-band %).
    The exposure metric (miles or drive_hours) is selected by *kind*.

    Returns 0.0 when the underlying exposure is below a tiny floor —
    we don't penalise a driver who didn't drive.
    """
    if kind not in _VALID_KINDS:
        return 0.0

    if kind == LINEAR_PER_KMI:
        if miles is None or miles < 1.0:   # < 1 mile is noise
            return 0.0
        rate = per_kmi(raw_value, miles)
    elif kind == LINEAR_PER_HOUR:
        if drive_hours is None or drive_hours < 0.1:
            return 0.0
        rate = per_hour(raw_value, drive_hours)
    else:  # LINEAR_PCT
        rate = float(raw_value or 0)

    return _interp(rate, x_zero, x_max, y_max)


# Type alias used by Rule.curve_value_fn — kept here so rules.py imports
# stay shallow.
ValueFn = Callable[[dict], float]
