"""The ELD table's migration on a database that already has the table.

Every test of this migration ran against a FRESH database, where
``CREATE TABLE IF NOT EXISTS`` builds the current shape and every column
arrives for free. Production is never on that path. It is always on the
other one: the table exists in an older shape, the CREATE is a no-op,
and the only thing that can add a column is the ALTER below it.

That gap shipped a self-perpetuating outage. An index over the newly
added ``company_code`` was written into the CREATE block, whose except
clause RETURNS — so on an existing database the column was not there
yet, the index raised, the return fired, and the ALTER that adds the
column was never reached. Every boot, identically. ``GET /api/eld/hours``
answered 500 with "column h.company_code does not exist" while the
repair that would have fixed it sat unreachable, eight lines below.

So these tests start from the OLD shape on purpose. A migration test
that only ever sees a fresh table is testing the branch production does
not take.
"""

from __future__ import annotations

import pytest

from adapters.storage.platform_migrations import migrate_eld_hos_live


async def _columns(conn) -> set[str]:
    cur = await conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'driver_hos_live'")
    return {r[0] for r in await cur.fetchall()}


async def _drop_to_the_old_shape(conn) -> None:
    """The table as it stood before ``company_code`` — what every
    already-running database actually has on disk."""
    await conn.execute("DROP TABLE IF EXISTS driver_hos_live")
    await conn.execute("""
        CREATE TABLE driver_hos_live (
            account_id              INTEGER NOT NULL,
            provider_id             TEXT    NOT NULL,
            provider_driver_id      TEXT    NOT NULL,
            user_id                 INTEGER,
            duty_status             TEXT    NOT NULL DEFAULT 'unknown',
            drive_remaining_seconds INTEGER,
            shift_remaining_seconds INTEGER,
            cycle_remaining_seconds INTEGER,
            break_in_seconds        INTEGER,
            last_status_change      TEXT    NOT NULL DEFAULT '',
            driver_name             TEXT    NOT NULL DEFAULT '',
            source_ts               TEXT    NOT NULL DEFAULT '',
            updated_at              TEXT    NOT NULL,
            PRIMARY KEY (account_id, provider_id, provider_driver_id)
        )
    """)


@pytest.mark.asyncio
async def test_an_existing_table_gains_the_company_column(seeded_db):
    """The bug, stated as the thing that must be true.

    Fails on the shipped order: the index raises, the CREATE block
    returns, and the column never arrives.
    """
    conn = seeded_db["db"]._db
    await _drop_to_the_old_shape(conn)
    assert "company_code" not in await _columns(conn)

    await migrate_eld_hos_live(conn)

    assert "company_code" in await _columns(conn), (
        "the upgrade path never reached the ALTER — check that nothing "
        "depending on a repaired column sits in the CREATE block, whose "
        "except clause returns"
    )


@pytest.mark.asyncio
async def test_the_reader_can_actually_select_the_column(seeded_db):
    """Present in information_schema is not the same as usable. This is
    the query the ELD page runs, and the shape the 500 came from."""
    conn = seeded_db["db"]._db
    await _drop_to_the_old_shape(conn)
    await migrate_eld_hos_live(conn)

    cur = await conn.execute(
        "SELECT h.company_code FROM driver_hos_live h WHERE h.account_id = ?",
        (1,))
    assert await cur.fetchall() == []


@pytest.mark.asyncio
async def test_rerunning_on_an_upgraded_table_changes_nothing(seeded_db):
    """It re-runs every boot rather than being version-tracked, so the
    second pass has to be a no-op — including the index, which is where
    the first version raised."""
    conn = seeded_db["db"]._db
    await _drop_to_the_old_shape(conn)
    await migrate_eld_hos_live(conn)
    before = await _columns(conn)

    await migrate_eld_hos_live(conn)
    await migrate_eld_hos_live(conn)

    assert await _columns(conn) == before


@pytest.mark.asyncio
async def test_a_fresh_database_gets_the_column_too(seeded_db):
    """The path that was already covered, kept so the two cannot drift:
    a new install and an upgrade must land on the same shape."""
    conn = seeded_db["db"]._db
    await conn.execute("DROP TABLE IF EXISTS driver_hos_live")

    await migrate_eld_hos_live(conn)

    assert "company_code" in await _columns(conn)


@pytest.mark.asyncio
async def test_an_existing_row_survives_the_upgrade(seeded_db):
    """The column is added, not rebuilt. A driver's last known duty
    status must not vanish because we added a column beside it."""
    conn = seeded_db["db"]._db
    await _drop_to_the_old_shape(conn)
    await conn.execute(
        "INSERT INTO driver_hos_live (account_id, provider_id, "
        "provider_driver_id, duty_status, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (1, "orient_eld", "19192", "driving", "2026-09-15T05:00:00+00:00"))

    await migrate_eld_hos_live(conn)

    cur = await conn.execute(
        "SELECT duty_status, company_code FROM driver_hos_live "
        "WHERE provider_driver_id = ?", ("19192",))
    row = (await cur.fetchall())[0]
    assert row[0] == "driving"
    assert row[1] == "", "a pre-existing row must default, never go NULL"
