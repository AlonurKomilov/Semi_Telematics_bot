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
    await migrate_drop_escalation_columns(conn)
    await migrate_dedup_alert_history(conn)
    await migrate_resolve_orphaned_acks(conn)
    await migrate_add_platform_geofences(conn)
    await migrate_add_geofence_zone_role(conn)
    await migrate_resolve_orphaned_acks(conn)


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


async def migrate_drop_escalation_columns(conn) -> None:
    """Drop escalation_level and next_escalation from alert_acknowledgments.

    These columns supported the re-alert/reminder system which has been removed.
    """
    cur = await conn.execute("PRAGMA table_info(alert_acknowledgments)")
    cols = {r[1] for r in await cur.fetchall()}

    dropped = False
    if "escalation_level" in cols:
        await conn.execute(
            "ALTER TABLE alert_acknowledgments DROP COLUMN escalation_level"
        )
        dropped = True
    if "next_escalation" in cols:
        await conn.execute(
            "ALTER TABLE alert_acknowledgments DROP COLUMN next_escalation"
        )
        dropped = True

    if dropped:
        await conn.execute("DROP INDEX IF EXISTS idx_alert_ack_pending")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_ack_pending "
            "ON alert_acknowledgments(acknowledged_at, status)"
        )
        await conn.commit()
        logger.info("Migration: dropped escalation columns from alert_acknowledgments")


async def migrate_dedup_alert_history(conn) -> None:
    """Deduplicate alert_history to one row per (account_id, alert_type, vehicle_id).

    Previously one row was created per subscriber per alert, resulting in N rows
    for the same vehicle alert when N subscribers are registered.  The correct
    design is one shared row per logical alert that accumulates the total
    occurrence count across all subscribers and all time.

    Migration steps:
    1. Rebuild alert_history keeping only the best row per vehicle+type:
       - Keep the row with the earliest first_seen (original detection time)
       - Accumulate occurrence_count as the MAX across duplicates (prevents
         double-counting while preserving the highest known count)
       - Preserve last_seen / last_detail from the most-recent row
    2. Drop old chat_id-keyed index.
    3. Add UNIQUE(account_id, alert_type, vehicle_id) constraint via table rebuild
       (SQLite does not support ADD CONSTRAINT after creation, so we rename →
       create → insert → drop old table).
    """
    try:
        # Check whether the UNIQUE constraint already exists
        cur = await conn.execute("PRAGMA index_list(alert_history)")
        indexes = {r[1] for r in await cur.fetchall()}
        if "sqlite_autoindex_alert_history_1" in indexes or "uniq_alert_history_vehicle" in indexes:
            logger.debug("migrate_dedup_alert_history: UNIQUE constraint already present, skipping")
            return

        # Step 1: Collapse duplicates into a single row per vehicle+type
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _alert_history_dedup AS
            SELECT
                MIN(id)                AS id,
                account_id,
                alert_type,
                vehicle_id,
                MAX(vehicle_name)      AS vehicle_name,
                0                      AS chat_id,
                0                      AS message_id,
                MAX(occurrence_count)  AS occurrence_count,
                MIN(first_seen)        AS first_seen,
                MAX(last_seen)         AS last_seen,
                MAX(last_detail)       AS last_detail,
                MAX(status)            AS status
            FROM alert_history
            GROUP BY account_id, alert_type, vehicle_id
        """)

        # Step 2: Replace the original table
        await conn.execute("DROP TABLE alert_history")
        await conn.execute("""
            CREATE TABLE alert_history (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id        INTEGER NOT NULL,
                alert_type        TEXT    NOT NULL,
                vehicle_id        TEXT    NOT NULL,
                vehicle_name      TEXT    NOT NULL DEFAULT '',
                chat_id           INTEGER NOT NULL DEFAULT 0,
                message_id        INTEGER NOT NULL DEFAULT 0,
                occurrence_count  INTEGER NOT NULL DEFAULT 1,
                first_seen        TEXT    NOT NULL,
                last_seen         TEXT    NOT NULL,
                last_detail       TEXT    NOT NULL DEFAULT '',
                status            TEXT    NOT NULL DEFAULT 'active',
                UNIQUE(account_id, alert_type, vehicle_id)
            )
        """)
        await conn.execute("""
            INSERT INTO alert_history
                (id, account_id, alert_type, vehicle_id, vehicle_name,
                 chat_id, message_id, occurrence_count,
                 first_seen, last_seen, last_detail, status)
            SELECT
                id, account_id, alert_type, vehicle_id, vehicle_name,
                chat_id, message_id, occurrence_count,
                first_seen, last_seen, last_detail, status
            FROM _alert_history_dedup
        """)
        await conn.execute("DROP TABLE _alert_history_dedup")

        # Step 3: Re-create index (without chat_id)
        await conn.execute("DROP INDEX IF EXISTS idx_alert_history_active")
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_alert_history_active
                ON alert_history(account_id, alert_type, vehicle_id, status)
        """)

        await conn.commit()
        logger.info("Migration: deduplicated alert_history to one row per vehicle+type"
                    " and added UNIQUE(account_id, alert_type, vehicle_id) constraint")
    except Exception as exc:
        logger.error("migrate_dedup_alert_history failed: %s", exc, exc_info=True)


async def migrate_resolve_orphaned_acks(conn) -> None:
    """Resolve alert_acknowledgments rows that are stuck 'active' after auto-resolve.

    When a vehicle's condition clears, two things must happen atomically:
      1. alert_history.status → 'cleared'
      2. alert_acknowledgments.status → 'acknowledged' for all subscribers

    In earlier versions these two steps were sometimes split across different
    call sites, leaving 'active' ack rows even though history was 'cleared'.
    This migration finds and fixes those orphans so they don't appear as
    pending alerts in the dashboard.

    Idempotent — safe to run on every startup.
    """
    try:
        cur = await conn.execute("""
            UPDATE alert_acknowledgments
            SET status = 'acknowledged',
                acknowledged_by = 0,
                acknowledged_at = datetime('now')
            WHERE status = 'active'
              AND id IN (
                  SELECT a.id
                  FROM alert_acknowledgments a
                  JOIN alert_history h
                       ON  h.account_id = a.account_id
                       AND h.alert_type = a.alert_type
                       AND h.vehicle_id = a.vehicle_id
                  WHERE a.status = 'active'
                    AND h.status = 'cleared'
              )
        """)
        if cur.rowcount:
            await conn.commit()
            logger.info(
                "Migration: resolved %d orphaned active ack row(s) "
                "whose alert_history was already cleared",
                cur.rowcount,
            )
    except Exception as exc:
        logger.error("migrate_resolve_orphaned_acks failed: %s", exc, exc_info=True)


async def migrate_add_platform_geofences(conn) -> None:
    """Create platform_geofences table for user-owned zones (pre-existing tenant DBs)."""
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='platform_geofences'"
    )
    if await cur.fetchone():
        return  # already exists
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS platform_geofences (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            name            TEXT    NOT NULL,
            description     TEXT    NOT NULL DEFAULT '',
            geofence_type   TEXT    NOT NULL DEFAULT 'custom',
            shape_type      TEXT    NOT NULL DEFAULT 'circle',
            latitude        REAL,
            longitude       REAL,
            radius_meters   REAL,
            vertices        TEXT    NOT NULL DEFAULT '[]',
            notify_roles    TEXT    NOT NULL DEFAULT '["owner","admin","fleet","safety","dispatcher","driver"]',
            is_active       INTEGER NOT NULL DEFAULT 1,
            created_by      INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, name)
        );
        CREATE INDEX IF NOT EXISTS idx_platform_geofences_account
            ON platform_geofences(account_id, is_active);
    """)
    await conn.commit()
    logger.info("Migration: created platform_geofences table")


async def migrate_add_geofence_zone_role(conn) -> None:
    """Add zone_role column to platform_geofences for role-based zone attribution."""
    cur = await conn.execute("PRAGMA table_info(platform_geofences)")
    cols = {r[1] for r in await cur.fetchall()}
    if "zone_role" in cols:
        return
    await conn.execute(
        "ALTER TABLE platform_geofences ADD COLUMN zone_role TEXT NOT NULL DEFAULT 'all'"
    )
    await conn.commit()
    logger.info("Migration: added zone_role column to platform_geofences")
