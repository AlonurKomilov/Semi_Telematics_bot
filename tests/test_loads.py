"""Loads mixin — the canonical load/shipment model.

Storage contract: CRUD round-trip, status validation, tab counts,
driver-scoped listing, soft delete, tenant isolation, retention prune.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.asyncio
async def test_add_list_get_round_trip(db):
    lid = await db.add_load(
        42, load_number="DT-1001", status="dispatched",
        customer="Go2 Logistics", company_code="PTG",
        pickup_location="Perry, GA", pickup_date="2026-07-01",
        delivery_location="Colonie, NY", delivery_date="2026-07-03",
        driver_name="David C", vehicle_unit="240", trailer_unit="TL644817",
        total_rate=3200.0, loaded_miles=1000.0, empty_miles=150.0,
        driver_pay=900.0,
    )
    assert lid > 0
    rows = await db.list_loads(42)
    assert len(rows) == 1
    l = rows[0]
    assert l.seq == 1                            # per-account sequential ID
    assert l.load_number == "DT-1001"
    assert l.status == "dispatched"
    assert l.source == "manual"
    assert l.total_rate == 3200.0
    fetched = await db.get_load(42, lid)
    assert fetched is not None and fetched.customer == "Go2 Logistics"


@pytest.mark.asyncio
async def test_add_rejects_bad_status(db):
    with pytest.raises(ValueError, match="status must be"):
        await db.add_load(42, status="teleported")
    with pytest.raises(ValueError, match="payment_status"):
        await db.add_load(42, payment_status="iou")


@pytest.mark.asyncio
async def test_counts_by_status_and_filters(db):
    await db.add_load(42, status="upcoming", pickup_date="2026-07-01")
    await db.add_load(42, status="dispatched", pickup_date="2026-07-02")
    await db.add_load(42, status="dispatched", pickup_date="2026-07-03")
    counts = await db.count_loads_by_status(42)
    assert counts["upcoming"] == 1 and counts["dispatched"] == 2
    assert len(await db.list_loads(42, status="dispatched")) == 2
    # Date-window filter on pickup_date.
    assert len(await db.list_loads(42, since="2026-07-02")) == 2
    assert len(await db.list_loads(42, until="2026-07-01")) == 1


@pytest.mark.asyncio
async def test_driver_scoped_listing(db):
    """The own-scope (driver) filter returns only loads linked to that
    driver's user id — the server-side rule behind driver visibility."""
    await db.add_load(42, driver_user_id=7, load_number="MINE")
    await db.add_load(42, driver_user_id=8, load_number="THEIRS")
    await db.add_load(42, load_number="UNASSIGNED")
    mine = await db.list_loads(42, driver_user_id=7)
    assert [l.load_number for l in mine] == ["MINE"]
    counts = await db.count_loads_by_status(42, driver_user_id=7)
    assert sum(counts.values()) == 1


@pytest.mark.asyncio
async def test_update_partial_and_soft_delete(db):
    lid = await db.add_load(42, status="dispatched", customer="A")
    assert await db.update_load(42, lid, status="delivered", total_rate=2500.0)
    l = await db.get_load(42, lid)
    assert l.status == "delivered" and l.total_rate == 2500.0
    assert l.customer == "A"                      # untouched field preserved
    with pytest.raises(ValueError):
        await db.update_load(42, lid, status="nope")
    # Soft delete: leaves the active list, stays fetchable for history.
    assert await db.deactivate_load(42, lid)
    assert await db.list_loads(42) == []
    assert len(await db.list_loads(42, include_inactive=True)) == 1


@pytest.mark.asyncio
async def test_tenant_isolation(db):
    await db.add_load(1001, load_number="A")
    await db.add_load(2002, load_number="B")
    a = await db.list_loads(1001)
    assert len(a) == 1 and a[0].load_number == "A"
    b_id = (await db.list_loads(2002))[0].id
    assert await db.get_load(1001, b_id) is None
    assert await db.update_load(1001, b_id, customer="stolen") is False


@pytest.mark.asyncio
async def test_prune_loads_by_pickup_date(db):
    old = (datetime.now(timezone.utc) - timedelta(days=800)).date().isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    await db.add_load(42, load_number="OLD", pickup_date=old)
    await db.add_load(42, load_number="NEW", pickup_date=recent)
    await db.add_load(42, load_number="NODATE")          # kept (no date)
    n = await db.prune_loads(42, days_keep=730)
    assert n == 1
    left = {l.load_number for l in await db.list_loads(42)}
    assert left == {"NEW", "NODATE"}
