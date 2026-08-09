"""The identity watch's resolution flow — an open event becomes a
human decision, and "different truck" performs the registry split.

Truck 128's gateway moved into a Freightliner (2026-08-07): Samsara's
entry kept the name "128" while everything physical behind it changed.
The split mints the new unit, moves the telematics link, restores the
old unit's true VIN, and — critically — survives the NEXT ingest tick,
which still reports the old display name (ref-first matching).
"""

from __future__ import annotations

import pytest

ACCT = 1
REF = "281475003801071"
OLD_VIN = "4V4NC9EH8KN196862"     # the Volvo that unit 128 really is
NEW_VIN = "3AKJGLDV5GSGZ4085"     # the Freightliner behind the ref now
NOW = "2026-08-07T12:21:59+00:00"


async def _seed(pg_db):
    """Unit 128 (PTG) linked to REF, plus its recorded vin_change."""
    vid = await pg_db.add_vehicle(
        ACCT, unit_number="128", company_code="PTG",
        vin=OLD_VIN, telematics_ref=REF, source="samsara",
    )
    new = await pg_db.record_device_events(ACCT, [{
        "registry_id": vid, "vehicle_id": REF, "vehicle_name": "128",
        "kind": "vin_change", "old_value": OLD_VIN, "new_value": NEW_VIN,
        "observed_at": NOW,
    }])
    assert len(new) == 1
    ev = (await pg_db.get_device_events(ACCT))[0]
    assert ev["status"] == "open" and ev["id"]
    return vid, ev


@pytest.mark.asyncio
async def test_resolve_claim_is_exclusive_and_reopenable(pg_db):
    _, ev = await _seed(pg_db)
    assert await pg_db.resolve_device_event(
        ACCT, ev["id"], resolution="same_truck", resolved_by=7)
    # Second claim loses — the endpoint's double-click lock.
    assert not await pg_db.resolve_device_event(
        ACCT, ev["id"], resolution="same_truck", resolved_by=9)
    got = await pg_db.get_device_event(ACCT, ev["id"])
    assert got["status"] == "resolved" and got["resolution"] == "same_truck"
    # A refused split hands the claim back.
    await pg_db.reopen_device_event(ACCT, ev["id"])
    got = await pg_db.get_device_event(ACCT, ev["id"])
    assert got["status"] == "open" and got["resolution"] == ""


@pytest.mark.asyncio
async def test_split_moves_the_link_and_restores_the_vin(pg_db):
    old_id, ev = await _seed(pg_db)
    new_id = await pg_db.split_vehicle_identity(
        ACCT, old_vehicle_id=old_id,
        new_company_code="PTG", new_unit_number="415",
        new_vin=NEW_VIN, restore_vin=OLD_VIN, archive_old=False,
    )
    old = await pg_db.get_vehicle(ACCT, old_id)
    new = await pg_db.get_vehicle(ACCT, new_id)
    assert old.telematics_ref == "" and old.vin == OLD_VIN
    assert old.is_active                      # kept, not auto-archived
    assert new.telematics_ref == REF and new.vin == NEW_VIN
    assert (new.company_code, new.unit_number) == ("PTG", "415")


@pytest.mark.asyncio
async def test_split_survives_the_next_ingest_tick(pg_db):
    """Samsara still displays '128' after the swap — a name-first match
    would re-link the OLD row and undo the split within 60s.  Ref-first
    matching pins the moved link to the new unit."""
    old_id, ev = await _seed(pg_db)
    new_id = await pg_db.split_vehicle_identity(
        ACCT, old_vehicle_id=old_id,
        new_company_code="PTG", new_unit_number="415",
        new_vin=NEW_VIN, restore_vin=OLD_VIN,
    )
    await pg_db.upsert_from_integration(ACCT, [{
        "company_code": "PTG", "unit_number": "128",   # provider's old name
        "telematics_ref": REF, "vin": NEW_VIN,
        "make": "Freightliner", "model": "Cascadia", "year": 2016,
        "plate_number": "", "gateway_serial": "G-XYZ",
    }], source="samsara")
    old = await pg_db.get_vehicle(ACCT, old_id)
    new = await pg_db.get_vehicle(ACCT, new_id)
    assert old.telematics_ref == "" and old.vin == OLD_VIN   # untouched
    assert new.telematics_ref == REF
    assert new.unit_number == "415"            # our name, not the provider's
    assert new.make == "Freightliner" and new.gateway_serial == "G-XYZ"


@pytest.mark.asyncio
async def test_split_refuses_unit_collision_and_archives_on_request(pg_db):
    old_id, ev = await _seed(pg_db)
    with pytest.raises(ValueError):
        await pg_db.split_vehicle_identity(
            ACCT, old_vehicle_id=old_id,
            new_company_code="PTG", new_unit_number="128",  # taken (itself)
            new_vin=NEW_VIN,
        )
    new_id = await pg_db.split_vehicle_identity(
        ACCT, old_vehicle_id=old_id,
        new_company_code="PTG", new_unit_number="415",
        new_vin=NEW_VIN, restore_vin=OLD_VIN, archive_old=True,
    )
    old = await pg_db.get_vehicle(ACCT, old_id)
    assert not old.is_active and old.status == "inactive"
    # Same unit number in ANOTHER company is two real trucks — allowed.
    other = await pg_db.add_vehicle(
        ACCT, unit_number="415", company_code="OSY", vin="1XKWD40X1EJ000001")
    assert other != new_id
