"""Sixty-nine drivers, sixty-nine dashes in the Truck column.

The ELD had been sending the truck on every row the whole time. We
dropped it at the protocol boundary and then read the column from OUR
roster — which is empty for a driver nobody has linked yet, and on this
account nobody had linked any of them. So the page showed the one thing
it did not know and threw away the one thing it did.

"Which truck" is half of what dispatch is asking on a page about hours
of service.

The rule, and it is not "use whichever we have":

  OUR ROSTER FIRST. An assignment is a decision somebody made. The
  device's answer is wherever the tractor happens to be, which can be
  right about the truck and wrong about who should be driving it.

  THE DEVICE SECOND, and MARKED. A surface must not present a device's
  report and an operator's decision as the same fact.

  AND NEVER AS SCOPE. A bare unit number cannot decide who may see a
  driver: numbers are reused across the companies inside one account,
  so "103" names two trucks and admitting this as identity would hand a
  company-restricted dispatcher the other company's driver.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from features.eld.service import get_hours, project


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def _row(**kw):
    base = {
        "provider_id": "orient_eld",
        "provider_driver_id": "19192",
        "user_id": None,
        "duty_status": "driving",
        "drive_remaining_seconds": None,
        "shift_remaining_seconds": None,
        "cycle_remaining_seconds": None,
        "break_in_seconds": None,
        "last_status_change": _iso(190),
        "driver_name": "Provider Jane",
        "source_ts": _iso(1),
        "company_code": "CFT",
        "provider_vehicle": "6729",
        "updated_at": _iso(0),
        "display_name": "",
        "truck_num": "",
        "linked": False,
        "vehicles": [],
    }
    base.update(kw)
    return base


class _DB:
    def __init__(self, rows):
        self._rows = list(rows)

    async def get_driver_hos_live(self, account_id, user_id=None):
        return list(self._rows)

    async def count_driver_hos_live(self, account_id):
        return len(self._rows)


# ── The dashes ────────────────────────────────────────────────────

def test_an_unlinked_driver_shows_the_truck_the_device_reported():
    p = project(_row())
    assert p["vehicle"] == "6729"
    assert p["vehicle_from"] == "eld"


def test_our_roster_wins_when_we_have_one():
    """An assignment is a decision; the device's answer is a location."""
    p = project(_row(truck_num="231", linked=True, user_id=7))
    assert p["vehicle"] == "231"
    assert p["vehicle_from"] == "roster"


def test_neither_is_blank_and_says_so():
    """A provider that reports no vehicle — Samsara's clocks endpoint
    carries none — must not be made to look like one that did."""
    p = project(_row(provider_vehicle=""))
    assert p["vehicle"] == ""
    assert p["vehicle_from"] == ""


def test_whitespace_is_not_a_truck():
    p = project(_row(provider_vehicle="   ", truck_num="  "))
    assert p["vehicle"] == ""
    assert p["vehicle_from"] == ""


# ── What it must never become ─────────────────────────────────────

@pytest.mark.asyncio
async def test_the_devices_truck_is_not_a_scope_rung():
    """The twin companies. A caller scoped to ONE company's truck 103
    must not be shown the other company's driver on the strength of a
    bare unit number the device reported."""
    from capabilities.permissions.vehicle_scope import (
        VehicleIdentity, VehicleScope,
    )
    db = _DB([
        _row(provider_driver_id="a", company_code="CFT",
             provider_vehicle="103"),
        _row(provider_driver_id="b", company_code="RMR",
             provider_vehicle="103"),
    ])
    scope = VehicleScope.of(
        VehicleIdentity.make(registry_id=42, external_id=None, name="103"))

    out = await get_hours(db, 1, vehicle_scope=scope, feed=None)

    assert out["drivers"] == [], (
        "a device-reported unit number was admitted as identity — that "
        "is the twin leak the vehicle ladder exists to stop"
    )


@pytest.mark.asyncio
async def test_a_linked_driver_is_still_scoped_by_their_assignment():
    """The rung that IS allowed still works, so the guard above did not
    simply break scoping."""
    from capabilities.permissions.vehicle_scope import (
        VehicleIdentity, VehicleScope,
    )
    db = _DB([_row(user_id=7, linked=True, truck_num="231",
                   vehicles=[{"name": "231", "registry_id": 42}])])
    scope = VehicleScope.of(
        VehicleIdentity.make(registry_id=42, external_id=None, name="231"))

    out = await get_hours(db, 1, vehicle_scope=scope, feed=None)
    assert [d["vehicle"] for d in out["drivers"]] == ["231"]
    assert out["drivers"][0]["vehicle_from"] == "roster"
