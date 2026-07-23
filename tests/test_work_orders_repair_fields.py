"""Repair-priority + 3C (complaint/cause/correction) fields round-trip.

Pins the Tier-1 work-order additions through the real storage methods
(migrations 146 / 147): both create-with-fields and update-later flow
through ``add_work_order`` / ``update_work_order`` / ``list_work_orders``
and read back intact, and existing rows created without them default to
'' (unclassified / undocumented) rather than NULL.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_repair_fields_create_and_readback(db):
    wo_id = await db.add_work_order(
        7, "ACME", "T100", "Roadside Repair Co",
        repair_priority="emergency",
        complaint="Driver reported grinding noise when braking",
        cause="Front pads worn below minimum, rotor scored",
        correction="Replaced front pads and rotors, road-tested",
    )
    rows = await db.list_work_orders(7)
    wo = next(r for r in rows if r["id"] == wo_id)
    assert wo["repair_priority"] == "emergency"
    assert wo["complaint"].startswith("Driver reported grinding")
    assert wo["cause"].startswith("Front pads worn")
    assert wo["correction"].startswith("Replaced front pads")


@pytest.mark.asyncio
async def test_repair_fields_default_blank_then_updatable(db):
    # Created WITHOUT the new fields — must default to '' (not NULL).
    wo_id = await db.add_work_order(7, "ACME", "T200", "Cross-Country Truck")
    wo = await db.get_work_order(wo_id, 7)
    assert wo["repair_priority"] == ""
    assert wo["complaint"] == "" and wo["cause"] == "" and wo["correction"] == ""

    # Classify + document it after the fact (the retro-tagging path).
    ok = await db.update_work_order(
        wo_id, account_id=7,
        repair_priority="scheduled",
        complaint="Routine PM due",
        correction="Oil + filter change",
    )
    assert ok is True
    wo = await db.get_work_order(wo_id, 7)
    assert wo["repair_priority"] == "scheduled"
    assert wo["complaint"] == "Routine PM due"
    assert wo["cause"] == ""          # left untouched
    assert wo["correction"] == "Oil + filter change"


@pytest.mark.asyncio
async def test_migration_154_folds_status_paid_into_payment_status(db):
    """status carries lifecycle only; the legacy status='paid' rows
    become submitted with payment_status promoted — unless payment
    already carries a more specific money state (money truth wins).
    Idempotent: a second run changes nothing."""
    from adapters.storage.migrations import (
        migrate_work_orders_status_lifecycle_only as mig,
    )
    a = 71
    legacy_unpaid = await db.add_work_order(
        a, "ACME", "T301", "Legacy Shop", status="paid",
        payment_status="unpaid", total_cost=100,
    )
    legacy_partial = await db.add_work_order(
        a, "ACME", "T302", "Legacy Shop", status="paid",
        payment_status="partial", total_cost=200,
    )
    untouched = await db.add_work_order(
        a, "ACME", "T303", "Legacy Shop", status="submitted",
        payment_status="unpaid", total_cost=300,
    )

    await mig(db._db)
    r1 = await db.get_work_order(legacy_unpaid, a)
    r2 = await db.get_work_order(legacy_partial, a)
    r3 = await db.get_work_order(untouched, a)
    assert (r1["status"], r1["payment_status"]) == ("submitted", "paid")
    assert (r2["status"], r2["payment_status"]) == ("submitted", "partial")
    assert (r3["status"], r3["payment_status"]) == ("submitted", "unpaid")

    # Second run: no-op.
    await mig(db._db)
    r1b = await db.get_work_order(legacy_unpaid, a)
    assert (r1b["status"], r1b["payment_status"]) == ("submitted", "paid")


@pytest.mark.asyncio
async def test_migration_159_status_to_fleetio_vocabulary(db):
    """draft→open, submitted→closed; void untouched.  Idempotent."""
    from adapters.storage.migrations import (
        migrate_work_orders_status_fleetio_vocabulary as mig,
    )
    a = 72
    d = await db.add_work_order(a, "ACME", "T1", "Shop", status="draft",
                                total_cost=10)
    s = await db.add_work_order(a, "ACME", "T2", "Shop", status="submitted",
                                total_cost=20)
    v = await db.add_work_order(a, "ACME", "T3", "Shop", status="void",
                                total_cost=30)
    await mig(db._db)
    assert (await db.get_work_order(d, a))["status"] == "open"
    assert (await db.get_work_order(s, a))["status"] == "closed"
    assert (await db.get_work_order(v, a))["status"] == "void"
    # Second run: no-op.
    await mig(db._db)
    assert (await db.get_work_order(s, a))["status"] == "closed"


@pytest.mark.asyncio
async def test_migration_160_finalizes_completed_no_void(db):
    """closed→completed and void→completed (void abolished); open
    untouched.  Idempotent."""
    from adapters.storage.migrations import (
        migrate_work_orders_status_completed_no_void as mig,
    )
    a = 73
    c = await db.add_work_order(a, "ACME", "T1", "Shop", status="closed",
                                total_cost=10)
    v = await db.add_work_order(a, "ACME", "T2", "Shop", status="void",
                                total_cost=20)
    o = await db.add_work_order(a, "ACME", "T3", "Shop", status="open",
                                total_cost=30)
    await mig(db._db)
    assert (await db.get_work_order(c, a))["status"] == "completed"
    assert (await db.get_work_order(v, a))["status"] == "completed"
    assert (await db.get_work_order(o, a))["status"] == "open"
    await mig(db._db)   # no-op
    assert (await db.get_work_order(c, a))["status"] == "completed"
