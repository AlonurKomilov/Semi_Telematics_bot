"""``warehouse.driver_hos_minute`` — the storage half of the HOS history tier."""

from __future__ import annotations

import pytest

from adapters.storage.migrations import migrate_driver_hos_minute


async def _exists_in_warehouse(conn) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'warehouse' AND table_name = 'driver_hos_minute'")
    return (await cur.fetchone()) is not None


@pytest.mark.asyncio
async def test_the_tier_lands_in_the_warehouse_schema(seeded_db):
    """public is FIRST on the search_path, so an unqualified CREATE would
    have put the tier in the wrong schema."""
    conn = seeded_db["db"]._db
    await migrate_driver_hos_minute(conn)
    assert await _exists_in_warehouse(conn)
    await migrate_driver_hos_minute(conn)          # idempotent
    assert await _exists_in_warehouse(conn)


@pytest.mark.asyncio
async def test_a_slot_is_written_once_and_pruned_by_age(seeded_db):
    db = seeded_db["db"]
    await migrate_driver_hos_minute(db._db)
    acct = seeded_db["owner"].account_id if "owner" in seeded_db else 1
    row = {"provider_id": "orient_eld", "provider_driver_id": "17", "user_id": None,
           "duty_status": "driving", "last_status_change": "", "driver_name": "J R",
           "source_ts": "2026-09-17T04:31:00+00:00", "company_code": "PTG",
           "provider_vehicle": "001", "captured_at": "2026-09-17T04:30:00+00:00",
           "drive_remaining_seconds": None}
    assert await db.upsert_driver_hos_minutes(acct, [row]) == 1
    # the same slot again is a no-op, not a duplicate
    await db.upsert_driver_hos_minutes(acct, [row])
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM driver_hos_minute WHERE account_id = ? AND provider_driver_id = '17'", (acct,))
    assert int((await cur.fetchone())[0]) == 1
    # an old slot is pruned, a recent one kept
    old = {**row, "captured_at": "2025-01-01T00:00:00+00:00"}
    await db.upsert_driver_hos_minutes(acct, [old])
    await db.prune_driver_hos_minutes(acct, days_keep=30)
    cur = await db._db.execute(
        "SELECT captured_at FROM driver_hos_minute WHERE account_id = ? ORDER BY captured_at", (acct,))
    assert [r[0] for r in await cur.fetchall()] == ["2026-09-17T04:30:00+00:00"]
