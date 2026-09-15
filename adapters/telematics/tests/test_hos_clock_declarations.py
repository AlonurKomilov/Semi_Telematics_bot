"""A provider's clock declaration has to match what it actually fills.

``hos_clocks_reported`` exists so Hours of Service can say "this ELD
reports duty status only" instead of rendering four blank columns that
read as four zeroes.  That sentence is only worth printing if the
declaration is true, and a declaration nothing checks drifts the first
time a mapping changes.

So this walks EVERY registered provider that claims
``Capability.DRIVER_HOS``, feeds it a payload through its own fake
client, and compares the clocks that came out against the clocks it
promised.  Both directions fail:

  * promising a clock it never fills would leave a column that stays
    empty forever — the exact thing the declaration was added to stop;
  * filling a clock it never promised would hide a working column,
    which is the quieter bug and the harder one to notice.

A new ELD with no fixture here fails too, by name.  That is deliberate:
the declaration is easy to copy-paste from whichever provider was open
at the time, and this is the only thing standing between a copied
``frozenset()`` and a page that tells an operator their device reports
nothing.
"""

from __future__ import annotations

import pytest

from adapters.telematics import list_registered_providers
from adapters.telematics.protocol import Capability, HosClock
from adapters.telematics.registry import get_provider


# provider_id → a callable returning a provider instance wired to a
# fake client that answers with a payload carrying EVERY clock that
# vendor is capable of reporting.
#
# "Every clock it is capable of" is the contract: the fixture must be
# the vendor's most generous realistic response, or a provider could
# pass by under-reporting on both sides at once.


def _samsara():
    from adapters.telematics.samsara.provider import SamsaraProvider

    class _C:
        async def get_hos_clocks(self):
            return [{
                "provider_driver_id": "d1",
                "driver_name": "Jane Ruiz",
                "raw_duty_status": "driving",
                "last_status_change": "2026-09-14T08:00:00Z",
                "drive_remaining_seconds": 3600,
                "shift_remaining_seconds": 7200,
                "cycle_remaining_seconds": 180000,
                "break_in_seconds": 25200,
            }]

    return SamsaraProvider(_C())


def _orient_eld():
    from adapters.telematics.orient_eld.provider import OrientEldProvider

    class _C:
        async def get_tracking(self):
            # A full live row.  There is no richer one — ORIENT's
            # public API has seven endpoints and none of them carries a
            # countdown.
            return [{
                "driver_id": 19192,
                "name": "Jane",
                "surname": "Ruiz",
                "status": "DRIVING",
                "status_duration": 496.4,
                "vehicle_number": "001",
                "datetime_utc": "2026-09-15T05:18:18Z",
                "status_activation_time_utc": "2026-09-15T05:19:42",
                "location_datetime_utc": "2026-09-15T05:18:18Z",
            }]

    return OrientEldProvider(_C())


_FIXTURES = {
    "samsara": _samsara,
    "orient_eld": _orient_eld,
}


def _hos_providers() -> list[str]:
    return sorted(
        pid for pid in list_registered_providers()
        if Capability.DRIVER_HOS in get_provider(pid).supported_capabilities
    )


def test_every_hos_provider_has_a_fixture_here():
    missing = [pid for pid in _hos_providers() if pid not in _FIXTURES]
    assert not missing, (
        f"{missing!r} declare Capability.DRIVER_HOS but have no fixture in "
        "this file, so nothing checks that their hos_clocks_reported is "
        "true.  Add one — the declaration decides what Hours of Service "
        "tells an operator their device can and cannot report."
    )


@pytest.mark.parametrize("provider_id", _hos_providers())
@pytest.mark.asyncio
async def test_the_declared_clocks_are_the_clocks_that_arrive(provider_id):
    provider = _FIXTURES[provider_id]()
    snaps = await provider.get_driver_hos()
    assert snaps, f"{provider_id} fixture produced no snapshots"

    observed = {
        clock_id
        for clock_id, field in HosClock.FIELDS.items()
        if any(getattr(s, field) is not None for s in snaps)
    }
    declared = set(provider.hos_clocks_reported)

    assert observed == declared, (
        f"{provider_id}: declares {sorted(declared)!r} but its most "
        f"generous payload fills {sorted(observed)!r}.  "
        f"Promised-but-never-filled {sorted(declared - observed)!r} "
        f"leaves a column empty forever; "
        f"filled-but-never-promised {sorted(observed - declared)!r} "
        f"hides a working one."
    )


@pytest.mark.parametrize("provider_id", sorted(_FIXTURES))
def test_a_declaration_is_always_a_legal_subset(provider_id):
    declared = set(get_provider(provider_id).hos_clocks_reported)
    assert declared <= HosClock.ALL, (
        f"{provider_id} declares clock ids outside the vocabulary: "
        f"{sorted(declared - HosClock.ALL)!r}"
    )


def test_a_provider_that_never_fetches_hos_declares_no_clocks():
    """A clock on a feed that is never fetched is a column nothing will
    ever fill.  The shared import guard refuses it; this states the rule
    for a reader."""
    for pid in list_registered_providers():
        cls = get_provider(pid)
        if Capability.DRIVER_HOS in cls.supported_capabilities:
            continue
        assert cls.hos_clocks_reported == frozenset(), (
            f"{pid} declares HOS clocks without Capability.DRIVER_HOS"
        )


def test_the_shared_guard_refuses_an_impossible_declaration():
    """The guard itself, exercised — otherwise the only proof it works
    is that nobody has yet written the bug it catches."""
    from adapters.telematics.catalog import assert_declarations_agree
    from adapters.telematics.registry import get_provider as _g

    real = _g("orient_eld")

    class _ClaimsAClockItNeverFetches:
        provider_id = "orient_eld"
        supported_capabilities = real.supported_capabilities
        hos_clocks_reported = frozenset({HosClock.DRIVE})

    # Legal ids, but DRIVER_HOS *is* declared here, so this one passes
    # the "no capability" rule and fails only in the honesty test above.
    assert_declarations_agree(_ClaimsAClockItNeverFetches)

    class _MadeUpClock:
        provider_id = "orient_eld"
        supported_capabilities = real.supported_capabilities
        hos_clocks_reported = frozenset({"nap"})

    with pytest.raises(ImportError, match="unknown HOS clocks"):
        assert_declarations_agree(_MadeUpClock)

    class _ClockWithoutTheCapability:
        provider_id = "datatruck"
        supported_capabilities = _g("datatruck").supported_capabilities
        hos_clocks_reported = frozenset({HosClock.CYCLE})

    with pytest.raises(ImportError, match="without Capability.DRIVER_HOS"):
        assert_declarations_agree(_ClockWithoutTheCapability)

    class _ForgotToDeclare:
        provider_id = "orient_eld"
        supported_capabilities = real.supported_capabilities

    with pytest.raises(ImportError, match="does not declare"):
        assert_declarations_agree(_ForgotToDeclare)
