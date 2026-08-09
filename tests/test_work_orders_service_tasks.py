"""Service-task layer on work-order parts (migration 148).

Pins the task→parts contract end-to-end through the real storage
methods: ``service_task`` round-trips on part lines, ``cost_by_
service_task`` sums at the PART level (a mixed invoice splits across
tasks; '' surfaces as ``untagged``), and ``cost_by_part`` merges part
names case-insensitively for the "which part keeps costing us" report.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_service_task_round_trip_and_aggregations(db):
    a = 9
    wo1 = await db.add_work_order(
        a, "ACME", "T100", "Shop A", service_date="2026-07-01",
    )
    await db.add_work_order_part(
        wo1, part_name="Brake Pads", quantity=4, unit_cost=50,
        total_cost=200, service_task="brakes",
    )
    await db.add_work_order_part(
        wo1, part_name="Oil filter", total_cost=30,
        service_task="custom_oil_change",
    )
    # Untagged line — must surface in the report, not vanish.
    await db.add_work_order_part(wo1, part_name="Shop supplies", total_cost=10)

    wo2 = await db.add_work_order(
        a, "ACME", "T200", "Shop B", service_date="2026-07-02",
    )
    await db.add_work_order_part(
        wo2, part_name="brake pads", quantity=2, unit_cost=55,
        total_cost=110, service_task="brakes",
    )

    # Round-trip: tags read back on the detail path.
    loaded = {p["part_name"]: p["service_task"] for p in await db.list_work_order_parts(wo1)}
    assert loaded == {
        "Brake Pads": "brakes",
        "Oil filter": "custom_oil_change",
        "Shop supplies": "",
    }

    # Per-task spend: PART-level sums — the mixed invoice splits.
    by_task = {r["service_task"]: r for r in await db.cost_by_service_task(a)}
    assert by_task["brakes"]["total_spent"] == 310
    assert by_task["brakes"]["work_order_count"] == 2
    assert by_task["custom_oil_change"]["total_spent"] == 30
    assert by_task["untagged"]["total_spent"] == 10

    # Per-part: case-insensitive merge ("Brake Pads" + "brake pads").
    parts = await db.cost_by_part(a)
    top = parts[0]
    assert top["part_name"].lower() == "brake pads"
    assert top["usage_count"] == 2
    assert top["work_order_count"] == 2
    assert top["total_quantity"] == 6
    assert top["total_spent"] == 310


@pytest.mark.asyncio
async def test_labor_lines_recompute_and_report(db):
    """Tier-2 B1 contracts: labor lines derive labor_cost + total_cost
    server-side; hours×rate fills a missing flat total; the per-task
    report gains additive labor_spent (parts total_spent unchanged);
    deleting the last line leaves the derived value as the new manual
    scalar instead of zeroing a real invoice amount."""
    a = 61
    wo = await db.add_work_order(
        a, "ACME", "T200", "Labor Shop", service_date="2026-07-02",
        labor_cost=50.0, parts_cost=0.0, tax_amount=10.0, total_cost=60.0,
    )
    await db.add_work_order_part(
        wo, part_name="Brake pads", total_cost=200, service_task="brakes",
    )

    # Line 1: hours × rate → total auto-computed (2.5h × $120 = $300).
    l1 = await db.add_work_order_labor(
        wo, a, description="Brake job labor", hours=2.5, rate=120.0,
        service_task="brakes",
    )
    # Line 2: flat total, different task.
    await db.add_work_order_labor(
        wo, a, description="Diag fee", total_cost=75.0,
        service_task="custom_diagnostic",
    )

    row = await db.get_work_order(wo, a)
    assert row["labor_cost"] == pytest.approx(375.0)     # 300 + 75 (manual 50 replaced)
    # total = labor 375 + parts_cost column + tax 10 (parts_cost scalar
    # is the form's job; here it was saved as 0).
    assert row["total_cost"] == pytest.approx(385.0)

    lines = await db.list_work_order_labor(wo, a)
    assert [l["total_cost"] for l in lines] == [300.0, 75.0]

    # Per-task report: parts spend unchanged, labor merged additively —
    # including a labor-only task bucket.
    by_task = {r["service_task"]: r for r in await db.cost_by_service_task(a)}
    assert by_task["brakes"]["total_spent"] == 200
    assert by_task["brakes"]["labor_spent"] == pytest.approx(300.0)
    assert by_task["custom_diagnostic"]["total_spent"] == 0
    assert by_task["custom_diagnostic"]["labor_spent"] == pytest.approx(75.0)

    # Cross-account scoping: another account can't delete the line.
    assert await db.delete_work_order_labor(l1, a + 1, wo) is False
    # Cross-WORK-ORDER scoping: the line must belong to the work order
    # named in the URL — the route only authorized visibility of THAT
    # work order, so a line nested under a different (visible) WO id
    # must not delete.
    assert await db.delete_work_order_labor(l1, a, wo + 999) is False
    # Delete one line → recompute.
    assert await db.delete_work_order_labor(l1, a, wo) is True
    row = await db.get_work_order(wo, a)
    assert row["labor_cost"] == pytest.approx(75.0)

    # Delete the LAST line → derived value stays (no zeroing).
    lines = await db.list_work_order_labor(wo, a)
    assert await db.delete_work_order_labor(lines[0]["id"], a, wo) is True
    row = await db.get_work_order(wo, a)
    assert row["labor_cost"] == pytest.approx(75.0)
    assert await db.list_work_order_labor(wo, a) == []


@pytest.mark.asyncio
async def test_void_work_orders_excluded_from_cost_reports(db):
    """Void invoices (either lifecycle or payment void) must not leak
    into any cost aggregate — parts, labor, or per-part usage."""
    a = 72
    ok = await db.add_work_order(
        a, "ACME", "T400", "Void Test Shop", status="completed",
        service_date="2026-07-05", total_cost=100,
    )
    await db.add_work_order_part(
        ok, part_name="Void Filter", total_cost=100, service_task="brakes",
    )
    await db.add_work_order_labor(
        ok, a, description="Real labor", total_cost=50, service_task="brakes",
    )

    voided = await db.add_work_order(
        a, "ACME", "T401", "Void Test Shop", status="void",
        service_date="2026-07-06", total_cost=999,
    )
    await db.add_work_order_part(
        voided, part_name="Void Filter", total_cost=999, service_task="brakes",
    )
    await db.add_work_order_labor(
        voided, a, description="Ghost labor", total_cost=999, service_task="brakes",
    )
    pay_voided = await db.add_work_order(
        a, "ACME", "T402", "Void Test Shop", status="completed",
        payment_status="void", service_date="2026-07-07", total_cost=500,
    )
    await db.add_work_order_part(
        pay_voided, part_name="Void Filter", total_cost=500, service_task="brakes",
    )

    by_task = {r["service_task"]: r for r in await db.cost_by_service_task(a)}
    assert by_task["brakes"]["total_spent"] == 100
    assert by_task["brakes"]["labor_spent"] == pytest.approx(50.0)

    parts = [p for p in await db.cost_by_part(a)
             if str(p.get("part_name", "")).lower() == "void filter"]
    assert parts and parts[0]["total_spent"] == 100


def test_sanitize_company_folder_rejects_path_components():
    """'.' / '..' are path components, not names — a company literally
    named that must not write a folder level above its own."""
    from features.work_orders.storage import (
        GENERIC_COMPANY_FOLDER, sanitize_company_folder,
    )
    assert sanitize_company_folder(".") == GENERIC_COMPANY_FOLDER
    assert sanitize_company_folder("..") == GENERIC_COMPANY_FOLDER
    assert sanitize_company_folder(" .. ") == GENERIC_COMPANY_FOLDER
    # trailing dots in real names stay legal (companies end in "INC.")
    assert sanitize_company_folder("ACME INC.") == "ACME INC."
    # separators still become underscores (pre-existing behavior)
    assert sanitize_company_folder("a/b") == "a_b"
