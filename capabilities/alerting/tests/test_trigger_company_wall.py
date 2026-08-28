"""The company wall the sweep builds, against a real database.

``_owner_scope`` is monkeypatched in every other evaluator test, so the
SQL that BUILDS the wall had no coverage at all — it shipped, and the
only thing that noticed a missing lifecycle filter was a structural
guard reading the query text.

The rule these pin is the one ``_target_scope`` already states: a
retired truck must not resolve into an allow-set, or the sweep keeps
judging the one that left.  The targeted path carried that rule; the
company wall did not, so the two disagreed about the same fleet.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from adapters.storage.models import Role
from capabilities.alerting.triggers import evaluator as ev


@pytest.fixture
async def walled(pg_db, monkeypatch):
    """One company-restricted user, one live truck, one archived truck."""
    acct = await pg_db.create_account("Wall Co")
    company = await pg_db.add_company(acct.id, "CFT", "key_cft", "Cargo Freight")
    user = await pg_db.create_user(9401, acct.id, role=Role.FLEET)
    await pg_db.set_user_companies(user.id, acct.id, [company.id])

    live = await pg_db.add_vehicle(
        acct.id, unit_number="101", company_code="CFT", telematics_ref="ext-101")
    gone = await pg_db.add_vehicle(
        acct.id, unit_number="102", company_code="CFT", telematics_ref="ext-102")
    await pg_db.deactivate_vehicle(acct.id, gone)

    monkeypatch.setattr(ev, "get_platform_db", lambda: pg_db)
    scope = await ev._owner_scope(pg_db, acct.id, user.id)
    return {"scope": scope, "live": live, "gone": gone}


def _row(registry_id: int, ext: str):
    return {"registry_id": registry_id, "vehicle_id": ext,
            "vehicle_name": f"unit-{registry_id}"}


async def test_the_wall_admits_a_truck_in_the_users_company(walled):
    assert walled["scope"] is not None, (
        "a company-restricted user must get a wall — without one the sweep "
        "DMs them trucks in companies they cannot open anywhere else"
    )
    assert walled["scope"].allows_row(
        _row(walled["live"], "ext-101"),
        name_key="vehicle_name", external_key="vehicle_id")


async def test_the_wall_refuses_a_retired_truck_in_that_same_company(walled):
    """The company matches; the truck left.  Company scoping and lifecycle
    are different questions, and answering only the first is how a
    retired truck keeps generating news."""
    assert not walled["scope"].allows_row(
        _row(walled["gone"], "ext-102"),
        name_key="vehicle_name", external_key="vehicle_id")
