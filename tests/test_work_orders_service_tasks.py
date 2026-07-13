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
