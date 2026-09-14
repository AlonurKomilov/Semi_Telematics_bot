"""Datatruck storage mixins — upsert/list/stats round-trips.

Exercises the ELT contract every ``datatruck_*`` table shares:
  * upsert is idempotent per (account_id, external_id) — re-syncing
    the same upstream record updates in place, no duplicates
  * rows without an external_id are skipped, not crashed on
  * payload survives as JSON regardless of dict/str input
  * stats reports count + last synced stamp
  * tenant isolation — account A never sees account B's rows
"""

from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_drivers_upsert_round_trip_and_idempotency(db):
    rows = [
        {
            "external_id": "drv_1",
            "first_name": "Claude",
            "last_name": "Safari",
            "display_name": "CLAUDE SAFARI",
            "phone": "+1 555 0100",
            "email": "claude@example.com",
            "status": "active",
            "payload": {"id": "drv_1", "cdl_class": "A"},
        },
        {
            "external_id": "drv_2",
            "display_name": "GERMAIN MUHIRE",
            "status": "active",
            "payload": {"id": "drv_2"},
        },
        # No external_id — must be skipped, not crash.
        {"display_name": "ghost row", "payload": {}},
    ]
    written = await db.upsert_datatruck_drivers(42, rows)
    assert written == 2

    listed = await db.list_datatruck_drivers(42)
    assert len(listed) == 2
    by_ext = {d.external_id: d for d in listed}
    assert by_ext["drv_1"].first_name == "Claude"
    assert by_ext["drv_1"].email == "claude@example.com"
    # Raw payload survives with un-promoted fields intact.
    assert json.loads(by_ext["drv_1"].payload)["cdl_class"] == "A"
    # Partial record: missing promoted columns default to ''.
    assert by_ext["drv_2"].first_name == ""

    # Re-sync the same external_id with changed fields — updates in
    # place, count stays 2.
    await db.upsert_datatruck_drivers(42, [{
        "external_id": "drv_1",
        "first_name": "Claude",
        "last_name": "Safari",
        "display_name": "CLAUDE SAFARI",
        "phone": "+1 555 9999",
        "email": "claude@example.com",
        "status": "inactive",
        "payload": {"id": "drv_1"},
    }])
    listed = await db.list_datatruck_drivers(42)
    assert len(listed) == 2
    drv1 = next(d for d in listed if d.external_id == "drv_1")
    assert drv1.phone == "+1 555 9999"
    assert drv1.status == "inactive"

    stats = await db.datatruck_drivers_stats(42)
    assert stats["count"] == 2
    assert stats["last_synced_at"]


@pytest.mark.asyncio
async def test_trucks_promoted_columns_and_numerics(db):
    written = await db.upsert_datatruck_trucks(42, [{
        "external_id": "443",
        "unit_number": "247",
        "plate_number": "ABC1234",
        "vin": "1HGTEST0000000001",
        "make": "Freightliner",
        "model": "Cascadia",
        "year": 2027,
        "status": "active",
        "owner_name": "TEL",
        "operator_name": "",
        "odometer": 558396.9,
        "payload": {"id": 443, "mc_number": "RMR TRANSPORTATION"},
    }])
    assert written == 1
    trucks = await db.list_datatruck_trucks(42)
    t = trucks[0]
    assert t.unit_number == "247"
    assert t.year == 2027
    assert t.odometer == pytest.approx(558396.9)
    assert json.loads(t.payload)["mc_number"] == "RMR TRANSPORTATION"


@pytest.mark.asyncio
async def test_orders_and_work_orders_round_trip(db):
    await db.upsert_datatruck_orders(42, [{
        "external_id": "ord_9",
        "order_number": "LD-1009",
        "status": "delivered",
        "pickup_date": "2026-06-01",
        "delivery_date": "2026-06-03",
        "origin": "Columbus, OH",
        "destination": "Chicago, IL",
        "driver_external_id": "drv_1",
        "truck_external_id": "443",
        "total_rate": 2150.0,
        "payload": {"id": "ord_9"},
    }])
    orders = await db.list_datatruck_orders(42)
    assert orders[0].order_number == "LD-1009"
    assert orders[0].total_rate == pytest.approx(2150.0)

    await db.upsert_datatruck_work_orders(42, [{
        "external_id": "wo_3",
        "number": "WO-3",
        "status": "open",
        "vehicle_unit": "247",
        "opened_at": "2026-06-05",
        "closed_at": "",
        "total_cost": 480.25,
        "payload": {"id": "wo_3", "parts": [{"name": "brake pad"}]},
    }])
    wos = await db.list_datatruck_work_orders(42)
    assert wos[0].vehicle_unit == "247"
    assert wos[0].total_cost == pytest.approx(480.25)
    assert json.loads(wos[0].payload)["parts"][0]["name"] == "brake pad"


@pytest.mark.asyncio
async def test_trailers_round_trip(db):
    await db.upsert_datatruck_trailers(42, [{
        "external_id": "269",
        "unit_number": "SS006414",
        "plate_number": "594147T",
        "vin": "3H3V532XXXX",
        "status": "active",
        "payload": {"id": 269},
    }])
    trailers = await db.list_datatruck_trailers(42)
    assert trailers[0].unit_number == "SS006414"


@pytest.mark.asyncio
async def test_tenant_isolation_between_accounts(db):
    await db.upsert_datatruck_drivers(1001, [
        {"external_id": "a", "display_name": "Account A driver", "payload": {}},
    ])
    await db.upsert_datatruck_drivers(2002, [
        {"external_id": "a", "display_name": "Account B driver", "payload": {}},
    ])
    a_rows = await db.list_datatruck_drivers(1001)
    b_rows = await db.list_datatruck_drivers(2002)
    assert len(a_rows) == 1 and len(b_rows) == 1
    assert a_rows[0].display_name == "Account A driver"
    assert b_rows[0].display_name == "Account B driver"
    # Same external_id under two accounts = two independent rows.
    assert (await db.datatruck_drivers_stats(1001))["count"] == 1
    assert (await db.datatruck_drivers_stats(2002))["count"] == 1


@pytest.mark.asyncio
async def test_empty_upsert_is_noop(db):
    assert await db.upsert_datatruck_drivers(42, []) == 0
    stats = await db.datatruck_drivers_stats(424242)
    assert stats == {"count": 0, "last_synced_at": None}
