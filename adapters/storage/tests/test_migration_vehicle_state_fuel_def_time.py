"""Migration 216 — ``vehicle_state_live`` gains ``fuel_time`` / ``def_time``.

``CREATE TABLE IF NOT EXISTS`` builds the current shape on a fresh
database; on every database already running, the table exists in the
older shape and the CREATE is a no-op.  So the columns reach production
only through this migration, and only if it works against the OLD shape
— which is what these tests start from, not the shape the code wishes
it had.
"""

from __future__ import annotations

import pytest

from adapters.storage.migrations import migrate_vehicle_state_fuel_def_time


async def _columns(conn) -> set[str]:
    cur = await conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'vehicle_state_live'")
    return {r[0] for r in await cur.fetchall()}


async def _drop_to_the_old_shape(conn) -> None:
    """The table as it stood before the two clocks.

    Not a DROP + hand-copied CREATE: ``vehicle_timeline`` is a VIEW over
    this table, so a DROP refuses (and CASCADE would take the view with
    it — a test that quietly deletes a production view to set itself up
    is worse than the bug it guards).  Removing exactly the two columns
    the migration adds is the truer old shape anyway: every other
    column, index and dependant stays exactly as a running database has
    them.
    """
    await conn.execute(
        "ALTER TABLE vehicle_state_live "
        "DROP COLUMN IF EXISTS fuel_time, DROP COLUMN IF EXISTS def_time")
    await conn.commit()


async def _view_exists(conn) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM information_schema.views WHERE table_name = 'vehicle_timeline'")
    return (await cur.fetchone()) is not None


@pytest.mark.asyncio
async def test_an_existing_table_gains_both_clocks(seeded_db):
    conn = seeded_db["db"]._db
    await _drop_to_the_old_shape(conn)
    assert not {"fuel_time", "def_time"} & await _columns(conn)

    await migrate_vehicle_state_fuel_def_time(conn)

    assert {"fuel_time", "def_time"} <= await _columns(conn), (
        "the upgrade path did not add the columns — on a running "
        "database the CREATE is a no-op, so this is the only way in"
    )


@pytest.mark.asyncio
async def test_the_dependent_view_survives_the_setup_and_the_migration(seeded_db):
    """The reason the old shape is reached by DROP COLUMN, not DROP TABLE."""
    conn = seeded_db["db"]._db
    assert await _view_exists(conn), "fixture has no vehicle_timeline view — precondition"
    await _drop_to_the_old_shape(conn)
    await migrate_vehicle_state_fuel_def_time(conn)
    assert await _view_exists(conn)


@pytest.mark.asyncio
async def test_running_it_twice_is_harmless(seeded_db):
    conn = seeded_db["db"]._db
    await _drop_to_the_old_shape(conn)
    await migrate_vehicle_state_fuel_def_time(conn)
    await migrate_vehicle_state_fuel_def_time(conn)
    assert {"fuel_time", "def_time"} <= await _columns(conn)


@pytest.mark.asyncio
async def test_the_reader_can_select_the_clocks_beside_the_values(seeded_db):
    """What the migration is FOR: the reader's SELECT names both columns,
    so a database that lacked them 500'd the vehicles list."""
    conn = seeded_db["db"]._db
    await _drop_to_the_old_shape(conn)
    await migrate_vehicle_state_fuel_def_time(conn)
    await conn.execute(
        "INSERT INTO vehicle_state_live (vehicle_id, account_id, fuel_pct, fuel_time, updated_at) "
        "VALUES ('v1', 1, 45.0, '2026-06-23T08:00:00Z', '')")
    await conn.commit()
    cur = await conn.execute(
        "SELECT fuel_pct, fuel_time, def_pct, def_time FROM vehicle_state_live WHERE vehicle_id='v1'")
    row = await cur.fetchone()
    assert tuple(row)[:2] == (45.0, "2026-06-23T08:00:00Z")
