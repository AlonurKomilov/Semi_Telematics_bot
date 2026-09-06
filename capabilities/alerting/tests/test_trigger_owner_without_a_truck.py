"""A driver-owned trigger with no truck assigned sweeps nothing.

``_owner_scope`` used to return the company wall alone — or ``None`` —
for a driver holding no assignment, so that person's trigger was
evaluated against every vehicle in the account and DM'd every reading.
It was the same fail-open the dashboard carried in
``deps.get_user_vehicle_scope``, restated here as deliberate.

The wall this pins is the one the module already states for a failed
resolution: a scope that cannot name a truck must not stand in for
"all of them".

Built against a real database rather than a monkeypatched scope, for the
reason ``test_trigger_company_wall`` gives — the SQL that BUILDS the
scope is the part that had no coverage.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from adapters.storage.models import Role
from capabilities.alerting.triggers import evaluator as ev


@pytest.fixture
async def account(pg_db, monkeypatch):
    acct = await pg_db.create_account("Trigger Co")
    await pg_db.add_vehicle(
        acct.id, unit_number="101", company_code="PTG", telematics_ref="ext-101")
    await pg_db.add_vehicle(
        acct.id, unit_number="102", company_code="PTG", telematics_ref="ext-102")
    monkeypatch.setattr(ev, "get_platform_db", lambda: pg_db)
    return acct


def _row(name: str, ext: str):
    return {"vehicle_id": ext, "vehicle_name": name}


async def test_a_driver_with_no_assignment_owns_a_trigger_that_sees_nothing(
    pg_db, account,
):
    driver = await pg_db.create_user(9501, account.id, role=Role.DRIVER)
    scope = await ev._owner_scope(pg_db, account.id, driver.id)
    assert scope is ev._DENY_ALL, (
        "no assignment must not read as 'every truck in the account'"
    )
    assert not scope.allows_row(_row("101", "ext-101"))
    assert not scope.allows_row(_row("102", "ext-102"))


async def test_an_assigned_driver_still_sweeps_their_own_truck(pg_db, account):
    driver = await pg_db.create_user(9502, account.id, role=Role.DRIVER,
                                     truck_num="101")
    scope = await ev._owner_scope(pg_db, account.id, driver.id)
    assert scope is not ev._DENY_ALL
    assert scope.allows_row(
        _row("101", "ext-101"), name_key="vehicle_name", external_key="vehicle_id")
    assert not scope.allows_row(
        _row("102", "ext-102"), name_key="vehicle_name", external_key="vehicle_id")


async def test_a_non_driver_owner_is_not_denied_by_this_rule(pg_db, account):
    """Only drivers are scoped here; the change must not silence a
    dispatcher's or a manager's trigger."""
    staff = await pg_db.create_user(9503, account.id, role=Role.FLEET)
    scope = await ev._owner_scope(pg_db, account.id, staff.id)
    assert scope is not ev._DENY_ALL
