"""Vendor reviews (Phase C2 — anonymous stars/comments, moderated).

Pins: the verified-usage eligibility gate, one-review-per-account
upsert with edits re-entering moderation, approved-only aggregates,
and the anonymization contract (account attribution never leaves the
account-facing read paths).
"""

from __future__ import annotations

import pytest


async def _seed_usage(db, account_id: int, shop: str):
    """Account uses the shop: vendor + linked WO + directory link."""
    v = await db.resolve_or_create_vendor(account_id, shop)
    await db.add_work_order(
        account_id, "CO", "T-1", shop,
        vendor_id=v["id"], service_date="2026-07-01", total_cost=100,
    )
    entry = await db.create_directory_entry(shop, status="active")
    await db.link_vendor_to_directory(account_id, v["id"], entry["id"])
    return entry


@pytest.mark.asyncio
async def test_eligibility_requires_real_usage(db):
    entry = await _seed_usage(db, 41, "Reviewed Repair Co")
    assert await db.review_eligible(41, entry["id"]) is True
    # Another account with no linked usage: not eligible.
    assert await db.review_eligible(42, entry["id"]) is False


@pytest.mark.asyncio
async def test_upsert_one_per_account_and_re_moderation(db):
    entry = await _seed_usage(db, 41, "Upsert Shop")
    r1 = await db.upsert_vendor_review(41, entry["id"], 5, "great")
    assert r1["status"] == "pending"
    assert await db.set_review_status(r1["id"], "approved") is True

    # Edit → same row, back to pending (no silent post-approval edits).
    r2 = await db.upsert_vendor_review(41, entry["id"], 2, "got worse")
    assert r2["id"] == r1["id"]
    assert r2["status"] == "pending"
    assert r2["rating"] == 2

    # Aggregates count APPROVED only — the re-pended edit drops out.
    agg = await db.review_aggregate_for_entry(entry["id"])
    assert agg == {"rating_count": 0, "rating_avg": None}


@pytest.mark.asyncio
async def test_aggregates_and_anonymized_reads(db):
    entry = await _seed_usage(db, 41, "Aggregate Garage")
    await _seed_usage(db, 42, "Aggregate Garage")   # second account uses it too
    ra = await db.upsert_vendor_review(41, entry["id"], 5, "fast work")
    rb = await db.upsert_vendor_review(42, entry["id"], 4, "")
    await db.set_review_status(ra["id"], "approved")
    await db.set_review_status(rb["id"], "approved")

    agg = await db.review_aggregate_for_entry(entry["id"])
    assert agg["rating_count"] == 2
    assert agg["rating_avg"] == 4.5

    shown = await db.approved_reviews_for_entry(entry["id"])
    assert len(shown) == 2
    for r in shown:
        # Anonymization contract: rating/comment/month ONLY.
        assert set(r.keys()) == {"rating", "comment", "month"}

    # Rejected reviews vanish from both.
    await db.set_review_status(rb["id"], "rejected")
    assert (await db.review_aggregate_for_entry(entry["id"]))["rating_count"] == 1

    # Moderation queue carries shop name + account id for the operator.
    pend = await db.list_reviews_moderation(status="rejected")
    assert any(r["entry_name"] == "Aggregate Garage" and r["account_id"] == 42 for r in pend)


@pytest.mark.asyncio
async def test_review_requires_active_entry(db):
    pending = await db.create_directory_entry(
        "Pending Only Shop", status="pending", source="suggestion",
        suggested_by_account=41,
    )
    assert await db.upsert_vendor_review(41, pending["id"], 5, "no") is None
