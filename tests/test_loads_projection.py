"""Datatruck orders → loads projection (project_external_loads).

The canonical loads table is the SSOT; the TMS projects in keyed on
(account, source, external_ref): status mapped from the vendor vocabulary,
driver resolved via users.datatruck_driver_id (Increment A), units resolved
from the synced rosters, data fields merged through the reconciliation hub
(operator pins win).
"""

from __future__ import annotations

import pytest

from adapters.storage import Role
from capabilities.integrations import reconciliation as recon


def _order(ref: str, **kw) -> dict:
    """A normalized Datatruck order row (the _norm_order shape)."""
    row = {
        "external_id": ref,
        "order_number": kw.get("order_number", f"DT-{ref}"),
        "status": kw.get("status", "Dispatched"),
        "pickup_date": kw.get("pickup_date", "2026-07-01"),
        "delivery_date": kw.get("delivery_date", "2026-07-03"),
        "origin": kw.get("origin", "Perry, GA"),
        "destination": kw.get("destination", "Colonie, NY"),
        "driver_external_id": kw.get("driver_external_id", ""),
        "truck_external_id": kw.get("truck_external_id", ""),
        "trailer_external_id": kw.get("trailer_external_id", ""),
        "total_rate": kw.get("total_rate", 3200.0),
        "customer": kw.get("customer", "Go2 Logistics"),
        "dispatcher_external_id": "",
        "dispatcher_name": kw.get("dispatcher_name", "Jasur"),
        "loaded_miles": kw.get("loaded_miles", 1000.0),
        "empty_miles": kw.get("empty_miles", 150.0),
        "driver_pay": kw.get("driver_pay", 900.0),
        "payload": {},
    }
    return row


async def _seed_driver(db, account_id: int, dt_ref: str, name: str) -> int:
    u = await db.create_user(9000 + account_id, account_id,
                             role=Role.DRIVER, display_name=name)
    await db._db.execute(
        "UPDATE users SET datatruck_driver_id = ? WHERE id = ?",
        (dt_ref, u.id),
    )
    await db._db.commit()
    return u.id


@pytest.mark.asyncio
async def test_project_creates_load_with_full_resolution(db):
    acct = await db.create_account("L2 Fleet")
    uid = await _seed_driver(db, acct.id, "D7", "David C")
    await db.upsert_datatruck_trucks(acct.id, [{
        "external_id": "T1", "unit_number": "240", "plate_number": "",
        "vin": "", "make": "", "model": "", "year": None, "status": "",
        "owner_name": "", "operator_name": "", "odometer": None, "payload": {},
    }])
    await db.upsert_datatruck_trailers(acct.id, [{
        "external_id": "TR1", "unit_number": "TL644817", "plate_number": "",
        "vin": "", "make": "", "model": "", "year": None, "status": "",
        "payload": {},
    }])

    n = await db.project_external_loads(acct.id, [
        _order("O1", status="In Transit", driver_external_id="D7",
               truck_external_id="T1", trailer_external_id="TR1"),
    ])
    assert n == 1
    loads = await db.list_loads(acct.id)
    assert len(loads) == 1
    l = loads[0]
    assert l.source == "datatruck" and l.external_ref == "O1"
    assert l.status == "in_transit"          # vendor "In Transit" mapped
    assert l.driver_user_id == uid           # Increment-A link resolved
    assert l.driver_name == "David C"
    assert l.vehicle_unit == "240"           # roster lookup
    assert l.trailer_unit == "TL644817"
    assert l.total_rate == 3200.0 and l.loaded_miles == 1000.0
    assert l.customer == "Go2 Logistics"
    assert l.dispatcher_name == "Jasur"


@pytest.mark.asyncio
async def test_resync_refreshes_status_payment_and_data(db):
    acct = await db.create_account("L2 Fleet 2")
    await db.project_external_loads(acct.id, [_order("O2", status="Dispatched")])
    # Upstream advances: delivered + invoiced unpaid, rate corrected.
    n = await db.project_external_loads(acct.id, [
        _order("O2", status="Unpaid", total_rate=3400.0),
    ])
    assert n == 1
    loads = await db.list_loads(acct.id)
    assert len(loads) == 1                   # keyed on external_ref, no dup
    l = loads[0]
    assert l.status == "delivered"           # unpaid = delivered + billing
    assert l.payment_status == "unpaid"
    assert l.total_rate == 3400.0            # TMS owns its own data


@pytest.mark.asyncio
async def test_operator_pin_survives_resync(db):
    acct = await db.create_account("L2 Fleet 3")
    await db.project_external_loads(acct.id, [_order("O3", total_rate=3200.0)])
    lid = (await db.list_loads(acct.id))[0].id
    # Operator corrects the rate → pinned manual.
    await db.update_load(acct.id, lid, total_rate=9999.0)
    await db.project_external_loads(acct.id, [_order("O3", total_rate=3400.0)])
    l = (await db.list_loads(acct.id))[0]
    assert l.total_rate == 9999.0            # pin wins over the sync
    # A manual pin is the operator's authority — not a source conflict.
    assert await recon.count_open(db, acct.id) == 0


@pytest.mark.asyncio
async def test_manual_loads_are_never_touched(db):
    acct = await db.create_account("L2 Fleet 4")
    mid = await db.add_load(acct.id, load_number="HAND-1", customer="Manual Co",
                            total_rate=1000.0)
    await db.project_external_loads(acct.id, [_order("O4")])
    loads = await db.list_loads(acct.id)
    assert len(loads) == 2
    manual = await db.get_load(acct.id, mid)
    assert manual.customer == "Manual Co" and manual.total_rate == 1000.0
    assert manual.source == "manual" and manual.external_ref == ""


@pytest.mark.asyncio
async def test_unknown_status_and_unmatched_driver_degrade_gracefully(db):
    acct = await db.create_account("L2 Fleet 5")
    n = await db.project_external_loads(acct.id, [
        _order("O5", status="Weird-Vendor-State", driver_external_id="NOBODY"),
    ])
    assert n == 1
    l = (await db.list_loads(acct.id))[0]
    assert l.status == "upcoming"            # unknown vocab lands safe
    assert l.driver_user_id is None          # no fake links
