"""Datatruck → driver-user linking (project_datatruck_drivers).

A driver IS a ``users`` row; the projection LINKS synced Datatruck drivers
onto existing driver-users (CDL → email, never name), enriches gaps through
the reconciliation hub, and never creates users — creation is the one-time
onboarding import's job.
"""

from __future__ import annotations

import pytest

from adapters.storage import Role
from capabilities.integrations import reconciliation as recon


def _dt_row(ref: str, *, name: str = "", phone: str = "", email: str = "",
            license_number: str = "") -> dict:
    """A normalized Datatruck driver row (the ``_norm_driver`` shape)."""
    return {
        "external_id": ref,
        "first_name": "", "last_name": "",
        "display_name": name,
        "phone": phone,
        "email": email,
        "status": "active",
        "payload": {"license_number": license_number} if license_number else {},
    }


async def _dt_ref(db, user_id: int) -> str:
    cur = await db._db.execute(
        "SELECT datatruck_driver_id FROM users WHERE id = ?", (user_id,),
    )
    row = await cur.fetchone()
    return str(row[0] or "") if row else ""


@pytest.mark.asyncio
async def test_links_by_cdl_and_never_overwrites_manual(db):
    """CDL match (normalization tolerant of dashes/spaces) links the driver;
    HR-entered phone/name are operator-owned → never overwritten."""
    acct = await db.create_account("DT Fleet")
    u = await db.create_user(1001, acct.id, role=Role.DRIVER,
                             display_name="John Smith")
    await db.update_driver_profile(u.id, cdl_number="D123-456",
                                   phone="+15551111")

    n = await db.project_datatruck_drivers(acct.id, [
        _dt_row("77", name="Johnny S.", phone="+15559999",
                license_number="D123 456"),
    ])
    assert n == 1
    assert await _dt_ref(db, u.id) == "77"
    p = (await db.list_drivers(acct.id))[0]
    assert p.phone == "+15551111"            # HR data preserved
    assert p.display_name == "John Smith"    # name preserved


@pytest.mark.asyncio
async def test_links_by_email_case_insensitive(db):
    acct = await db.create_account("DT Fleet 2")
    u = await db.create_user(1002, acct.id, role=Role.DRIVER,
                             display_name="Jane Doe")
    await db.set_user_email_password(u.id, "Jane@Example.com", "x-hash")

    n = await db.project_datatruck_drivers(acct.id, [
        _dt_row("88", name="Jane Doe", email="JANE@example.COM"),
    ])
    assert n == 1
    assert await _dt_ref(db, u.id) == "88"


@pytest.mark.asyncio
async def test_fills_empty_phone_and_cdl_with_provenance(db):
    """Gaps ARE filled: an empty phone/CDL takes Datatruck's value, stamped
    provenance=datatruck (phone/CDL are PII → stored via the encrypt path,
    decrypted transparently on read)."""
    acct = await db.create_account("DT Fleet 3")
    u = await db.create_user(1003, acct.id, role=Role.DRIVER,
                             display_name="Empty Fields")
    await db.set_user_email_password(u.id, "empty@x.com", "x-hash")

    n = await db.project_datatruck_drivers(acct.id, [
        _dt_row("99", email="empty@x.com", phone="+15550000",
                license_number="CDL999"),
    ])
    assert n == 1
    p = (await db.list_drivers(acct.id))[0]
    assert p.phone == "+15550000"            # filled (decrypted read)
    assert p.cdl_number == "CDL999"          # filled
    cur = await db._db.execute(
        "SELECT driver_field_provenance FROM users WHERE id = ?", (u.id,),
    )
    prov = (await cur.fetchone())[0]
    assert "datatruck" in str(prov)


@pytest.mark.asyncio
async def test_no_match_creates_nothing(db):
    """An unknown Datatruck driver is left alone — no link, no new user
    (creation belongs to the deliberate onboarding import)."""
    acct = await db.create_account("DT Fleet 4")
    await db.create_user(1004, acct.id, role=Role.DRIVER,
                         display_name="Only Driver")

    n = await db.project_datatruck_drivers(acct.id, [
        _dt_row("55", name="Stranger", email="stranger@x.com",
                license_number="ZZZ111"),
    ])
    assert n == 0
    assert len(await db.list_drivers(acct.id)) == 1   # nothing created


@pytest.mark.asyncio
async def test_ambiguous_cdl_is_skipped(db):
    """Two drivers sharing a CDL is ambiguous — never guess."""
    acct = await db.create_account("DT Fleet 5")
    a = await db.create_user(1005, acct.id, role=Role.DRIVER, display_name="A")
    b = await db.create_user(1006, acct.id, role=Role.DRIVER, display_name="B")
    await db.update_driver_profile(a.id, cdl_number="SAME1")
    await db.update_driver_profile(b.id, cdl_number="SAME1")

    n = await db.project_datatruck_drivers(acct.id, [
        _dt_row("66", license_number="SAME1"),
    ])
    assert n == 0
    assert await _dt_ref(db, a.id) == "" and await _dt_ref(db, b.id) == ""


@pytest.mark.asyncio
async def test_rerun_is_stable_via_existing_ref(db):
    """A second sync matches by the stored datatruck_driver_id and leaves
    the state unchanged (idempotent linking)."""
    acct = await db.create_account("DT Fleet 6")
    u = await db.create_user(1007, acct.id, role=Role.DRIVER,
                             display_name="Stable")
    await db.update_driver_profile(u.id, cdl_number="STB-1")

    rows = [_dt_row("11", name="Stable", license_number="STB1")]
    await db.project_datatruck_drivers(acct.id, rows)
    await db.project_datatruck_drivers(acct.id, rows)   # re-run
    assert await _dt_ref(db, u.id) == "11"
    assert len(await db.list_drivers(acct.id)) == 1


@pytest.mark.asyncio
async def test_operator_edit_pins_field_against_resync(db):
    """HR corrects a Datatruck-filled phone → the edit pins it; the next
    sync can't revert it (Layer-2 semantics on the users row)."""
    acct = await db.create_account("DT Fleet 7")
    u = await db.create_user(1008, acct.id, role=Role.DRIVER,
                             display_name="Pin Me")
    await db.set_user_email_password(u.id, "pin@x.com", "x-hash")

    await db.project_datatruck_drivers(acct.id, [
        _dt_row("22", email="pin@x.com", phone="+15550001"),
    ])
    assert (await db.list_drivers(acct.id))[0].phone == "+15550001"

    # Operator corrects the phone → pinned manual.
    await db.update_driver_profile(u.id, phone="+15559999")
    await db.project_datatruck_drivers(acct.id, [
        _dt_row("22", email="pin@x.com", phone="+15550001"),
    ])
    assert (await db.list_drivers(acct.id))[0].phone == "+15559999"


@pytest.mark.asyncio
async def test_cross_source_name_conflict_recorded(db):
    """When a field is owned by ANOTHER integration (samsara) and Datatruck
    disagrees, precedence picks the winner and the conflict is recorded for
    review — proving the hub covers the driver entity end-to-end."""
    acct = await db.create_account("DT Fleet 8")
    u = await db.create_user(1009, acct.id, role=Role.DRIVER,
                             display_name="Samsara Name")
    await db.update_driver_profile(u.id, cdl_number="CONF-1")
    # Seed provenance as samsara-owned (as a future roster ingest would).
    await db._db.execute(
        "UPDATE users SET driver_field_provenance = ? WHERE id = ?",
        ('{"display_name": "samsara"}', u.id),
    )
    await db._db.commit()

    n = await db.project_datatruck_drivers(acct.id, [
        _dt_row("33", name="Datatruck Name", license_number="CONF1"),
    ])
    assert n == 1
    # Datatruck outranks samsara by default → its name wins…
    assert (await db.list_drivers(acct.id))[0].display_name == "Datatruck Name"
    # …and the disagreement is on record for the review panel.
    conflicts = await recon.list_open(db, acct.id, entity_type="driver")
    names = [c for c in conflicts if c["field"] == "display_name"]
    assert len(names) == 1
    assert names[0]["current_source"] == "datatruck"
    assert names[0]["incoming_source"] == "samsara"
