"""An account is five carriers, and a driver belongs to one of them.

Hours of service is regulated per DRIVER of a named carrier. This
account runs five — CFT, G1, OSY, PTG, RMR — each its own legal
company with its own USDOT number and its own ELD key.

The feature shipped against one company and so never carried the
company anywhere. That was invisible at one and is the whole problem at
five: a hundred drivers from five separate businesses in one
undifferentiated list, with no way to say whose driver is whose, no way
to filter, and nothing to check a key against.

Both fan-outs already knew. Samsara tags every row ``_org`` and ORIENT
tags ``_company_code``; both providers dropped it at the protocol
boundary. These pin that it now survives the whole way, and that a
single-company account does not pay for it with a column of blanks.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from features.eld.service import get_hours, project


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def _row(company="PTG", **kw):
    base = {
        "provider_id": "orient_eld",
        "provider_driver_id": "19192",
        "user_id": None,
        "duty_status": "driving",
        "drive_remaining_seconds": None,
        "shift_remaining_seconds": None,
        "cycle_remaining_seconds": None,
        "break_in_seconds": None,
        "last_status_change": _iso(30),
        "driver_name": "Provider Jane",
        "source_ts": _iso(2),
        "company_code": company,
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


# ── The company survives the trip ─────────────────────────────────

def test_the_projection_carries_the_carrier():
    assert project(_row(company="CFT"))["company"] == "CFT"


def test_a_row_with_no_company_is_blank_not_missing():
    """A single-company account and a provider that reports no company
    both land here, and a surface reads blank as "not applicable"."""
    assert project(_row(company=""))["company"] == ""


@pytest.mark.asyncio
async def test_five_carriers_are_named_in_the_answer():
    db = _DB([
        _row(company=c, provider_driver_id=str(1000 + i))
        for i, c in enumerate(["CFT", "G1", "OSY", "PTG", "RMR"])
    ])
    out = await get_hours(db, 1)

    assert out["companies"] == ["CFT", "G1", "OSY", "PTG", "RMR"]
    assert {d["company"] for d in out["drivers"]} == {
        "CFT", "G1", "OSY", "PTG", "RMR",
    }


@pytest.mark.asyncio
async def test_one_carrier_does_not_earn_a_column():
    """``companies`` is what the page builds the Company column from.
    A single-carrier account must not get a column that says the same
    word on every row."""
    out = await get_hours(_DB([_row(company="PTG")]), 1)
    assert out["companies"] == ["PTG"]


@pytest.mark.asyncio
async def test_no_carrier_anywhere_is_an_empty_list_not_a_blank_entry():
    out = await get_hours(_DB([_row(company="")]), 1)
    assert out["companies"] == [], (
        "a blank must not become a carrier named ''"
    )


@pytest.mark.asyncio
async def test_the_carriers_named_are_only_the_ones_this_caller_can_see():
    """Naming a carrier whose every driver the scope removed would leak
    which businesses the account runs to someone walled out of them."""
    from capabilities.permissions.vehicle_scope import (
        VehicleIdentity, VehicleScope,
    )
    db = _DB([
        _row(company="PTG", provider_driver_id="a", user_id=7, linked=True,
             vehicles=[{"name": "231", "registry_id": 42}]),
        _row(company="RMR", provider_driver_id="b", user_id=8, linked=True,
             vehicles=[{"name": "104", "registry_id": 99}]),
    ])
    scope = VehicleScope.of(
        VehicleIdentity.make(registry_id=42, external_id=None, name="231"))

    out = await get_hours(db, 1, vehicle_scope=scope)
    assert out["companies"] == ["PTG"]


# ── What the company is NOT ───────────────────────────────────────

@pytest.mark.asyncio
async def test_the_carrier_is_not_a_scope_rung():
    """Scope is decided by the vehicle identity ladder, which splits the
    twins a bare name cannot. If the company were quietly admitted as a
    rung, a dispatcher scoped to two trucks would start seeing every
    driver at those trucks' carrier — a much wider view than anyone
    granted them."""
    from capabilities.permissions.vehicle_scope import VehicleScope

    db = _DB([_row(company="PTG", user_id=7, linked=True,
                   vehicles=[{"name": "231", "registry_id": 42}])])
    out = await get_hours(db, 1, vehicle_scope=VehicleScope())
    assert out["drivers"] == [], (
        "an empty vehicle scope admitted a driver on the strength of "
        "their company"
    )
