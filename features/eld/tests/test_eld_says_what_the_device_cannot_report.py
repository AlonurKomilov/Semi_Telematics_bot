"""An ELD that reports no countdowns must not read as "everyone is fine".

Not every electronic logging device publishes remaining hours.  ORIENT
ELD is the worked example: it reports each driver's duty status and
when it began, and none of the four countdowns.  That is a real product
working correctly, not a broken integration.

The failure it creates is subtle and it is the one this whole feature
was built to avoid.  Four blank columns on a compliance page read as
four zeroes; a header that never warns reads as "nobody is near a
limit"; and a model handed twenty-two drivers whose every clock says
``unknown`` can conclude that none of them is out of hours.  In all
three the silence comes from the device, and in all three the reader
hears it as an answer.

So the coverage is DECLARED by the provider and stated by the surface.
These pin that it is declared, that it travels, and that it is never
derived from whether a clock happens to be filled right now.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from features.eld.ai_tool import get_driver_hos_status
from features.eld.service import get_hours


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def _samsara_row(**kw):
    base = {
        "provider_id": "samsara",
        "provider_driver_id": "s1",
        "user_id": 7,
        "duty_status": "driving",
        "drive_remaining_seconds": 3600,
        "shift_remaining_seconds": 7200,
        "cycle_remaining_seconds": 180000,
        "break_in_seconds": 25200,
        "last_status_change": "2026-09-14T08:00:00+00:00",
        "driver_name": "Provider Jane",
        "source_ts": _iso(2),
        "updated_at": _iso(0),
        "display_name": "Jane Ruiz",
        "truck_num": "231",
        "linked": True,
        "vehicles": [{"name": "231", "registry_id": 42}],
    }
    base.update(kw)
    return base


def _orient_row(**kw):
    """What ORIENT ELD actually stores: a status, an age, no clocks."""
    base = dict(_samsara_row())
    base.update({
        "provider_id": "orient_eld",
        "provider_driver_id": "19192",
        "drive_remaining_seconds": None,
        "shift_remaining_seconds": None,
        "cycle_remaining_seconds": None,
        "break_in_seconds": None,
    })
    base.update(kw)
    return base


class _DB:
    def __init__(self, rows, ever=None):
        self._rows = list(rows)
        self._ever = len(self._rows) if ever is None else ever

    async def get_driver_hos_live(self, account_id, user_id=None):
        if user_id is None:
            return list(self._rows)
        return [r for r in self._rows if r.get("user_id") == user_id]

    async def count_driver_hos_live(self, account_id):
        return self._ever


# ── What the service reports about the device ─────────────────────

@pytest.mark.asyncio
async def test_a_duty_status_only_eld_reports_no_clocks():
    out = await get_hours(_DB([_orient_row()]), 1)
    assert out["clocks_reported"] == []
    assert out["providers"]["orient_eld"]["clocks_reported"] == []
    assert out["providers"]["orient_eld"]["name"] == "ORIENT ELD"


@pytest.mark.asyncio
async def test_an_eld_that_reports_everything_still_says_so():
    out = await get_hours(_DB([_samsara_row()]), 1)
    assert set(out["clocks_reported"]) == {"drive", "shift", "cycle", "break"}
    assert out["providers"]["samsara"]["clocks_reported"] == [
        "break", "cycle", "drive", "shift",
    ]


@pytest.mark.asyncio
async def test_the_coverage_is_declared_not_read_off_the_rows():
    """The trap this avoids.  Every Samsara clock is null here — which
    happens to a driver who has not started their day, and to one the
    ELD has no session for.  Deriving the answer from the data would
    make the columns disappear because nobody is driving yet."""
    out = await get_hours(_DB([_samsara_row(
        drive_remaining_seconds=None, shift_remaining_seconds=None,
        cycle_remaining_seconds=None, break_in_seconds=None,
    )]), 1)
    assert set(out["clocks_reported"]) == {"drive", "shift", "cycle", "break"}


@pytest.mark.asyncio
async def test_two_elds_on_one_account_union_their_clocks():
    """A column must exist if ANY connected device fills it; the rows
    from the device that does not show a dash inside it."""
    out = await get_hours(_DB([_samsara_row(), _orient_row(user_id=8)]), 1)
    assert set(out["clocks_reported"]) == {"drive", "shift", "cycle", "break"}
    assert out["providers"]["orient_eld"]["clocks_reported"] == []
    assert len(out["providers"]) == 2


@pytest.mark.asyncio
async def test_an_unregistered_provider_is_unknown_not_none():
    """A row written by a provider that is no longer registered.  We
    cannot say what it reports, and "unknown" and "reports nothing" are
    different sentences — only one of them may be printed."""
    out = await get_hours(_DB([_orient_row(provider_id="retired_eld")]), 1)
    assert out["providers"]["retired_eld"]["clocks_reported"] is None


@pytest.mark.asyncio
async def test_a_column_holding_real_data_is_never_hidden():
    """The floor under the declaration.  If a row actually carries a
    clock, its column exists no matter what anybody declared — the
    failure mode of a stale declaration must be a visible column, never
    hidden data."""
    out = await get_hours(_DB([
        _orient_row(provider_id="retired_eld", drive_remaining_seconds=900),
    ]), 1)
    assert "drive" in out["clocks_reported"]


@pytest.mark.asyncio
async def test_a_provider_the_scope_removed_is_not_named():
    """Naming an ELD whose every driver the caller cannot see would
    leak which products the account runs."""
    from capabilities.permissions.vehicle_scope import (
        VehicleIdentity, VehicleScope,
    )
    scope = VehicleScope.of(
        VehicleIdentity.make(registry_id=42, external_id=None, name="231"))
    out = await get_hours(
        _DB([
            _samsara_row(),
            _orient_row(user_id=8, vehicles=[{"name": "104",
                                              "registry_id": 99}]),
        ]),
        1, vehicle_scope=scope)
    assert list(out["providers"]) == ["samsara"]


# ── What the model is told ────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_model_is_told_the_device_cannot_answer_that():
    res = await get_driver_hos_status(
        {}, None, account_id=1, db=_DB([_orient_row()]))

    note = res.get("clock_coverage") or ""
    assert note, "the model got four 'unknown' clocks and no explanation"
    assert "ORIENT ELD" in note, "the note must name whose limitation it is"
    assert "NOT a statement that these drivers have hours remaining" in note
    assert "who can take this load" in note, (
        "the note must refuse the dispatch question by name"
    )
    # And the clocks themselves still read as unknown, never as zero.
    assert res["drivers"][0]["drive_remaining"] == "unknown"


@pytest.mark.asyncio
async def test_a_full_eld_gets_no_extra_words():
    """A note that says "nothing is missing" is noise the model has to
    read on every call."""
    res = await get_driver_hos_status(
        {}, None, account_id=1, db=_DB([_samsara_row()]))
    assert "clock_coverage" not in res


@pytest.mark.asyncio
async def test_a_mixed_fleet_is_not_silent_just_because_the_union_is_full():
    """The bug the union-based version had.

    One ELD reports all four clocks, the other reports none.  The
    account-wide union is therefore complete, so the first version of
    this note found nothing missing and said nothing — while half the
    drivers in the very same answer showed 'unknown' across the board
    with no hint of which device they came from.  A model reading that
    can conclude the fleet is clear from the half that has numbers.

    Real rows through the real registry — no monkeypatching, because
    the monkeypatched version of this test passed while the bug was
    live.
    """
    res = await get_driver_hos_status({}, None, account_id=1, db=_DB([
        _samsara_row(),
        _orient_row(user_id=8),
    ]))

    note = res.get("clock_coverage") or ""
    assert note, (
        "a full-clock ELD masked a duty-status-only one and the model "
        "was told nothing"
    )
    assert "ORIENT ELD" in note, "the limited device must be named"
    assert "Samsara" not in note, (
        "Samsara reports every clock — naming it as limited is false"
    )
    assert "source" in note, (
        "the model needs pointing at the field that says which device "
        "each driver came from"
    )
    assert "whole account" in note

    # And the rows still carry the truth per driver.
    by_source = {d["source"]: d for d in res["drivers"]}
    assert by_source["orient_eld"]["drive_remaining"] == "unknown"
    assert by_source["samsara"]["drive_remaining"] != "unknown"


@pytest.mark.asyncio
async def test_a_partly_reporting_device_names_only_what_is_absent():
    """The third ELD, which reports two of the four.  A surface written
    around "is it ORIENT" would be wrong about it on day one."""
    from adapters.telematics.protocol import HosClock
    from adapters.telematics.registry import (
        get_provider, register_provider,
    )

    real = get_provider("orient_eld")

    class _TwoClockEld(real):  # type: ignore[misc, valid-type]
        hos_clocks_reported = frozenset({HosClock.DRIVE, HosClock.SHIFT})

    register_provider("orient_eld", _TwoClockEld)
    try:
        res = await get_driver_hos_status(
            {}, None, account_id=1, db=_DB([_orient_row()]))
    finally:
        register_provider("orient_eld", real)

    note = res.get("clock_coverage") or ""
    assert "cycle" in note and "break" in note
    assert "drive" not in note.split("does not publish")[1].split(".")[0], (
        "a clock the device DOES report must not be listed as absent"
    )
    assert "duty status only" not in note, (
        "a device that reports two clocks does not report none"
    )


@pytest.mark.asyncio
async def test_an_unregistered_provider_gets_no_sentence():
    """We cannot say what it reports, so we say nothing about it —
    "unknown" is not the same claim as "reports nothing"."""
    res = await get_driver_hos_status(
        {}, None, account_id=1,
        db=_DB([_orient_row(provider_id="retired_eld")]))
    assert "clock_coverage" not in res


@pytest.mark.asyncio
async def test_no_eld_at_all_still_takes_precedence():
    """An absent feed and a device with no countdowns are different
    answers, and the first one must not be softened into the second."""
    res = await get_driver_hos_status({}, None, account_id=1,
                                      db=_DB([], ever=0))
    assert res["hos_unavailable"] is True
    assert "clock_coverage" not in res
