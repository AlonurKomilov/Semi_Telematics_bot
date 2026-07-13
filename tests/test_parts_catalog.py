"""Parts catalog (Phase B, migration 150).

Pins the master-data contracts for parts, mirroring the vendor suite:
resolve-or-create dedup on name_key, alias-aware merge that survives a
re-sync, account scoping through the parent work order (the line table
has no account_id), sync auto-linking of Datatruck line items, and the
part-id-keyed Top Parts report merging name variants.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_resolve_dedup_and_report_rekey(db):
    a = 21
    cp = await db.resolve_or_create_part(a, "Oil Filter", part_number="PH7317")
    cp2 = await db.resolve_or_create_part(a, "  oil   FILTER ")
    assert cp["id"] == cp2["id"]
    assert cp["part_number"] == "PH7317"

    wo1 = await db.add_work_order(a, "ACME", "T1", "Shop A", service_date="2026-07-01")
    wo2 = await db.add_work_order(a, "ACME", "T2", "Shop B", service_date="2026-07-02")
    # Same part, two casings, two vendors — must merge in the report.
    await db.add_work_order_part(wo1, part_name="Oil Filter", quantity=1,
                                 total_cost=30, part_id=cp["id"])
    await db.add_work_order_part(wo2, part_name="OIL FILTER", quantity=2,
                                 total_cost=70, part_id=cp["id"])
    rows = await db.cost_by_part(a)
    assert len(rows) == 1
    top = rows[0]
    assert top["part_id"] == cp["id"]
    assert top["part_name"] == "Oil Filter"       # catalog display name
    assert top["total_spent"] == 100
    assert top["work_order_count"] == 2
    assert top["total_quantity"] == 3


@pytest.mark.asyncio
async def test_merge_repoints_and_alias_survives(db):
    a = 21
    good = await db.resolve_or_create_part(a, "Brake Pad Set")
    dup = await db.resolve_or_create_part(a, "Brake Pads Set")
    wo = await db.add_work_order(a, "ACME", "T3", "Shop C", service_date="2026-07-03")
    await db.add_work_order_part(wo, part_name="Brake Pads Set",
                                 total_cost=200, part_id=dup["id"])
    assert await db.merge_catalog_parts(a, loser_id=dup["id"], winner_id=good["id"]) is True
    parts = await db.list_work_order_parts(wo)
    assert parts[0]["part_id"] == good["id"]
    assert await db.get_catalog_part(dup["id"], a) is None
    # Alias: the dup name re-resolves to the survivor.
    again = await db.resolve_or_create_part(a, "Brake Pads Set")
    assert again["id"] == good["id"]
    # Cross-account merge refuses.
    theirs = await db.resolve_or_create_part(22, "Brake Pad Set")
    assert await db.merge_catalog_parts(a, loser_id=theirs["id"], winner_id=good["id"]) is False


@pytest.mark.asyncio
async def test_sync_line_items_autolink(db):
    a = 23
    rows = [{
        "external_id": "7001", "invoice_number": "INV-7001",
        "vehicle_unit": "T700", "vendor_name": "Sync Shop",
        "opened_at": "2026-07-01T00:00:00Z", "total_cost": 120.0,
        "line_items": [
            {"name": "DEF Pump", "quantity": 1.0, "unit_cost": 100.0, "total": 100.0},
            {"name": "def pump", "quantity": 1.0, "unit_cost": 20.0, "total": 20.0},
        ],
    }]
    assert await db.project_external_work_orders(a, rows, source="datatruck") == 1
    catalog = await db.list_parts_catalog(a)
    # Both casings resolved to ONE catalog part.
    assert len(catalog) == 1
    assert catalog[0]["usage_count"] == 2
    assert catalog[0]["total_spent"] == 120.0
