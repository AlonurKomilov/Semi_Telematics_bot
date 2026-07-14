"""Vendor registry (Phase A, migration 149).

Pins the master-data contracts: resolve-or-create dedup on the
normalized name_key, backfill-equivalent linking via add_work_order,
merge (repoint + alias + tombstone; strictly account-scoped), the
alias-aware re-resolve after a merge (re-sync must NOT recreate the
loser), and the vendor-id-keyed spend report merging casing variants.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_resolve_or_create_dedupes_on_name_key(db):
    a = 11
    v1 = await db.resolve_or_create_vendor(a, "Bob's Repair", phone="555-1")
    v2 = await db.resolve_or_create_vendor(a, "  bob's   REPAIR ")   # same key
    assert v1["id"] == v2["id"]
    assert v1["name"] == "Bob's Repair"          # first casing wins display
    # Different account, same name → separate vendor.
    v3 = await db.resolve_or_create_vendor(12, "Bob's Repair")
    assert v3["id"] != v1["id"]


@pytest.mark.asyncio
async def test_vendor_report_merges_casing_variants(db):
    a = 11
    v = await db.resolve_or_create_vendor(a, "Roadside Repair Co")
    await db.add_work_order(
        a, "ACME", "T1", "Roadside Repair Co",
        vendor_id=v["id"], service_date="2026-07-01", total_cost=100,
    )
    await db.add_work_order(
        a, "ACME", "T2", "ROADSIDE REPAIR CO",     # invoice said it loud
        vendor_id=v["id"], service_date="2026-07-02", total_cost=50,
    )
    rows = await db.cost_by_vendor(a)
    assert len(rows) == 1
    assert rows[0]["vendor_id"] == v["id"]
    assert rows[0]["total_spent"] == 150
    assert rows[0]["work_order_count"] == 2
    # Registry display name, not either invoice casing necessarily.
    assert rows[0]["vendor_name"] == "Roadside Repair Co"


@pytest.mark.asyncio
async def test_merge_repoints_and_alias_survives_resync(db):
    a = 11
    good = await db.resolve_or_create_vendor(a, "Cross-Country Truck Service")
    typo = await db.resolve_or_create_vendor(a, "Cross Country Truck Service")  # dup
    assert good["id"] != typo["id"]
    wo = await db.add_work_order(
        a, "ACME", "T3", "Cross Country Truck Service",
        vendor_id=typo["id"], service_date="2026-07-03", total_cost=75,
    )
    ok = await db.merge_vendors(a, loser_id=typo["id"], winner_id=good["id"])
    assert ok is True
    # Work order repointed; loser gone.
    row = await db.get_work_order(wo, a)
    assert row["vendor_id"] == good["id"]
    assert await db.get_vendor(typo["id"], a) is None
    # Alias-aware: the loser's name re-resolves to the WINNER (a later
    # Datatruck re-sync must not recreate the duplicate).
    again = await db.resolve_or_create_vendor(a, "Cross Country Truck Service")
    assert again["id"] == good["id"]


@pytest.mark.asyncio
async def test_merge_is_account_scoped(db):
    a, other = 11, 12
    mine = await db.resolve_or_create_vendor(a, "My Shop")
    theirs = await db.resolve_or_create_vendor(other, "Their Shop")
    # Cross-account ids: nothing moves, returns False.
    assert await db.merge_vendors(a, loser_id=theirs["id"], winner_id=mine["id"]) is False
    assert await db.get_vendor(theirs["id"], other) is not None


@pytest.mark.asyncio
async def test_projection_links_vendor_and_survives_resync(db):
    a = 13
    rows = [{
        "external_id": "9001", "invoice_number": "INV-9001",
        "vehicle_unit": "T900", "vendor_name": "Springfield Truck & Trailer",
        "opened_at": "2026-07-01T00:00:00Z", "total_cost": 500.0,
        "line_items": [],
    }]
    assert await db.project_external_work_orders(a, rows, source="datatruck") == 1
    wos = await db.list_work_orders(a)
    assert len(wos) == 1 and wos[0]["vendor_id"] is not None
    vid = wos[0]["vendor_id"]
    # Re-sync: same vendor id, no duplicate vendor rows.
    assert await db.project_external_work_orders(a, rows, source="datatruck") == 1
    vendors = await db.list_vendors(a)
    assert len(vendors) == 1 and vendors[0]["id"] == vid
    # Rollups on the list read.
    assert vendors[0]["work_order_count"] == 1
    assert vendors[0]["total_spent"] == 500.0


@pytest.mark.asyncio
async def test_resolve_enriches_empty_fields_only(db):
    """Enrich-on-save: an existing vendor LEARNS contact info it lacks
    from later saves (WO forms, sync) — but never overwrites a value
    someone already set."""
    a = 15
    v = await db.resolve_or_create_vendor(a, "Learn Shop")
    assert v["address"] == "" and v["phone"] == ""

    # Later WO save carries address + phone → empty fields fill.
    v2 = await db.resolve_or_create_vendor(
        a, "learn shop", address="9 Oak St", phone="555-77",
    )
    assert v2["id"] == v["id"]
    assert v2["address"] == "9 Oak St"
    assert v2["phone"] == "555-77"

    # A DIFFERENT address later does NOT overwrite the learned one.
    v3 = await db.resolve_or_create_vendor(
        a, "Learn Shop", address="1 Elm Ave", email="ls@x.com",
    )
    assert v3["address"] == "9 Oak St"      # kept
    assert v3["email"] == "ls@x.com"        # was empty → filled
