"""``pick_readings`` — a reading moves whole, freshness is a fact, zero is
a value.  Each rule in the module docstring has a test that would fail
without it."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from capabilities.source.readings import NEWEST, Reading, pick_readings

NOW = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)
ORDER = {"odometer": ("samsara", "orient_eld"),
         "location": ("samsara", "orient_eld")}


def _t(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def _odo(source: str, miles, minutes_ago) -> Reading:
    return Reading(source, {"odometer_mi": miles},
                   _t(minutes_ago) if minutes_ago is not None else None)


def _pick(cands, order=ORDER, sla=15):
    return pick_readings(cands, precedence=order, sla_min=sla, now=NOW)


def test_fresh_preferred_wins_over_a_fresh_newer_secondary():
    """The order says whom the owner trusts; a newer reading from the
    other device does not outrank it while both are inside tolerance."""
    out = _pick({"odometer": [_odo("orient_eld", 100.0, 1), _odo("samsara", 99.0, 10)]})
    assert out["odometer"].source == "samsara"


def test_a_stale_preferred_device_yields_to_a_fresh_secondary():
    out = _pick({"odometer": [_odo("samsara", 99.0, 40), _odo("orient_eld", 100.0, 3)]})
    assert out["odometer"].source == "orient_eld"


def test_when_every_reading_is_stale_the_newest_wins_whatever_the_order():
    out = _pick({"odometer": [_odo("samsara", 99.0, 120), _odo("orient_eld", 100.0, 60)]})
    assert out["odometer"].source == "orient_eld"


def test_newest_as_the_rule_picks_the_newest_among_the_fresh():
    order = {"odometer": (NEWEST, "samsara", "orient_eld")}
    out = _pick({"odometer": [_odo("samsara", 99.0, 10), _odo("orient_eld", 100.0, 1)]}, order)
    assert out["odometer"].source == "orient_eld"
    # ...and still refuses a stale reading while a fresh one exists.
    out = _pick({"odometer": [_odo("samsara", 99.0, 10), _odo("orient_eld", 100.0, 40)]}, order)
    assert out["odometer"].source == "samsara"


def test_a_reading_with_no_value_never_competes():
    """Preferred and fresh, but empty: the stale secondary is the only
    reading there is."""
    out = _pick({"odometer": [_odo("samsara", None, 1), _odo("orient_eld", 100.0, 40)]})
    assert out["odometer"].source == "orient_eld"


def test_zero_is_a_value():
    """A parked truck reports 0 mph.  ``is_unset`` would call that absent
    and hand the group to a stale 50 mph from the other device.  The
    fresh reading carries NOTHING but the zero, so only the zero rule
    keeps it in the contest."""
    out = _pick({"location": [
        Reading("samsara", {"lat": None, "lon": None, "speed_mph": 0}, _t(1)),
        Reading("orient_eld", {"lat": 40.0, "lon": -74.0, "speed_mph": 50}, _t(40)),
    ]})
    assert out["location"].source == "samsara"
    assert out["location"].values["speed_mph"] == 0
    # Same for a gauge: a fresh 0.0 is a reading, not a gap.
    out = _pick({"odometer": [_odo("samsara", 0.0, 1), _odo("orient_eld", 12.0, 40)]})
    assert out["odometer"].source == "samsara"


def test_a_group_nobody_reported_is_absent_from_the_answer():
    out = _pick({"odometer": [_odo("samsara", None, 1), _odo("orient_eld", "", 1)]})
    assert out == {}


def test_an_unknown_clock_counts_as_stale():
    """Same rule as every reader: unknown age IS stale."""
    out = _pick({"odometer": [_odo("samsara", 99.0, None), _odo("orient_eld", 100.0, 3)]})
    assert out["odometer"].source == "orient_eld"
    # Both unknown: nothing is fresh, nothing is newer — the order decides.
    out = _pick({"odometer": [_odo("orient_eld", 100.0, None), _odo("samsara", 99.0, None)]})
    assert out["odometer"].source == "samsara"


def test_the_winner_is_one_observation_not_a_blend():
    """The answer is the Reading object itself — values AND clock from
    one source.  A caller cannot receive samsara's miles under
    orient's time because there is no such object to hand back."""
    samsara = _odo("samsara", 99.0, 10)
    orient = _odo("orient_eld", 100.0, 1)
    out = _pick({"odometer": [orient, samsara]})
    assert out["odometer"] is samsara
    assert out["odometer"].time == samsara.time
