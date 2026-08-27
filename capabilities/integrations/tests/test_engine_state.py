"""The engine-state vocabulary both telemetry writers share.

Roll-ups count ``moving`` samples as drive minutes and ``idle`` samples
as idle minutes, so what this resolver returns IS the utilisation
number a customer reads.  The case that matters most is a running
engine on a stationary truck: calling that "moving" would book parked
hours as driving and leave idle time — the hours that burn fuel for no
miles — showing zero.
"""

import pytest

from capabilities.integrations.shared.engine_state import (
    IDLE,
    MOVING,
    OFF,
    UNKNOWN,
    resolve_engine_state,
)


@pytest.mark.parametrize("raw,speed,expected", [
    # The provider says the engine runs; speed decides what that means.
    ("On", 65.0, MOVING),
    ("On", 0.0, IDLE),
    ("on", 0.5, IDLE),          # GPS jitter on a parked truck
    ("ON", 2.0, MOVING),        # case is the provider's business
    # An explicit idle state needs no help.
    ("Idle", 0.0, IDLE),
    ("Idle", None, IDLE),
    # Off stays off even if a stale speed reading disagrees.
    ("Off", 0.0, OFF),
    ("Off", 70.0, OFF),
])
def test_provider_state_resolves_against_speed(raw, speed, expected):
    assert resolve_engine_state(raw, speed) == expected


def test_running_engine_without_speed_stays_moving():
    """No speed reading is not evidence of standing still — trust the
    provider rather than invent idle time."""
    assert resolve_engine_state("On", None) == MOVING


@pytest.mark.parametrize("speed,expected", [
    (70.0, MOVING),   # plainly driving, whatever the missing feed says
    (0.0, UNKNOWN),
    (None, UNKNOWN),
])
def test_missing_provider_state_falls_back_to_speed(speed, expected):
    assert resolve_engine_state(None, speed) == expected


def test_silence_is_never_reported_as_off():
    """A truck with no engine feed and no speed is unknown.

    Answering "off" would let the roll-ups count a dead feed as a parked
    truck — the same confusion between silence and fact that made a
    frozen odometer look like a fresh reading.
    """
    assert resolve_engine_state("", None) == UNKNOWN
    assert resolve_engine_state(None, None) == UNKNOWN
    assert resolve_engine_state("garbled", None) == UNKNOWN


def test_unparseable_speed_is_ignored_not_fatal():
    assert resolve_engine_state("On", "not-a-number") == MOVING
    assert resolve_engine_state(None, "not-a-number") == UNKNOWN
