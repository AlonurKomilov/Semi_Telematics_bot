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
