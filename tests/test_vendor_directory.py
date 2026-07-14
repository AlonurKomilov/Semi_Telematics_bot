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


@pytest.mark.asyncio
async def test_geo_set_clear_and_validation(db):
    """C2 map-layer prerequisite: coordinates are set only through
    set_directory_geo (operator pin-confirm flow), validated, clearable,
    and exposed to accounts via active search."""
    e = await db.create_directory_entry("Geo Shop", status="active")

    # Set — appears in the account-facing search payload.
    assert await db.set_directory_geo(e["id"], 41.85, -87.65) is True
    hit = (await db.search_directory_active("geo shop"))[0]
    assert hit["lat"] == pytest.approx(41.85)
    assert hit["lng"] == pytest.approx(-87.65)

    # Partial pair and out-of-range are rejected without touching the row.
    assert await db.set_directory_geo(e["id"], 41.85, None) is False
    assert await db.set_directory_geo(e["id"], 91.0, 0.0) is False
    assert await db.set_directory_geo(e["id"], 0.0, -181.0) is False
    hit = (await db.search_directory_active("geo shop"))[0]
    assert hit["lat"] == pytest.approx(41.85)

    # Clear — both null.
    assert await db.set_directory_geo(e["id"], None, None) is True
    hit = (await db.search_directory_active("geo shop"))[0]
    assert hit["lat"] is None and hit["lng"] is None

    # Unknown entry.
    assert await db.set_directory_geo(999999, 1.0, 1.0) is False


@pytest.mark.asyncio
async def test_generic_update_never_touches_geo(db):
    """Geo has exactly one writer.  A generic field update (even one
    smuggling lat/lng kwargs) must not modify coordinates."""
    e = await db.create_directory_entry("Pinned Shop", status="active")
    assert await db.set_directory_geo(e["id"], 40.0, -100.0) is True

    await db.update_directory_entry(e["id"], phone="555-9", lat=0.0, lng=0.0)
    row = await db.get_directory_entry(e["id"])
    assert row["phone"] == "555-9"
    assert row["lat"] == pytest.approx(40.0)
    assert row["lng"] == pytest.approx(-100.0)


@pytest.mark.asyncio
async def test_directory_entries_in_bbox(db):
    """Map layer source: only ACTIVE + geocoded entries inside the
    viewport, identity fields only."""
    # Coordinates deliberately DISJOINT from the geo-API suite's bbox
    # (41–42/-88–-87), and asserts filter to THIS test's own rows —
    # never global counts (concurrent xdist workers may share the DB).
    inside = await db.create_directory_entry("Bbox Shop Inside", status="active")
    await db.set_directory_geo(inside["id"], 45.52, -93.21)
    outside = await db.create_directory_entry("Bbox Shop Outside", status="active")
    await db.set_directory_geo(outside["id"], 34.05, -118.24)
    no_geo = await db.create_directory_entry("Bbox Shop NoGeo", status="active")
    pend = await db.create_directory_entry(
        "Bbox Shop Pending", status="pending", source="suggestion",
        suggested_by_account=41,
    )
    await db.set_directory_geo(pend["id"], 45.53, -93.22)

    rows = await db.directory_entries_in_bbox(45.0, -94.0, 46.0, -93.0)
    mine = [r for r in rows if str(r["name"]).startswith("Bbox Shop")]
    assert [r["name"] for r in mine] == ["Bbox Shop Inside"]
    assert no_geo["id"] not in [r["id"] for r in rows]
    # Identity-only payload — nothing operator/audit flavoured.
    assert "suggested_by_account" not in mine[0]
    assert "status" not in mine[0]
    assert mine[0]["lat"] == pytest.approx(45.52)


@pytest.mark.asyncio
async def test_link_enriches_empty_vendor_fields(db):
    """Linking pulls the operator-verified identity DOWN into the
    private vendor's empty fields; set fields stay untouched."""
    a = 45
    v = await db.resolve_or_create_vendor(a, "Enrich Shop", phone="555-me")
    e = await db.create_directory_entry(
        "Enrich Shop Global", address="77 Verified Rd", phone="555-dir",
        email="global@shop.com", status="active",
    )
    assert await db.link_vendor_to_directory(a, v["id"], e["id"]) is True
    row = await db.get_vendor(v["id"], a)
    assert row["address"] == "77 Verified Rd"   # empty → filled from entry
    assert row["email"] == "global@shop.com"    # empty → filled
    assert row["phone"] == "555-me"             # user-set → kept


@pytest.mark.asyncio
async def test_browse_directory_aggregate_and_link_flag(db):
    """Browse payload: identity + anonymous rating aggregate + ONLY the
    caller's own link status."""
    a, other = 46, 47
    e = await db.create_directory_entry("Browse Shop", address="1 B St", status="active")
    v = await db.resolve_or_create_vendor(a, "Browse Shop Local")
    await db.link_vendor_to_directory(a, v["id"], e["id"])

    # Approved review from ANOTHER account feeds the aggregate anonymously.
    await db.add_work_order(other, "C1", "T-9", "Browse Shop Local2")
    ov = await db.resolve_or_create_vendor(other, "Browse Shop Local2")
    await db.link_vendor_to_directory(other, ov["id"], e["id"])
    r = await db.upsert_vendor_review(other, e["id"], 4, "solid work")
    await db.set_review_status(r["id"], "approved")

    rows = await db.browse_directory(a, q="browse shop")
    mine = [x for x in rows if x["id"] == e["id"]]
    assert len(mine) == 1
    row = mine[0]
    assert row["rating_count"] == 1
    assert row["rating_avg"] == 4.0
    assert row["linked_vendor_id"] == v["id"]
    assert row["linked_vendor_name"] == "Browse Shop Local"
    assert "suggested_by_account" not in row

    # The OTHER account sees its own link, not account a's.
    rows = await db.browse_directory(other, q="browse shop")
    row = [x for x in rows if x["id"] == e["id"]][0]
    assert row["linked_vendor_id"] == ov["id"]
