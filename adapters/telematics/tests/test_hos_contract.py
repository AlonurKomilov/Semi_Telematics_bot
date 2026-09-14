"""The hours-of-service shape belongs to the protocol, not to a vendor.

Every ELD spells duty status differently — Samsara says ``offDuty``,
the regulation says "off duty", the next provider will say something
else.  If a feature ever learns one vendor's spelling, swapping the
vendor becomes a change to the feature, which is the whole thing this
seam exists to prevent.

So the vocabulary, the snapshot and the method live in
``adapters/telematics/protocol.py``, and each adapter maps into them.
These pin that, plus the two decisions inside the shape that are easy
to "simplify" away later and expensive to get wrong.
"""

from __future__ import annotations

import dataclasses

import pytest

from adapters.telematics.protocol import (
    Capability,
    DutyStatus,
    HosSnapshot,
    TelematicsProvider,
)


def test_the_vocabulary_is_closed_and_complete():
    assert DutyStatus.ALL == {
        "off_duty", "sleeper", "driving", "on_duty",
        "personal_conveyance", "yard_move", "unknown",
    }


def test_the_two_fmcsa_sub_statuses_are_their_own_values():
    """Personal conveyance is OFF duty and a yard move is ON duty, but
    neither may be collapsed into its parent: a driver moving under
    personal conveyance who gets reported as driving has a violation
    that does not exist."""
    assert DutyStatus.PERSONAL_CONVEYANCE != DutyStatus.OFF_DUTY
    assert DutyStatus.YARD_MOVE != DutyStatus.ON_DUTY
    assert DutyStatus.PERSONAL_CONVEYANCE != DutyStatus.DRIVING


def test_unknown_stays_in_the_set():
    """A status we could not map must be sayable.  Coercing it to
    off-duty would turn a gap in OUR mapping into a statement that a
    driver was resting."""
    assert DutyStatus.UNKNOWN in DutyStatus.ALL
    assert HosSnapshot(provider_driver_id="d1").duty_status == DutyStatus.UNKNOWN


def test_an_unreported_clock_is_none_and_not_zero():
    """Zero means "out of hours".  They are opposite answers."""
    snap = HosSnapshot(provider_driver_id="d1")
    assert snap.drive_remaining_seconds is None
    assert snap.shift_remaining_seconds is None
    assert snap.cycle_remaining_seconds is None
    assert snap.break_in_seconds is None


def test_the_snapshot_is_frozen():
    """It mirrors a certified device; nothing downstream may edit it."""
    snap = HosSnapshot(provider_driver_id="d1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.duty_status = DutyStatus.DRIVING        # type: ignore[misc]


def test_the_provider_time_is_separate_from_ours():
    """HOS goes stale in minutes.  A surface that shows our write time
    instead of the provider's observation calls a twenty-minute-old
    reading fresh."""
    fields = {f.name for f in dataclasses.fields(HosSnapshot)}
    assert "source_ts" in fields
    assert "updated_at" not in fields, (
        "our write time belongs to the row, not to the reading"
    )


def test_every_registered_provider_can_be_asked():
    """The protocol is runtime-checkable, so a provider missing the
    method stops being recognised as a provider at all — no silent
    AttributeError at 3am inside an ingest."""
    import adapters.telematics  # noqa: F401  (registers the providers)
    from adapters.telematics.registry import (
        get_provider, list_registered_providers,
    )

    registered = list_registered_providers()
    assert registered, "nothing registered — this guard would pass vacuously"
    for provider_id in registered:
        assert hasattr(get_provider(provider_id), "get_driver_hos"), provider_id


def test_the_capability_id_is_a_stable_wire_value():
    """It appears in ``account_integrations.feature_toggles`` rows, so
    renaming it silently disables the feed on every account that has
    an explicit toggle stored."""
    assert Capability.DRIVER_HOS == "driver_hos"
