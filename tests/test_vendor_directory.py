"""Global vendor directory (Phase C1).

Pins the platform master-identity contracts: global dedup on name_key
(two accounts suggesting the same shop converge on ONE entry), the
suggestion → approve lifecycle, active-only account-facing search and
linking, rejected tombstones that don't bounce back, and identity-only
data flow (suggested_by_account is operator-audit only).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_suggestion_lifecycle_and_global_dedup(db):
    a1, a2 = 31, 32
    # Account 1 suggests its vendor.
    e1 = await db.create_directory_entry(
        "Springfield Truck & Trailer", address="1 Main St", phone="555-1",
        status="pending", source="suggestion", suggested_by_account=a1,
    )
    assert e1["status"] == "pending"
    # Account 2 suggests the same shop (different casing) → SAME entry.
    e2 = await db.create_directory_entry(
        "  springfield  TRUCK & trailer ",
        status="pending", source="suggestion", suggested_by_account=a2,
    )
    assert e2["id"] == e1["id"]

    # Not searchable while pending.
    assert await db.search_directory_active("springfield") == []

    # Operator approves → searchable, identity fields only.
    assert await db.update_directory_entry(e1["id"], status="active") is True
    hits = await db.search_directory_active("springfield")
    assert len(hits) == 1
    assert hits[0]["name"] == "Springfield Truck & Trailer"
    assert "suggested_by_account" not in hits[0]
    assert "status" not in hits[0]


@pytest.mark.asyncio
async def test_link_only_active_and_unlink(db):
    a = 33
    v = await db.resolve_or_create_vendor(a, "Local Shop")
    pending = await db.create_directory_entry(
        "Pending Shop", status="pending", source="suggestion",
        suggested_by_account=a,
    )
    active = await db.create_directory_entry("Approved Shop", status="active")

    # Pending entries are not linkable.
    assert await db.link_vendor_to_directory(a, v["id"], pending["id"]) is False
    # Active links fine; vendor row carries the id.
    assert await db.link_vendor_to_directory(a, v["id"], active["id"]) is True
    row = await db.get_vendor(v["id"], a)
    assert row["global_vendor_id"] == active["id"]
    # Unlink clears it.
    assert await db.link_vendor_to_directory(a, v["id"], None) is True
    row = await db.get_vendor(v["id"], a)
    assert row["global_vendor_id"] is None
    # Cross-account vendor id: nothing happens.
    assert await db.link_vendor_to_directory(34, v["id"], active["id"]) is False


@pytest.mark.asyncio
async def test_rejected_tombstone_does_not_bounce(db):
    e = await db.create_directory_entry(
        "Sketchy Shop", status="pending", source="suggestion",
        suggested_by_account=35,
    )
    assert await db.update_directory_entry(e["id"], status="rejected") is True
    # A re-suggestion converges on the SAME rejected entry (no new
    # pending row spamming the queue), and it stays unsearchable.
    again = await db.create_directory_entry(
        "Sketchy Shop", status="pending", source="suggestion",
        suggested_by_account=36,
    )
    assert again["id"] == e["id"]
    assert again["status"] == "rejected"
    assert await db.search_directory_active("sketchy") == []
