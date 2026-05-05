"""Exposure-normalisation helpers for scorecard signals.

Pure, side-effect-free.  Used by the curve evaluators so the scoring
engine stays unit-testable without mocks.
"""

from __future__ import annotations


def per_kmi(count: float, miles: float) -> float:
    """Events per 1000 miles.  Returns 0.0 when miles == 0."""
    if miles is None or miles <= 0:
        return 0.0
    return float(count) / (float(miles) / 1000.0)


def per_hour(count: float, hours: float) -> float:
    """Events per hour.  Returns 0.0 when hours == 0."""
    if hours is None or hours <= 0:
        return 0.0
    return float(count) / float(hours)
