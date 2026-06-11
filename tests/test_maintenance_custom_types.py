"""Smoke tests for the per-account custom maintenance task type
storage helpers.

Verifies:

  * ``schema.create_tables`` creates the ``maintenance_custom_task_types``
    table + the UNIQUE(account_id, value) constraint.
  * Label slugification — common inputs round-trip to stable
    ``custom_<slug>`` keys.
  * Create is idempotent — re-submitting the same label returns the
    existing row instead of failing on the UNIQUE constraint.
  * Empty / whitespace-only labels return ``None`` so the API can
    map that to a 400.
  * List returns rows sorted by label.
  * Delete removes only the targeted row for the targeted account.
"""

from __future__ import annotations

import pytest

from adapters.storage import Database
from adapters.storage.maintenance import _slugify_type_value


# ── Slugifier ─────────────────────────────────────────────────────


def test_slugify_basic():
    assert _slugify_type_value("Tire change") == "custom_tire-change"


def test_slugify_strips_special_characters():
    assert _slugify_type_value("Oil & Filter (synthetic)") == "custom_oil-filter-synthetic"


def test_slugify_handles_unicode_and_extra_spaces():
    assert _slugify_type_value("   Fuel  Filter   ") == "custom_fuel-filter"


def test_slugify_empty_label_returns_untitled():
    assert _slugify_type_value("") == "custom_untitled"
    assert _slugify_type_value("   ") == "custom_untitled"


def test_slugify_truncates_pathologically_long_labels():
    long_label = "x" * 200
    out = _slugify_type_value(long_label)
    assert len(out) <= 80
    assert out.startswith("custom_")


# ── CRUD round-trip ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_row_with_expected_shape(seeded_db):
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    row = await db.create_maintenance_custom_task_type(
        account.id, "Tire change", created_by=42,
    )
    assert row is not None
    assert row["account_id"] == account.id
    assert row["value"] == "custom_tire-change"
    assert row["label"] == "Tire change"
    assert row["created_by"] == 42


@pytest.mark.asyncio
async def test_create_is_idempotent_for_same_label(seeded_db):
    """Re-submitting the same label returns the existing row rather
    than failing on the UNIQUE constraint."""
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    row1 = await db.create_maintenance_custom_task_type(
        account.id, "Tire change", created_by=1,
    )
    row2 = await db.create_maintenance_custom_task_type(
        account.id, "Tire change", created_by=2,
    )
    assert row1 is not None and row2 is not None
    assert row1["id"] == row2["id"]
    # Updated_at refreshes; label preserved.
    assert row2["label"] == "Tire change"


@pytest.mark.asyncio
async def test_create_empty_label_returns_none(seeded_db):
    db: Database = seeded_db["db"]
    account = seeded_db["account"]
    assert await db.create_maintenance_custom_task_type(account.id, "") is None
    assert await db.create_maintenance_custom_task_type(account.id, "   ") is None


@pytest.mark.asyncio
async def test_create_truncates_long_labels_to_60_chars(seeded_db):
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    long = "A" * 200
    row = await db.create_maintenance_custom_task_type(account.id, long)
    assert row is not None
    assert len(row["label"]) <= 60


@pytest.mark.asyncio
async def test_list_returns_rows_sorted_by_label(seeded_db):
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    await db.create_maintenance_custom_task_type(account.id, "Zebra service")
    await db.create_maintenance_custom_task_type(account.id, "Apple service")
    await db.create_maintenance_custom_task_type(account.id, "Middle service")

    rows = await db.list_maintenance_custom_task_types(account.id)
    labels = [r["label"] for r in rows]
    assert labels == ["Apple service", "Middle service", "Zebra service"]


@pytest.mark.asyncio
async def test_delete_removes_only_targeted_row(seeded_db):
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    row_a = await db.create_maintenance_custom_task_type(account.id, "A type")
    row_b = await db.create_maintenance_custom_task_type(account.id, "B type")
    assert row_a is not None and row_b is not None

    ok = await db.delete_maintenance_custom_task_type(account.id, row_a["id"])
    assert ok is True

    remaining = await db.list_maintenance_custom_task_types(account.id)
    values = {r["value"] for r in remaining}
    assert "custom_a-type" not in values
    assert "custom_b-type" in values


@pytest.mark.asyncio
async def test_delete_missing_row_returns_false(seeded_db):
    db: Database = seeded_db["db"]
    account = seeded_db["account"]
    assert await db.delete_maintenance_custom_task_type(account.id, 99999) is False


# ── Per-account isolation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_account_a_does_not_see_account_b_custom_types(seeded_db):
    """Strict per-account isolation — the same value can exist on
    two accounts as separate rows and neither sees the other's."""
    db: Database = seeded_db["db"]
    account_a = seeded_db["account"]

    # Create a second account for the cross-account check.
    account_b = await db.create_account("Other Fleet Co")

    await db.create_maintenance_custom_task_type(account_a.id, "Shared label")
    await db.create_maintenance_custom_task_type(account_b.id, "Shared label")

    a_rows = await db.list_maintenance_custom_task_types(account_a.id)
    b_rows = await db.list_maintenance_custom_task_types(account_b.id)
    assert len(a_rows) == 1
    assert len(b_rows) == 1
    assert a_rows[0]["account_id"] == account_a.id
    assert b_rows[0]["account_id"] == account_b.id
    # Same slug but different row ids.
    assert a_rows[0]["id"] != b_rows[0]["id"]
