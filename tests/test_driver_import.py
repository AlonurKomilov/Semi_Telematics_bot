"""One-time Datatruck driver import (plan + apply).

The ongoing sync only LINKS; this deliberate onboarding action CREATES
driver-users for unmatched active Datatruck drivers — previewed first,
idempotent, never guessing on ambiguous keys, never resurrecting
inactive drivers.
"""

from __future__ import annotations

import pytest

from adapters.storage import Role


def _staged(ref: str, *, name: str, phone: str = "", email: str = "",
            status: str = "active", license_number: str = "") -> dict:
    return {
        "external_id": ref, "first_name": "", "last_name": "",
        "display_name": name, "phone": phone, "email": email,
        "status": status,
        "payload": {"license_number": license_number} if license_number else {},
    }


async def _seed(db, account_id: int) -> dict:
    """One existing driver with a CDL (link target), one already linked."""
    u1 = await db.create_user(7101, account_id, role=Role.DRIVER,
                              display_name="Linked ByCdl")
    await db.update_driver_profile(u1.id, cdl_number="CDL-100")
    u2 = await db.create_user(7102, account_id, role=Role.DRIVER,
                              display_name="Already Linked")
    await db._db.execute(
        "UPDATE users SET datatruck_driver_id = 'D2' WHERE id = ?", (u2.id,),
    )
    await db._db.commit()
    await db.upsert_datatruck_drivers(account_id, [
        _staged("D1", name="Linked ByCdl", license_number="CDL 100"),
        _staged("D2", name="Already Linked"),
        _staged("D3", name="Brand New", phone="+15551234",
                email="new@x.com", license_number="CDL-300"),
        _staged("D4", name="Ex Driver", status="terminated"),
    ])
    return {"u1": u1.id, "u2": u2.id}


@pytest.mark.asyncio
async def test_plan_classifies_link_create_skip_already(db):
    acct = await db.create_account("Import Co")
    await _seed(db, acct.id)
    plan = await db.plan_datatruck_driver_import(acct.id)
    assert plan["counts"] == {
        "already_linked": 1, "link": 1, "create": 1,
        "review": 0, "skipped": 1,
    }
    assert plan["link"][0]["external_id"] == "D1"
    assert plan["link"][0]["by"] == "cdl"
    assert plan["create"][0]["name"] == "Brand New"
    assert plan["skipped"][0]["reason"] == "inactive in Datatruck"


@pytest.mark.asyncio
async def test_ambiguous_cdl_lands_in_review(db):
    acct = await db.create_account("Import Co 2")
    a = await db.create_user(7201, acct.id, role=Role.DRIVER, display_name="A")
    b = await db.create_user(7202, acct.id, role=Role.DRIVER, display_name="B")
    await db.update_driver_profile(a.id, cdl_number="SAME-1")
    await db.update_driver_profile(b.id, cdl_number="SAME-1")
    await db.upsert_datatruck_drivers(acct.id, [
        _staged("D9", name="Which One", license_number="SAME1"),
    ])
    plan = await db.plan_datatruck_driver_import(acct.id)
    assert plan["counts"]["review"] == 1
    assert plan["counts"]["create"] == 0      # never guess, never create a dup


@pytest.mark.asyncio
async def test_apply_creates_links_and_is_idempotent(db):
    acct = await db.create_account("Import Co 3")
    seeded = await _seed(db, acct.id)

    result = await db.apply_datatruck_driver_import(acct.id)
    assert result["created"] == 1
    assert result["skipped"] == 1

    drivers = await db.list_drivers(acct.id)
    assert len(drivers) == 3                   # 2 seeded + 1 created
    new = next(d for d in drivers if d.display_name == "Brand New")
    assert new.phone == "+15551234"            # decrypted read of PII write
    assert new.cdl_number == "CDL-300"         # filled by the projection pass
    # Roster entry, not a login: telegram NULL + datatruck ref stamped.
    cur = await db._db.execute(
        "SELECT telegram_id, datatruck_driver_id, email, role FROM users "
        "WHERE id = ?", (new.user_id,),
    )
    row = await cur.fetchone()
    assert row[0] is None and row[1] == "D3"
    assert row[2] == "new@x.com" and row[3] == "driver"
    # The CDL match got linked too.
    cur = await db._db.execute(
        "SELECT datatruck_driver_id FROM users WHERE id = ?", (seeded["u1"],),
    )
    assert (await cur.fetchone())[0] == "D1"

    # Idempotent — a second apply creates nothing.
    again = await db.apply_datatruck_driver_import(acct.id)
    assert again["created"] == 0
    assert len(await db.list_drivers(acct.id)) == 3
