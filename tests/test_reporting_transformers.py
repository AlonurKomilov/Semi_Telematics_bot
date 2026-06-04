"""Unit tests for ``capabilities.reporting.transformers``.

Focus: regression coverage for the ``simplify_efficiency`` 500 bug
(2026-06).  ``adapters/samsara/client.py:1258`` explicitly sets
``_miles``/``_mpg``/``_green_pct``/etc. to ``None`` for vehicles
without a paired driver; the old ``round(v.get("_miles", 0), 1)``
path threw ``TypeError: type NoneType doesn't define __round__``
because ``dict.get`` only returns its default when the key is
*missing*, not when it's present-but-None.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.reporting.transformers import (
    _round_or_none,
    simplify_efficiency,
)


# ── _round_or_none helper ────────────────────────────────────────


def test_round_or_none_handles_none():
    """The whole point of the helper — None in, None out, no
    TypeError."""
    assert _round_or_none(None) is None
    assert _round_or_none(None, 2) is None


def test_round_or_none_rounds_floats():
    assert _round_or_none(1.234) == 1.2
    assert _round_or_none(1.234, 2) == 1.23
    assert _round_or_none(0) == 0


def test_round_or_none_swallows_other_bad_inputs():
    """Defensive: a misshapen payload (string, dict) shouldn't 500
    the endpoint — return None so the cell renders as 'no data'."""
    assert _round_or_none("not a number") is None
    assert _round_or_none({"oops": 1}) is None


# ── simplify_efficiency regression ───────────────────────────────


def test_simplify_efficiency_with_paired_driver():
    """Sanity check — vehicles with a driver pair have real numbers
    and they round through correctly."""
    out = simplify_efficiency({
        "name": "Truck 42",
        "_org": "ACME",
        "driver_name": "Jane Doe",
        "_miles": 1234.567,
        "_mpg": 7.890,
        "_drive_h": 8.5,
        "_idle_h": 1.25,
        "_drive_pct": 87.123,
        "_idle_pct": 12.876,
        "_green_pct": 95.5,
        "_overspeed_min": 2.3,
    })
    assert out["vehicle_name"] == "Truck 42"
    assert out["company"] == "ACME"
    assert out["driver_name"] == "Jane Doe"
    assert out["miles"] == 1234.6
    assert out["mpg"] == 7.9
    assert out["drive_hours"] == 8.5
    assert out["idle_hours"] == 1.2  # banker's-rounding on 1.25 → 1.2 with ndigits=1
    assert out["eco_pct"] == 95.5
    assert out["overspeed_min"] == 2.3


def test_simplify_efficiency_no_driver_does_not_crash():
    """The actual regression: client.py:1258 sets these fields to
    None when there's no driver paired.  Before the fix, this 500'd
    every efficiency report on every account with unpaired vehicles."""
    out = simplify_efficiency({
        "name": "Truck 99",
        "_org": "ACME",
        # Driver not paired — every metric field is explicitly None.
        "_driver_name": None,
        "_miles": None,
        "_mpg": None,
        "_drive_h": None,
        "_idle_h": None,
        "_drive_pct": None,
        "_idle_pct": None,
        "_green_pct": None,
        "_overspeed_min": None,
        "_fuel_gal": None,
    })
    # Doesn't raise — that's the win.
    assert out["vehicle_name"] == "Truck 99"
    assert out["miles"] is None
    assert out["mpg"] is None
    assert out["drive_hours"] is None
    assert out["idle_hours"] is None
    assert out["drive_pct"] is None
    assert out["idle_pct"] is None
    assert out["eco_pct"] is None
    assert out["overspeed_min"] is None


def test_simplify_efficiency_missing_fields_treats_as_none():
    """Defensive: completely empty record (no metric fields at all)
    must also pass cleanly — same null output, no TypeError."""
    out = simplify_efficiency({"name": "Truck 7"})
    assert out["vehicle_name"] == "Truck 7"
    assert out["miles"] is None
    assert out["mpg"] is None
    assert out["eco_pct"] is None


def test_simplify_efficiency_partial_data():
    """Realistic mid-state: driver paired but the activity window had
    no drive (engine-on stationary).  Some fields populated, others
    None — both code paths must survive in the same record."""
    out = simplify_efficiency({
        "name": "Truck 13",
        "_org": "ACME",
        "driver_name": "John Smith",
        "_miles": 0.0,           # parked all window
        "_mpg": None,            # no fuel data
        "_drive_h": 0.0,
        "_idle_h": 8.0,
        "_drive_pct": 0.0,
        "_idle_pct": 100.0,
        "_green_pct": None,      # eco score doesn't compute when no drive
        "_overspeed_min": 0.0,
    })
    assert out["miles"] == 0.0
    assert out["mpg"] is None
    assert out["drive_hours"] == 0.0
    assert out["idle_hours"] == 8.0
    assert out["eco_pct"] is None
    assert out["overspeed_min"] == 0.0
