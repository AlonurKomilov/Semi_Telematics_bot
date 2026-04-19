"""Schema migrations for per-tenant database tables.

These run after tenant_schema.create_tables() and add columns/indexes
introduced after the initial multi-tenant schema was created.

Each migration function MUST be idempotent — check whether the migration
has already been applied (e.g. column exists, DDL already changed) before
modifying the schema.  They run on every startup for every tenant DB.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def run_all(conn) -> None:
    """Execute every tenant migration in order.

    Add new migrations here as they are created.  Example::

        await migrate_add_maintenance_priority(conn)
    """
    await migrate_add_parking_map_image(conn)
    await migrate_add_camera_image_path(conn)
    await migrate_add_knowledge_base(conn)
    await migrate_rename_fleet_manager_visibility(conn)


async def migrate_add_parking_map_image(conn) -> None:
    """Add map_image_path column to parking_events for storing AI map screenshots."""
    cur = await conn.execute("PRAGMA table_info(parking_events)")
    cols = {r[1] for r in await cur.fetchall()}
    if "map_image_path" not in cols:
        await conn.execute(
            "ALTER TABLE parking_events ADD COLUMN map_image_path TEXT NOT NULL DEFAULT ''"
        )
        await conn.commit()
        logger.info("Migration: added parking_events.map_image_path")


async def migrate_add_camera_image_path(conn) -> None:
    """Add image_path column to camera_checks for dashcam screenshots."""
    cur = await conn.execute("PRAGMA table_info(camera_checks)")
    cols = {r[1] for r in await cur.fetchall()}
    if "image_path" not in cols:
        await conn.execute(
            "ALTER TABLE camera_checks ADD COLUMN image_path TEXT NOT NULL DEFAULT ''"
        )
        await conn.commit()
        logger.info("Migration: added camera_checks.image_path")


async def migrate_add_knowledge_base(conn) -> None:
    """Create knowledge_base table if it doesn't exist (for pre-existing tenant DBs)."""
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_base'"
    )
    if not await cur.fetchone():
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                title           TEXT    NOT NULL,
                description     TEXT    NOT NULL DEFAULT '',
                category        TEXT    NOT NULL DEFAULT 'general',
                media_url       TEXT    NOT NULL DEFAULT '',
                media_type      TEXT    NOT NULL DEFAULT 'link',
                tags            TEXT    NOT NULL DEFAULT '',
                visibility      TEXT    NOT NULL DEFAULT 'all',
                pinned          INTEGER NOT NULL DEFAULT 0,
                created_by      INTEGER NOT NULL DEFAULT 0,
                updated_at      TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kb_account_cat
                ON knowledge_base(account_id, category);
            CREATE INDEX IF NOT EXISTS idx_kb_pinned
                ON knowledge_base(account_id, pinned);
        """)
        await conn.commit()
        logger.info("Migration: created knowledge_base table")


async def migrate_rename_fleet_manager_visibility(conn) -> None:
    """Rename visibility 'fleet_manager' to 'fleet' in knowledge_base."""
    try:
        cur = await conn.execute(
            "UPDATE knowledge_base SET visibility = 'fleet' WHERE visibility = 'fleet_manager'"
        )
        if cur.rowcount:
            await conn.commit()
            logger.info("Renamed %d fleet_manager → fleet visibility in knowledge_base", cur.rowcount)
    except Exception:
        pass  # table may not exist yet
