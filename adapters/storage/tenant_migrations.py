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
    await migrate_add_custom_poi_layers(conn)
    await migrate_score_rules_pillar_curves(conn)
    await migrate_warehouse_tables(conn)
    await migrate_alert_ack_indexes(conn)
    await migrate_rename_fleet_rule_ids(conn)
    await migrate_add_vehicle_health_snapshot(conn)
    await migrate_add_fault_weather_efficiency(conn)
    await migrate_add_geofence_definitions(conn)
    await migrate_add_payroll_tables(conn)
    await migrate_add_coaching_tables(conn)
    await migrate_add_vehicle_state_odometer_time(conn)
    await migrate_add_alert_mutes_table(conn)
    await migrate_add_alert_history_reescalate_columns(conn)
    await migrate_add_alert_history_severity_location(conn)
    await migrate_add_alert_history_subkey(conn)
    await migrate_alert_history_subkey_index(conn)


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


async def migrate_add_custom_poi_layers(conn) -> None:
    """Create custom_poi_layers + custom_poi_points tables for tenant DBs
    that pre-date the custom-POI feature."""
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='custom_poi_layers'"
    )
    if await cur.fetchone():
        return
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS custom_poi_layers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            layer_key       TEXT    NOT NULL,
            label           TEXT    NOT NULL,
            color           TEXT    NOT NULL DEFAULT '#3b82f6',
            icon            TEXT    NOT NULL DEFAULT '⚫',
            source_type     TEXT    NOT NULL DEFAULT 'overpass',
            overpass_query  TEXT    NOT NULL DEFAULT '',
            brand_filters   TEXT    NOT NULL DEFAULT '[]',
            default_on      INTEGER NOT NULL DEFAULT 0,
            is_active       INTEGER NOT NULL DEFAULT 1,
            created_by      INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, layer_key)
        );
        CREATE INDEX IF NOT EXISTS idx_custom_poi_layers_account
            ON custom_poi_layers(account_id, is_active);

        CREATE TABLE IF NOT EXISTS custom_poi_points (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL,
            layer_id    INTEGER NOT NULL,
            name        TEXT    NOT NULL DEFAULT '',
            brand       TEXT    NOT NULL DEFAULT '',
            lat         REAL    NOT NULL,
            lng         REAL    NOT NULL,
            properties  TEXT    NOT NULL DEFAULT '',
            FOREIGN KEY (layer_id) REFERENCES custom_poi_layers(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_custom_poi_points_layer
            ON custom_poi_points(layer_id, account_id);
        CREATE INDEX IF NOT EXISTS idx_custom_poi_points_bbox
            ON custom_poi_points(layer_id, lat, lng);
    """)
    await conn.commit()
    logger.info("Migration: created custom_poi_layers + custom_poi_points tables")
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


async def migrate_score_rules_pillar_curves(conn) -> None:
    """Add pillar + curve anchor columns to score_rules (Audit Option C).

    Idempotent: checks PRAGMA table_info before each ALTER. All four
    columns are nullable / default empty so legacy rows continue to
    behave identically — engine falls back to the flat path when curve
    fields are NULL.
    """
    cur = await conn.execute("PRAGMA table_info(score_rules)")
    cols = {r[1] for r in await cur.fetchall()}
    new_cols = [
        ("pillar",       "TEXT NOT NULL DEFAULT ''"),
        ("curve_x_zero", "REAL"),
        ("curve_x_max",  "REAL"),
        ("curve_y_max",  "INTEGER"),
    ]
    for name, ddl in new_cols:
        if name in cols:
            continue
        try:
            await conn.execute(f"ALTER TABLE score_rules ADD COLUMN {name} {ddl}")
            await conn.commit()
            logger.info("Migration: added score_rules.%s", name)
        except Exception:
            logger.exception("Failed to add score_rules.%s", name)


async def migrate_warehouse_tables(conn) -> None:
    """Phase C — create the four telemetry warehouse tables on existing
    tenant DBs.  ``tenant_schema.create_tables`` already creates them
    for fresh DBs; this fills the gap for tenants that were initialised
    before Phase C landed.

    Idempotent: each ``CREATE TABLE IF NOT EXISTS`` is a no-op on DBs
    that already saw create_tables() in this release.  The migration
    exists for the brief window where an older tenant DB might be
    opened by a Phase-C build before the schema script has run end to
    end (e.g. a long-lived connection or a migration ordering bug).
    """
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS vehicle_state (
            vehicle_id          TEXT    NOT NULL PRIMARY KEY,
            account_id          INTEGER NOT NULL,
            vehicle_name        TEXT    NOT NULL DEFAULT '',
            company_code        TEXT    NOT NULL DEFAULT '',
            lat                 REAL,
            lon                 REAL,
            speed_mph           REAL,
            heading             REAL,
            address             TEXT    NOT NULL DEFAULT '',
            engine_state        TEXT    NOT NULL DEFAULT '',
            fuel_pct            REAL,
            def_pct             REAL,
            odometer_mi         REAL,
            fault_count         INTEGER NOT NULL DEFAULT 0,
            dtc_critical_count  INTEGER NOT NULL DEFAULT 0,
            last_driver_id      TEXT    NOT NULL DEFAULT '',
            last_driver_name    TEXT    NOT NULL DEFAULT '',
            captured_at         TEXT    NOT NULL DEFAULT '',
            updated_at          TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_vehicle_state_company
            ON vehicle_state(account_id, company_code);
        CREATE INDEX IF NOT EXISTS idx_vehicle_state_name
            ON vehicle_state(account_id, vehicle_name);

        CREATE TABLE IF NOT EXISTS safety_event_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id          INTEGER NOT NULL,
            samsara_event_id    TEXT    NOT NULL UNIQUE,
            vehicle_id          TEXT    NOT NULL DEFAULT '',
            vehicle_name        TEXT    NOT NULL DEFAULT '',
            driver_id           TEXT    NOT NULL DEFAULT '',
            driver_name         TEXT    NOT NULL DEFAULT '',
            event_type          TEXT    NOT NULL DEFAULT '',
            severity            TEXT    NOT NULL DEFAULT '',
            occurred_at         TEXT    NOT NULL DEFAULT '',
            lat                 REAL,
            lon                 REAL,
            speed_mph           REAL,
            video_url           TEXT    NOT NULL DEFAULT '',
            raw_json            TEXT    NOT NULL DEFAULT '',
            ingested_at         TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_safety_event_log_occurred
            ON safety_event_log(account_id, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_safety_event_log_vehicle
            ON safety_event_log(account_id, vehicle_id, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_safety_event_log_vehicle_name
            ON safety_event_log(account_id, vehicle_name, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_safety_event_log_driver
            ON safety_event_log(account_id, driver_id, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_safety_event_log_type
            ON safety_event_log(account_id, event_type, occurred_at DESC);

        CREATE TABLE IF NOT EXISTS driver_efficiency_daily (
            account_id      INTEGER NOT NULL,
            driver_id       TEXT    NOT NULL,
            driver_name     TEXT    NOT NULL DEFAULT '',
            day             TEXT    NOT NULL,
            miles           REAL    NOT NULL DEFAULT 0,
            drive_h         REAL    NOT NULL DEFAULT 0,
            idle_h          REAL    NOT NULL DEFAULT 0,
            mpg             REAL,
            antic_pct       REAL,
            green_pct       REAL,
            harsh_brake     INTEGER NOT NULL DEFAULT 0,
            harsh_turn      INTEGER NOT NULL DEFAULT 0,
            harsh_accel     INTEGER NOT NULL DEFAULT 0,
            overspeed_min   REAL    NOT NULL DEFAULT 0,
            raw_json        TEXT    NOT NULL DEFAULT '',
            ingested_at     TEXT    NOT NULL DEFAULT '',
            PRIMARY KEY (account_id, driver_id, day)
        );
        CREATE INDEX IF NOT EXISTS idx_driver_eff_daily_day
            ON driver_efficiency_daily(account_id, day);

        CREATE TABLE IF NOT EXISTS vehicle_telemetry_hourly (
            account_id          INTEGER NOT NULL,
            vehicle_id          TEXT    NOT NULL,
            hour_utc            TEXT    NOT NULL,
            miles               REAL    NOT NULL DEFAULT 0,
            drive_min           REAL    NOT NULL DEFAULT 0,
            idle_min            REAL    NOT NULL DEFAULT 0,
            max_speed_mph       REAL    NOT NULL DEFAULT 0,
            harsh_event_count   INTEGER NOT NULL DEFAULT 0,
            ingested_at         TEXT    NOT NULL DEFAULT '',
            PRIMARY KEY (account_id, vehicle_id, hour_utc)
        );
        CREATE INDEX IF NOT EXISTS idx_vehicle_tel_hourly_hour
            ON vehicle_telemetry_hourly(account_id, hour_utc);
    """)
    await conn.commit()
    logger.info("Migration: warehouse tables ensured")


async def migrate_alert_ack_indexes(conn) -> None:
    """Add composite indexes that match how `pending_alerts` reads the table.

    The miniapp's badge-count poll and the dashboard pending list both query
    ``alert_acknowledgments`` by ``status='active'`` ordered by ``created_at``,
    and (for driver isolation) by ``vehicle_id``.  Without these indexes
    SQLite falls back to a full scan once the table grows.
    """
    await conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_alert_ack_status_created
            ON alert_acknowledgments(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_alert_ack_vehicle_status
            ON alert_acknowledgments(vehicle_id, status);
        -- Composite for the bulk-lookup paths in send_alert
        -- (get_active_vehicle_acks_bulk + get_info_alert_acks_bulk).
        CREATE INDEX IF NOT EXISTS idx_alert_ack_acct_veh
            ON alert_acknowledgments(account_id, vehicle_id, status);
    """)
    await conn.commit()
    logger.info("Migration: alert_acknowledgments composite indexes ensured")


async def migrate_rename_fleet_rule_ids(conn) -> None:
    """Rename legacy ``fleet.*`` score rule IDs to neutral namespaces.

    Rule IDs are stored in ``score_rules`` only when an admin has explicitly
    overridden them.  Most accounts will have zero rows to update.  The
    rename is idempotent — re-running when the new IDs already exist is safe
    because each UPDATE only touches the exact old ID string.
    """
    renames = [
        ("fleet.fuel_anomaly",       "efficiency.fuel_anomaly"),
        ("fleet.pti_on_time",        "compliance.pti_on_time"),
        ("fleet.pti_overdue",        "compliance.pti_overdue"),
        ("fleet.maintenance_overdue","compliance.maintenance_overdue"),
        ("fleet.camera_clean",       "compliance.camera_clean"),
        ("fleet.camera_obstructed",  "compliance.camera_obstructed"),
        ("fleet.fuel_logged",        "compliance.fuel_logged"),
        ("fleet.fleet_health_clean", "compliance.vehicle_health_clean"),
        ("fleet.active_dtc",         "compliance.active_dtc"),
        ("fleet.health_critical",    "compliance.health_critical"),
        ("fleet.health_minor",       "compliance.health_minor"),
    ]
    for old_id, new_id in renames:
        await conn.execute(
            "UPDATE score_rules SET rule_id = ? WHERE rule_id = ?",
            (new_id, old_id),
        )
    await conn.commit()
    logger.info("Migration: fleet.* score rule IDs renamed to compliance.*/efficiency.*")


async def migrate_add_vehicle_health_snapshot(conn) -> None:
    """Phase 2 — current vehicle-health snapshot table.

    Backs the warehouse-routed ``capabilities/telemetry/service.py``
    ``get_vehicle_health()`` so the dashboard / bot stop hammering
    Samsara for /fleet/vehicles/stats every page-view.

    Idempotent: ``IF NOT EXISTS`` on table + indexes.
    """
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS vehicle_health_snapshot (
            vehicle_id      TEXT    NOT NULL PRIMARY KEY,
            account_id      INTEGER NOT NULL,
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            company_code    TEXT    NOT NULL DEFAULT '',
            alert_count     INTEGER NOT NULL DEFAULT 0,
            raw_json        TEXT    NOT NULL DEFAULT '',
            captured_at     TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_vehicle_health_company
            ON vehicle_health_snapshot(account_id, company_code);
        CREATE INDEX IF NOT EXISTS idx_vehicle_health_name
            ON vehicle_health_snapshot(account_id, vehicle_name);
    """)
    await conn.commit()
    logger.info("Migration: vehicle_health_snapshot ensured")


async def migrate_add_fault_weather_efficiency(conn) -> None:
    """Phase 2 — fault snapshot + per-DTC detail (with cleared_at lifecycle)
    + fleet weather snapshot + fleet efficiency snapshot.  All are
    idempotent ``CREATE TABLE IF NOT EXISTS`` so re-running is a no-op.
    """
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS vehicle_fault_snapshot (
            vehicle_id      TEXT    NOT NULL PRIMARY KEY,
            account_id      INTEGER NOT NULL,
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            company_code    TEXT    NOT NULL DEFAULT '',
            dtc_count       INTEGER NOT NULL DEFAULT 0,
            has_critical    INTEGER NOT NULL DEFAULT 0,
            raw_json        TEXT    NOT NULL DEFAULT '',
            captured_at     TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_vehicle_fault_company
            ON vehicle_fault_snapshot(account_id, company_code);
        CREATE INDEX IF NOT EXISTS idx_vehicle_fault_critical
            ON vehicle_fault_snapshot(account_id, has_critical);

        CREATE TABLE IF NOT EXISTS vehicle_fault_detail (
            account_id      INTEGER NOT NULL,
            vehicle_id      TEXT    NOT NULL,
            dtc_id          TEXT    NOT NULL,
            spn             INTEGER,
            fmi             INTEGER,
            description     TEXT    NOT NULL DEFAULT '',
            severity        TEXT    NOT NULL DEFAULT '',
            observed_at     TEXT    NOT NULL DEFAULT '',
            cleared_at      TEXT,
            raw_json        TEXT    NOT NULL DEFAULT '',
            PRIMARY KEY (account_id, vehicle_id, dtc_id)
        );
        CREATE INDEX IF NOT EXISTS idx_fault_detail_active
            ON vehicle_fault_detail(account_id, vehicle_id, cleared_at);
        CREATE INDEX IF NOT EXISTS idx_fault_detail_observed
            ON vehicle_fault_detail(account_id, observed_at DESC);

        CREATE TABLE IF NOT EXISTS fleet_weather_snapshot (
            vehicle_id      TEXT    NOT NULL PRIMARY KEY,
            account_id      INTEGER NOT NULL,
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            company_code    TEXT    NOT NULL DEFAULT '',
            temp_f          REAL,
            raw_json        TEXT    NOT NULL DEFAULT '',
            captured_at     TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_fleet_weather_company
            ON fleet_weather_snapshot(account_id, company_code);

        CREATE TABLE IF NOT EXISTS fleet_efficiency_snapshot (
            account_id      INTEGER NOT NULL,
            window_days     INTEGER NOT NULL,
            company_code    TEXT    NOT NULL DEFAULT '',
            payload_json    TEXT    NOT NULL DEFAULT '',
            captured_at     TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT '',
            PRIMARY KEY (account_id, window_days, company_code)
        );
    """)
    await conn.commit()
    logger.info("Migration: fault/weather/efficiency snapshot tables ensured")


async def migrate_add_geofence_definitions(conn) -> None:
    """Phase 4 — geofence definitions cache (rarely changing, hourly ingest).
    Idempotent ``CREATE TABLE IF NOT EXISTS``.
    """
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS geofence_definitions (
            geofence_id     TEXT    NOT NULL PRIMARY KEY,
            account_id      INTEGER NOT NULL,
            company_code    TEXT    NOT NULL DEFAULT '',
            name            TEXT    NOT NULL DEFAULT '',
            geofence_type   TEXT    NOT NULL DEFAULT '',
            raw_json        TEXT    NOT NULL DEFAULT '',
            captured_at     TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_geofence_definitions_company
            ON geofence_definitions(account_id, company_code);
    """)
    await conn.commit()
    logger.info("Migration: geofence_definitions ensured")


async def migrate_add_payroll_tables(conn) -> None:
    """Create the 4 payroll tables on existing tenant DBs (Phase 2 P4P).

    Idempotent — uses CREATE TABLE IF NOT EXISTS.
    """
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS bonus_rules (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                name            TEXT    NOT NULL,
                kind            TEXT    NOT NULL DEFAULT 'score_threshold',
                score_min       REAL,
                event_type      TEXT,
                max_count       INTEGER,
                period_days     INTEGER NOT NULL DEFAULT 30,
                amount_cents    INTEGER NOT NULL DEFAULT 0,
                active          INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT    NOT NULL DEFAULT '',
                updated_at      TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_bonus_rules_account
                ON bonus_rules(account_id, active);

            CREATE TABLE IF NOT EXISTS driver_pay_settings (
                account_id      INTEGER NOT NULL,
                driver_id       TEXT    NOT NULL,
                base_pay_cents  INTEGER NOT NULL DEFAULT 0,
                opt_in          INTEGER NOT NULL DEFAULT 1,
                updated_at      TEXT    NOT NULL DEFAULT '',
                PRIMARY KEY (account_id, driver_id)
            );

            CREATE TABLE IF NOT EXISTS payroll_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                period_start    TEXT    NOT NULL,
                period_end      TEXT    NOT NULL,
                status          TEXT    NOT NULL DEFAULT 'draft',
                created_by      INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT    NOT NULL DEFAULT '',
                finalized_at    TEXT,
                total_cents     INTEGER NOT NULL DEFAULT 0,
                UNIQUE(account_id, period_start, period_end)
            );
            CREATE INDEX IF NOT EXISTS idx_payroll_runs_account
                ON payroll_runs(account_id, period_start);

            CREATE TABLE IF NOT EXISTS payroll_run_items (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id            INTEGER NOT NULL,
                driver_id         TEXT    NOT NULL,
                driver_name       TEXT    NOT NULL DEFAULT '',
                base_pay_cents    INTEGER NOT NULL DEFAULT 0,
                bonus_total_cents INTEGER NOT NULL DEFAULT 0,
                total_cents       INTEGER NOT NULL DEFAULT 0,
                breakdown_json    TEXT    NOT NULL DEFAULT '[]',
                created_at        TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_payroll_items_run
                ON payroll_run_items(run_id);
            CREATE INDEX IF NOT EXISTS idx_payroll_items_driver
                ON payroll_run_items(driver_id);
        """)
        await conn.commit()
        logger.info("Migration: payroll tables ensured")
    except Exception as e:
        logger.error("Payroll tables migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_add_coaching_tables(conn) -> None:
    """Create the 4 coaching tables on existing tenant DBs (Phase 3 Auto Coaching).

    Idempotent — uses CREATE TABLE IF NOT EXISTS.  Also seeds the default
    topic catalogue per (account_id) lazily — engine.evaluate handles
    seeding for known account ids when first invoked.
    """
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS coaching_topics (
                account_id       INTEGER NOT NULL,
                key              TEXT    NOT NULL,
                label            TEXT    NOT NULL DEFAULT '',
                default_message  TEXT    NOT NULL DEFAULT '',
                active           INTEGER NOT NULL DEFAULT 1,
                updated_at       TEXT    NOT NULL DEFAULT '',
                PRIMARY KEY (account_id, key)
            );

            CREATE TABLE IF NOT EXISTS coaching_rules (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                name            TEXT    NOT NULL,
                kind            TEXT    NOT NULL DEFAULT 'score_threshold',
                score_max       REAL,
                event_type      TEXT,
                min_count       INTEGER,
                period_days     INTEGER NOT NULL DEFAULT 7,
                topic_key       TEXT    NOT NULL DEFAULT '',
                severity        TEXT    NOT NULL DEFAULT 'medium',
                message         TEXT    NOT NULL DEFAULT '',
                active          INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT    NOT NULL DEFAULT '',
                updated_at      TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_coaching_rules_account
                ON coaching_rules(account_id, active);

            CREATE TABLE IF NOT EXISTS coaching_assignments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                driver_id       TEXT    NOT NULL,
                rule_id         INTEGER,
                topic_key       TEXT    NOT NULL DEFAULT '',
                severity        TEXT    NOT NULL DEFAULT 'medium',
                reason          TEXT    NOT NULL DEFAULT '',
                status          TEXT    NOT NULL DEFAULT 'pending',
                assigned_by     INTEGER NOT NULL DEFAULT 0,
                assigned_at     TEXT    NOT NULL DEFAULT '',
                due_at          TEXT,
                acknowledged_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_coaching_assignments_account
                ON coaching_assignments(account_id, status);
            CREATE INDEX IF NOT EXISTS idx_coaching_assignments_driver
                ON coaching_assignments(account_id, driver_id, status);

            CREATE TABLE IF NOT EXISTS coaching_acknowledgments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                assignment_id   INTEGER NOT NULL,
                driver_id       TEXT    NOT NULL,
                acked_at        TEXT    NOT NULL DEFAULT '',
                note            TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_coaching_acks_assignment
                ON coaching_acknowledgments(assignment_id);
        """)
        await conn.commit()
        logger.info("Migration: coaching tables ensured")
    except Exception as e:
        logger.error("Coaching tables migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass

async def migrate_add_vehicle_state_odometer_time(conn) -> None:
    """Add vehicle_state.odometer_time so the warehouse persists the
    timestamp Samsara reports alongside the odometer reading.  Used by
    the maintenance-progress UI to show how fresh the value is."""
    try:
        cur = await conn.execute("PRAGMA table_info(vehicle_state)")
        cols = {r[1] for r in await cur.fetchall()}
        if "odometer_time" not in cols:
            await conn.execute(
                "ALTER TABLE vehicle_state ADD COLUMN odometer_time TEXT"
            )
            await conn.commit()
            logger.info("Migration: added vehicle_state.odometer_time")
    except Exception as e:
        logger.error("vehicle_state.odometer_time migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_add_alert_mutes_table(conn) -> None:
    """Per-alert mute table — see tenant_schema.py for column rationale."""
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS alert_mutes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id        INTEGER NOT NULL,
                alert_history_id  INTEGER NOT NULL,
                muted_by          BIGINT  NOT NULL,
                scope             TEXT    NOT NULL DEFAULT 'all_recipients',
                recipient_id      BIGINT,
                reason            TEXT    NOT NULL DEFAULT '',
                muted_until       TEXT    NOT NULL,
                created_at        TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_alert_mutes_active
                ON alert_mutes(account_id, alert_history_id, muted_until);
        """)
        await conn.commit()
        logger.info("Migration: alert_mutes table ensured")
    except Exception as e:
        logger.error("alert_mutes migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_add_alert_history_reescalate_columns(conn) -> None:
    """Add reescalate_count + reescalate_last_sent_at to alert_history."""
    try:
        cur = await conn.execute("PRAGMA table_info(alert_history)")
        cols = {r[1] for r in await cur.fetchall()}
        if "reescalate_count" not in cols:
            await conn.execute(
                "ALTER TABLE alert_history "
                "ADD COLUMN reescalate_count INTEGER NOT NULL DEFAULT 0"
            )
        if "reescalate_last_sent_at" not in cols:
            await conn.execute(
                "ALTER TABLE alert_history ADD COLUMN reescalate_last_sent_at TEXT"
            )
        await conn.commit()
        logger.info("Migration: alert_history reescalate columns ensured")
    except Exception as e:
        logger.error("alert_history reescalate migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_add_alert_history_severity_location(conn) -> None:
    """Add severity + location to alert_history (tenant DBs)."""
    try:
        cur = await conn.execute("PRAGMA table_info(alert_history)")
        cols = {r[1] for r in await cur.fetchall()}
        if "severity" not in cols:
            await conn.execute(
                "ALTER TABLE alert_history "
                "ADD COLUMN severity TEXT NOT NULL DEFAULT 'warning'"
            )
        if "location" not in cols:
            await conn.execute(
                "ALTER TABLE alert_history "
                "ADD COLUMN location TEXT NOT NULL DEFAULT ''"
            )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_history_active_sort "
            "ON alert_history(account_id, status, severity, last_seen DESC)"
        )
        await conn.commit()
        logger.info("Migration: alert_history severity/location ensured")
    except Exception as e:
        logger.error("alert_history severity/location migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_add_alert_history_subkey(conn) -> None:
    """Add alert_subkey column + per-subkey UNIQUE constraint.

    Previously the unique key was (account_id, alert_type, vehicle_id),
    which meant ALL events on a vehicle (Following Distance, Harsh Brake,
    Lane Departure, …) collapsed into a single alert_history row.  The
    occurrence count therefore reported "any event on this truck" while
    the alert title showed only the latest subtype — confusing UX.

    After this migration, each (alert_type, vehicle, subtype) gets its
    own row.  For events the subkey = event_type; for fault / fuel /
    health it stays '' so their dedup behavior is unchanged.

    Backfill for legacy events rows: extract the subtype from the
    last_detail field (which has format "{event_type}:{event_id}" — see
    pipeline.send_alert callers).  This attributes the existing
    occurrence count to the last-known subtype rather than splitting
    it (the subtype-level history was never recorded).  Imperfect, but
    better than dumping everything into the '' bucket.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(alert_history)")
        cols = {r[1] for r in await cur.fetchall()}
        if "alert_subkey" in cols:
            return  # already migrated

        # Safety: if a prior migration aborted mid-rebuild, the temp
        # table from that run will still exist and the RENAME below
        # would fail with "already exists".  Drop it first so the
        # retry can complete cleanly.
        await conn.execute("DROP TABLE IF EXISTS _alert_history_subkey_old")
        # SQLite can't ALTER a UNIQUE constraint, and we want both column
        # add + constraint update in one atomic step.  Rebuild via
        # rename → create new → copy → drop old.
        await conn.execute("ALTER TABLE alert_history RENAME TO _alert_history_subkey_old")
        await conn.execute("""
            CREATE TABLE alert_history (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id              INTEGER NOT NULL,
                alert_type              TEXT    NOT NULL,
                vehicle_id              TEXT    NOT NULL,
                vehicle_name            TEXT    NOT NULL DEFAULT '',
                chat_id                 BIGINT  NOT NULL DEFAULT 0,
                message_id              BIGINT  NOT NULL DEFAULT 0,
                occurrence_count        INTEGER NOT NULL DEFAULT 1,
                first_seen              TEXT    NOT NULL,
                last_seen               TEXT    NOT NULL,
                last_detail             TEXT    NOT NULL DEFAULT '',
                status                  TEXT    NOT NULL DEFAULT 'active',
                severity                TEXT    NOT NULL DEFAULT 'warning',
                location                TEXT    NOT NULL DEFAULT '',
                reescalate_count        INTEGER NOT NULL DEFAULT 0,
                reescalate_last_sent_at TEXT,
                alert_subkey            TEXT    NOT NULL DEFAULT '',
                UNIQUE(account_id, alert_type, vehicle_id, alert_subkey)
            )
        """)
        # Backfill: attribute legacy events rows to their last-recorded
        # subtype using the "{event_type}:{event_id}" detail pattern.
        await conn.execute("""
            INSERT INTO alert_history (
                id, account_id, alert_type, vehicle_id, vehicle_name,
                chat_id, message_id, occurrence_count,
                first_seen, last_seen, last_detail, status,
                severity, location, reescalate_count, reescalate_last_sent_at,
                alert_subkey
            )
            SELECT
                id, account_id, alert_type, vehicle_id, vehicle_name,
                chat_id, message_id, occurrence_count,
                first_seen, last_seen, last_detail, status,
                severity, location, reescalate_count, reescalate_last_sent_at,
                CASE
                    WHEN alert_type = 'events' AND instr(last_detail, ':') > 0
                    THEN substr(last_detail, 1, instr(last_detail, ':') - 1)
                    ELSE ''
                END
            FROM _alert_history_subkey_old
        """)
        await conn.execute("DROP TABLE _alert_history_subkey_old")
        # Re-create the active-sort index lost during the rebuild.
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_history_active_sort "
            "ON alert_history(account_id, status, severity, last_seen DESC)"
        )
        # Drop the legacy 4-column index and recreate with alert_subkey
        # in the prefix — every active-history lookup filters on subkey
        # too, so missing it forced a seq-scan over the partial match.
        await conn.execute("DROP INDEX IF EXISTS idx_alert_history_active")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_history_active "
            "ON alert_history(account_id, alert_type, vehicle_id, alert_subkey, status)"
        )
        await conn.commit()
        logger.info("Migration: alert_history alert_subkey added + UNIQUE updated")
    except Exception as e:
        logger.error("alert_history subkey migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_alert_history_subkey_index(conn) -> None:
    """Replace the legacy ``idx_alert_history_active`` (4 columns) with
    a 5-column variant that includes ``alert_subkey``.

    The subkey column was added by ``migrate_add_alert_history_subkey``
    but the index recreated at the end of that migration didn't include
    it. ``get_active_alert_history()`` filters on
    ``(account_id, alert_type, vehicle_id, alert_subkey, status)`` so
    every alert delivery scans rows on the trailing predicate. This
    follow-up rebuilds the index for accounts already migrated; new
    accounts pick up the correct shape from ``tenant_schema``.
    """
    try:
        cur = await conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'index' AND name = 'idx_alert_history_active'"
        )
        row = await cur.fetchone()
        if row and "alert_subkey" in (row[0] or ""):
            return  # already correct
        await conn.execute("DROP INDEX IF EXISTS idx_alert_history_active")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_history_active "
            "ON alert_history(account_id, alert_type, vehicle_id, alert_subkey, status)"
        )
        await conn.commit()
        logger.info("Migration: idx_alert_history_active extended with alert_subkey")
    except Exception as e:
        logger.error("alert_history subkey index migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass
