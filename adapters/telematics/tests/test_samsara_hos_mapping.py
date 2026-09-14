"""Where Samsara's spelling stops.

Everything above the provider sees our ``DutyStatus`` values and
seconds; nothing above it has ever heard of ``hosStatusType`` or a
duration in milliseconds.  These pin that boundary, and especially the
two places where a wrong guess would be worse than no answer.

The exact Samsara field names are the one thing here that cannot be
checked from this repo — there is no recorded HOS payload to replay.
So the mapping is written to DEGRADE rather than to assume: an
unrecognised duty status becomes ``unknown``, and a clock key we do not
find becomes ``None``.  Both are honest; both are what the ingest
refuses to answer from.  A live payload should still be diffed against
this before the capability is switched on.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from adapters.telematics.protocol import DutyStatus
from adapters.telematics.samsara.provider import SamsaraProvider, _duty_status


class _Client:
    def __init__(self, rows):
        self._rows = rows
        self.asked = 0

    async def get_hos_clocks(self):
        self.asked += 1
        return list(self._rows)


def _flat(**kw):
    base = {
        "provider_driver_id": "d1",
        "driver_name": "Jane Ruiz",
        "raw_duty_status": "driving",
        "last_status_change": "2026-09-14T08:00:00Z",
        "drive_seconds_today": 3600,
        "on_duty_seconds_today": 7200,
        "cycle_seconds_remaining": 180000,
        "shift_seconds_remaining": 25200,
    }
    base.update(kw)
    return base


# ── The vocabulary boundary ───────────────────────────────────────

@pytest.mark.parametrize("vendor,ours", [
    ("offDuty",            DutyStatus.OFF_DUTY),
    ("sleeperBerth",       DutyStatus.SLEEPER),
    ("sleeperBed",         DutyStatus.SLEEPER),
    ("driving",            DutyStatus.DRIVING),
    ("onDuty",             DutyStatus.ON_DUTY),
    ("personalConveyance", DutyStatus.PERSONAL_CONVEYANCE),
    ("yardMove",           DutyStatus.YARD_MOVE),
])
def test_every_known_spelling_maps(vendor, ours):
    assert _duty_status(vendor) == ours


@pytest.mark.parametrize("spelling", [
    "off_duty", "OFF-DUTY", "offduty", "Off Duty",
])
def test_a_rename_that_only_changes_punctuation_costs_nothing(spelling):
    """Keys are normalised to letters and digits, so a vendor moving
    between camelCase and snake_case does not break the feed."""
    assert _duty_status(spelling) == DutyStatus.OFF_DUTY


@pytest.mark.parametrize("raw", ["somethingNew", "", None, "  ", 42])
def test_an_unmapped_status_becomes_unknown_and_never_off_duty(raw):
    """The tempting default is off_duty — it is the commonest real
    value — and it is the one answer we must never invent.  It would
    turn a gap in OUR table into a statement that a driver was
    resting."""
    got = _duty_status(raw)
    assert got == DutyStatus.UNKNOWN
    assert got != DutyStatus.OFF_DUTY


# ── The provider's projection ─────────────────────────────────────

@pytest.mark.asyncio
async def test_the_snapshot_carries_our_words_and_our_units():
    provider = SamsaraProvider(_Client([_flat()]))
    snaps = await provider.get_driver_hos()

    assert len(snaps) == 1
    s = snaps[0]
    assert s.provider_driver_id == "d1"
    assert s.duty_status == DutyStatus.DRIVING
    assert s.drive_seconds_today == 3600
    assert s.cycle_seconds_remaining == 180000
    assert s.driver_name == "Jane Ruiz"
    assert s.last_status_change == "2026-09-14T08:00:00Z"


@pytest.mark.asyncio
async def test_source_ts_is_stamped_because_this_endpoint_means_now():
    """``/fleet/hos/clocks`` answers "right now", so fetch time IS the
    observation time.  A provider that reports its own would carry
    that instead — but an empty source_ts would leave every reading
    looking equally fresh, which is the failure the column exists to
    prevent."""
    provider = SamsaraProvider(_Client([_flat()]))
    snaps = await provider.get_driver_hos()
    assert snaps[0].source_ts
    assert "T" in snaps[0].source_ts


@pytest.mark.asyncio
async def test_an_unreported_clock_stays_none():
    provider = SamsaraProvider(_Client([_flat(
        cycle_seconds_remaining=None, shift_seconds_remaining=0,
    )]))
    s = (await provider.get_driver_hos())[0]
    assert s.cycle_seconds_remaining is None
    assert s.shift_seconds_remaining == 0


@pytest.mark.asyncio
async def test_a_driver_with_no_provider_id_is_dropped():
    """Nothing can be keyed on or linked to it, and inventing a key
    would put a nameless row in front of a dispatcher."""
    provider = SamsaraProvider(_Client([_flat(provider_driver_id=""),
                                        _flat(provider_driver_id="d2")]))
    snaps = await provider.get_driver_hos()
    assert [s.provider_driver_id for s in snaps] == ["d2"]


@pytest.mark.asyncio
async def test_no_drivers_is_an_empty_list_not_an_error():
    provider = SamsaraProvider(_Client([]))
    assert await provider.get_driver_hos() == []


# ── Switched on ───────────────────────────────────────────────────

def test_the_capability_is_declared_with_a_feed_behind_it():
    """It was deliberately undeclared until the ingest existed; the
    test that asserted that deleted itself when the ingest shipped.
    What replaces it is the rule that mattered: the declaration and
    the feed arrive together, so the toggle an owner sees always
    controls something real."""
    from adapters.telematics.catalog import PROVIDER_CATALOG
    from adapters.telematics.protocol import Capability

    assert Capability.DRIVER_HOS in SamsaraProvider.supported_capabilities
    entry = PROVIDER_CATALOG["samsara"]
    assert Capability.DRIVER_HOS in entry.capabilities
    assert any(f.capability == Capability.DRIVER_HOS for f in entry.feeds), (
        "a capability with no feed renders a toggle over an empty "
        "Synced data row"
    )


def test_the_provider_still_satisfies_the_protocol():
    from adapters.telematics.protocol import TelematicsProvider

    assert isinstance(SamsaraProvider(MagicMock()), TelematicsProvider)


# ── The HTTP shape ────────────────────────────────────────────────
#
# The one part of this file that cannot be checked against the repo:
# there is no recorded Samsara HOS payload to replay.  So the client
# is tested for how it DEGRADES — a renamed or missing key must give
# None, never zero — rather than for agreeing with a shape we guessed.

def _clocks_payload(**over):
    entry = {
        "driver": {"id": "12345", "name": "Jane Ruiz"},
        "currentDutyStatus": {
            "hosStatusType": "driving",
            "utcStartTime": "2026-09-14T08:00:00Z",
        },
        "clocks": {
            "drive": {"driveRemainingDurationMs": 3_600_000},
            "shift": {"shiftRemainingDurationMs": 25_200_000},
            "cycle": {"cycleRemainingDurationMs": 180_000_000},
        },
    }
    entry.update(over)
    return {"data": [entry]}


class _Http:
    """Stands in for the circuit-breaker-wrapped GET."""

    def __init__(self, payload):
        self.payload = payload
        self.path = None

    async def _get(self, endpoint, params=None):
        self.path = endpoint
        return self.payload


def _client_with(payload):
    from adapters.telematics.samsara.client import SamsaraClient
    c = SamsaraClient.__new__(SamsaraClient)
    http = _Http(payload)
    c._get = http._get
    return c, http


@pytest.mark.asyncio
async def test_the_client_converts_milliseconds_to_seconds():
    c, http = _client_with(_clocks_payload())
    rows = await c.get_hos_clocks()

    assert http.path == "/fleet/hos/clocks"
    assert rows[0]["provider_driver_id"] == "12345"
    assert rows[0]["raw_duty_status"] == "driving"
    assert rows[0]["drive_seconds_today"] == 3600
    assert rows[0]["shift_seconds_remaining"] == 25200
    assert rows[0]["cycle_seconds_remaining"] == 180000


@pytest.mark.asyncio
async def test_a_renamed_clock_key_degrades_to_none_not_zero():
    """If Samsara renames a duration field, the answer must become
    "we do not know", never "no hours left"."""
    c, _ = _client_with(_clocks_payload(clocks={
        "drive": {"someNewNameForDriveMs": 3_600_000},
        "shift": {},
        "cycle": {"cycleRemainingDurationMs": 180_000_000},
    }))
    row = (await c.get_hos_clocks())[0]

    assert row["drive_seconds_today"] is None
    assert row["shift_seconds_remaining"] is None
    assert row["cycle_seconds_remaining"] == 180000


@pytest.mark.asyncio
async def test_a_missing_clocks_block_does_not_raise():
    c, _ = _client_with(_clocks_payload(clocks=None))
    row = (await c.get_hos_clocks())[0]
    assert row["cycle_seconds_remaining"] is None
    assert row["provider_driver_id"] == "12345"


@pytest.mark.asyncio
async def test_an_empty_response_is_an_empty_list():
    c, _ = _client_with({"data": []})
    assert await c.get_hos_clocks() == []
    c, _ = _client_with({})
    assert await c.get_hos_clocks() == []


@pytest.mark.asyncio
async def test_a_non_numeric_duration_is_none_not_a_crash():
    c, _ = _client_with(_clocks_payload(clocks={
        "cycle": {"cycleRemainingDurationMs": "not a number"},
    }))
    assert (await c.get_hos_clocks())[0]["cycle_seconds_remaining"] is None
