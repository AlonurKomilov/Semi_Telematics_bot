"""An assignment can name ONE truck.

``driver_trucks`` stored a bare ``truck_num``; a unit number is reused
across companies, so "103" meant every 103.  ``registry_id`` (nullable)
says which — and NULL keeps meaning exactly what it did.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")

import pytest

from adapters.storage import Role


@pytest.mark.asyncio
async def test_set_user_vehicles_carries_registry_ids(pg_db):
    acct = await pg_db.create_account("Twin Co")
    u = await pg_db.create_user(9101, acct.id, role=Role.DRIVER)
    rows = await pg_db.set_user_vehicles(u.id, acct.id, ["103", "229"], registry_ids=[87, None])
    assert [(r.vehicle_num, r.registry_id) for r in rows] == [("103", 87), ("229", None)]
    assert await pg_db.get_user_vehicle_assignments(u.id) == [("103", 87), ("229", None)]
    # The names-only contract is untouched — every caller of it keeps working.
    assert await pg_db.get_user_vehicle_nums(u.id) == ["103", "229"]


@pytest.mark.asyncio
async def test_both_twins_can_be_assigned_explicitly(pg_db):
    """(user, name) used to be unique.  With the id in the key a person
    can hold OSY's 103 and G1's 103 as two rows — and re-assigning one
    updates it rather than colliding."""
    acct = await pg_db.create_account("Twin Co 2")
    u = await pg_db.create_user(9102, acct.id, role=Role.DISPATCHER)
    await pg_db.assign_vehicle(u.id, acct.id, "103", registry_id=87, is_primary=True)
    await pg_db.assign_vehicle(u.id, acct.id, "103", registry_id=9)
    again = await pg_db.assign_vehicle(u.id, acct.id, "103", registry_id=9)
    rows = await pg_db.get_user_vehicles(u.id)
    assert sorted((r.vehicle_num, r.registry_id) for r in rows) == [("103", 9), ("103", 87)]
    assert again.registry_id == 9 and len(rows) == 2


@pytest.mark.asyncio
async def test_null_stays_null_and_distinct_from_pinned(pg_db):
    acct = await pg_db.create_account("Twin Co 3")
    u = await pg_db.create_user(9103, acct.id, role=Role.DRIVER)
    await pg_db.assign_vehicle(u.id, acct.id, "103")            # the name, every 103
    await pg_db.assign_vehicle(u.id, acct.id, "103", registry_id=87)
    rows = await pg_db.get_user_vehicles(u.id)
    assert sorted(((r.vehicle_num, r.registry_id) for r in rows), key=str) == [("103", 87), ("103", None)]
