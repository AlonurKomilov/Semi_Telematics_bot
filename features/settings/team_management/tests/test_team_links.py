"""Team Management integration links (T2/T3).

Provision-as-pending, manual Datatruck-driver linking, loads name backfill,
and the projector's unique-name association (driver + dispatcher).
"""

from __future__ import annotations

import pytest

from adapters.storage import Role
from features.settings.team_management.service import member_lifecycle


async def _lifecycle(db, uid: int) -> str:
    cur = await db._db.execute(
        "SELECT telegram_id, password_hash, is_active FROM users WHERE id = ?",
        (uid,),
    )
    r = await cur.fetchone()
    class U:
        telegram_id, password_hash, is_active = r[0], r[1], bool(r[2])
    return member_lifecycle(U)


@pytest.mark.asyncio
async def test_provisioned_member_is_pending(db):
    acct = await db.create_account("Links Co")
    uid = await db.create_pending_user(
        acct.id, Role.DISPATCHER, "Otabek Sobirov", email="ot@x.com",
    )
    assert await _lifecycle(db, uid) == "pending"
    # Appears in the account roster with the right role.
    cur = await db._db.execute(
        "SELECT role, email FROM users WHERE id = ?", (uid,),
    )
    r = await cur.fetchone()
    assert r[0] == "dispatcher" and r[1] == "ot@x.com"


@pytest.mark.asyncio
async def test_manual_datatruck_link_and_conflict(db):
    acct = await db.create_account("Links Co 2")
    a = await db.create_user(8101, acct.id, role=Role.DRIVER, display_name="A")
    b = await db.create_user(8102, acct.id, role=Role.DRIVER, display_name="B")
    await db.link_datatruck_driver(acct.id, a.id, "D5")
    # Same ref on another member → refused (one person, one link).
    with pytest.raises(ValueError):
        await db.link_datatruck_driver(acct.id, b.id, "D5")
    # Unlink frees it.
    await db.link_datatruck_driver(acct.id, a.id, "")
    await db.link_datatruck_driver(acct.id, b.id, "D5")
    # The hydrated User model must expose the link — the members-list
    # serializer reads it via getattr, which silently returned None
    # while the field was missing from the model (drawer showed
    # "Not linked" for members the picker knew were linked).
    users = {u.id: u for u in await db.list_account_users(acct.id)}
    assert users[b.id].datatruck_driver_id == "D5"
    assert users[a.id].datatruck_driver_id is None


@pytest.mark.asyncio
async def test_name_backfill_on_manual_link(db):
    acct = await db.create_account("Links Co 3")
    u = await db.create_pending_user(acct.id, Role.DRIVER, "Eugene B")
    await db.add_load(acct.id, load_number="L1", driver_name="Eugene B")
    await db.add_load(acct.id, load_number="L2", driver_name="Eugene B")
    await db.add_load(acct.id, load_number="L3", driver_name="Someone Else")
    n = await db.assign_load_person_by_name(acct.id, u.id if hasattr(u, "id") else u, "eugene b", field="driver")
    assert n == 2                                    # case-insensitive
    mine = await db.list_loads(acct.id, driver_user_id=u if isinstance(u, int) else u.id)
    assert {l.load_number for l in mine} == {"L1", "L2"}


@pytest.mark.asyncio
async def test_projector_associates_unique_names(db):
    acct = await db.create_account("Links Co 4")
    drv = await db.create_pending_user(acct.id, Role.DRIVER, "Eugene B")
    dsp = await db.create_pending_user(acct.id, Role.DISPATCHER, "Jasur")
    # Ambiguous driver name — two members share it → never guessed.
    await db.create_pending_user(acct.id, Role.DRIVER, "John Smith")
    await db.create_pending_user(acct.id, Role.DRIVER, "John Smith")

    await db.project_external_loads(acct.id, [
        {"external_id": "O1", "order_number": "N1", "status": "Dispatched",
         "driver_name": "Eugene B", "dispatcher_name": "jasur",
         "pickup_date": "", "delivery_date": "", "origin": "", "destination": "",
         "driver_external_id": "", "truck_external_id": "", "trailer_external_id": "",
         "total_rate": 100.0, "customer": "", "dispatcher_external_id": "",
         "loaded_miles": None, "empty_miles": None, "driver_pay": None, "payload": {}},
        {"external_id": "O2", "order_number": "N2", "status": "Dispatched",
         "driver_name": "John Smith", "dispatcher_name": "",
         "pickup_date": "", "delivery_date": "", "origin": "", "destination": "",
         "driver_external_id": "", "truck_external_id": "", "trailer_external_id": "",
         "total_rate": 100.0, "customer": "", "dispatcher_external_id": "",
         "loaded_miles": None, "empty_miles": None, "driver_pay": None, "payload": {}},
    ])
    loads = {l.external_ref: l for l in await db.list_loads(acct.id)}
    assert loads["O1"].driver_user_id == drv            # unique name → linked
    assert loads["O1"].dispatcher_user_id == dsp        # case-insensitive
    assert loads["O2"].driver_user_id is None           # ambiguous → never guess


@pytest.mark.asyncio
async def test_samsara_link_and_conflict(db):
    acct = await db.create_account("Links Co 5")
    a = await db.create_user(8201, acct.id, role=Role.DRIVER, display_name="A")
    b = await db.create_user(8202, acct.id, role=Role.DRIVER, display_name="B")
    await db.link_samsara_driver(acct.id, a.id, "S9")
    with pytest.raises(ValueError):
        await db.link_samsara_driver(acct.id, b.id, "S9")
    await db.link_samsara_driver(acct.id, a.id, "")     # unlink frees it
    await db.link_samsara_driver(acct.id, b.id, "S9")


@pytest.mark.asyncio
async def test_datatruck_driver_options(db):
    acct = await db.create_account("Links Co 6")
    u = await db.create_user(8301, acct.id, role=Role.DRIVER, display_name="Taken")
    await db.upsert_datatruck_drivers(acct.id, [
        {"external_id": "D1", "first_name": "", "last_name": "",
         "display_name": "Free Driver", "phone": "", "email": "",
         "status": "active", "payload": {}},
        {"external_id": "D2", "first_name": "", "last_name": "",
         "display_name": "Taken Driver", "phone": "", "email": "",
         "status": "active", "payload": {}},
        # Real Datatruck rows can arrive with only first/last set —
        # the picker must never render a blank option.  The assigned
        # truck in the payload becomes the picker's secondary tag.
        {"external_id": "D3", "first_name": "Jean", "last_name": "Bosco",
         "display_name": "", "phone": "", "email": "",
         "status": "active",
         "payload": {"assigned_truck": {"id": 7, "unit_number": "T-12"}}},
    ])
    await db.link_datatruck_driver(acct.id, u.id, "D2")
    opts = {o["external_id"]: o
            for o in await db.list_datatruck_driver_options(acct.id)}
    assert opts["D1"]["linked_user_id"] is None
    assert opts["D1"]["name"] == "Free Driver"
    assert opts["D2"]["linked_user_id"] == u.id
    assert opts["D3"]["name"] == "Jean Bosco"
    assert opts["D3"]["truck_unit"] == "T-12"


@pytest.mark.asyncio
async def test_datatruck_options_name_from_payload_account(db):
    """Rows staged BEFORE the account-nesting mapper fix have empty
    promoted name columns — the name must still surface from the stored
    payload's ``account`` object, without waiting for a re-sync."""
    acct = await db.create_account("Links Co 7")
    await db.upsert_datatruck_drivers(acct.id, [
        {"external_id": "D9", "first_name": "", "last_name": "",
         "display_name": "", "phone": "", "email": "",
         "status": "active",
         "payload": {
             "id": 9,
             "account": {"first_name": "Eric", "last_name": "Gasabato",
                         "full_name": "Eric Gasabato", "email": "eg@x.com"},
             "contact_number": "+1 555",
         }},
    ])
    opts = {o["external_id"]: o
            for o in await db.list_datatruck_driver_options(acct.id)}
    assert opts["D9"]["name"] == "Eric Gasabato"
    # The loads-backfill name lookup uses the same fallback.
    assert await db.get_datatruck_driver_name(acct.id, "D9") == "Eric Gasabato"
    # And the import plan buckets show the name + match by payload email.
    plan = await db.plan_datatruck_driver_import(acct.id)
    names = [e["name"] for b in ("link", "create", "review") for e in plan[b]]
    assert "Eric Gasabato" in names


def test_router_get_tenant_db_not_shadowed():
    """``from infra.platform import get_tenant_db`` once shadowed the FastAPI
    dependency of the same name — every ``Depends(get_tenant_db)`` endpoint
    then demanded a spurious ``account_id`` query parameter (422s from the
    dashboard).  Guard the whole router's OpenAPI surface against it."""
    import os
    os.environ.setdefault("JWT_SECRET", "x" * 40)
    from fastapi import FastAPI
    from features.settings.team_management.router import router as team_router
    app = FastAPI()
    app.include_router(team_router)
    for path, ops in app.openapi()["paths"].items():
        for op in ops.values():
            names = [p["name"] for p in op.get("parameters", [])]
            assert "account_id" not in names, (
                f"{path} leaks account_id as a query param — "
                "get_tenant_db dependency is shadowed again"
            )


@pytest.mark.asyncio
async def test_account_vehicle_nums_map(db):
    """Batch map matches the per-user lookups it replaced in list_users."""
    acct = await db.create_account("Links Co 8")
    a = await db.create_user(8401, acct.id, role=Role.DRIVER, display_name="A")
    b = await db.create_user(8402, acct.id, role=Role.DRIVER, display_name="B")
    await db.assign_vehicle(a.id, acct.id, "T1")
    await db.assign_vehicle(a.id, acct.id, "T2", is_primary=True)
    await db.assign_vehicle(b.id, acct.id, "T9")
    m = await db.get_account_vehicle_nums_map(acct.id)
    assert m[a.id] == ["T2", "T1"]          # primary first, then by number
    assert m[b.id] == ["T9"]
    assert m[a.id] == await db.get_user_vehicle_nums(a.id)
