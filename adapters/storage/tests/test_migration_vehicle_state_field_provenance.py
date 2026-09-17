"""Migration 218 — ``vehicle_state_live`` and ``vehicle_state_minute``
gain ``field_provenance``.

Same shape as the 216 tests, for the same reason: the CREATE is a no-op
on every running database, so the column reaches production only
through the ALTER — tested from the OLD shape, reached by DROP COLUMN
because ``vehicle_timeline`` is a view over the live table.
"""

from __future__ import annotations

import pytest

from adapters.storage.migrations import migrate_vehicle_state_field_provenance

TABLES = ("vehicle_state_live", "vehicle_state_minute")


async def _columns(conn, table: str) -> set[str]:
    cur = await conn.execute(
        "SELECT column_name FROM information_schema.columns "
        f"WHERE table_name = '{table}'")
    return {r[0] for r in await cur.fetchall()}


async def _drop_to_the_old_shape(conn) -> None:
    for table in TABLES:
        await conn.execute(
            f"ALTER TABLE {table} DROP COLUMN IF EXISTS field_provenance")
    await conn.commit()


async def _view_exists(conn) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM information_schema.views WHERE table_name = 'vehicle_timeline'")
    return (await cur.fetchone()) is not None


@pytest.mark.asyncio
async def test_both_grains_gain_the_column(seeded_db):
    conn = seeded_db["db"]._db
    await _drop_to_the_old_shape(conn)
    for table in TABLES:
        assert "field_provenance" not in await _columns(conn, table)
    await migrate_vehicle_state_field_provenance(conn)
    for table in TABLES:
        assert "field_provenance" in await _columns(conn, table), table


@pytest.mark.asyncio
async def test_the_dependent_view_survives(seeded_db):
    conn = seeded_db["db"]._db
    assert await _view_exists(conn), "fixture has no vehicle_timeline view — precondition"
    await _drop_to_the_old_shape(conn)
    await migrate_vehicle_state_field_provenance(conn)
    assert await _view_exists(conn)


@pytest.mark.asyncio
async def test_running_it_twice_is_harmless(seeded_db):
    conn = seeded_db["db"]._db
    await _drop_to_the_old_shape(conn)
    await migrate_vehicle_state_field_provenance(conn)
    await migrate_vehicle_state_field_provenance(conn)
    for table in TABLES:
        assert "field_provenance" in await _columns(conn, table)


@pytest.mark.asyncio
async def test_the_store_writes_a_dict_and_reads_it_back(seeded_db):
    """What the column is FOR — and what a row from before it reads as."""
    db = seeded_db["db"]
    await _drop_to_the_old_shape(db._db)
    await migrate_vehicle_state_field_provenance(db._db)
    await db.upsert_vehicle_state(1, [
        {"vehicle_id": "v1", "vehicle_name": "T-1",
         "field_provenance": {"location": "samsara", "odometer": "orient_eld"}},
        {"vehicle_id": "v2", "vehicle_name": "T-2"},
    ])
    by_id = {r["vehicle_id"]: r["field_provenance"]
             for r in await db.get_vehicle_state(1)}
    assert by_id == {"v1": {"location": "samsara", "odometer": "orient_eld"},
                     "v2": {}}
