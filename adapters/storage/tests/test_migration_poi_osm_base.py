"""The upgrade path, which no test had ever walked.

``migrate_poi_points`` both CREATES and REPAIRS: it builds the two POI
tables on a fresh database, and on one that already has them it adds the
``osm_base`` column the freshness line reads.  Every existing test runs
against a FRESH database — ``_pg_template`` builds one per worker — so
`CREATE TABLE` produces the current shape and the ALTER is never the
statement that does the work.  Production is never on that path.

A sibling session lost a production endpoint to exactly this shape last
night: an index over a newly added column sat ABOVE the ALTER inside a
try block whose except RETURNS, so on every existing database the index
raised, the return fired, and the column was never added.  Fresh
databases never saw it.

Ours is ordered so nothing above the ALTER can raise on an old database
— the tables and indexes are all IF NOT EXISTS and none of them names
``osm_base`` — but "is ordered so" is a claim, and this file is the
check.  It starts from the OLD SHAPE on purpose.
"""
from __future__ import annotations

import pytest

from adapters.storage.platform_migrations import migrate_poi_points

pytestmark = pytest.mark.asyncio

OLD_SHAPE = """
    CREATE TABLE poi_imports (
        layer        TEXT    PRIMARY KEY,
        imported_at  TEXT    NOT NULL,
        points       INTEGER NOT NULL DEFAULT 0,
        ok           INTEGER NOT NULL DEFAULT 0,
        note         TEXT    NOT NULL DEFAULT ''
    )
"""


async def _columns(db, table: str) -> set[str]:
    cur = await db._db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
        (table,))
    return {dict(r)["column_name"] for r in await cur.fetchall()}


async def _rewind_to_the_old_shape(db):
    """Put the database back the way it was before osm_base existed."""
    await db._db.execute("DROP TABLE IF EXISTS poi_imports")
    await db._db.execute(OLD_SHAPE)
    await db._db.commit()


async def test_an_existing_table_gains_the_column(db):
    await _rewind_to_the_old_shape(db)
    assert "osm_base" not in await _columns(db, "poi_imports"), "rewind did not"

    await migrate_poi_points(db._db)

    assert "osm_base" in await _columns(db, "poi_imports"), (
        "the ALTER never ran on a database that already had the table — "
        "which is every database except a brand-new one, and the only "
        "kind production ever is")


async def test_the_rows_that_were_already_there_survive_it(db):
    """A repair that empties the table is not a repair.  The layers hold
    thousands of points keyed to these rows' dates."""
    await _rewind_to_the_old_shape(db)
    await db._db.execute(
        "INSERT INTO poi_imports (layer, imported_at, points, ok, note) "
        "VALUES (?, ?, ?, ?, ?)",
        ("weigh_station", "2026-09-14T02:20:44Z", 4500, 1, ""))
    await db._db.commit()

    await migrate_poi_points(db._db)

    row = await db.read_one(
        "SELECT imported_at, points, osm_base FROM poi_imports WHERE layer = ?",
        ("weigh_station",))
    assert row["imported_at"] == "2026-09-14T02:20:44Z"
    assert row["points"] == 4500
    # Unknown, not invented: the run that wrote this row predates the
    # column, so nothing is known about the extract behind it.
    assert row["osm_base"] is None


async def test_the_writer_works_against_the_repaired_table(db):
    """The column existing is not the same claim as the INSERT working.

    `finish_poi_import` names all six columns in one statement, so a
    table repaired halfway would fail there — AFTER its sweep had already
    deleted rows, and inside a caller that swallows the exception.
    """
    await _rewind_to_the_old_shape(db)
    await migrate_poi_points(db._db)

    await db.finish_poi_import(
        "shower", "2026-09-15T10:00:00Z", 597, ok=True,
        osm_base="2026-06-01T00:00:00Z")

    assert await db.poi_layer_imported_at("shower") == "2026-09-15T10:00:00Z"
    assert await db.poi_layer_source_as_of("shower") == "2026-06-01T00:00:00Z"


async def test_running_it_again_changes_nothing(db):
    """Every boot runs it.  Twice must equal once."""
    await _rewind_to_the_old_shape(db)
    await migrate_poi_points(db._db)
    before = await _columns(db, "poi_imports")
    await migrate_poi_points(db._db)
    assert await _columns(db, "poi_imports") == before
