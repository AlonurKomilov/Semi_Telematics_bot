"""Loads mixin — the canonical load/shipment model.

Storage contract: CRUD round-trip, status validation, tab counts,
driver-scoped listing, soft delete, tenant isolation, retention prune.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adapters.storage import Role


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


@pytest.mark.asyncio
async def test_list_loads_limit_none_returns_everything(db):
    """``limit=None`` is the aggregation contract (KPI) — a row cap here
    silently understates every total."""
    acct = await db.create_account("Loads Cap Co")
    for i in range(5):
        await db.add_load(acct.id, load_number=f"L{i}", total_rate=100.0)
    capped = await db.list_loads(acct.id, limit=2)
    assert len(capped) == 2
    everything = await db.list_loads(acct.id, limit=None)
    assert len(everything) == 5


@pytest.mark.asyncio
async def test_list_loads_company_scope_fail_open(db):
    """``company_codes`` keeps only those companies PLUS rows with no
    company (fail-open, same rule as the Drivers/Vehicles lists)."""
    acct = await db.create_account("Loads Scope Co")
    await db.add_load(acct.id, load_number="A1", company_code="OSY")
    await db.add_load(acct.id, load_number="B1", company_code="PTG")
    await db.add_load(acct.id, load_number="N1")            # no company
    rows = await db.list_loads(acct.id, company_codes=["OSY"])
    assert {l.load_number for l in rows} == {"A1", "N1"}
    counts = await db.count_loads_by_status(acct.id, company_codes=["OSY"])
    assert sum(counts.values()) == 2
    # Empty allowed-list (restricted user with no companies) → only
    # company-less rows remain visible.
    rows = await db.list_loads(acct.id, company_codes=[])
    assert {l.load_number for l in rows} == {"N1"}


# ── Line items (extra pay & costs) ──────────────────────────────


@pytest.mark.asyncio
async def test_line_items_ride_into_gross(db):
    from features.loads import service as loads_service
    acct = await db.create_account("Items Co")
    lid = await db.add_load(
        acct.id, load_number="L1", total_rate=3000.0, driver_pay=800.0,
        pickup_date="2026-07-01",
    )
    # TONU defaults to the driver-pay bucket, tolls to expenses.
    await db.add_load_line_item(acct.id, kind="tonu", amount=150, load_id=lid)
    await db.add_load_line_item(acct.id, kind="tolls", amount=87.5, load_id=lid)
    # Same path the service takes: batch aggregate → serializer.
    rows = await db.list_loads(acct.id)
    sums = await db.sum_load_line_items(acct.id, [x.id for x in rows])
    l = loads_service.load_to_dict(
        next(x for x in rows if x.id == lid), sums.get(lid),
    )
    assert l["extra_driver_pay"] == 150.0
    assert l["extra_costs"] == 87.5
    assert l["gross"] == round(3000 - 800 - 150 - 87.5, 2)


@pytest.mark.asyncio
async def test_line_item_inherits_people_and_date_from_load(db):
    acct = await db.create_account("Items Co 2")
    drv = await db.create_pending_user(acct.id, Role.DRIVER, "Eugene B")
    dsp = await db.create_pending_user(acct.id, Role.DISPATCHER, "Jasur")
    lid = await db.add_load(
        acct.id, load_number="L2", driver_user_id=drv,
        dispatcher_user_id=dsp, pickup_date="2026-07-02",
    )
    iid = await db.add_load_line_item(
        acct.id, kind="bonus", amount=50, load_id=lid,
    )
    items = await db.list_load_line_items(acct.id, load_id=lid)
    it = next(i for i in items if i.id == iid)
    assert it.driver_user_id == drv
    assert it.dispatcher_user_id == dsp
    assert it.item_date == "2026-07-02"
    assert it.bucket == "driver_pay"


@pytest.mark.asyncio
async def test_off_load_layover_and_validation(db):
    acct = await db.create_account("Items Co 3")
    drv = await db.create_pending_user(acct.id, Role.DRIVER, "Sat Waiting")
    dsp = await db.create_pending_user(acct.id, Role.DISPATCHER, "Slow Dispatch")
    # No anchor at all → refused.
    with pytest.raises(ValueError):
        await db.add_load_line_item(acct.id, kind="layover", amount=300)
    with pytest.raises(ValueError):
        await db.add_load_line_item(acct.id, kind="nope", amount=10,
                                    driver_user_id=drv, item_date="2026-07-03")
    with pytest.raises(ValueError):
        await db.add_load_line_item(acct.id, kind="layover", amount=0,
                                    driver_user_id=drv, item_date="2026-07-03")
    iid = await db.add_load_line_item(
        acct.id, kind="layover", amount=300,
        driver_user_id=drv, dispatcher_user_id=dsp, item_date="2026-07-03",
    )
    off = await db.list_load_line_items(acct.id, off_load_only=True)
    assert [i.id for i in off] == [iid]
    assert off[0].bucket == "driver_pay"
    # Window filter + tenant isolation + delete.
    assert await db.list_load_line_items(
        acct.id, off_load_only=True, since="2026-07-04",
    ) == []
    other = await db.create_account("Items Co 4")
    assert await db.list_load_line_items(other.id) == []
    assert await db.delete_load_line_item(other.id, iid) is False
    assert await db.delete_load_line_item(acct.id, iid) is True


@pytest.mark.asyncio
async def test_get_load_line_item_scoped(db):
    """The delete endpoint's company guard resolves items through this
    getter — it must honor tenant boundaries like every other read."""
    acct = await db.create_account("Items Co 5")
    other = await db.create_account("Items Co 6")
    lid = await db.add_load(acct.id, load_number="G1", company_code="OSY",
                            pickup_date="2026-07-05")
    iid = await db.add_load_line_item(acct.id, kind="tolls", amount=42,
                                      load_id=lid)
    it = await db.get_load_line_item(acct.id, iid)
    assert it is not None and it.load_id == lid and it.amount == 42.0
    assert await db.get_load_line_item(other.id, iid) is None


@pytest.mark.asyncio
async def test_deduction_bucket_and_settlement_items(db):
    """Deductions are a third line-item bucket (unified with additions):
    they do NOT reduce the load's gross (driver-side, not company cost),
    and settlement-items returns driver_pay + deduction tagged by bucket."""
    from features.loads import service as loads_service
    acct = await db.create_account("Ded Bucket Co")
    lid = await db.add_load(acct.id, load_number="D1", total_rate=3000.0,
                            driver_pay=800.0, pickup_date="2026-07-02")
    await db.add_load_line_item(acct.id, kind="tonu", amount=150, load_id=lid)
    # 'insurance' auto-buckets to deduction; on-load charge here.
    await db.add_load_line_item(acct.id, kind="insurance", amount=85, load_id=lid)
    rows = await db.list_loads(acct.id)
    sums = await db.sum_load_line_items(acct.id, [x.id for x in rows])
    l = loads_service.load_to_dict(rows[0], sums.get(lid))
    # Gross = rate − pay − tonu; the deduction does NOT touch gross.
    assert l["extra_driver_pay"] == 150.0
    assert l["extra_deductions"] == 85.0
    assert l["gross"] == round(3000 - 800 - 150, 2)   # deduction excluded

    items = await db.list_driver_settlement_items(
        acct.id, since="2026-07-01", until="2026-07-31",
    )
    by_kind = {i["kind"]: i for i in items}
    assert by_kind["tonu"]["bucket"] == "driver_pay"
    assert by_kind["insurance"]["bucket"] == "deduction"
