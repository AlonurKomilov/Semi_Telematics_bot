"""Schema migrations — each adds columns / tables introduced after v1.

Migrations are tracked in a _schema_versions table so they only run once
per database, not on every startup. This is critical for 500+ tenants
where startup time would otherwise grow linearly.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Ordered list of (version_name, migration_function) pairs.
# New migrations MUST be appended at the end — never reorder.
_MIGRATIONS: list[tuple[str, ...]] = []


def _register(name: str):
    """Decorator to register a migration function by name."""
    def decorator(fn):
        _MIGRATIONS.append((name, fn))
        return fn
    return decorator


async def _ensure_version_table(conn) -> None:
    """Create the migration tracking table if it doesn't exist."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS _schema_versions (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)
    await conn.commit()


async def _is_applied(conn, version: str) -> bool:
    """Check if a migration version has already been applied."""
    cur = await conn.execute(
        "SELECT 1 FROM _schema_versions WHERE version = ?", (version,)
    )
    return (await cur.fetchone()) is not None


async def _mark_applied(conn, version: str) -> None:
    """Record that a migration was successfully applied."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT OR IGNORE INTO _schema_versions (version, applied_at) VALUES (?, ?)",
        (version, now),
    )
    await conn.commit()


async def run_all(conn) -> None:
    """Execute pending migrations only.  Called after schema.create_tables()."""
    await _ensure_version_table(conn)

    applied = 0
    skipped = 0
    for version, migrate_fn in _MIGRATIONS:
        if await _is_applied(conn, version):
            skipped += 1
            continue
        try:
            await migrate_fn(conn)
            await _mark_applied(conn, version)
            applied += 1
        except Exception:
            logger.exception("Migration '%s' failed", version)
            raise  # Don't continue with a broken schema

    if applied:
        logger.info("Ran %d migration(s), skipped %d already applied", applied, skipped)
    elif skipped:
        logger.debug("All %d migrations already applied", skipped)


@_register("001_alert_prefs")
async def migrate_alert_prefs(conn) -> None:
    """Add alert_faults/health/fuel/geofence columns if missing."""
    new_cols = [
        ("alert_faults", "INTEGER NOT NULL DEFAULT 1"),
        ("alert_health", "INTEGER NOT NULL DEFAULT 1"),
        ("alert_fuel", "INTEGER NOT NULL DEFAULT 1"),
        ("alert_geofence", "INTEGER NOT NULL DEFAULT 1"),
        ("ai_fault", "INTEGER NOT NULL DEFAULT 0"),
        ("ai_health", "INTEGER NOT NULL DEFAULT 0"),
        ("ai_fuel", "INTEGER NOT NULL DEFAULT 0"),
        ("alert_events", "INTEGER NOT NULL DEFAULT 1"),
        ("ai_events", "INTEGER NOT NULL DEFAULT 0"),
        ("alert_parking", "INTEGER NOT NULL DEFAULT 1"),
        ("ai_parking", "INTEGER NOT NULL DEFAULT 0"),
        ("alert_camera", "INTEGER NOT NULL DEFAULT 1"),
    ]
    for col_name, col_def in new_cols:
        try:
            await conn.execute(
                f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"
            )
            await conn.commit()
            logger.info(f"Added column users.{col_name}")
        except Exception:
            pass  # column already exists


@_register("002_user_quiet_hours")
async def migrate_user_quiet_hours(conn) -> None:
    """Add quiet_start, quiet_end, timezone columns to users."""
    new_cols = [
        ("quiet_start", "INTEGER"),           # hour 0-23, NULL = no DND
        ("quiet_end", "INTEGER"),             # hour 0-23
        ("timezone", "TEXT NOT NULL DEFAULT 'America/New_York'"),
    ]
    for col_name, col_def in new_cols:
        try:
            await conn.execute(
                f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"
            )
            await conn.commit()
            logger.info(f"Added column users.{col_name}")
        except Exception:
            pass


@_register("003_digest_timezone")
async def migrate_digest_timezone(conn) -> None:
    """Ensure digest_subscriptions has timezone column."""
    try:
        await conn.execute(
            "ALTER TABLE digest_subscriptions ADD COLUMN timezone TEXT NOT NULL DEFAULT 'America/New_York'"
        )
        await conn.commit()
        logger.info("Added column digest_subscriptions.timezone")
    except Exception:
        pass


@_register("004_digest_report_type")
async def migrate_digest_report_type(conn) -> None:
    """Ensure digest_subscriptions has report_type column."""
    try:
        await conn.execute(
            "ALTER TABLE digest_subscriptions ADD COLUMN report_type TEXT NOT NULL DEFAULT 'faults'"
        )
        await conn.commit()
        logger.info("Added column digest_subscriptions.report_type")
    except Exception:
        pass


@_register("005_user_display_name")
async def migrate_user_display_name(conn) -> None:
    """Add display_name column to users table."""
    try:
        await conn.execute(
            "ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"
        )
        await conn.commit()
        logger.info("Added column users.display_name")
    except Exception:
        pass


@_register("006_alert_ack_status")
async def migrate_alert_ack_status(conn) -> None:
    """Add status column to alert_acknowledgments table."""
    try:
        await conn.execute(
            "ALTER TABLE alert_acknowledgments ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
        )
        await conn.commit()
        logger.info("Added column alert_acknowledgments.status")
    except Exception:
        pass  # column already exists


@_register("007_user_language")
async def migrate_user_language(conn) -> None:
    """Add language column to users table."""
    try:
        await conn.execute(
            "ALTER TABLE users ADD COLUMN language TEXT NOT NULL DEFAULT 'en'"
        )
        await conn.commit()
        logger.info("Added column users.language")
    except Exception:
        pass  # column already exists


@_register("008_camera_checks_table")
async def migrate_camera_checks_table(conn) -> None:
    """Create camera_checks table for history tracking."""
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS camera_checks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id    INTEGER NOT NULL REFERENCES accounts(id),
                vehicle_id    TEXT    NOT NULL DEFAULT '',
                vehicle_name  TEXT    NOT NULL DEFAULT '',
                camera_type   TEXT    NOT NULL DEFAULT 'forward',
                status        TEXT    NOT NULL DEFAULT 'OK',
                obstruction   TEXT    NOT NULL DEFAULT 'none',
                alignment     TEXT    NOT NULL DEFAULT 'centered',
                quality       TEXT    NOT NULL DEFAULT 'good',
                summary       TEXT    NOT NULL DEFAULT '',
                checked_at    TEXT    NOT NULL
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_camera_checks_account
                ON camera_checks(account_id, checked_at DESC)
        """)
        await conn.commit()
    except Exception:
        pass  # already exists


@_register("009_parking_events_table")
async def migrate_parking_events_table(conn) -> None:
    """Create parking_events table for unsafe parking detection."""
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS parking_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL REFERENCES accounts(id),
                vehicle_id      TEXT    NOT NULL DEFAULT '',
                vehicle_name    TEXT    NOT NULL DEFAULT '',
                company_code    TEXT    NOT NULL DEFAULT '',
                latitude        REAL    NOT NULL DEFAULT 0,
                longitude       REAL    NOT NULL DEFAULT 0,
                address         TEXT    NOT NULL DEFAULT '',
                first_stopped   TEXT    NOT NULL,
                duration_hours  REAL    NOT NULL DEFAULT 0,
                location_class  TEXT    NOT NULL DEFAULT 'unknown',
                alert_level     TEXT    NOT NULL DEFAULT 'none',
                ai_analysis     TEXT    NOT NULL DEFAULT '',
                resolved        INTEGER NOT NULL DEFAULT 0,
                last_checked    TEXT    NOT NULL,
                created_at      TEXT    NOT NULL
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_parking_events_active
                ON parking_events(account_id, vehicle_id, resolved)
        """)
        await conn.commit()
    except Exception:
        pass  # already exists


@_register("010_parking_events_created_at")
async def migrate_parking_events_created_at(conn) -> None:
    """Add created_at column to parking_events if it was created without it."""
    try:
        await conn.execute(
            "ALTER TABLE parking_events ADD COLUMN created_at TEXT NOT NULL DEFAULT ''"
        )
        await conn.commit()
        logger.info("Added created_at column to parking_events")
    except Exception:
        pass  # column already exists


@_register("011_maintenance_recurring")
async def migrate_maintenance_recurring(conn) -> None:
    """Add recur_interval_days and recur_interval_miles to maintenance_tasks."""
    for col_name, col_def in [
        ("recur_interval_days", "INTEGER"),
        ("recur_interval_miles", "REAL"),
    ]:
        try:
            await conn.execute(
                f"ALTER TABLE maintenance_tasks ADD COLUMN {col_name} {col_def}"
            )
            await conn.commit()
            logger.info(f"Added column maintenance_tasks.{col_name}")
        except Exception:
            pass  # column already exists


@_register("012_work_schedules_table")
async def migrate_work_schedules_table(conn) -> None:
    """Create work_schedules table for admin-defined working hour presets."""
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS work_schedules (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                label       TEXT    NOT NULL,
                start_hour  INTEGER NOT NULL,
                end_hour    INTEGER NOT NULL,
                target_role TEXT    NOT NULL DEFAULT 'all',
                created_by  BIGINT  NOT NULL,
                created_at  TEXT    NOT NULL
            )
        """)
        await conn.commit()
    except Exception:
        pass  # already exists


async def migrate_encrypt_api_keys(conn) -> int:
    """Encrypt all plaintext API keys in the companies table.

    Skips keys that are already encrypted (start with 'enc::').
    Returns the number of keys that were encrypted.
    """
    from infra.crypto import is_enabled, encrypt as _encrypt, _ENC_PREFIX

    if not is_enabled():
        logger.info("Encryption not enabled — skipping key migration")
        return 0

    cur = await conn.execute("SELECT id, samsara_api_key FROM companies")
    rows = await cur.fetchall()
    count = 0
    for row in rows:
        raw = row["samsara_api_key"]
        if raw.startswith(_ENC_PREFIX):
            continue  # already encrypted
        encrypted = _encrypt(raw)
        await conn.execute(
            "UPDATE companies SET samsara_api_key = ? WHERE id = ?",
            (encrypted, row["id"]),
        )
        count += 1
    if count:
        await conn.commit()
        logger.info(f"Encrypted {count} plaintext API key(s)")
    return count


@_register("014_user_last_shift_report")
async def migrate_user_last_shift_report(conn) -> None:
    """Add last_shift_report column to users table for duplicate prevention."""
    try:
        await conn.execute(
            "ALTER TABLE users ADD COLUMN last_shift_report TEXT"
        )
        await conn.commit()
        logger.info("Added column users.last_shift_report")
    except Exception:
        pass  # column already exists


@_register("015_user_email_password")
async def migrate_user_email_password(conn) -> None:
    """Add email + password_hash columns for dashboard login."""
    for col_name, col_def in [
        ("email", "TEXT"),
        ("password_hash", "TEXT"),
    ]:
        try:
            await conn.execute(
                f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"
            )
            await conn.commit()
            logger.info(f"Added column users.{col_name}")
        except Exception:
            pass  # already exists
    # Unique index on email (only for non-NULL values)
    try:
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email "
            "ON users(email) WHERE email IS NOT NULL"
        )
        await conn.commit()
    except Exception:
        pass


@_register("016_parking_map_image_path")
async def migrate_parking_map_image_path(conn) -> None:
    """Add map_image_path column to parking_events for AI map screenshots."""
    try:
        await conn.execute(
            "ALTER TABLE parking_events ADD COLUMN map_image_path TEXT NOT NULL DEFAULT ''"
        )
        await conn.commit()
        logger.info("Added column parking_events.map_image_path")
    except Exception:
        pass  # already exists


@_register("017_camera_image_path")
async def migrate_camera_image_path(conn) -> None:
    """Add image_path column to camera_checks for storing dashcam screenshots."""
    try:
        await conn.execute(
            "ALTER TABLE camera_checks ADD COLUMN image_path TEXT NOT NULL DEFAULT ''"
        )
        await conn.commit()
        logger.info("Added column camera_checks.image_path")
    except Exception:
        pass  # already exists


@_register("018_add_bot_columns")
async def migrate_add_bot_columns(conn) -> None:
    """Add bot_token_encrypted, bot_username, webhook_secret to accounts table.

    Idempotent — skips columns that already exist.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(accounts)")
        existing = {r[1] for r in await cur.fetchall()}

        new_cols = [
            ("bot_token_encrypted", "TEXT"),
            ("bot_username", "TEXT NOT NULL DEFAULT ''"),
            ("webhook_secret", "TEXT NOT NULL DEFAULT ''"),
        ]
        added = False
        for col_name, col_def in new_cols:
            if col_name not in existing:
                await conn.execute(
                    f"ALTER TABLE accounts ADD COLUMN {col_name} {col_def}"
                )
                logger.info("Added accounts.%s column", col_name)
                added = True

        if added:
            await conn.commit()
    except Exception as e:
        logger.error("Bot columns migration failed: %s", e)


@_register("019_knowledge_base_table")
async def migrate_knowledge_base_table(conn) -> None:
    """Create knowledge_base table if it doesn't exist, add new columns."""
    try:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_base'"
        )
        if not await cur.fetchone():
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_base (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id      INTEGER NOT NULL,
                    title           TEXT    NOT NULL,
                    description     TEXT    NOT NULL DEFAULT '',
                    category        TEXT    NOT NULL DEFAULT 'general',
                    media_url       TEXT    NOT NULL DEFAULT '',
                    media_type      TEXT    NOT NULL DEFAULT 'link',
                    tags            TEXT    NOT NULL DEFAULT '',
                    visibility      TEXT    NOT NULL DEFAULT 'private',
                    target_role     TEXT    NOT NULL DEFAULT 'all',
                    pinned          INTEGER NOT NULL DEFAULT 0,
                    created_by      BIGINT  NOT NULL DEFAULT 0,
                    creator_name    TEXT    NOT NULL DEFAULT '',
                    approved        INTEGER NOT NULL DEFAULT 1,
                    updated_at      TEXT    NOT NULL DEFAULT '',
                    created_at      TEXT    NOT NULL
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_kb_account
                    ON knowledge_base(account_id, category)
            """)
            await conn.commit()
            logger.info("Migration: created knowledge_base table")
            return

        # Table exists — add new columns if missing
        cur = await conn.execute("PRAGMA table_info(knowledge_base)")
        existing = {r[1] for r in await cur.fetchall()}

        if "target_role" not in existing:
            await conn.execute(
                "ALTER TABLE knowledge_base ADD COLUMN target_role TEXT NOT NULL DEFAULT 'all'"
            )
            logger.info("Added knowledge_base.target_role column")

        if "creator_name" not in existing:
            await conn.execute(
                "ALTER TABLE knowledge_base ADD COLUMN creator_name TEXT NOT NULL DEFAULT ''"
            )
            logger.info("Added knowledge_base.creator_name column")

        if "approved" not in existing:
            await conn.execute(
                "ALTER TABLE knowledge_base ADD COLUMN approved INTEGER NOT NULL DEFAULT 1"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_kb_approved ON knowledge_base(approved)"
            )
            logger.info("Added knowledge_base.approved column")

        # Migrate old role-based visibility to private/public model
        old_roles = ("all", "owner", "admin", "fleet", "safety", "dispatcher", "driver")
        for old_val in old_roles:
            if old_val == "all":
                await conn.execute(
                    "UPDATE knowledge_base SET visibility = 'private', target_role = 'all' "
                    "WHERE visibility = ?",
                    (old_val,),
                )
            else:
                await conn.execute(
                    "UPDATE knowledge_base SET target_role = ?, visibility = 'private' "
                    "WHERE visibility = ?",
                    (old_val, old_val),
                )

        # Backfill creator_name from users table
        await conn.execute(
            """UPDATE knowledge_base SET creator_name = (
                SELECT COALESCE(u.display_name, '')
                FROM users u WHERE u.telegram_id = knowledge_base.created_by
               )
               WHERE creator_name = '' AND created_by != 0"""
        )

        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_visibility "
            "ON knowledge_base(visibility, target_role)"
        )

        await conn.commit()
        logger.info("Migration: knowledge_base table updated")
    except Exception as e:
        logger.error("knowledge_base migration failed: %s", e)


@_register("020_seed_role_permissions")
async def migrate_seed_role_permissions(conn) -> None:
    """Seed role_permissions with defaults for every active account."""
    import json
    from dataclasses import asdict
    try:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='role_permissions'"
        )
        if not (await cur.fetchone()):
            return

        from capabilities.permissions.roles import ROLE_PERMISSIONS

        cur = await conn.execute("SELECT id FROM accounts WHERE is_active = 1")
        accounts = [r[0] for r in await cur.fetchall()]
        if not accounts:
            return

        now = __import__("datetime").datetime.utcnow().isoformat()
        inserted = 0
        for acct_id in accounts:
            for role, feature_set in ROLE_PERMISSIONS.items():
                cur2 = await conn.execute(
                    "SELECT 1 FROM role_permissions "
                    "WHERE account_id = ? AND role = ? AND company_id IS NULL",
                    (acct_id, role.value),
                )
                if await cur2.fetchone():
                    continue
                perm_json = json.dumps(asdict(feature_set))
                await conn.execute(
                    "INSERT INTO role_permissions "
                    "(account_id, role, company_id, permissions, updated_by, updated_at) "
                    "VALUES (?, ?, NULL, ?, 0, ?)",
                    (acct_id, role.value, perm_json, now),
                )
                inserted += 1

        await conn.commit()
        if inserted:
            logger.info("Seeded %d role_permissions rows for %d accounts", inserted, len(accounts))
    except Exception as e:
        logger.error("role_permissions seed migration failed: %s", e)


@_register("021_seed_driver_trucks")
async def migrate_seed_driver_trucks(conn) -> None:
    """Migrate existing users.truck_num into the driver_trucks junction table."""
    try:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='driver_trucks'"
        )
        if not (await cur.fetchone()):
            return

        # Find users with truck_num set that don't already have a driver_trucks row
        cur = await conn.execute(
            "SELECT u.id, u.account_id, u.truck_num FROM users u "
            "WHERE u.truck_num IS NOT NULL AND u.truck_num != '' "
            "AND u.is_active = 1 "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM driver_trucks dt WHERE dt.user_id = u.id"
            ")"
        )
        rows = await cur.fetchall()
        if not rows:
            return

        now = __import__("datetime").datetime.utcnow().isoformat()
        inserted = 0
        for user_id, account_id, truck_num in rows:
            await conn.execute(
                "INSERT INTO driver_trucks "
                "(user_id, account_id, truck_num, is_primary, assigned_by, assigned_at) "
                "VALUES (?, ?, ?, 1, 0, ?)",
                (user_id, account_id, truck_num, now),
            )
            inserted += 1

        await conn.commit()
        if inserted:
            logger.info("Migrated %d truck assignments to driver_trucks", inserted)
    except Exception as e:
        logger.error("driver_trucks seed migration failed: %s", e)


@_register("022_user_companies_table")
async def migrate_user_companies_table(conn) -> None:
    """Create user_companies table if it doesn't exist (for pre-Phase-6 databases)."""
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_companies (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                company_id  INTEGER NOT NULL REFERENCES companies(id),
                assigned_by BIGINT  NOT NULL DEFAULT 0,
                assigned_at TEXT    NOT NULL DEFAULT ''
            )
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_user_companies_user_company
                ON user_companies(user_id, company_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_companies_user
                ON user_companies(user_id)
        """)
        await conn.commit()
    except Exception as e:
        logger.debug("user_companies migration skipped: %s", e)


@_register("023_drop_escalation_columns")
async def migrate_drop_escalation_columns(conn) -> None:
    """Drop escalation_level and next_escalation from alert_acknowledgments.

    These columns supported the re-alert/reminder system which has been removed.
    SQLite 3.35+ supports ALTER TABLE DROP COLUMN.
    """
    cur = await conn.execute("PRAGMA table_info(alert_acknowledgments)")
    cols = {r[1] for r in await cur.fetchall()}

    needs_work = "escalation_level" in cols or "next_escalation" in cols

    # Drop the stale index first — it may reference next_escalation, which
    # causes SQLite to reject the subsequent DROP COLUMN with "error in index".
    await conn.execute("DROP INDEX IF EXISTS idx_alert_ack_pending")

    if "escalation_level" in cols:
        await conn.execute(
            "ALTER TABLE alert_acknowledgments DROP COLUMN escalation_level"
        )
    if "next_escalation" in cols:
        await conn.execute(
            "ALTER TABLE alert_acknowledgments DROP COLUMN next_escalation"
        )

    # Always recreate the index in its correct form
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_alert_ack_pending "
        "ON alert_acknowledgments(acknowledged_at)"
    )
    if needs_work:
        await conn.commit()
        logger.info("Migration: dropped escalation columns from alert_acknowledgments")


@_register("024_rename_work_schedules_to_work_hours")
async def migrate_rename_work_schedules_to_work_hours(conn) -> None:
    """Rename work_schedules table to work_hours to match consistent naming."""
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='work_schedules'"
    )
    if await cur.fetchone():
        await conn.execute("ALTER TABLE work_schedules RENAME TO work_hours")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_work_hours_account ON work_hours(account_id)"
        )
        await conn.commit()
        logger.info("Migration: renamed work_schedules → work_hours")


@_register("025_platform_geofences_table")
async def migrate_platform_geofences_table(conn) -> None:
    """Create platform_geofences table in legacy (single-DB) mode."""
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
            zone_role       TEXT    NOT NULL DEFAULT 'all',
            is_active       INTEGER NOT NULL DEFAULT 1,
            created_by      BIGINT  NOT NULL DEFAULT 0,
            created_at      TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, name)
        );
        CREATE INDEX IF NOT EXISTS idx_platform_geofences_account
            ON platform_geofences(account_id, is_active);
    """)
    await conn.commit()
    logger.info("Migration: created platform_geofences table (legacy DB)")


@_register("026_custom_poi_layers")
async def migrate_custom_poi_layers_legacy(conn) -> None:
    """Create custom_poi_layers + custom_poi_points tables.

    Idempotent — falls through silently if the tables already exist.
    """
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
            created_by      BIGINT  NOT NULL DEFAULT 0,
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
    logger.info("Migration: created custom_poi_layers + custom_poi_points tables (legacy DB)")


@_register("027_score_rules_pillar_curves")
async def migrate_score_rules_pillar_curves(conn) -> None:
    """Add pillar + curve columns to score_rules.

    All four columns are nullable / default-empty so existing override
    rows continue to behave exactly as before — the engine falls back
    to the legacy flat ``points × occurrences`` path when curve fields
    are NULL.
    """
    new_cols = [
        ("pillar",       "TEXT NOT NULL DEFAULT ''"),
        ("curve_x_zero", "REAL"),
        ("curve_x_max",  "REAL"),
        ("curve_y_max",  "INTEGER"),
    ]
    cur = await conn.execute("PRAGMA table_info(score_rules)")
    existing = {row[1] for row in await cur.fetchall()}
    for name, ddl in new_cols:
        if name in existing:
            continue
        try:
            await conn.execute(f"ALTER TABLE score_rules ADD COLUMN {name} {ddl}")
            await conn.commit()
            logger.info("Added column score_rules.%s", name)
        except Exception:
            logger.exception("Failed to add score_rules.%s", name)


@_register("028_warehouse_tables")
async def migrate_warehouse_tables(conn) -> None:
    """Create the telemetry warehouse tables.

    Backs ``WarehouseMixin`` — vehicle_state, safety_event_log,
    driver_efficiency_daily, vehicle_telemetry_hourly.  Without this
    migration, ``ingest_vehicle_state`` (and every consumer that reads
    the warehouse) crashes with ``relation "vehicle_state" does not
    exist`` on a fresh Postgres install.

    PG-translation handled automatically by ``pg_adapter`` (TEXT/REAL
    pass through; INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL).
    """
    await conn.execute("""
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
            odometer_time       TEXT,
            engine_hours        REAL,
            engine_hours_time   TEXT,
            fault_count         INTEGER NOT NULL DEFAULT 0,
            dtc_critical_count  INTEGER NOT NULL DEFAULT 0,
            last_driver_id      TEXT    NOT NULL DEFAULT '',
            last_driver_name    TEXT    NOT NULL DEFAULT '',
            captured_at         TEXT    NOT NULL DEFAULT '',
            updated_at          TEXT    NOT NULL DEFAULT ''
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vehicle_state_company "
        "ON vehicle_state(account_id, company_code)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vehicle_state_name "
        "ON vehicle_state(account_id, vehicle_name)"
    )
    # Defense in depth: prevent any caller from looking up a name and
    # getting the wrong company's vehicle.  See migration 078 for the
    # motivation.  Belongs in the fresh-DB CREATE path so a brand-new
    # database starts with the same guarantee a migrated one gets.
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uniq_vehicle_state_account_company_name "
        "ON vehicle_state(account_id, company_code, vehicle_name)"
    )
    # Activity-window scan for billing — counts vehicles whose last
    # Samsara signal landed within the past N days.  Pairs with the
    # ``count_active_vehicles`` / ``compute_billing`` helpers.
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vehicle_state_active_billing "
        "ON vehicle_state(account_id, captured_at)"
    )

    await conn.execute("""
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
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_safety_event_log_occurred "
        "ON safety_event_log(account_id, occurred_at DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_safety_event_log_vehicle "
        "ON safety_event_log(account_id, vehicle_id, occurred_at DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_safety_event_log_driver "
        "ON safety_event_log(account_id, driver_id, occurred_at DESC)"
    )

    await conn.execute("""
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
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_driver_efficiency_daily_day "
        "ON driver_efficiency_daily(account_id, day DESC)"
    )

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS vehicle_telemetry_hourly (
            account_id    INTEGER NOT NULL,
            vehicle_id    TEXT    NOT NULL,
            vehicle_name  TEXT    NOT NULL DEFAULT '',
            hour_utc      TEXT    NOT NULL,
            miles         REAL    NOT NULL DEFAULT 0,
            drive_min     REAL    NOT NULL DEFAULT 0,
            idle_min      REAL    NOT NULL DEFAULT 0,
            top_speed_mph REAL,
            avg_fuel_pct  REAL,
            ingested_at   TEXT    NOT NULL DEFAULT '',
            PRIMARY KEY (account_id, vehicle_id, hour_utc)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vehicle_telemetry_hourly_hour "
        "ON vehicle_telemetry_hourly(account_id, hour_utc DESC)"
    )

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS vehicle_health_snapshot (
            vehicle_id    TEXT    NOT NULL PRIMARY KEY,
            account_id    INTEGER NOT NULL,
            vehicle_name  TEXT    NOT NULL DEFAULT '',
            company_code  TEXT    NOT NULL DEFAULT '',
            alert_count   INTEGER NOT NULL DEFAULT 0,
            captured_at   TEXT    NOT NULL DEFAULT '',
            raw_json      TEXT    NOT NULL DEFAULT '',
            updated_at    TEXT    NOT NULL DEFAULT ''
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS vehicle_fault_snapshot (
            vehicle_id    TEXT    NOT NULL PRIMARY KEY,
            account_id    INTEGER NOT NULL,
            vehicle_name  TEXT    NOT NULL DEFAULT '',
            company_code  TEXT    NOT NULL DEFAULT '',
            dtc_count     INTEGER NOT NULL DEFAULT 0,
            captured_at   TEXT    NOT NULL DEFAULT '',
            raw_json      TEXT    NOT NULL DEFAULT '',
            updated_at    TEXT    NOT NULL DEFAULT ''
        )
    """)
    await conn.commit()
    logger.info("Migration 028: warehouse tables created")


@_register("029_vehicle_state_odometer_time")
async def migrate_vehicle_state_odometer_time(conn) -> None:
    """Add ``vehicle_state.odometer_time`` for DBs that ran the original
    028 migration before the column was added.  Idempotent — no-op when
    the column is already present."""
    try:
        await conn.execute(
            "ALTER TABLE vehicle_state ADD COLUMN odometer_time TEXT"
        )
        await conn.commit()
        logger.info("Migration 029: added vehicle_state.odometer_time")
    except Exception as e:
        # Already exists — pg_adapter raises a generic error here; SQLite
        # raises 'duplicate column name'.  Either way, idempotent: swallow.
        logger.debug("vehicle_state.odometer_time migration skipped: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("030_alert_mutes_table")
async def migrate_alert_mutes_table(conn) -> None:
    """Per-alert mute table.

    A mute targets a specific ``alert_history.id`` (the canonical
    AlertID surfaced as "#1234" in the UI).  While the mute is active
    (``muted_until`` in the future), ``pipeline.send_alert`` skips
    Telegram delivery for that alert — the dashboard still shows it
    so operators can see what's quieted.

    ``muted_by`` is the operator's telegram_id; ``scope`` is currently
    'all_recipients' but the column is reserved for future per-recipient
    mutes ('recipient' + recipient_id).
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_mutes (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id        INTEGER NOT NULL REFERENCES accounts(id),
            alert_history_id  INTEGER NOT NULL,
            muted_by          BIGINT  NOT NULL,
            scope             TEXT    NOT NULL DEFAULT 'all_recipients',
            recipient_id      BIGINT,
            reason            TEXT    NOT NULL DEFAULT '',
            muted_until       TEXT    NOT NULL,
            created_at        TEXT    NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_alert_mutes_active "
        "ON alert_mutes(account_id, alert_history_id, muted_until)"
    )
    await conn.commit()
    logger.info("Migration 030: alert_mutes table created")


@_register("031_alert_history_reescalate_columns")
async def migrate_alert_history_reescalate(conn) -> None:
    """Add re-escalation tracking to alert_history: count of reminders
    sent and last reminder timestamp.  Lets re_escalate_critical_alerts
    enforce a max-attempts cap and exponential backoff per logical alert
    instead of firing every hour forever."""
    for col, ddl in (
        ("reescalate_count", "INTEGER NOT NULL DEFAULT 0"),
        ("reescalate_last_sent_at", "TEXT"),
    ):
        try:
            await conn.execute(
                f"ALTER TABLE alert_history ADD COLUMN {col} {ddl}"
            )
            await conn.commit()
            logger.info("Migration 031: added alert_history.%s", col)
        except Exception as e:
            logger.debug("alert_history.%s already present or skipped: %s", col, e)
            try:
                await conn.rollback()
            except Exception:
                pass


@_register("032_alert_history_severity_location")
async def migrate_alert_history_severity_location(conn) -> None:
    """Add severity + location columns to alert_history.

    severity becomes the cross-surface SSOT — bot/dashboard/mini-app all
    read this value instead of re-deriving from alert_type.  Default
    'warning' is the safe middle tier (existing rows backfill to warning).

    location is a snapshot string ("Mojave Freeway, CA") populated at
    first fire so consumers don't need a Samsara fetch to render it.
    """
    for col, ddl in (
        ("severity", "TEXT NOT NULL DEFAULT 'warning'"),
        ("location", "TEXT NOT NULL DEFAULT ''"),
    ):
        try:
            await conn.execute(
                f"ALTER TABLE alert_history ADD COLUMN {col} {ddl}"
            )
            await conn.commit()
            logger.info("Migration 032: added alert_history.%s", col)
        except Exception as e:
            logger.debug("alert_history.%s already present or skipped: %s", col, e)
            try:
                await conn.rollback()
            except Exception:
                pass
    try:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_history_active_sort "
            "ON alert_history(account_id, status, severity, last_seen DESC)"
        )
        await conn.commit()
    except Exception as e:
        logger.debug("idx_alert_history_active_sort skipped: %s", e)


@_register("033_alert_history_subkey")
async def migrate_alert_history_subkey(conn) -> None:
    """Add alert_subkey column + per-subkey UNIQUE constraint.

    Adds a ``alert_subkey`` column to ``alert_history`` plus a per-subkey
    UNIQUE constraint so distinct event subtypes (rollingStop, braking,
    etc.) don't collapse together when the same vehicle triggers them.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(alert_history)")
        cols = {r[1] for r in await cur.fetchall()}
        if "alert_subkey" in cols:
            return  # already migrated

        # Recover from a prior aborted migration: drop the leftover
        # temp table so the RENAME doesn't crash on "already exists".
        await conn.execute("DROP TABLE IF EXISTS _alert_history_subkey_old")
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
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_history_active_sort "
            "ON alert_history(account_id, status, severity, last_seen DESC)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_history_active "
            "ON alert_history(account_id, alert_type, vehicle_id, status)"
        )
        await conn.commit()
        logger.info("Migration 033: alert_history alert_subkey added + UNIQUE updated")
    except Exception as e:
        logger.error("Migration 033 alert_subkey failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("034_phase2_legacy_warehouse_tables")
async def migrate_phase2_legacy_warehouse_tables(conn) -> None:
    """Backfill the Phase-2 warehouse tables that only existed in tenant DBs.

    Missing-table backfill — calls to /api/fleet/weather,
    /api/fleet/geofences, etc. were 500ing with
    ``UndefinedTableError: relation "<table>" does not exist`` on
    deployments that pre-dated these tables.

    All ``CREATE TABLE IF NOT EXISTS`` — idempotent.
    """
    try:
        # vehicle_fault_detail — per-DTC detail with cleared_at lifecycle
        await conn.execute("""
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
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fault_detail_active "
            "ON vehicle_fault_detail(account_id, vehicle_id, cleared_at)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fault_detail_observed "
            "ON vehicle_fault_detail(account_id, observed_at DESC)"
        )

        # fleet_weather_snapshot — cabin temperature per truck.
        # Read by /api/fleet/weather; missing-table = 500.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS fleet_weather_snapshot (
                vehicle_id      TEXT    NOT NULL PRIMARY KEY,
                account_id      INTEGER NOT NULL,
                vehicle_name    TEXT    NOT NULL DEFAULT '',
                company_code    TEXT    NOT NULL DEFAULT '',
                temp_f          REAL,
                raw_json        TEXT    NOT NULL DEFAULT '',
                captured_at     TEXT    NOT NULL DEFAULT '',
                updated_at      TEXT    NOT NULL DEFAULT ''
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fleet_weather_company "
            "ON fleet_weather_snapshot(account_id, company_code)"
        )

        # fleet_efficiency_snapshot — windowed driver-efficiency cache.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS fleet_efficiency_snapshot (
                account_id      INTEGER NOT NULL,
                window_days     INTEGER NOT NULL,
                company_code    TEXT    NOT NULL DEFAULT '',
                payload_json    TEXT    NOT NULL DEFAULT '',
                captured_at     TEXT    NOT NULL DEFAULT '',
                updated_at      TEXT    NOT NULL DEFAULT '',
                PRIMARY KEY (account_id, window_days, company_code)
            )
        """)

        # geofence_definitions — read by /api/fleet/geofences;
        # missing-table = 500.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS geofence_definitions (
                geofence_id     TEXT    NOT NULL PRIMARY KEY,
                account_id      INTEGER NOT NULL,
                company_code    TEXT    NOT NULL DEFAULT '',
                name            TEXT    NOT NULL DEFAULT '',
                geofence_type   TEXT    NOT NULL DEFAULT '',
                raw_json        TEXT    NOT NULL DEFAULT '',
                captured_at     TEXT    NOT NULL DEFAULT '',
                updated_at      TEXT    NOT NULL DEFAULT ''
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_geofence_definitions_company "
            "ON geofence_definitions(account_id, company_code)"
        )

        await conn.commit()
        logger.info("Migration 034: Phase-2 warehouse tables ensured on legacy DB")
    except Exception as e:
        logger.error("Migration 034 phase2 tables failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("035_warehouse_legacy_column_alignment")
async def migrate_warehouse_legacy_column_alignment(conn) -> None:
    """Align legacy warehouse columns with the canonical tenant schema.

    Migration 028 created ``vehicle_telemetry_hourly`` and
    ``vehicle_fault_snapshot`` for the legacy single-DB Postgres path,
    but with column names that drifted from the tenant schema:

    * ``vehicle_telemetry_hourly.top_speed_mph`` (legacy)
      vs ``max_speed_mph`` (tenant; what the reader/ingestor query)
    * ``vehicle_telemetry_hourly`` was missing ``harsh_event_count``
    * ``vehicle_fault_snapshot`` was missing ``has_critical``

    Result: ``UndefinedColumnError`` from production
    (/api/vehicles/{name}/timeline 500s; scheduler fault_check crashes).

    Strategy — additive only; never drop columns the legacy schema
    already exposes.  Add the missing columns with safe defaults so
    the codebase's tenant-shaped queries succeed; legacy callers that
    referenced ``top_speed_mph`` keep working since that column stays.
    """
    # vehicle_telemetry_hourly — add the two missing columns.
    for col, ddl in (
        ("max_speed_mph", "REAL NOT NULL DEFAULT 0"),
        ("harsh_event_count", "INTEGER NOT NULL DEFAULT 0"),
    ):
        try:
            await conn.execute(
                f"ALTER TABLE vehicle_telemetry_hourly ADD COLUMN {col} {ddl}"
            )
            await conn.commit()
            logger.info("Migration 035: added vehicle_telemetry_hourly.%s", col)
        except Exception as e:
            logger.debug("vehicle_telemetry_hourly.%s skipped: %s", col, e)
            try:
                await conn.rollback()
            except Exception:
                pass

    # vehicle_fault_snapshot — add has_critical.
    try:
        await conn.execute(
            "ALTER TABLE vehicle_fault_snapshot "
            "ADD COLUMN has_critical INTEGER NOT NULL DEFAULT 0"
        )
        await conn.commit()
        logger.info("Migration 035: added vehicle_fault_snapshot.has_critical")
    except Exception as e:
        logger.debug("vehicle_fault_snapshot.has_critical skipped: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass

    # Index used by the alerting hot path (sort by critical first).
    try:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vehicle_fault_critical "
            "ON vehicle_fault_snapshot(account_id, has_critical)"
        )
        await conn.commit()
    except Exception as e:
        logger.debug("idx_vehicle_fault_critical skipped: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("036_warehouse_legacy_columns_v2")
async def migrate_warehouse_legacy_columns_v2(conn) -> None:
    """Retry 035 with explicit column-existence check.

    Migration 035 wrapped each ``ALTER TABLE … ADD COLUMN`` in a generic
    try/except so it could swallow the "duplicate column" error.  On
    Postgres, when ANY statement in a transaction fails, every following
    statement fails too with "current transaction is aborted" — so a
    transient hiccup on the *first* ALTER silently flunked the rest, and
    ``_mark_applied`` still recorded 035 as successful.  Production was
    left without ``has_critical`` / ``max_speed_mph`` / ``harsh_event_count``
    and the scheduler kept crashing on ``UndefinedColumnError``.

    This v2 uses ``PRAGMA table_info`` (translated by pg_adapter) to
    check column existence *before* ALTERing — the same pattern used by
    the working migrations 031/032.  Each ALTER runs in its own clean
    transaction so a stray failure can't poison the rest.
    """
    # Helper: add a column only when it's missing.
    async def _ensure_col(table: str, col: str, ddl: str) -> None:
        try:
            cur = await conn.execute(f"PRAGMA table_info({table})")
            existing = {r[1] for r in await cur.fetchall()}
            if col in existing:
                return
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            await conn.commit()
            logger.info("Migration 036: added %s.%s", table, col)
        except Exception as e:
            logger.error("Migration 036: failed to add %s.%s — %s", table, col, e)
            try:
                await conn.rollback()
            except Exception:
                pass

    await _ensure_col("vehicle_telemetry_hourly", "max_speed_mph",     "REAL NOT NULL DEFAULT 0")
    await _ensure_col("vehicle_telemetry_hourly", "harsh_event_count", "INTEGER NOT NULL DEFAULT 0")
    await _ensure_col("vehicle_fault_snapshot",   "has_critical",      "INTEGER NOT NULL DEFAULT 0")

    # Critical-faults index — recreate (IF NOT EXISTS makes this safe).
    try:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vehicle_fault_critical "
            "ON vehicle_fault_snapshot(account_id, has_critical)"
        )
        await conn.commit()
    except Exception as e:
        logger.debug("idx_vehicle_fault_critical skipped: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("037_safety_event_log_company_code")
async def migrate_safety_event_log_company_code(conn) -> None:
    """Add ``company_code`` column to ``safety_event_log``.

    The dashboard's ``/api/safety/events`` route applies a
    ``filter_by_allowed_companies`` defence-in-depth check that needs
    each row's company.  Today that company is buried in ``raw_json``
    and we ``json.loads`` every row just to read it — for a 30-day
    window with ~1500 events that's hundreds of ms of pure-CPU waste
    per request.  Promoting ``company_code`` to a real column lets
    the reader skip the JSON decode entirely (other warehouse tables
    already have it; this brings safety_event_log in line).

    Additive, idempotent.  Old rows backfill to the empty string;
    the ingestor populates new rows going forward.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(safety_event_log)")
        cols = {r[1] for r in await cur.fetchall()}
        if "company_code" in cols:
            return
        await conn.execute(
            "ALTER TABLE safety_event_log "
            "ADD COLUMN company_code TEXT NOT NULL DEFAULT ''"
        )
        await conn.commit()
        logger.info("Migration 037: added safety_event_log.company_code")
    except Exception as e:
        logger.error("Migration 037 company_code failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("038_maintenance_alerted_at")
async def migrate_maintenance_alerted_at(conn) -> None:
    """Add ``alerted_at`` to ``maintenance_tasks`` for alert throttling.

    See the tenant-DB twin (``migrate_add_maintenance_alerted_at``) for
    the full rationale.  This is the legacy/single-tenant copy so old
    deployments running off the platform DB also pick up the column.
    Additive, idempotent.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(maintenance_tasks)")
        cols = {r[1] for r in await cur.fetchall()}
        if "alerted_at" in cols:
            return
        await conn.execute(
            "ALTER TABLE maintenance_tasks ADD COLUMN alerted_at TEXT"
        )
        await conn.commit()
        logger.info("Migration 038: added maintenance_tasks.alerted_at")
    except Exception as e:
        logger.error("Migration 038 alerted_at failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("039_maintenance_priority_engine_hours_columns")
async def migrate_maintenance_priority_engine_hours_columns(conn) -> None:
    """Add priority + engine_hours + warning_sent_at + work_order_id columns.

    Twin of the tenant-DB ``migrate_extend_maintenance_tasks_columns``;
    see that function's docstring for the per-column rationale.  Single
    migration that adds all 6 columns idempotently so legacy single-
    tenant deployments don't accumulate version-table rows per column.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(maintenance_tasks)")
        cols = {r[1] for r in await cur.fetchall()}
        to_add = [
            ("priority",                    "TEXT NOT NULL DEFAULT 'medium'"),
            ("due_engine_hours",            "REAL"),
            ("last_engine_hours",           "REAL"),
            ("recur_interval_engine_hours", "REAL"),
            ("warning_sent_at",             "TEXT"),
            ("work_order_id",               "INTEGER"),
        ]
        added = 0
        for col_name, col_def in to_add:
            if col_name in cols:
                continue
            await conn.execute(
                f"ALTER TABLE maintenance_tasks ADD COLUMN {col_name} {col_def}"
            )
            added += 1
        if added:
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_maintenance_tasks_status_priority "
                "ON maintenance_tasks(account_id, status, priority)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_maintenance_tasks_work_order "
                "ON maintenance_tasks(work_order_id)"
            )
            await conn.commit()
            logger.info("Migration 039: added %d maintenance_tasks column(s)", added)
    except Exception as e:
        logger.error("Migration 039 phase3 columns failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("040_work_orders_skeleton")
async def migrate_work_orders_skeleton(conn) -> None:
    """Create work_orders / work_order_parts / work_order_attachments.

    Twin of the tenant-DB ``migrate_create_work_orders_tables``.  Tables
    only — no routes or UI yet.  See project memory
    ``project-work-orders-module`` for the planned Work Orders module
    that will consume these.
    """
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS work_orders (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id               INTEGER NOT NULL,
                company_code             TEXT    NOT NULL DEFAULT '',
                vehicle_id               TEXT    NOT NULL DEFAULT '',
                vehicle_name             TEXT    NOT NULL DEFAULT '',
                vendor_name              TEXT    NOT NULL DEFAULT '',
                vendor_address           TEXT    NOT NULL DEFAULT '',
                vendor_phone             TEXT    NOT NULL DEFAULT '',
                service_date             TEXT,
                odometer_at_service      REAL,
                engine_hours_at_service  REAL,
                labor_cost               REAL    NOT NULL DEFAULT 0,
                parts_cost               REAL    NOT NULL DEFAULT 0,
                tax_amount               REAL    NOT NULL DEFAULT 0,
                total_cost               REAL    NOT NULL DEFAULT 0,
                invoice_number           TEXT    NOT NULL DEFAULT '',
                payment_method           TEXT    NOT NULL DEFAULT '',
                payment_status           TEXT    NOT NULL DEFAULT 'unpaid',
                status                   TEXT    NOT NULL DEFAULT 'draft',
                repair_priority          TEXT    NOT NULL DEFAULT '',
                complaint                TEXT    NOT NULL DEFAULT '',
                cause                    TEXT    NOT NULL DEFAULT '',
                correction               TEXT    NOT NULL DEFAULT '',
                notes                    TEXT    NOT NULL DEFAULT '',
                source                   TEXT    NOT NULL DEFAULT 'manual',
                external_id             TEXT    NOT NULL DEFAULT '',
                -- Human-readable reference from the source system
                -- (Datatruck "WO-00983") — distinct from external_id
                -- (its internal numeric id).  Shown as a hover tip on
                -- the Source badge; generic across future integrations.
                external_number          TEXT    NOT NULL DEFAULT '',
                assigned_to                 TEXT    NOT NULL DEFAULT '',
                created_by               BIGINT  NOT NULL,
                created_at               TEXT    NOT NULL,
                updated_at               TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_work_orders_vehicle
                ON work_orders(account_id, vehicle_name, service_date DESC);
            CREATE INDEX IF NOT EXISTS idx_work_orders_status
                ON work_orders(account_id, status);
            -- One synced work order per (account, source, external id).
            -- Partial so the many manual rows (external_id='') aren't
            -- forced unique against each other — only integration-sourced
            -- rows are deduped.  This is what makes the Datatruck
            -- projection idempotent across re-syncs.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_work_orders_external
                ON work_orders(account_id, source, external_id)
                WHERE external_id <> '';

            CREATE TABLE IF NOT EXISTS work_order_parts (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                work_order_id       INTEGER NOT NULL,
                part_name           TEXT    NOT NULL DEFAULT '',
                part_number         TEXT    NOT NULL DEFAULT '',
                quantity            REAL    NOT NULL DEFAULT 1,
                unit_cost           REAL    NOT NULL DEFAULT 0,
                total_cost          REAL    NOT NULL DEFAULT 0,
                warranty_months     INTEGER NOT NULL DEFAULT 0,
                -- Service-task tag (maintenance task-type slug, e.g.
                -- 'brakes' / 'custom_oil_change').  The parts editor
                -- groups lines by this, giving the task→parts
                -- hierarchy without a separate grouping table; ''
                -- means untagged/general.
                service_task        TEXT    NOT NULL DEFAULT '',
                notes               TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_work_order_parts_wo
                ON work_order_parts(work_order_id);

            CREATE TABLE IF NOT EXISTS work_order_attachments (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                work_order_id       INTEGER NOT NULL,
                file_path           TEXT    NOT NULL,
                file_name           TEXT    NOT NULL DEFAULT '',
                file_size           INTEGER NOT NULL DEFAULT 0,
                content_type        TEXT    NOT NULL DEFAULT '',
                kind                TEXT    NOT NULL DEFAULT 'other',
                uploaded_by         BIGINT  NOT NULL,
                uploaded_at         TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_work_order_attachments_wo
                ON work_order_attachments(work_order_id);
        """)
        await conn.commit()
        logger.info("Migration 040: created work_orders skeleton tables")
    except Exception as e:
        logger.error("Migration 040 work_orders skeleton failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("041_maintenance_attestation")
async def migrate_maintenance_attestation(conn) -> None:
    """Add ``attested_by`` + ``attested_at`` to ``maintenance_tasks``.

    Twin of the tenant-DB ``migrate_add_maintenance_attestation``.
    Driver sign-off columns for the DOT audit trail.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(maintenance_tasks)")
        cols = {r[1] for r in await cur.fetchall()}
        added = 0
        for col_name, col_def in [
            ("attested_by", "BIGINT"),
            ("attested_at", "TEXT"),
        ]:
            if col_name in cols:
                continue
            await conn.execute(
                f"ALTER TABLE maintenance_tasks ADD COLUMN {col_name} {col_def}"
            )
            added += 1
        if added:
            await conn.commit()
            logger.info("Migration 041: added %d attestation column(s)", added)
    except Exception as e:
        logger.error("Migration 041 attestation failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("042_maintenance_spawned_from_id")
async def migrate_maintenance_spawned_from(conn) -> None:
    """Add ``spawned_from_id`` to ``maintenance_tasks`` (legacy DB).

    Twin of the tenant-DB ``migrate_add_maintenance_spawned_from``;
    lineage column for the dashboard's auto-renewal breadcrumb.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(maintenance_tasks)")
        cols = {r[1] for r in await cur.fetchall()}
        if "spawned_from_id" in cols:
            return
        await conn.execute(
            "ALTER TABLE maintenance_tasks ADD COLUMN spawned_from_id INTEGER"
        )
        await conn.commit()
        logger.info("Migration 042: added maintenance_tasks.spawned_from_id")
    except Exception as e:
        logger.error("Migration 042 spawned_from_id failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


# ── Driver Module — legacy SQLite migrations ──────────────────
#
# Same logical migrations as the ones in ``platform_migrations.py``
# but registered with version IDs so they apply to legacy single-file
# SQLite databases (which is also what the test harness uses).  Each
# is internally idempotent so running BOTH the legacy-versioned path
# AND the platform-migration path is safe.


@_register("042b_users_samsara_driver_id")
async def migrate_users_samsara_driver_id(conn) -> None:
    """Add ``users.samsara_driver_id`` — twin of the migration in
    ``platform_migrations.py``.  Required so the new driver-profile
    SELECT (which reads samsara_driver_id) doesn't ``no such column``
    on legacy single-file SQLite databases."""
    try:
        cur = await conn.execute("PRAGMA table_info(users)")
        cols = {r[1] for r in await cur.fetchall()}
        if "samsara_driver_id" not in cols:
            await conn.execute(
                "ALTER TABLE users ADD COLUMN samsara_driver_id TEXT"
            )
            await conn.commit()
            logger.info("Migration 042b: added users.samsara_driver_id")
    except Exception as e:
        logger.error("Migration 042b failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("043_driver_profile_columns")
async def migrate_driver_profile_columns(conn) -> None:
    """Add 11 driver-profile columns to users (CDL, medical, hire date,
    contact).  All nullable; non-driver rows unaffected."""
    cols_to_add = (
        ("cdl_number",       "TEXT"),
        ("cdl_state",        "TEXT"),
        ("cdl_class",        "TEXT"),
        ("cdl_expires",      "TEXT"),
        ("med_card_expires", "TEXT"),
        ("hire_date",        "TEXT"),
        ("termination_date", "TEXT"),
        ("dob",              "TEXT"),
        ("phone",            "TEXT"),
        ("home_address",     "TEXT"),
        ("driver_notes",     "TEXT"),
    )
    try:
        cur = await conn.execute("PRAGMA table_info(users)")
        cols = {r[1] for r in await cur.fetchall()}
        added = 0
        for name, sqltype in cols_to_add:
            if name not in cols:
                await conn.execute(
                    f"ALTER TABLE users ADD COLUMN {name} {sqltype}"
                )
                added += 1
        if added:
            await conn.commit()
            logger.info("Migration 043: added %d driver-profile column(s)", added)
    except Exception as e:
        logger.error("Migration 043 driver-profile failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("044_driver_vehicle_assignments")
async def migrate_create_driver_vehicle_assignments(conn) -> None:
    """Create driver_vehicle_assignments — single source of truth for
    driver↔vehicle mapping with history."""
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS driver_vehicle_assignments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                vehicle_name    TEXT    NOT NULL,
                vehicle_id      TEXT,
                is_primary      INTEGER NOT NULL DEFAULT 0,
                assigned_by     INTEGER,
                assigned_at     TEXT    NOT NULL,
                unassigned_at   TEXT,
                notes           TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_dva_active
                ON driver_vehicle_assignments(user_id, unassigned_at);
            CREATE INDEX IF NOT EXISTS idx_dva_vehicle
                ON driver_vehicle_assignments(account_id, vehicle_name, unassigned_at);
            CREATE INDEX IF NOT EXISTS idx_dva_account
                ON driver_vehicle_assignments(account_id, unassigned_at);
        """)
        await conn.commit()
        logger.info("Migration 044: created driver_vehicle_assignments")
    except Exception as e:
        logger.error("Migration 044 driver_vehicle_assignments failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("045_driver_documents")
async def migrate_create_driver_documents(conn) -> None:
    """Create driver_documents — per-driver document store with
    expiration tracking.  Files are addressed via ``object_key`` in
    the existing ``ObjectStore`` (Google Drive or local disk)."""
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS driver_documents (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                doc_type        TEXT    NOT NULL,
                bucket          TEXT    NOT NULL DEFAULT 'driver_documents',
                object_key      TEXT    NOT NULL,
                drive_file_id   TEXT,
                file_name       TEXT    NOT NULL,
                file_size       INTEGER,
                mime_type       TEXT,
                issued_at       TEXT,
                expires_at      TEXT,
                status          TEXT    NOT NULL DEFAULT 'active',
                uploaded_by     INTEGER,
                uploaded_at     TEXT    NOT NULL,
                notes           TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_docs_user
                ON driver_documents(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_docs_expiring
                ON driver_documents(account_id, expires_at, status);
            CREATE INDEX IF NOT EXISTS idx_docs_account_type
                ON driver_documents(account_id, doc_type, status);
        """)
        await conn.commit()
        logger.info("Migration 045: created driver_documents")
    except Exception as e:
        logger.error("Migration 045 driver_documents failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("046_account_storage_quota")
async def migrate_add_account_storage_quota(conn) -> None:
    """Add storage_quota_bytes / storage_used_bytes to accounts (for
    local-disk-fallback quota enforcement)."""
    try:
        cur = await conn.execute("PRAGMA table_info(accounts)")
        cols = {r[1] for r in await cur.fetchall()}
        added = 0
        if "storage_quota_bytes" not in cols:
            await conn.execute(
                "ALTER TABLE accounts ADD COLUMN storage_quota_bytes "
                "INTEGER NOT NULL DEFAULT 524288000"
            )
            added += 1
        if "storage_used_bytes" not in cols:
            await conn.execute(
                "ALTER TABLE accounts ADD COLUMN storage_used_bytes "
                "INTEGER NOT NULL DEFAULT 0"
            )
            added += 1
        if added:
            await conn.commit()
            logger.info("Migration 046: added %d storage-quota column(s)", added)
    except Exception as e:
        logger.error("Migration 046 storage-quota failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("047_driver_vehicle_assignments_backfill")
async def migrate_backfill_driver_vehicle_assignments(conn) -> None:
    """Backfill driver_vehicle_assignments from existing data sources
    (driver_trucks first, then users.truck_num).  Additive only — no
    deletes."""
    try:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='driver_vehicle_assignments'"
        )
        if not (await cur.fetchone()):
            return

        now = __import__("datetime").datetime.utcnow().isoformat()
        inserted = 0

        # 1) From driver_trucks
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='driver_trucks'"
        )
        if await cur.fetchone():
            cur = await conn.execute(
                "SELECT dt.user_id, dt.account_id, dt.truck_num, "
                "       dt.is_primary, COALESCE(dt.assigned_by, 0), "
                "       COALESCE(dt.assigned_at, ?) "
                "FROM driver_trucks dt "
                "WHERE NOT EXISTS ("
                "    SELECT 1 FROM driver_vehicle_assignments dva "
                "    WHERE dva.user_id = dt.user_id "
                "      AND dva.vehicle_name = dt.truck_num "
                "      AND dva.unassigned_at IS NULL"
                ")",
                (now,),
            )
            for user_id, acct_id, vehicle, is_primary, by_id, at in await cur.fetchall():
                await conn.execute(
                    "INSERT INTO driver_vehicle_assignments "
                    "(account_id, user_id, vehicle_name, is_primary, "
                    " assigned_by, assigned_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (acct_id, user_id, vehicle, is_primary, by_id, at),
                )
                inserted += 1

        # 2) From users.truck_num for drivers still missing an active row
        cur = await conn.execute(
            "SELECT u.id, u.account_id, u.truck_num FROM users u "
            "WHERE u.truck_num IS NOT NULL AND u.truck_num != '' "
            "  AND u.is_active = 1 "
            "  AND NOT EXISTS ("
            "      SELECT 1 FROM driver_vehicle_assignments dva "
            "      WHERE dva.user_id = u.id "
            "        AND dva.unassigned_at IS NULL"
            "  )"
        )
        for user_id, acct_id, truck in await cur.fetchall():
            await conn.execute(
                "INSERT INTO driver_vehicle_assignments "
                "(account_id, user_id, vehicle_name, is_primary, "
                " assigned_by, assigned_at) "
                "VALUES (?, ?, ?, 1, 0, ?)",
                (acct_id, user_id, truck, now),
            )
            inserted += 1

        if inserted:
            await conn.commit()
            logger.info("Migration 047: backfilled %d driver_vehicle_assignments row(s)", inserted)
    except Exception as e:
        logger.error("Migration 047 backfill failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("048_driver_document_notifications")
async def migrate_create_driver_document_notifications(conn) -> None:
    """Per-(doc_id, bucket_days) ledger for the daily expiration scheduler.

    The composite PK is the dedup hook — re-running the scheduler the
    same day or weeks later won't re-fire an already-sent bucket for
    the same doc.
    """
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS driver_document_notifications (
                doc_id        INTEGER NOT NULL,
                bucket_days   INTEGER NOT NULL,
                notified_at   TEXT    NOT NULL,
                PRIMARY KEY (doc_id, bucket_days)
            );
            CREATE INDEX IF NOT EXISTS idx_doc_notif_doc
                ON driver_document_notifications(doc_id);
        """)
        await conn.commit()
        logger.info("Migration 048: created driver_document_notifications table")
    except Exception as e:
        logger.error("Migration 048 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("049_payroll_tables")
async def migrate_create_payroll_tables(conn) -> None:
    """Create payroll tables on the unified Database.

    Historically these lived on ``TenantDB`` and were created by
    ``tenant_schema.create_tables``.  After the platform/tenant DB
    unification they need an explicit migration so SQLite-mode tenants
    pick them up on the next startup (``_initialize_sqlite`` doesn't
    run ``platform_migrations.run_all``).

    Idempotent — CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS
    means a re-run is a no-op for tenants already on the new schema.
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
                run_id            INTEGER NOT NULL REFERENCES payroll_runs(id),
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
        logger.info("Migration 049: created payroll tables")
    except Exception as e:
        logger.error("Migration 049 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("050_driver_future_tables")
async def migrate_create_driver_future_tables(conn) -> None:
    """Foundation tables for upcoming driver-facing modules.

    Schema-only — the full features (inspections, trainings, HOS
    cache) land in follow-on PRs.  Tables are created now so the
    migration ordering stays simple and adapter stubs in
    ``capabilities/drivers/`` can be filled in without another schema
    migration.

    Idempotent.
    """
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS driver_inspections (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                vehicle_name    TEXT,
                inspection_type TEXT    NOT NULL,
                status          TEXT    NOT NULL DEFAULT 'pass',
                inspected_at    TEXT    NOT NULL,
                defects_json    TEXT,
                notes           TEXT,
                created_at      TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_inspections_user
                ON driver_inspections(user_id, inspected_at);
            CREATE INDEX IF NOT EXISTS idx_inspections_account
                ON driver_inspections(account_id, inspected_at);

            CREATE TABLE IF NOT EXISTS driver_trainings (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id         INTEGER NOT NULL,
                user_id            INTEGER NOT NULL,
                training_type      TEXT    NOT NULL,
                provider           TEXT,
                completed_at       TEXT,
                expires_at         TEXT,
                certificate_doc_id INTEGER,
                status             TEXT    NOT NULL DEFAULT 'completed',
                notes              TEXT,
                created_at         TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_trainings_user
                ON driver_trainings(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_trainings_expiring
                ON driver_trainings(account_id, expires_at);

            CREATE TABLE IF NOT EXISTS driver_hos_status (
                user_id                 INTEGER PRIMARY KEY,
                account_id              INTEGER NOT NULL,
                samsara_driver_id       TEXT,
                duty_status             TEXT,
                drive_seconds_today     INTEGER DEFAULT 0,
                on_duty_seconds_today   INTEGER DEFAULT 0,
                cycle_seconds_remaining INTEGER,
                shift_seconds_remaining INTEGER,
                last_status_change      TEXT,
                updated_at              TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_hos_account
                ON driver_hos_status(account_id);
        """)
        await conn.commit()
        logger.info("Migration 050: created driver_inspections / trainings / hos_status")
    except Exception as e:
        logger.error("Migration 050 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("051_coaching_tables")
async def migrate_create_coaching_tables(conn) -> None:
    """Create coaching tables on the unified Database.

    Historically these lived on ``TenantDB`` and were created by
    ``tenant_schema.create_tables``.  Same gap as 049 (payroll) — the
    SQLite-mode init path doesn't run platform_migrations, so we
    need an explicit migration here.

    Idempotent.
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
                assignment_id   INTEGER NOT NULL REFERENCES coaching_assignments(id),
                driver_id       TEXT    NOT NULL,
                acked_at        TEXT    NOT NULL DEFAULT '',
                note            TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_coaching_acks_assignment
                ON coaching_acknowledgments(assignment_id);
        """)
        await conn.commit()
        logger.info("Migration 051: created coaching tables")
    except Exception as e:
        logger.error("Migration 051 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("052_account_timezone")
async def migrate_add_account_timezone(conn) -> None:
    """Add ``accounts.timezone`` for the account-level timezone default.

    Per-user ``users.timezone`` is the optional override; this column
    is the account-wide default that admins set via Settings, and the
    fallback every cron + display formatter consults via
    ``capabilities.localization.tz.effective_tz_for_*``.
    Idempotent.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(accounts)")
        existing = {r[1] for r in await cur.fetchall()}
        if "timezone" in existing:
            return
        await conn.execute(
            "ALTER TABLE accounts ADD COLUMN timezone "
            "TEXT NOT NULL DEFAULT 'America/New_York'"
        )
        await conn.commit()
        logger.info("Migration 052: added accounts.timezone column")
    except Exception as e:
        logger.error("Migration 052 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("053_forum_routing")
async def migrate_forum_routing(conn) -> None:
    """Tables for per-account Telegram forum groups + alert→topic routing.

    ``forum_groups`` binds an account to its Telegram supergroup (one
    per account); ``alert_routing`` maps each canonical alert type to
    a specific topic (``message_thread_id``) inside that group.

    Both tables are additive — no existing alert delivery code reads
    them yet.  The pipeline refactor in Phase 4 turns them on.
    """
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS forum_groups (
                account_id          INTEGER PRIMARY KEY REFERENCES accounts(id),
                chat_id             BIGINT  NOT NULL,
                chat_title          TEXT    NOT NULL DEFAULT '',
                is_forum_enabled    INTEGER NOT NULL DEFAULT 0,
                setup_status        TEXT    NOT NULL DEFAULT 'pending',
                last_setup_at       TEXT,
                last_repair_at      TEXT,
                created_by_user_id  INTEGER NOT NULL REFERENCES users(id),
                created_at          TEXT    NOT NULL,
                updated_at          TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_forum_groups_chat
                ON forum_groups(chat_id);

            CREATE TABLE IF NOT EXISTS alert_routing (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id              INTEGER NOT NULL REFERENCES accounts(id),
                alert_type              TEXT    NOT NULL,
                chat_id                 BIGINT  NOT NULL,
                message_thread_id       BIGINT  NOT NULL,
                topic_name_snapshot     TEXT    NOT NULL DEFAULT '',
                icon_emoji              TEXT    NOT NULL DEFAULT '',
                is_active               INTEGER NOT NULL DEFAULT 1,
                created_at              TEXT    NOT NULL,
                updated_at              TEXT    NOT NULL,
                UNIQUE(account_id, alert_type)
            );

            CREATE INDEX IF NOT EXISTS idx_alert_routing_account
                ON alert_routing(account_id, is_active);
        """)
        await conn.commit()
        logger.info("Migration 053: created forum_groups + alert_routing tables")
    except Exception as e:
        logger.error("Migration 053 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("054_forum_topics_seen")
async def migrate_forum_topics_seen(conn) -> None:
    """Index of forum topics observed by the bot.

    Populated by service-message handlers (forum_topic_created /
    forum_topic_edited) — these fire whenever any member or the bot
    itself creates or renames a topic in a forum group.  The
    ``/setupforum`` and ``/repairforum`` commands consult this
    index *before* calling ``createForumTopic``: when a topic with
    the target name already exists in the chat (e.g. an orphan
    from a previous timed-out create attempt), the bot adopts it
    instead of creating a duplicate.

    Telegram's Bot API has no ``getForumTopics`` method, so this
    table is the only way for the bot to know what topics exist
    in a forum chat.  Bootstrapping happens organically: every
    topic event sent in the chat updates the index.
    """
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS forum_topics_seen (
                chat_id           BIGINT NOT NULL,
                message_thread_id BIGINT NOT NULL,
                name              TEXT   NOT NULL,
                last_seen_at      TEXT   NOT NULL,
                PRIMARY KEY (chat_id, message_thread_id)
            );

            CREATE INDEX IF NOT EXISTS idx_forum_topics_seen_name
                ON forum_topics_seen(chat_id, name);
        """)
        await conn.commit()
        logger.info("Migration 054: created forum_topics_seen table")
    except Exception as e:
        logger.error("Migration 054 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("055_vehicle_state_engine_hours")
async def migrate_vehicle_state_engine_hours(conn) -> None:
    """Add ``vehicle_state.engine_hours`` + ``engine_hours_time``.

    Without these columns, ``mark_overdue_tasks_by_engine_hours`` exits
    early ("warehouse has no engine_hours yet") and engine-hours
    maintenance tasks never flip to overdue.  Adds the parallel of
    ``odometer_mi`` / ``odometer_time`` so the ingestor can stamp the
    cumulative OBD engine-hours counter on each 60s refresh.

    Idempotent — silently no-ops when the columns are already present
    (same pattern as migration 029).
    """
    for col, ddl in (
        ("engine_hours",      "ALTER TABLE vehicle_state ADD COLUMN engine_hours REAL"),
        ("engine_hours_time", "ALTER TABLE vehicle_state ADD COLUMN engine_hours_time TEXT"),
    ):
        try:
            await conn.execute(ddl)
            await conn.commit()
            logger.info("Migration 055: added vehicle_state.%s", col)
        except Exception as e:
            logger.debug("vehicle_state.%s migration skipped: %s", col, e)
            try:
                await conn.rollback()
            except Exception:
                pass


@_register("056_maintenance_tasks_last_odometer")
async def migrate_maintenance_tasks_last_odometer(conn) -> None:
    """Add the missing ``maintenance_tasks.last_odometer`` column.

    The column is referenced across the storage adapter, scheduler
    service, bot UI, and DOT-binder report, but no prior migration
    ever created it.  Production overdue-mileage cron fails with
    ``UndefinedColumnError: column "last_odometer" of relation
    "maintenance_tasks" does not exist`` every 6 hours until this lands.

    Twin of ``last_engine_hours`` added in migration 039 — same shape
    (REAL, nullable, filled in by ``update_maintenance_last_odometer_bulk``
    on each scheduler tick).
    """
    try:
        await conn.execute(
            "ALTER TABLE maintenance_tasks ADD COLUMN last_odometer REAL"
        )
        await conn.commit()
        logger.info("Migration 056: added maintenance_tasks.last_odometer")
    except Exception as e:
        logger.debug("maintenance_tasks.last_odometer migration skipped: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("057_enable_rls_tenant_tables")
async def migrate_enable_rls_tenant_tables(conn) -> None:
    """Enable Row Level Security on every tenant-scoped table.

    Postgres RLS makes ``WHERE account_id = $1`` enforced by the database
    instead of application code.  Each policy uses the session-local
    GUC ``app.account_id`` (set by :meth:`Database.with_account`):

        USING (account_id::text = current_setting('app.account_id', true))

    The ``true`` second argument makes a missing GUC return NULL, which
    yields zero rows (fail-closed).  ``FORCE ROW LEVEL SECURITY`` makes
    the table owner subject to the policy too — otherwise the app role,
    which usually owns the table, bypasses RLS by default.

    **Gated by ``ENABLE_RLS`` env var (default off).**  This migration
    is registered unconditionally so the schema is consistent across
    fresh databases, but the per-table block early-exits when the flag
    is off, leaving production behavior unchanged.  Operator flips the
    flag during the staged rollout — see docs/runbooks/rls-rollout.md.

    Idempotent: ``ALTER TABLE`` and ``DROP POLICY IF EXISTS`` are
    re-runnable; missing tables (fresh DB without yet-applied migrations)
    are skipped silently.
    """
    import os
    if os.getenv("ENABLE_RLS", "0").strip() not in ("1", "true", "TRUE", "yes"):
        logger.info("Migration 057: ENABLE_RLS not set; RLS policies skipped")
        return

    # Every tenant-scoped table.  Audit source: 2026-05-18 sweep of
    # schema.py + migrations.py + platform_schema.py + platform_migrations.py.
    # ``forum_groups`` has nullable account_id but the policy excludes
    # NULL rows anyway, which is the desired behavior (NULL rows are
    # un-claimed / system-owned and should not be visible to a tenant).
    TENANT_TABLES = [
        "account_settings", "ai_usage", "alert_acknowledgments",
        "alert_history", "alert_routing", "audit_log", "authorized_chats",
        "bonus_rules", "coaching_assignments", "coaching_rules",
        "coaching_topics", "companies", "custom_poi_layers",
        "custom_poi_points", "daily_scorecard_snapshots", "dnd_alert_queue",
        "driver_documents", "driver_hos_status", "driver_inspections",
        "driver_pay_settings", "driver_trainings", "driver_trucks",
        "driver_vehicle_assignments", "forum_groups", "fuel_entries",
        "maintenance_tasks", "payroll_runs", "role_permissions",
        "score_events", "score_rules", "user_companies",
        "work_orders",  # work_order_parts and work_order_attachments are
                        # joined through work_orders.account_id so a policy
                        # there would be redundant; the parent's policy
                        # already gates access.
        # Warehouse tables — all have account_id, all queried per tenant.
        "vehicle_state", "safety_event_log", "driver_efficiency_daily",
        "vehicle_telemetry_hourly", "vehicle_health_snapshot",
        "vehicle_fault_snapshot", "vehicle_fault_detail",
        "fleet_weather_snapshot", "fleet_efficiency_snapshot",
        "geofence_definitions",
    ]

    enabled = 0
    skipped = 0
    for tbl in TENANT_TABLES:
        try:
            # ALTER TABLE is rolled into its own savepoint so one missing
            # table (mid-migration fresh DB) doesn't abort the whole
            # transaction.  asyncpg auto-commits each execute() outside
            # an explicit BEGIN so this is structurally clean.
            await conn.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
            await conn.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
            await conn.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl}")
            await conn.execute(
                f"""
                CREATE POLICY tenant_isolation ON {tbl}
                USING       (account_id::text = current_setting('app.account_id', true))
                WITH CHECK  (account_id::text = current_setting('app.account_id', true))
                """
            )
            enabled += 1
        except Exception as e:
            # Missing table on a fresh DB — log and continue.
            logger.debug("RLS policy on %s skipped: %s", tbl, e)
            skipped += 1
            try:
                await conn.rollback()
            except Exception:
                pass

    try:
        await conn.commit()
    except Exception:
        pass
    logger.info(
        "Migration 057: RLS enabled on %d table(s), skipped %d (missing/error)",
        enabled, skipped,
    )


@_register("058_maintenance_tasks_updated_at")
async def migrate_maintenance_tasks_updated_at(conn) -> None:
    """Add the missing ``maintenance_tasks.updated_at`` column.

    The dashboard's task list has rendered an "Updated" column for some
    time, but the column never existed in the schema — every row
    rendered as ``—`` because the field was always NULL.  This migration
    adds it and backfills existing rows from ``created_at`` so the UI
    has a reasonable starting value.  ``update_maintenance_task`` and
    ``update_maintenance_status`` now stamp the column on every write
    (see [adapters/storage/maintenance.py]).
    """
    try:
        cur = await conn.execute("PRAGMA table_info(maintenance_tasks)")
        cols = {r[1] for r in await cur.fetchall()}
        if "updated_at" in cols:
            return
        await conn.execute(
            "ALTER TABLE maintenance_tasks ADD COLUMN updated_at TEXT"
        )
        # Backfill: an existing task with no ``updated_at`` value would
        # show ``—`` in the dashboard, which we just fixed by adding
        # the column.  Seed with ``created_at`` so the column is
        # meaningful from the first deploy after this migration.
        await conn.execute(
            "UPDATE maintenance_tasks SET updated_at = created_at "
            "WHERE updated_at IS NULL"
        )
        await conn.commit()
        logger.info("Migration 058: added maintenance_tasks.updated_at + backfilled from created_at")
    except Exception as e:
        logger.debug("maintenance_tasks.updated_at migration skipped: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("059_vehicle_state_active_billing_index")
async def migrate_vehicle_state_active_billing_index(conn) -> None:
    """Index ``vehicle_state(account_id, captured_at)`` for billing scans.

    The billing layer counts "active vehicles" (any Samsara signal in
    the last 3 days) by scanning ``vehicle_state.captured_at`` per
    account.  Without this index the query is a full sequential scan of
    every vehicle ever seen for every account on every billing sync —
    cheap at 10 fleets, ugly at 1000.
    """
    try:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vehicle_state_active_billing "
            "ON vehicle_state(account_id, captured_at)"
        )
        await conn.commit()
        logger.info("Migration 059: created idx_vehicle_state_active_billing")
    except Exception as e:
        logger.debug("vehicle_state active-billing index migration skipped: %s", e)


# ── 060: PTI (Pre-Trip Inspection) module ──────────────────────────────────
#
# Extends the bare ``driver_inspections`` table from migration 050 with the
# full PTI workflow: per-item checklist snapshot, photo/video media, editable
# per-account checklist templates, status workflow (scheduled → submitted →
# approved / needs_service / rejected), and a one-time seed of the Standard
# DOT default template (truck + trailer) for every existing account.

# Default checklist — kept here (not in a Python module) so a fresh DB
# migrated standalone still gets the seed without needing the capability
# layer importable.  ``capabilities/pti/templates.py`` re-exports these for
# runtime use; the seed below is the source of truth for new accounts.
_STANDARD_DOT_TRUCK_ITEMS: list[tuple[str, str, str, bool, bool, int]] = [
    # (item_key, label, category, requires_media, required, sort_order)
    ("brakes_service",    "Service brake response",                "brakes",     False, True,  1),
    ("brakes_parking",    "Parking brake hold",                    "brakes",     False, True,  2),
    ("tires_walkaround",  "All tires — tread depth + sidewall",    "tires",      True,  True,  3),
    ("lights_all",        "Headlights / brake / turn / hazard",    "lights",     False, True,  4),
    ("mirrors",           "Mirrors — alignment + condition",       "mirrors",    False, True,  5),
    ("wipers",            "Wipers + washer fluid",                 "cab",        False, True,  6),
    ("horn",              "Horn",                                  "cab",        False, True,  7),
    ("fluids",            "Oil / coolant / power steering",        "fluids",     False, True,  8),
    ("belts_hoses",       "Belts + hoses",                         "engine",     False, True,  9),
    ("battery",           "Battery + cable condition",             "engine",     False, True, 10),
    ("seatbelt",          "Seatbelt + cab integrity",              "cab",        False, True, 11),
    ("emergency_kit",     "Triangles / extinguisher / spare fuses", "safety",    False, True, 12),
    ("fifth_wheel",       "Fifth wheel + coupling",                "coupling",   True,  True, 13),
    ("suspension",        "Suspension + air bags",                 "chassis",    False, True, 14),
    ("exhaust",           "Exhaust + air intake",                  "engine",     False, True, 15),
    ("fuel_def",          "Fuel + DEF level",                      "fluids",     False, True, 16),
    ("plate_visible",     "License plate + DOT number visible",    "exterior",   False, True, 17),
    ("walkaround_damage", "Walkaround damage — 4 angles",          "walkaround", True,  True, 18),
]

_STANDARD_DOT_TRAILER_ITEMS: list[tuple[str, str, str, bool, bool, int]] = [
    ("tr_tires",          "Trailer tires — tread + sidewall",      "tires",      True,  True,  1),
    ("tr_lights",         "Trailer lights — brake / marker / turn", "lights",    False, True,  2),
    ("tr_doors",          "Doors + latches + seal",                "exterior",   False, True,  3),
    ("tr_floor",          "Cargo floor + walls",                   "interior",   False, True,  4),
    ("tr_landing_gear",   "Landing gear",                          "chassis",    False, True,  5),
    ("tr_brakes",         "Trailer brakes + air lines",            "brakes",     False, True,  6),
    ("tr_suspension",     "Trailer suspension",                    "chassis",    False, True,  7),
    ("tr_reflectors",     "Reflectors + conspicuity tape",         "exterior",   False, True,  8),
    ("tr_plate",          "License plate visible",                 "exterior",   False, True,  9),
    ("tr_damage",         "Walkaround damage — 4 angles",          "walkaround", True,  True, 10),
]


async def _seed_pti_template_for_account(
    conn, account_id: int, vehicle_type: str, items: list[tuple],
) -> None:
    """Insert one default template + its items for an account.

    Idempotent on the unique ``(account_id, vehicle_type, inspection_type,
    version)`` constraint — re-running this migration won't double-seed.
    """
    now = (
        __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat()
    )
    cur = await conn.execute(
        "SELECT id FROM pti_checklist_templates "
        "WHERE account_id = ? AND vehicle_type = ? "
        "AND inspection_type = 'weekly' AND is_active = 1",
        (account_id, vehicle_type),
    )
    row = await cur.fetchone()
    if row:
        return
    cur = await conn.execute(
        "INSERT INTO pti_checklist_templates "
        "(account_id, vehicle_type, inspection_type, version, is_active, "
        " created_at, updated_at) "
        "VALUES (?, ?, 'weekly', 1, 1, ?, ?)",
        (account_id, vehicle_type, now, now),
    )
    template_id = cur.lastrowid
    for item_key, label, category, requires_media, required, sort_order in items:
        await conn.execute(
            "INSERT INTO pti_checklist_template_items "
            "(template_id, item_key, label, category, requires_media, "
            " required, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                template_id, item_key, label, category,
                1 if requires_media else 0,
                1 if required else 0,
                sort_order,
            ),
        )


@_register("060_pti_inspection_full")
async def migrate_pti_inspection_full(conn) -> None:
    """PTI workflow tables + Standard DOT template seed.

    Five-part migration:

    1. Extend ``driver_inspections`` from migration 050 with workflow
       columns (scheduled_for, due_by, submitted_at, reviewed_at,
       review_status, template_id, defects_count, has_oos_defect,
       trailer_name, recurrence_parent_id).
    2. Create ``pti_inspection_items`` — per-inspection checklist
       snapshot, copied from the active template at spawn time so
       template edits don't retroactively change submitted inspections.
    3. Create ``pti_inspection_media`` — photo/video locator rows
       backed by the existing ``ObjectStore`` (same path the work-orders
       module uses).
    4. Create ``pti_checklist_templates`` + ``pti_checklist_template_items``
       — fleet-editable per-account checklist, versioned so a new
       version can ship without breaking inspections in flight.
    5. Seed Standard DOT template (truck + trailer) for every active
       account.  Idempotent — re-running is a no-op when the templates
       already exist.

    See ``capabilities/pti/`` for the runtime layer.  Permissions and
    cron wiring land in follow-up migrations / commits.
    """
    # ── Part 1: extend driver_inspections ──────────────────────────────
    new_cols = [
        ("trailer_name",          "TEXT"),
        ("scheduled_for",         "TEXT"),
        ("due_by",                "TEXT"),
        ("submitted_at",          "TEXT"),
        ("reviewed_at",           "TEXT"),
        ("reviewed_by",           "BIGINT"),
        ("review_status",         "TEXT"),
        ("review_notes",          "TEXT"),
        ("template_id",           "INTEGER"),
        ("template_version",      "INTEGER"),
        ("defects_count",         "INTEGER NOT NULL DEFAULT 0"),
        ("has_oos_defect",        "INTEGER NOT NULL DEFAULT 0"),
        ("recurrence_parent_id",  "INTEGER"),
    ]
    try:
        cur = await conn.execute("PRAGMA table_info(driver_inspections)")
        existing = {r[1] for r in await cur.fetchall()}
    except Exception:
        existing = set()
    for col_name, col_def in new_cols:
        if col_name in existing:
            continue
        try:
            await conn.execute(
                f"ALTER TABLE driver_inspections ADD COLUMN {col_name} {col_def}"
            )
        except Exception as e:
            logger.debug("driver_inspections.%s already present: %s", col_name, e)

    # ── Part 2-4: new tables ───────────────────────────────────────────
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS pti_inspection_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                inspection_id   INTEGER NOT NULL REFERENCES driver_inspections(id) ON DELETE CASCADE,
                item_key        TEXT    NOT NULL,
                label           TEXT    NOT NULL,
                category        TEXT    NOT NULL DEFAULT '',
                status          TEXT    NOT NULL DEFAULT 'pending',
                notes           TEXT,
                requires_media  INTEGER NOT NULL DEFAULT 0,
                required        INTEGER NOT NULL DEFAULT 1,
                sort_order      INTEGER NOT NULL DEFAULT 0,
                completed_at    TEXT,
                UNIQUE(inspection_id, item_key)
            );
            CREATE INDEX IF NOT EXISTS idx_pti_items_inspection
                ON pti_inspection_items(inspection_id);

            CREATE TABLE IF NOT EXISTS pti_inspection_media (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                inspection_id   INTEGER NOT NULL REFERENCES driver_inspections(id) ON DELETE CASCADE,
                item_id         INTEGER REFERENCES pti_inspection_items(id) ON DELETE SET NULL,
                media_type      TEXT    NOT NULL DEFAULT 'photo',
                file_path       TEXT    NOT NULL,
                file_name       TEXT    NOT NULL DEFAULT '',
                file_size       INTEGER NOT NULL DEFAULT 0,
                content_type    TEXT    NOT NULL DEFAULT '',
                uploaded_by     BIGINT  NOT NULL DEFAULT 0,
                uploaded_at     TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pti_media_inspection
                ON pti_inspection_media(inspection_id);
            CREATE INDEX IF NOT EXISTS idx_pti_media_item
                ON pti_inspection_media(item_id);

            CREATE TABLE IF NOT EXISTS pti_checklist_templates (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL REFERENCES accounts(id),
                vehicle_type    TEXT    NOT NULL,
                inspection_type TEXT    NOT NULL DEFAULT 'weekly',
                version         INTEGER NOT NULL DEFAULT 1,
                is_active       INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL,
                UNIQUE(account_id, vehicle_type, inspection_type, version)
            );
            CREATE INDEX IF NOT EXISTS idx_pti_templates_account
                ON pti_checklist_templates(account_id, vehicle_type, is_active);

            CREATE TABLE IF NOT EXISTS pti_checklist_template_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id     INTEGER NOT NULL REFERENCES pti_checklist_templates(id) ON DELETE CASCADE,
                item_key        TEXT    NOT NULL,
                label           TEXT    NOT NULL,
                category        TEXT    NOT NULL DEFAULT '',
                requires_media  INTEGER NOT NULL DEFAULT 0,
                required        INTEGER NOT NULL DEFAULT 1,
                sort_order      INTEGER NOT NULL DEFAULT 0,
                UNIQUE(template_id, item_key)
            );
            CREATE INDEX IF NOT EXISTS idx_pti_template_items_template
                ON pti_checklist_template_items(template_id);

            -- Dashboard list filters by review_status + due_by, often
            -- combined with account_id; this index covers both.
            CREATE INDEX IF NOT EXISTS idx_driver_inspections_review
                ON driver_inspections(account_id, review_status);
            CREATE INDEX IF NOT EXISTS idx_driver_inspections_due
                ON driver_inspections(account_id, due_by);
        """)
        await conn.commit()
        logger.info("Migration 060: PTI tables + indexes created")
    except Exception as e:
        logger.error("Migration 060 table creation failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise

    # ── Part 5: seed Standard DOT for every existing active account ───
    try:
        cur = await conn.execute(
            "SELECT id FROM accounts WHERE is_active = 1"
        )
        account_ids = [r[0] for r in await cur.fetchall()]
        seeded = 0
        for aid in account_ids:
            await _seed_pti_template_for_account(
                conn, aid, "truck", _STANDARD_DOT_TRUCK_ITEMS,
            )
            await _seed_pti_template_for_account(
                conn, aid, "trailer", _STANDARD_DOT_TRAILER_ITEMS,
            )
            seeded += 1
        await conn.commit()
        logger.info(
            "Migration 060: seeded Standard DOT template for %d active accounts",
            seeded,
        )
    except Exception as e:
        logger.error("Migration 060 seed failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("061_maintenance_tasks_last_odometer_repair")
async def migrate_maintenance_tasks_last_odometer_repair(conn) -> None:
    """Idempotent repair for ``maintenance_tasks.last_odometer``.

    Migration 056 (``056_maintenance_tasks_last_odometer``) tried to
    add this column but its error handler logged failures at DEBUG
    and swallowed them — so any production where the ``ALTER TABLE``
    failed (race, permission, schema state, etc.) ended up with the
    migration marked applied in ``_schema_versions`` but no column
    on the table.  The overdue-mileage scheduler then fails every
    6h with ``UndefinedColumnError: column "last_odometer" of
    relation "maintenance_tasks" does not exist``.

    This migration uses ``ADD COLUMN IF NOT EXISTS`` so it's safe
    on databases where 056 DID succeed (no-op) AND repairs the ones
    where it silently didn't.  Any unexpected error re-raises so
    the migration runner records the real failure instead of
    pretending success.
    """
    try:
        await conn.execute(
            "ALTER TABLE maintenance_tasks "
            "ADD COLUMN IF NOT EXISTS last_odometer REAL"
        )
        await conn.commit()
        logger.info(
            "Migration 061: ensured maintenance_tasks.last_odometer exists"
        )
    except Exception as e:
        logger.error(
            "Migration 061 failed: %s", e, exc_info=True,
        )
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("062_driver_inspections_alerted_at")
async def migrate_driver_inspections_alerted_at(conn) -> None:
    """Add ``driver_inspections.alerted_at`` for PTI ping throttle.

    The PTI bot reminder cron pings each driver whose inspection is in
    the due-soon window.  Without a throttle column, the hourly cron
    re-pings every tick.  ``alerted_at`` is stamped after a successful
    send; the cron skips rows where it's already set in the current
    cycle (same pattern maintenance_tasks uses for overdue alerts).
    """
    try:
        await conn.execute(
            "ALTER TABLE driver_inspections "
            "ADD COLUMN IF NOT EXISTS alerted_at TEXT"
        )
        await conn.commit()
        logger.info("Migration 062: ensured driver_inspections.alerted_at exists")
    except Exception as e:
        logger.error("Migration 062 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("063_alert_ack_expires_at")
async def migrate_alert_ack_expires_at(conn) -> None:
    """Add ``alert_acknowledgments.expires_at`` — first-class TTL.

    Why
    ───
    Before this column existed, ack rows had only two ways to close:
    a user tapping Acknowledge, or the system auto-resolving when
    the underlying condition cleared.  If neither happened (chronic
    fault on a known-broken truck, driver who never acks), the rows
    accumulated unbounded — one account hit 1895 pending rows.

    ``expires_at`` lets the system express "this alert has aged out
    of operational relevance" as a first-class property of the row,
    instead of bolting on a periodic "stale sweep" that pretends
    those rows don't exist.  Dashboard queries can WHERE-filter on
    it; the nightly job becomes the enforcer of the TTL contract
    (closes expired rows + history), not a separate "cleanup"
    concept.

    Default ladder (applied by ``send_alert`` at insert time):
      * critical → NULL  (never expires — must be acked / resolved)
      * warning  → created_at + 14 days
      * info     → created_at + 7 days

    Existing rows get NULL on backfill — they fall under the old
    "sweep" logic until they're naturally closed.  Future fires
    populate the column.

    Stored as TEXT (ISO-8601) to match the existing date columns
    in this table; lexicographic comparison is correct for the
    ISO-8601 format.
    """
    try:
        await conn.execute(
            "ALTER TABLE alert_acknowledgments "
            "ADD COLUMN IF NOT EXISTS expires_at TEXT"
        )
        # Partial index on non-null expiries so the nightly sweep
        # query (``WHERE expires_at < NOW()``) hits a small index
        # rather than scanning the whole table.  Critical alerts
        # have ``expires_at IS NULL`` and don't appear in the index.
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_ack_expires_at "
            "ON alert_acknowledgments(expires_at) "
            "WHERE expires_at IS NOT NULL"
        )
        await conn.commit()
        logger.info(
            "Migration 063: ensured alert_acknowledgments.expires_at + index"
        )
    except Exception as e:
        logger.error("Migration 063 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("064_chronic_alert_suppressions")
async def migrate_chronic_alert_suppressions(conn) -> None:
    """Add ``chronic_alert_suppressions`` — pattern-level mute table.

    Why a separate table from ``alert_mutes``
    ─────────────────────────────────────────
    ``alert_mutes`` keys mute rows to a specific ``alert_history_id`` —
    i.e. a single fire.  Under the per-fire model (each fire creates a
    new ``alert_history`` row with a unique ``alert_subkey``), an
    ``alert_mutes`` row only silences that exact fire; the next fire
    of the same chronic pattern creates a new history row that
    bypasses the mute.

    ``chronic_alert_suppressions`` keys mutes to the broader
    ``(account, alert_type, vehicle_id)`` pattern, so a chronic
    Mostly-Same DTC stays suppressed for the duration regardless of
    how many new alert_history rows the per-fire model creates.

    Usage flow (Fix C):
      1. Operator notices Truck #237's ABS sensor keeps firing.
      2. They (or an auto-rule based on occurrence count) call
         ``mute_chronic_pattern(account, "fault", "v_237", hours=168)``.
      3. ``send_alert`` checks ``is_chronic_pattern_muted`` before
         creating ack rows or sending Telegram — chronic fires now
         only update ``alert_history.occurrence_count`` (data
         preserved for audit) without spamming the chat.
      4. After ``muted_until`` passes, the next fire re-enables
         normal delivery automatically.

    UNIQUE constraint on (account, type, vehicle_id) means each
    pattern has at most one active mute row.  Re-muting the same
    pattern UPDATEs the existing row's ``muted_until`` rather than
    inserting a duplicate (handled in the mixin's helper).
    """
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chronic_alert_suppressions (
                id          SERIAL PRIMARY KEY,
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                alert_type  TEXT    NOT NULL,
                vehicle_id  TEXT    NOT NULL,
                muted_by    BIGINT,
                muted_until TEXT    NOT NULL,
                reason      TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL,
                UNIQUE (account_id, alert_type, vehicle_id)
            );
        """)
        # Hot-path index: ``is_chronic_pattern_muted`` runs on every
        # alert fire, so it MUST be cheap.  The partial index limits
        # itself to currently-active mutes (a few hundred rows even
        # for a noisy fleet) instead of scanning history.
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chronic_suppress_active "
            "ON chronic_alert_suppressions(account_id, alert_type, vehicle_id, muted_until)"
        )
        await conn.commit()
        logger.info("Migration 064: chronic_alert_suppressions table + index created")
    except Exception as e:
        logger.error("Migration 064 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("065_driver_inspections_signatures")
async def migrate_driver_inspections_signatures(conn) -> None:
    """Add e-signature columns to ``driver_inspections`` for DOT audit.

    Two signatures per inspection:
      * ``driver_signature``  + ``driver_signed_at``   — captured at
        submit time inside the Mini App wizard.  Required to flip the
        inspection from ``in_progress`` to ``submitted``.
      * ``reviewer_signature`` + ``reviewer_signed_at`` — optional
        co-signature captured on the dashboard review modal.  Useful
        when an account's compliance workflow needs a second human in
        the chain (insurance / FMCSA audits).

    Stored as base64-encoded PNG data URLs directly on the row rather
    than offloaded to ObjectStore: signatures are tiny (5–15 KB), only
    one per role per inspection, and we always read them together with
    the rest of the row.  Embedding avoids a second HTTP round-trip on
    the dashboard detail drawer.
    """
    cols: list[tuple[str, str]] = [
        ("driver_signature",    "TEXT"),
        ("driver_signed_at",    "TEXT"),
        ("reviewer_signature",  "TEXT"),
        ("reviewer_signed_at",  "TEXT"),
    ]
    try:
        for col_name, col_def in cols:
            try:
                await conn.execute(
                    f"ALTER TABLE driver_inspections "
                    f"ADD COLUMN IF NOT EXISTS {col_name} {col_def}"
                )
            except Exception as e:
                # Same idempotence pattern as 060 — IF NOT EXISTS on
                # ALTER TABLE wasn't supported on every Postgres
                # version we ship to, so we swallow "already exists".
                logger.debug(
                    "driver_inspections.%s already present: %s", col_name, e,
                )
        await conn.commit()
        logger.info(
            "Migration 065: driver_inspections signature columns ensured",
        )
    except Exception as e:
        logger.error("Migration 065 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("066_pti_media_annotated_at")
async def migrate_pti_media_annotated_at(conn) -> None:
    """Add ``annotated_at`` to ``pti_inspection_media``.

    Driver-side defect annotation bakes red-marker overlays directly
    into the canonical PNG (single file for DOT audit — no separate
    overlay layer to lose).  ``annotated_at`` records WHEN the overlay
    was applied so the dashboard can render an "Annotated" badge and
    so the bot's notify-to-fleet ping can mention that the driver
    has marked the defect location.

    Null = original capture, untouched by the annotator.
    """
    try:
        try:
            await conn.execute(
                "ALTER TABLE pti_inspection_media "
                "ADD COLUMN IF NOT EXISTS annotated_at TEXT"
            )
        except Exception as e:
            logger.debug(
                "pti_inspection_media.annotated_at already present: %s", e,
            )
        await conn.commit()
        logger.info(
            "Migration 066: pti_inspection_media.annotated_at ensured",
        )
    except Exception as e:
        logger.error("Migration 066 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("067_pti_media_ai_review")
async def migrate_pti_media_ai_review(conn) -> None:
    """Add per-photo AI-review columns to ``pti_inspection_media``.

    When a driver captures a checklist photo, the Mini App fires an
    automatic Gemini-vision check of that single photo against the
    component it's for (e.g. "does this mudflap photo show damage?").
    The verdict is stored per-media so it both:
      * gives the driver instant feedback in the wizard, and
      * rolls up into the final PTI report the fleet reviewer reads.

    Columns:
      * ``ai_review_status``  — 'pending' | 'completed' | 'error'
      * ``ai_review_result``  — JSON blob: {verdict, summary,
                                 confidence, model}.  verdict is one of
                                 ok|possible_issue|likely_defect|unclear.
      * ``ai_reviewed_at``    — ISO timestamp of the last check.

    All null = never checked (AI disabled, or video, or pre-feature
    rows).  The dashboard + wizard treat null as "no AI verdict".
    """
    cols: list[tuple[str, str]] = [
        ("ai_review_status",  "TEXT"),
        ("ai_review_result",  "TEXT"),
        ("ai_reviewed_at",    "TEXT"),
    ]
    try:
        for col_name, col_def in cols:
            try:
                await conn.execute(
                    f"ALTER TABLE pti_inspection_media "
                    f"ADD COLUMN IF NOT EXISTS {col_name} {col_def}"
                )
            except Exception as e:
                logger.debug(
                    "pti_inspection_media.%s already present: %s", col_name, e,
                )
        await conn.commit()
        logger.info(
            "Migration 067: pti_inspection_media AI-review columns ensured",
        )
    except Exception as e:
        logger.error("Migration 067 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("068_alert_history_acknowledged_by")
async def migrate_alert_history_acknowledged_by(conn) -> None:
    """Record *who* acknowledged each logical alert on ``alert_history``.

    Why
    ───
    Acknowledging an alert cascaded the actor (``acknowledged_by`` +
    ``acknowledged_at``) onto the per-delivery ``alert_acknowledgments``
    rows, but the canonical ``alert_history`` row — the one the
    dashboard reads — only flipped ``status`` to 'cleared'.  So the
    history row knew it was closed, but not by whom.

    The dashboard now shows acknowledged alerts in the windowed view
    (not just active ones), and each needs an accountability line:

      * ``acknowledged_by > 0``  → human ack → "Acknowledged by {name}"
      * ``acknowledged_by`` NULL → self-cleared by a check loop
                                   (``clear_alert_history``) → "Auto-resolved"

    Resolution to a display name happens at read time via a LEFT JOIN
    onto ``users.telegram_id`` so renames flow through automatically.

    ``idx_alert_history_window`` backs the new date-windowed query
    (``WHERE account_id = ? AND first_seen >= ?``) the dashboard uses
    to scope alerts to the selected range (7d / 30d / 90d).
    """
    cols: list[tuple[str, str]] = [
        ("acknowledged_by",  "BIGINT"),
        ("acknowledged_at",  "TEXT"),
    ]
    try:
        for col_name, col_def in cols:
            try:
                await conn.execute(
                    f"ALTER TABLE alert_history "
                    f"ADD COLUMN IF NOT EXISTS {col_name} {col_def}"
                )
            except Exception as e:
                logger.debug(
                    "alert_history.%s already present: %s", col_name, e,
                )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_history_window "
            "ON alert_history(account_id, first_seen DESC)"
        )
        await conn.commit()
        logger.info(
            "Migration 068: alert_history acknowledged_by/at + window index",
        )
    except Exception as e:
        logger.error("Migration 068 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("069_pti_item_types_refimg_location")
async def migrate_pti_item_types_refimg_location(conn) -> None:
    """PTI template/inspection enrichment: item types, reference photos,
    and inspection geolocation.

    Three additions land together because they all extend the PTI
    capture model:

    1. ``item_type`` on template + inspection items — distinguishes the
       capture widget the driver sees:
         * ``check``    (default) — OK/Minor/Major/OOS/N/A buttons
         * ``photo``    — a guided walk-around photo point (no status,
                          gate = photo present)
         * ``document`` — upload a compliance doc (annual report,
                          registration) as a PDF or photo of paper
       Existing rows default to ``check`` so behaviour is unchanged.

    2. ``reference_image_url`` on template + inspection items — an
       optional example image the fleet uploads so the driver knows
       what a correct photo looks like.  Snapshotted onto the
       inspection item at spawn so an in-flight inspection keeps the
       reference even if the template later changes.

    3. ``location_*`` on ``driver_inspections`` — WHERE the inspection
       happened.  Primary source is the vehicle's telematics position
       (no device-permission prompt); device GPS is a fallback.
         * ``location_lat`` / ``location_lon`` — coordinates
         * ``location_source`` — 'vehicle' | 'device' | 'none'
         * ``location_at``     — ISO timestamp the fix was resolved
    """
    item_cols: list[tuple[str, str, str]] = [
        ("pti_checklist_template_items", "item_type",           "TEXT DEFAULT 'check'"),
        ("pti_checklist_template_items", "reference_image_url", "TEXT"),
        ("pti_inspection_items",         "item_type",           "TEXT DEFAULT 'check'"),
        ("pti_inspection_items",         "reference_image_url", "TEXT"),
        ("driver_inspections",           "location_lat",        "REAL"),
        ("driver_inspections",           "location_lon",        "REAL"),
        ("driver_inspections",           "location_source",     "TEXT"),
        ("driver_inspections",           "location_at",         "TEXT"),
    ]
    try:
        for table, col_name, col_def in item_cols:
            try:
                await conn.execute(
                    f"ALTER TABLE {table} "
                    f"ADD COLUMN IF NOT EXISTS {col_name} {col_def}"
                )
            except Exception as e:
                logger.debug("%s.%s already present: %s", table, col_name, e)
        await conn.commit()
        logger.info(
            "Migration 069: PTI item_type + reference_image_url + "
            "inspection location columns ensured",
        )
    except Exception as e:
        logger.error("Migration 069 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("070_hybrid_storage_foundation")
async def migrate_hybrid_storage_foundation(conn) -> None:
    """Foundation for the hybrid disk-primary / cloud-async storage model.

    Phase 1 of the per-tenant tiered-storage rollout — see the design
    audit in the project notes.  This migration is intentionally
    additive and inert: existing media rows keep working unchanged
    (``storage_state`` defaults to ``'remote'`` for legacy rows, so
    the eventual ``HybridObjectStore.get`` reads them via the legacy
    ``file_path`` fallback).

    Adds:

    1. **Media-row state extension on ``pti_inspection_media``** —
       ``local_path`` / ``remote_path`` / ``storage_state`` /
       ``sha256``.  The forward-only state machine is
       ``local → syncing → remote`` with ``stuck`` as a side state
       reached when an account's sync is blocked (e.g. expired Drive
       token).  Legacy rows default to ``'remote'`` so they're
       considered already-settled and ignored by the sync worker.

    2. **``storage_sync_queue``** — the durable DB outbox.  Polymorphic
       (``entity_type`` + ``entity_id``) so PTI media, work-order
       attachments, driver docs, etc. all queue through one table
       without further migrations.  Rows survive process restarts —
       a worker crash leaves the file safely on disk + the queue row
       intact, and the next poll resumes the upload.

    3. **Indexes for the SKIP-LOCKED polling pattern** — every worker
       claims due rows via ``SELECT … FOR UPDATE SKIP LOCKED`` (Phase
       3), which scales horizontally with zero code changes.  The
       ``(next_attempt_at)`` index makes the "what's due now" query
       cheap forever; the ``(account_id, next_attempt_at)`` index
       makes per-account fair scheduling (Phase 3) cheap forever.

    No data backfill — every existing row's ``storage_state`` ends up
    ``'remote'`` via the column default, which exactly matches the
    legacy assumption that ``file_path`` is the canonical location.
    """
    media_cols: list[tuple[str, str]] = [
        ("local_path",     "TEXT"),
        ("remote_path",    "TEXT"),
        # 'local' | 'syncing' | 'remote' | 'stuck'.  Default 'remote'
        # so legacy rows are treated as already-settled.
        ("storage_state",  "TEXT DEFAULT 'remote'"),
        ("sha256",         "TEXT"),
    ]
    try:
        for col_name, col_def in media_cols:
            try:
                await conn.execute(
                    f"ALTER TABLE pti_inspection_media "
                    f"ADD COLUMN IF NOT EXISTS {col_name} {col_def}"
                )
            except Exception as e:
                logger.debug(
                    "pti_inspection_media.%s already present: %s", col_name, e,
                )

        # Polymorphic outbox.  ``entity_type`` + ``entity_id`` lets the
        # same queue route PTI media, work-order attachments, driver
        # docs, etc. without a per-module table.  UNIQUE(entity_type,
        # entity_id) makes enqueue idempotent — re-enqueueing the same
        # media is a no-op rather than a duplicate row.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS storage_sync_queue (
                id              SERIAL PRIMARY KEY,
                account_id      INTEGER NOT NULL REFERENCES accounts(id),
                entity_type     TEXT    NOT NULL,
                entity_id       INTEGER NOT NULL,
                bucket          TEXT    NOT NULL,
                filename        TEXT    NOT NULL,
                local_path      TEXT    NOT NULL,
                file_size       BIGINT  NOT NULL DEFAULT 0,
                attempts        INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT    NOT NULL,
                last_error      TEXT,
                error_code      TEXT,
                enqueued_at     TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL,
                UNIQUE (entity_type, entity_id)
            );
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_storage_sync_queue_due "
            "ON storage_sync_queue(next_attempt_at)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_storage_sync_queue_account_due "
            "ON storage_sync_queue(account_id, next_attempt_at)"
        )
        await conn.commit()
        logger.info(
            "Migration 070: hybrid-storage foundation ensured "
            "(media columns + storage_sync_queue + indexes)",
        )
    except Exception as e:
        logger.error("Migration 070 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("071_maintenance_tasks_snoozed_until")
async def migrate_maintenance_tasks_snoozed_until(conn) -> None:
    """Add ``snoozed_until`` (ISO timestamp) to maintenance_tasks.

    The overdue / mileage / engine-hours schedulers consult this column
    and skip any task where ``snoozed_until > now`` so the operator can
    suppress a red-flag task while parts are on order without
    re-triggering the alert every 6h.  ``NULL`` means "not snoozed";
    snooze expires automatically once the timestamp falls into the past.
    No index — the column is filtered alongside ``alerted_at IS NULL``
    inside small per-account queries, not in a full-table hot path.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(maintenance_tasks)")
        cols = {r[1] for r in await cur.fetchall()}
        if "snoozed_until" in cols:
            return
        await conn.execute(
            "ALTER TABLE maintenance_tasks ADD COLUMN snoozed_until TEXT"
        )
        await conn.commit()
        logger.info("Migration 071: added maintenance_tasks.snoozed_until")
    except Exception as e:
        logger.error("Migration 071 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("072_maintenance_tasks_attachment")
async def migrate_maintenance_tasks_attachment(conn) -> None:
    """Add 3 attachment columns to maintenance_tasks.

    Lightweight one-file attachment so drivers can attach a phone-photo
    of a roadside DEF refill or oil-receipt without spinning up a full
    Work Order.  Work Orders still own the multi-file invoice/photo
    timeline for shop visits; this is for quick driver-side proof.

    ``attachment_path`` — opaque key returned by ObjectStore.put.
    ``attachment_name`` — original filename (sanitized).
    ``attachment_content_type`` — for the download response.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(maintenance_tasks)")
        cols = {r[1] for r in await cur.fetchall()}
        for col in ("attachment_path", "attachment_name", "attachment_content_type"):
            if col in cols:
                continue
            await conn.execute(
                f"ALTER TABLE maintenance_tasks ADD COLUMN {col} TEXT"
            )
        await conn.commit()
        logger.info("Migration 072: added maintenance_tasks.attachment_*")
    except Exception as e:
        logger.error("Migration 072 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("073_maintenance_tasks_cost_vendor")
async def migrate_maintenance_tasks_cost_vendor(conn) -> None:
    """Add ``cost_cents`` + ``vendor_name`` to maintenance_tasks.

    Captures the spend total + who did the work for tasks closed
    outside the formal Work Order flow (driver roadside DEF refill,
    in-house tech quickie).  The Work Orders module owns the
    multi-line invoice for shop visits; this is a single-cell
    fallback so cost visibility doesn't disappear when a Work Order
    wasn't created.  ``cost_cents`` (INTEGER) avoids floating-point
    drift on aggregate totals; ``vendor_name`` (TEXT) is free-text
    because shops aren't first-class entities yet.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(maintenance_tasks)")
        cols = {r[1] for r in await cur.fetchall()}
        for col, ddl in (
            ("cost_cents", "INTEGER"),
            ("vendor_name", "TEXT"),
        ):
            if col in cols:
                continue
            await conn.execute(
                f"ALTER TABLE maintenance_tasks ADD COLUMN {col} {ddl}"
            )
        await conn.commit()
        logger.info("Migration 073: added maintenance_tasks.cost_cents + vendor_name")
    except Exception as e:
        logger.error("Migration 073 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("074_maintenance_templates")
async def migrate_maintenance_templates(conn) -> None:
    """Create the ``maintenance_templates`` table.

    A template is a re-usable task definition: name + default
    task_type, description, priority, due triggers (date/miles/hours),
    and recurrence intervals.  Applying a template to a vehicle
    creates a regular ``maintenance_tasks`` row with the template's
    defaults; templates themselves don't accrue completion data.

    Templates are account-scoped — each tenant defines their own set.
    No tenant-cross visibility (no global templates yet); when that
    becomes useful, a NULL ``account_id`` can mean "system template".
    """
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS maintenance_templates (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id    INTEGER NOT NULL,
                name          TEXT    NOT NULL,
                task_type     TEXT    NOT NULL DEFAULT 'custom',
                description   TEXT    NOT NULL DEFAULT '',
                priority      TEXT    NOT NULL DEFAULT 'medium',
                due_in_days   INTEGER,
                due_in_miles  REAL,
                due_in_hours  REAL,
                recur_interval_days         INTEGER,
                recur_interval_miles        REAL,
                recur_interval_engine_hours REAL,
                created_by    INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT    NOT NULL,
                updated_at    TEXT    NOT NULL,
                UNIQUE(account_id, name)
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_maintenance_templates_account "
            "ON maintenance_templates(account_id)"
        )
        await conn.commit()
        logger.info("Migration 074: created maintenance_templates table")
    except Exception as e:
        logger.error("Migration 074 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("075_userdata_path_prefix")
async def migrate_userdata_path_prefix(conn) -> None:
    """Rewrite stored file paths from ``data/X`` to ``data/userdata/X``.

    The on-disk layout split (``data/system/`` for platform files,
    ``data/userdata/`` for tenant blobs) requires every column that
    stores a disk-relative file path to gain the ``userdata/`` segment.
    Drive-backed rows (where the column holds an opaque Drive file ID,
    not a path) are untouched — the ``LIKE 'data/%'`` filter skips them
    naturally.

    Columns rewritten:
      * ``pti_inspection_media.file_path``
      * ``pti_inspection_media.local_path``
      * ``work_order_attachments.file_path``
      * ``maintenance_tasks.attachment_path``
      * ``camera_checks.image_path``
      * ``parking_events.map_image_path``
      * ``storage_sync_queue.local_path``

    Idempotent: rerunning is a no-op because rows that already start
    with ``data/userdata/`` are excluded.  Reversible: a single UPDATE
    can strip the ``userdata/`` segment if you ever need to roll back.
    """
    columns = [
        ("pti_inspection_media",   "file_path"),
        ("pti_inspection_media",   "local_path"),
        ("work_order_attachments", "file_path"),
        ("maintenance_tasks",      "attachment_path"),
        ("camera_checks",          "image_path"),
        ("parking_events",         "map_image_path"),
        ("storage_sync_queue",     "local_path"),
    ]
    try:
        total = 0
        for table, col in columns:
            try:
                cur = await conn.execute(
                    f"UPDATE {table} "
                    f"SET {col} = 'data/userdata/' || substr({col}, 6) "
                    f"WHERE {col} LIKE 'data/%' "
                    f"AND {col} NOT LIKE 'data/userdata/%'"
                )
                rowcount = getattr(cur, "rowcount", 0) or 0
                total += rowcount
                logger.info(
                    "Migration 075: %s.%s rewrote %d row(s)", table, col, rowcount,
                )
            except Exception as e:
                # ``maintenance_tasks.attachment_path`` was added by migration
                # 072 and may be missing on installations that never ran 072
                # (e.g. fresh tests).  Log and continue — the absence of the
                # column is benign for this UPDATE.
                logger.warning(
                    "Migration 075: skipping %s.%s (%s)", table, col, e,
                )
        await conn.commit()
        logger.info("Migration 075: rewrote %d path(s) under userdata/", total)
    except Exception as e:
        logger.error("Migration 075 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise



@_register("076_digest_delivery_channels")
async def migrate_digest_delivery_channels(conn) -> None:
    """Add ``delivery_channels`` to ``digest_subscriptions``.

    Enables Scheduled Reports email delivery on top of the existing
    Telegram channel.  Stored as a comma-separated string ("telegram",
    "email", or "telegram,email") so callers can pick either or both.
    Existing rows get the default ``'telegram'`` — pre-2026-06
    subscribers keep their current behaviour unchanged.
    """
    try:
        await conn.execute(
            "ALTER TABLE digest_subscriptions "
            "ADD COLUMN delivery_channels TEXT NOT NULL DEFAULT 'telegram'"
        )
        await conn.commit()
        logger.info("Added column digest_subscriptions.delivery_channels")
    except Exception:
        # Column already exists (idempotent rerun).
        pass


@_register("077_digest_unique_per_report_type")
async def migrate_digest_unique_per_report_type(conn) -> None:
    """Replace ``UNIQUE(user_id)`` with ``UNIQUE(user_id, report_type)``.

    Enables multi-schedule support — one user can now have Faults Daily,
    Fuel Weekly, Health Monthly, etc. as independent rows.  The pre-2026
    layout forced a user to pick one report only.

    Postgres allows ``ALTER TABLE`` constraint juggling; SQLite (test
    runs) does not, so we drop+recreate on SQLite via temp-table copy.
    Either path preserves every existing row.  Idempotent: a re-run on
    the new constraint just rediscovers it and exits.
    """
    try:
        # Postgres path — direct constraint swap.  Constraint names are
        # the defaults Postgres assigns when the table was created with
        # ``UNIQUE(user_id)``.
        await conn.execute(
            "ALTER TABLE digest_subscriptions "
            "DROP CONSTRAINT IF EXISTS digest_subscriptions_user_id_key"
        )
        await conn.execute(
            "ALTER TABLE digest_subscriptions "
            "ADD CONSTRAINT digest_subscriptions_user_id_report_type_key "
            "UNIQUE (user_id, report_type)"
        )
        await conn.commit()
        logger.info(
            "Migration 077: digest_subscriptions now UNIQUE(user_id, report_type)"
        )
    except Exception as e:
        # Most likely path:
        #   - Constraint already named differently (defensive try/catch)
        #   - The new constraint already exists from a prior run
        # In both cases the migration is effectively complete; log and
        # move on so a partial-state DB doesn't block startup.
        logger.info(
            "Migration 077: constraint swap skipped (%s) — assuming already applied",
            e,
        )
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("078_vehicle_state_unique_per_company_name")
async def migrate_vehicle_state_unique_per_company_name(conn) -> None:
    """Add ``UNIQUE(account_id, company_code, vehicle_name)`` to vehicle_state.

    Motivation
    ----------
    The maintenance route used to look up live odometer / engine-hours
    by ``vehicle_name`` alone (``get_vehicle_state(account_id,
    vehicle_nums=[name])``).  When the same account had two companies
    each operating a vehicle named "103", the query returned BOTH
    rows and the python-side dict ``live_by_name["103"] = row`` kept
    only the last one — silently mixing one company's odometer onto
    the other company's maintenance task.

    The route fix (use vehicle_id or (company_code, vehicle_name)
    instead of name-only) is in interfaces/api/routes/maintenance.py.
    THIS migration is the defense-in-depth layer: a UNIQUE index that
    makes the bug unrepresentable at the storage level, so any future
    caller that forgets to scope by company can't return two rows.

    Safety
    ------
    - Refuses to apply if any existing duplicate row would block the
      constraint.  Logs which keys are colliding so the operator can
      decide whether to clean them up manually.
    - On Postgres this is a CONCURRENTLY-ish CREATE UNIQUE INDEX so
      writes aren't blocked; if the index already exists from a prior
      partial run, the IF NOT EXISTS guard makes it a no-op.

    Why an index, not a table constraint
    ------------------------------------
    UNIQUE INDEX has the same enforcement semantics in Postgres but
    is far easier to drop / re-create if we ever need to relax the
    rule (e.g. multi-company merges).
    """
    try:
        # Pre-flight: check for duplicates that would block the index.
        # Limit to one hit so the query is constant-time on a large
        # vehicle_state table.
        dup_cur = await conn.execute("""
            SELECT account_id, company_code, vehicle_name, COUNT(*) AS cnt
              FROM vehicle_state
             GROUP BY account_id, company_code, vehicle_name
            HAVING COUNT(*) > 1
             LIMIT 1
        """)
        dup_row = await dup_cur.fetchone()
        if dup_row:
            row = dict(dup_row) if not isinstance(dup_row, dict) else dup_row
            logger.warning(
                "Migration 078 skipped: vehicle_state has duplicate "
                "(account_id=%s, company_code=%r, vehicle_name=%r) — "
                "clean up the duplicate rows manually before re-running.",
                row.get("account_id"),
                row.get("company_code"),
                row.get("vehicle_name"),
            )
            return

        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uniq_vehicle_state_account_company_name "
            "ON vehicle_state(account_id, company_code, vehicle_name)"
        )
        await conn.commit()
        logger.info(
            "Migration 078: vehicle_state now has UNIQUE(account_id, "
            "company_code, vehicle_name) — cross-company name collisions "
            "are no longer possible."
        )
    except Exception as e:
        logger.warning("Migration 078 failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("079_alert_routing_send_resolve_receipt")
async def migrate_alert_routing_send_resolve_receipt(conn) -> None:
    """Add per-topic '🟢 RESOLVED' receipt toggle to ``alert_routing``.

    The auto-resolve pipeline (capabilities/alerting/escalation.py)
    currently fans the resolve receipt to every recipient of the
    original alert — including the forum topic the alert was posted to.
    Operators with high-volume parking / geofence topics report this
    as chat noise: every truck that ends a stop adds a "RESOLVED —
    Vehicle Moving" follow-up.

    This column lets the admin disable the resolve receipt PER TOPIC
    without losing the alert itself (the underlying ``alert_history``
    row still flips to resolved — the dashboard's monitoring view
    stays accurate; only the new chat message is suppressed).

    Default: ``1`` (current behavior preserved on upgrade).  The admin
    flips it off via the ForumRoutingSection on the Account Settings
    page.  Personal DM receipts are governed by a parallel per-user
    column (``users.alert_resolve_receipts``, migration 080).
    """
    try:
        cur = await conn.execute("PRAGMA table_info(alert_routing)")
        cols = {r[1] for r in await cur.fetchall()}
        if "send_resolve_receipt" in cols:
            return
        await conn.execute(
            "ALTER TABLE alert_routing "
            "ADD COLUMN send_resolve_receipt INTEGER NOT NULL DEFAULT 1"
        )
        await conn.commit()
        logger.info("Migration 079: added alert_routing.send_resolve_receipt")
    except Exception as e:
        logger.error("Migration 079 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("080_users_alert_resolve_receipts")
async def migrate_users_alert_resolve_receipts(conn) -> None:
    """Add per-user '🟢 RESOLVED' DM-receipt toggle to ``users``.

    Twin of migration 079 but for personal DMs.  When set to 1 the
    user receives the resolve receipt in their personal Telegram
    chat; when 0 they don't.  The per-user toggle is independent of
    the per-topic toggle — a user can have DM receipts off while
    still seeing them in a forum topic they belong to (or vice
    versa).

    Default: ``0`` (opt-in).  Existing users start silent — they
    won't get a flood of receipts on the first deploy.  Each user
    enables their own via the new "My Notifications" page in the
    dashboard avatar menu.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(users)")
        cols = {r[1] for r in await cur.fetchall()}
        if "alert_resolve_receipts" in cols:
            return
        await conn.execute(
            "ALTER TABLE users "
            "ADD COLUMN alert_resolve_receipts INTEGER NOT NULL DEFAULT 0"
        )
        await conn.commit()
        logger.info("Migration 080: added users.alert_resolve_receipts (default OFF)")
    except Exception as e:
        logger.error("Migration 080 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("087_invites_revoked_at")
async def migrate_invites_revoked_at(conn) -> None:
    """Add per-row ``revoked_at`` timestamp to ``invites``.

    Mirrors the canonical "revoke" template established by
    ``users.revoke_user_session`` (adapters/storage/users.py:300-348):
    a nullable timestamp column where ``NULL`` means active and a
    populated value means revoked-at-this-instant.  Chose this over an
    ``is_active`` boolean because it preserves the revoke instant for
    forensic audit reads and gives ``WHERE revoked_at IS NULL`` as a
    natural idempotency guard on retries.

    Default behaviour on upgrade: every existing invite row has
    ``revoked_at = NULL`` (= active).  ``get_invite`` /
    ``redeem_invite`` / ``list_invites`` all filter ``revoked_at IS
    NULL`` by default, so the column landing on a deployed DB before
    any code reads it is a no-op.

    Surfaces this lights up: the dashboard's Team Management → Invites
    tab grows a "Revoke" inline action; the bot's ``/join`` path and
    the email-signup endpoint both refuse revoked codes (the check
    lives in ``get_invite`` so every redemption surface inherits it).
    """
    try:
        cur = await conn.execute("PRAGMA table_info(invites)")
        cols = {r[1] for r in await cur.fetchall()}
        if "revoked_at" in cols:
            return
        await conn.execute(
            "ALTER TABLE invites ADD COLUMN revoked_at TEXT"
        )
        await conn.commit()
        logger.info("Migration 087: added invites.revoked_at")
    except Exception as e:
        logger.error("Migration 087 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("090_invites_email_channel")
async def migrate_invites_email_channel(conn) -> None:
    """Add per-invite email-channel columns to ``invites``.

    The invite-email channel lets the create-invite dialog send the
    link via SMTP (through Resend) instead of relying on the operator
    to copy + paste a Telegram deep-link manually.  Three new columns:

      sent_to_email TEXT
        - NULL on link-channel invites (the default; preserves the
          current zero-impact behaviour for any invite issued before
          this column landed).
        - Populated on email-channel invites with the recipient address.
          Encrypted at rest via infra.crypto when ENCRYPTION_KEY is set
          (mirrors the Samsara API key handling) — recipient PII on a
          row anyone with can_invite can read shouldn't sit there in
          plaintext, especially after the invite is revoked.

      email_sent_at TEXT
        - NULL = the SMTP send never happened (failure, or this is a
          link-channel invite).
        - ISO-8601 UTC = the moment send_email() returned True.
        - The pair (sent_to_email NOT NULL AND email_sent_at IS NULL)
          is the "we tried but the relay refused" state — surfaced as
          "Created but email failed" in the dialog.

      email_send_count INTEGER NOT NULL DEFAULT 0
        - Incremented on every successful send (initial + each resend).
          Surfaced in audit-log details so an auditor can spot abuse
          ("HR resent 10 invites to alice@evil.com in an hour") without
          joining log rows.

    Default behaviour on upgrade: every existing invite row has
    ``sent_to_email = NULL, email_sent_at = NULL, email_send_count = 0``
    — column landing on a deployed DB before any code reads it is a
    no-op for the link-channel.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(invites)")
        cols = {r[1] for r in await cur.fetchall()}
        if "sent_to_email" not in cols:
            await conn.execute(
                "ALTER TABLE invites ADD COLUMN sent_to_email TEXT"
            )
        if "email_sent_at" not in cols:
            await conn.execute(
                "ALTER TABLE invites ADD COLUMN email_sent_at TEXT"
            )
        if "email_send_count" not in cols:
            await conn.execute(
                "ALTER TABLE invites "
                "ADD COLUMN email_send_count INTEGER NOT NULL DEFAULT 0"
            )
        await conn.commit()
        logger.info("Migration 090: added invites email-channel columns")
    except Exception as e:
        logger.error("Migration 090 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("097_invites_bounce_channel")
async def migrate_invites_bounce_channel(conn) -> None:
    """Bounce/complaint state for the invite-email channel.

    Five additions on ``invites``:
      resend_email_id     — Resend HTTP API's per-send identifier.
                            Populated when MAIL_PROVIDER=resend so the
                            bounce-webhook handler can match an event
                            to an invite without trusting recipient
                            address (which can collide cross-account).
                            NULL for SMTP-channel sends.
      email_bounced_at    — ISO-8601 stamp of hard bounce / soft-cap
                            reached / spam complaint (with subtype
                            captured separately).  NULL = no bounce.
      email_bounce_reason — encrypted-at-rest free-text from the relay
                            (e.g. SES 'recipient rejected') because
                            providers routinely echo the recipient
                            address verbatim in the failure message.
                            Reuses infra.crypto same as sent_to_email.
      email_bounce_type   — 'hard' | 'soft' | 'complaint' — kept
                            plaintext + indexed-friendly for
                            'show all hard bounces' style queries.
      email_soft_bounce_count — incremented on each soft-bounce event.
                            Bounced state flips permanent at >=3 soft
                            bounces (operator can act earlier).

    Plus a sibling table ``email_webhook_events`` providing svix-id
    idempotency so Resend's at-least-once retry schedule (5s, 5min,
    30min, 2h, 5h, 10h) doesn't re-fire the handler.  Mirrors the
    Stripe ``mark_stripe_event_processed`` pattern.

    Partial index ``idx_invites_email_lookup`` accelerates the two hot
    paths: bounce-webhook resend_email_id lookup, and the dashboard's
    "show me email-channel invites for this account" query.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(invites)")
        cols = {r[1] for r in await cur.fetchall()}
        for col_name, col_def in (
            ("resend_email_id",      "TEXT"),
            ("email_bounced_at",     "TEXT"),
            ("email_bounce_reason",  "TEXT"),
            ("email_bounce_type",    "TEXT"),
            ("email_soft_bounce_count", "INTEGER NOT NULL DEFAULT 0"),
            # Complaint is conceptually distinct from bounce — both
            # can be set on the same invite (recipient bounces, then
            # later somehow complains).  Operator UX renders different
            # badges per state.
            ("email_complained_at",  "TEXT"),
        ):
            if col_name not in cols:
                await conn.execute(
                    f"ALTER TABLE invites ADD COLUMN {col_name} {col_def}"
                )
        # Partial index — only the (typically small) email-channel
        # subset gets indexed.  Speeds up resend_email_id lookups
        # AND the orphan-detection scan if we ever need it.
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invites_resend_email_id "
            "ON invites(resend_email_id) WHERE resend_email_id IS NOT NULL"
        )
        # svix-id idempotency table — INSERT-OR-IGNORE primary key
        # gives us the dedup gate.  account_id is nullable because
        # not every event maps to an invite (we still record the
        # svix-id to drop the duplicate fast on retry).
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS email_webhook_events (
                svix_id     TEXT    PRIMARY KEY,
                event_type  TEXT    NOT NULL,
                account_id  INTEGER,
                invite_id   INTEGER,
                created_at  TEXT    NOT NULL
            )"""
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_email_webhook_events_created "
            "ON email_webhook_events(created_at)"
        )
        await conn.commit()
        logger.info("Migration 097: invites bounce/complaint columns + idempotency table ready")
    except Exception as e:
        logger.error("Migration 097 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("098_drop_department_column")
async def migrate_drop_department_column(conn) -> None:
    """Drop ``department`` from invites and users.

    The free-text ``department`` per-person label was vestigial — 99%
    of users carried the default ``'general'`` value, and the term
    collides confusingly with the account-level "department modules"
    concept (the 5 toggleable Fleet/Dispatch/Safety/HR/Accounting
    capability surfaces).  Role already covers the per-person scope
    that ``department`` was trying to express, so the column adds
    cognitive overhead without operational value.

    Migration shape:
      ALTER TABLE invites DROP COLUMN department;
      ALTER TABLE users   DROP COLUMN department;

    Postgres supports this since 9.0; SQLite since 3.35 (March 2021).
    Older SQLite would need the table-recreate dance — flagged in the
    runbook but not handled here since the project runs Postgres in
    every prod deploy.

    Data loss: any non-default value (operations, dispatch,
    management, etc.) is unrecoverable post-drop.  The migration is
    intentional and the operator-facing change is the disappearance
    of the per-person department label from invite creation, audit
    logs, user profile cards, and the TeamManagement column.
    """
    try:
        # ``ALTER TABLE DROP COLUMN`` is idempotent in Postgres via
        # ``IF EXISTS`` but SQLite predates that syntax.  Wrap in
        # try/except so a re-run on a deploy that already dropped is
        # a no-op.
        for stmt in (
            "ALTER TABLE invites DROP COLUMN department",
            "ALTER TABLE users   DROP COLUMN department",
        ):
            try:
                await conn.execute(stmt)
            except Exception as e:
                # Most likely cause: column already dropped in a prior
                # run on this deploy.  Log + continue rather than
                # rolling back the whole migration.
                logger.info(
                    "Migration 098: %r — assuming already-dropped: %s",
                    stmt, e,
                )
        await conn.commit()
        logger.info("Migration 098: dropped invites.department + users.department")
    except Exception as e:
        logger.error("Migration 098 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("099_permissions_own_to_vehicle")
async def migrate_permissions_own_to_vehicle(conn) -> None:
    """Rename the 11 vehicle-scoped ``can_*_own`` keys to ``can_*_vehicle``
    inside every stored ``role_permissions.permissions`` JSON blob.

    The data-scope model was renamed Own → Vehicle (All / Company /
    Vehicle) and the flag identifiers followed in code; stored custom
    role-permission blobs still carry the old keys, so this rewrites them
    in place (values preserved).

    The 4 self-service ``_own`` flags (coaching_view / payroll_view /
    driver_docs / risk_report) are deliberately LEFT untouched — they
    mean "own self", not "own vehicle".

    Idempotent: rows with no old keys are skipped, so a re-run is a no-op.
    """
    import json as _json
    rename = {
        "can_alerts_own": "can_alerts_vehicle",
        "can_scorecard_own": "can_scorecard_vehicle",
        "can_vehicle_own": "can_vehicle_vehicle",
        "can_events_own": "can_events_vehicle",
        "can_maintenance_own": "can_maintenance_vehicle",
        "can_geofence_own": "can_geofence_vehicle",
        "can_inspections_own": "can_inspections_vehicle",
        "can_route_own": "can_route_vehicle",
        "can_location_own": "can_location_vehicle",
        "can_parking_own": "can_parking_vehicle",
        "can_work_orders_own": "can_work_orders_vehicle",
    }
    try:
        cur = await conn.execute("SELECT id, permissions FROM role_permissions")
        rows = await cur.fetchall()
        updated = 0
        for r in rows:
            try:
                perms = _json.loads(r["permissions"] or "{}")
            except Exception:
                continue
            if not isinstance(perms, dict):
                continue
            touched = False
            for old, new in rename.items():
                if old in perms:
                    perms[new] = perms.pop(old)
                    touched = True
            if touched:
                await conn.execute(
                    "UPDATE role_permissions SET permissions = ? WHERE id = ?",
                    (_json.dumps(perms), r["id"]),
                )
                updated += 1
        await conn.commit()
        logger.info(
            "Migration 099: rewrote own→vehicle keys in %d role_permissions rows",
            updated,
        )
    except Exception as e:
        logger.error("Migration 099 (own→vehicle) failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("100_vehicle_metrics_daily")
async def migrate_vehicle_metrics_daily(conn) -> None:
    """Create the per-day vehicle roll-up table.

    Backs ``WarehouseMixin.upsert/get/prune_vehicle_metrics_daily`` and
    the daily-velocity reads — the feature's storage code shipped
    without its DDL, so it only worked on dev DBs where the table
    already existed; a fresh Postgres install crashed with
    ``relation "vehicle_metrics_daily" does not exist``.

    UNIQUE(account_id, vehicle_id, day_utc) backs the upsert's
    ``ON CONFLICT`` clause.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS vehicle_metrics_daily (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id          INTEGER NOT NULL,
            vehicle_id          TEXT    NOT NULL,
            day_utc             TEXT    NOT NULL,
            miles               REAL    NOT NULL DEFAULT 0,
            drive_min           REAL    NOT NULL DEFAULT 0,
            idle_min            REAL    NOT NULL DEFAULT 0,
            max_speed_mph       REAL,
            avg_fuel_pct        REAL,
            harsh_event_count   INTEGER NOT NULL DEFAULT 0,
            fault_count_eod     INTEGER NOT NULL DEFAULT 0,
            odometer_eod        REAL,
            engine_hours_eod    REAL,
            ingested_at         TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, vehicle_id, day_utc)
        );
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_vehicle_metrics_daily_account_day
            ON vehicle_metrics_daily(account_id, day_utc);
    """)
    # 5-minute vehicle state history (the M1 snapshot feature) — same
    # ship-without-DDL story as vehicle_metrics_daily above.  UNIQUE
    # backs the upsert's ON CONFLICT DO NOTHING.
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS vehicle_state_snapshot (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id          INTEGER NOT NULL,
            vehicle_id          TEXT    NOT NULL,
            captured_at         TEXT    NOT NULL,
            lat                 REAL,
            lon                 REAL,
            speed_mph           REAL,
            engine_state        TEXT,
            fuel_pct            REAL,
            def_pct             REAL,
            odometer_mi         REAL,
            engine_hours        REAL,
            fault_count         INTEGER NOT NULL DEFAULT 0,
            dtc_critical_count  INTEGER NOT NULL DEFAULT 0,
            last_driver_id      TEXT,
            battery_v           REAL,
            oil_psi             REAL,
            coolant_c           REAL,
            engine_load_pct     REAL,
            rpm                 REAL,
            UNIQUE(account_id, vehicle_id, captured_at)
        );
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_vehicle_state_snapshot_acct_day
            ON vehicle_state_snapshot(account_id, captured_at);
    """)
    await conn.commit()
    logger.info("Migration 100: vehicle_metrics_daily + vehicle_state_snapshot ready")


@_register("102_settings_component_flags")
async def migrate_settings_component_flags(conn) -> None:
    """Default the granular Settings-component flags from the stored
    ``can_manage_account`` value in customised role_permissions blobs.

    Step 2 of the Settings consolidation: Permissions / Integrations /
    Storage / Working Hours got their own delegation flags
    (``can_manage_permissions`` etc.).  Accounts that customised a role
    keep equivalent access — each new key inherits the row's
    ``can_manage_account`` unless the key was already set.  Rows without
    ``can_manage_account`` fall through to the role defaults at resolve
    time (owner True, everyone else False), so they need no rewrite.

    Idempotent: setdefault never overwrites an existing key.
    """
    import json as _json
    new_keys = (
        "can_manage_permissions", "can_manage_integrations",
        "can_manage_storage", "can_manage_work_hours",
    )
    try:
        cur = await conn.execute("SELECT id, permissions FROM role_permissions")
        rows = await cur.fetchall()
        updated = 0
        for r in rows:
            try:
                perms = _json.loads(r["permissions"] or "{}")
            except Exception:
                continue
            if not isinstance(perms, dict) or "can_manage_account" not in perms:
                continue
            base = bool(perms["can_manage_account"])
            touched = False
            for k in new_keys:
                if k not in perms:
                    perms[k] = base
                    touched = True
            if touched:
                await conn.execute(
                    "UPDATE role_permissions SET permissions = ? WHERE id = ?",
                    (_json.dumps(perms), r["id"]),
                )
                updated += 1
        await conn.commit()
        logger.info(
            "Migration 102: settings-component flags defaulted in %d rows", updated,
        )
    except Exception as e:
        logger.error("Migration 102 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("106_rename_fleet_aggregate_snapshots")
async def migrate_rename_fleet_snapshots(conn) -> None:
    """Rename the two fleet-wide aggregate snapshot tables to
    ``aggregate_*`` for naming consistency with the rest of the
    fleet→vehicle/aggregate rename.

    ``ALTER TABLE … RENAME`` (not copy) is deliberate: in Postgres the
    tenant_isolation RLS policy (applied to the old names by migration
    057) **and** the indexes follow the table through a rename
    automatically — no policy rewrite, no data copy, no downtime.

    Ordering: registered after the creating migration (which makes
    ``fleet_*_snapshot``) and after 057 (which RLS-es them), so on a
    fresh DB the old-named table exists *with its policy* by the time
    this runs, and the rename carries the policy + indexes onto the new
    name.  On an existing DB the populated table is renamed in place
    (these are ephemeral 10–30 min snapshots, so even the brief instant
    is invisible — the next ingest writes to the new name).

    Idempotent: a table already renamed (old name absent) is skipped,
    so re-runs and fresh installs both converge.

    Rollback note: this codebase has no down-migration framework.  To
    revert, ``git revert`` the code commit AND add a reverse-rename
    migration — or just let the ingestor repopulate, since the data is
    ephemeral.  (This is the DB-table caveat I flagged before stage 4.)
    """
    renames = [
        ("fleet_weather_snapshot",    "aggregate_weather_snapshot"),
        ("fleet_efficiency_snapshot", "aggregate_efficiency_snapshot"),
    ]
    for old, new in renames:
        try:
            cur = await conn.execute(f"SELECT to_regclass('{old}')")
            row = await cur.fetchone()
            if not row or row[0] is None:
                continue  # already renamed / never created — idempotent
            await conn.execute(f"ALTER TABLE {old} RENAME TO {new}")
            logger.info("Migration 106: renamed %s → %s", old, new)
        except Exception as e:
            logger.warning("Migration 106: rename %s skipped (%s)", old, e)

    # Tidy the one explicitly-named index so it matches the new table.
    try:
        cur = await conn.execute("SELECT to_regclass('idx_fleet_weather_company')")
        row = await cur.fetchone()
        if row and row[0] is not None:
            await conn.execute(
                "ALTER INDEX idx_fleet_weather_company "
                "RENAME TO idx_aggregate_weather_company"
            )
    except Exception as e:
        logger.warning("Migration 106: index rename skipped (%s)", e)


@_register("103_datatruck_tables_rls")
async def migrate_datatruck_tables_rls(conn) -> None:
    """Apply the tenant_isolation RLS policy to the five
    ``datatruck_*`` TMS-sync tables.

    The tables themselves come from ``schema.create_tables`` (which
    runs before migrations on every boot), so by the time this runs
    they exist on both fresh installs and upgrades.  Migration 057
    applied the policy to every tenant table that existed at the
    time; new tenant tables each need a follow-up like this one.

    Same gating + shape as 057: skipped entirely unless ``ENABLE_RLS``
    is set, fail-closed policy on ``app.account_id`` GUC, FORCE so the
    table owner is subject to the policy too.  Idempotent — the DROP
    POLICY IF EXISTS + CREATE POLICY pair re-runs cleanly.
    """
    import os
    if os.getenv("ENABLE_RLS", "0").strip() not in ("1", "true", "TRUE", "yes"):
        logger.info("Migration 103: ENABLE_RLS not set; RLS policies skipped")
        return

    tables = [
        "datatruck_drivers", "datatruck_trucks", "datatruck_trailers",
        "datatruck_orders", "datatruck_work_orders",
    ]
    enabled = 0
    for tbl in tables:
        try:
            await conn.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
            await conn.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
            await conn.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl}")
            await conn.execute(
                f"""
                CREATE POLICY tenant_isolation ON {tbl}
                USING       (account_id::text = current_setting('app.account_id', true))
                WITH CHECK  (account_id::text = current_setting('app.account_id', true))
                """
            )
            enabled += 1
        except Exception as e:
            logger.warning("Migration 103: %s skipped (%s)", tbl, e)
    logger.info("Migration 103: RLS enabled on %d datatruck tables", enabled)


@_register("105_vehicles_registry")
async def migrate_vehicles_registry(conn) -> None:
    """Backfill the ``vehicles`` registry from ``vehicle_state`` and
    apply its RLS policy.

    The ``vehicles`` table is created by ``schema.create_tables`` (runs
    before migrations every boot), so it exists here on fresh + upgrade.

    Two steps:

    1. **Backfill** — one registry row per existing ``vehicle_state``
       row (``source='samsara'``, ``vehicle_type='truck'``,
       ``telematics_ref=vehicle_id``).  ``ON CONFLICT DO NOTHING`` keeps
       it idempotent and never clobbers a row the operator added by
       hand.  This makes every account's registry mirror its current
       fleet so the registry-first read shows no change on day one.

       Best-effort under RLS: ``vehicle_state`` carries the
       tenant_isolation policy (migration 057), so the cross-account
       ``SELECT`` here needs ``row_security = off``.  If that can't be
       set (FORCE RLS + non-superuser), the backfill may read zero rows
       — that's fine: the 60s ingestor's
       ``upsert_from_integration(source='samsara')`` repopulates the
       registry within a minute, and the registry-first read falls back
       to the legacy Samsara path while the registry is empty.  So the
       one-time backfill is a convenience, not a correctness dependency.

    2. **RLS** — apply the same tenant_isolation policy the other tenant
       tables use, gated by ENABLE_RLS.  Done AFTER the backfill so the
       INSERT isn't blocked by the table's own WITH CHECK.
    """
    import os

    # ── Step 1: backfill (best-effort, idempotent) ──
    # A single auto-committed INSERT…SELECT.  No explicit transaction /
    # SET LOCAL row_security: the migration ``conn`` is the raw pool
    # proxy (no ``transaction()`` method), and under the default
    # ENABLE_RLS=off this reads every account's vehicle_state freely.
    # If FORCE RLS is on, the cross-account SELECT may return nothing —
    # that's fine: the 60s ingestor's upsert_from_integration repopulates
    # the registry within a minute, so the backfill is a convenience,
    # not a correctness dependency.
    try:
        now = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc,
        ).isoformat()
        await conn.execute(
            """
            INSERT INTO vehicles
                (account_id, company_code, unit_number, vehicle_type,
                 vin, plate_number, make, model, year, status, source,
                 telematics_ref, notes, is_active, created_at, updated_at)
            SELECT account_id, company_code, vehicle_name, 'truck',
                   '', '', '', '', NULL, 'active', 'samsara',
                   vehicle_id, '', 1, ?, ?
              FROM vehicle_state
             WHERE vehicle_name <> ''
            ON CONFLICT(account_id, company_code, unit_number)
            DO NOTHING
            """,
            (now, now),
        )
        logger.info("Migration 105: vehicles registry backfilled from vehicle_state")
    except Exception as e:
        # Non-fatal — the ingestor self-heals the registry on the next
        # tick.  Log and continue to the RLS step.
        logger.warning("Migration 105: backfill skipped (%s)", e)

    # ── Step 2: RLS policy (gated) ──
    if os.getenv("ENABLE_RLS", "0").strip() not in ("1", "true", "TRUE", "yes"):
        logger.info("Migration 105: ENABLE_RLS not set; RLS policy skipped")
        return
    try:
        await conn.execute("ALTER TABLE vehicles ENABLE ROW LEVEL SECURITY")
        await conn.execute("ALTER TABLE vehicles FORCE ROW LEVEL SECURITY")
        await conn.execute("DROP POLICY IF EXISTS tenant_isolation ON vehicles")
        await conn.execute(
            """
            CREATE POLICY tenant_isolation ON vehicles
            USING       (account_id::text = current_setting('app.account_id', true))
            WITH CHECK  (account_id::text = current_setting('app.account_id', true))
            """
        )
        logger.info("Migration 105: RLS enabled on vehicles")
    except Exception as e:
        logger.warning("Migration 105: vehicles RLS skipped (%s)", e)


@_register("107_work_orders_source")
async def migrate_work_orders_source(conn) -> None:
    """Add ``source`` + ``external_id`` to ``work_orders`` so synced
    integration work orders (Datatruck) live on the same page as the
    operator's hand-entered shop invoices.

    The Work Orders module table is the SSOT; an integration ENRICHES it
    (``source='datatruck'``, ``external_id`` = the upstream id) rather
    than defining a parallel store — the same inversion the vehicle
    registry uses.  Existing rows default to ``source='manual'`` so the
    page is unchanged until a sync projects rows in.

    Idempotent: each ADD COLUMN is guarded (re-runs no-op on the
    "duplicate column" error), and the partial unique index dedupes
    synced rows without constraining the empty-``external_id`` manual
    rows against each other.
    """
    for col_name, col_def in (
        ("source", "TEXT NOT NULL DEFAULT 'manual'"),
        ("external_id", "TEXT NOT NULL DEFAULT ''"),
    ):
        try:
            await conn.execute(
                f"ALTER TABLE work_orders ADD COLUMN {col_name} {col_def}"
            )
            await conn.commit()
            logger.info("Migration 107: added work_orders.%s", col_name)
        except Exception:
            pass  # column already exists
    try:
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_work_orders_external
                ON work_orders(account_id, source, external_id)
                WHERE external_id <> ''
            """
        )
        await conn.commit()
        logger.info("Migration 107: work_orders external-ref index ready")
    except Exception as e:
        logger.warning("Migration 107: work_orders external index skipped (%s)", e)


@_register("108_work_orders_vehicle_type")
async def migrate_work_orders_vehicle_type(conn) -> None:
    """Add ``work_orders.vehicle_type`` (``truck`` | ``trailer`` | '').

    A work order is performed on a specific asset, and a truck "103" and a
    trailer "103" are different vehicles — the type lets the WO link to the
    right registry asset and lets the form show a Truck/Trailer selector.
    Synced Datatruck WOs set it from whichever of ``truck``/``trailer`` the
    upstream record carries; hand-entered WOs default to '' (unset).

    Idempotent: ADD COLUMN no-ops on the duplicate-column error on re-run.
    """
    try:
        await conn.execute(
            "ALTER TABLE work_orders ADD COLUMN vehicle_type TEXT NOT NULL DEFAULT ''"
        )
        await conn.commit()
        logger.info("Migration 108: added work_orders.vehicle_type")
    except Exception as e:
        logger.info("Migration 108: work_orders.vehicle_type likely exists — %s", e)


@_register("109_companies_mc_dot")
async def migrate_companies_mc_dot(conn) -> None:
    """Add ``companies.mc_number`` + ``usdot_number``.

    MC and USDOT are immutable federal carrier identifiers — far stabler
    than names — so an account's Companies become the SSOT join key for
    matching integration records (e.g. a Datatruck work order's
    ``mc_number``) to the right sub-company.  Reusable by any future
    integration / FMCSA-style feature.

    Idempotent: each ADD COLUMN no-ops on the duplicate-column error.
    """
    for col in ("mc_number", "usdot_number"):
        try:
            await conn.execute(
                f"ALTER TABLE companies ADD COLUMN {col} TEXT NOT NULL DEFAULT ''"
            )
            await conn.commit()
            logger.info("Migration 109: added companies.%s", col)
        except Exception:
            pass  # column already exists


@_register("110_work_orders_assigned_to")
async def migrate_work_orders_assigned_to(conn) -> None:
    """Add ``work_orders.assigned_to`` — the person a work order is assigned
    to.  Synced Datatruck WOs fill it from the upstream ``assigned_to``
    name; hand-entered ones default to '' and the operator can set it.

    Idempotent: ADD COLUMN no-ops on the duplicate-column error.
    """
    try:
        await conn.execute(
            "ALTER TABLE work_orders ADD COLUMN assigned_to TEXT NOT NULL DEFAULT ''"
        )
        await conn.commit()
        logger.info("Migration 110: added work_orders.assigned_to")
    except Exception as e:
        logger.info("Migration 110: work_orders.assigned_to likely exists — %s", e)


@_register("100_users_dnd_enabled")
async def migrate_users_dnd_enabled(conn) -> None:
    """Add ``users.dnd_enabled`` — single per-user toggle that controls
    whether the user honours the admin-set Working Hours.

    Default TRUE preserves existing behaviour: every user keeps
    respecting the schedule the admin configured (per-user override or
    role-level Working Hours).  Users who opt OUT via the Profile DND
    toggle will get alerts 24/7 regardless of the schedule.

    Replaces the previous design where users could edit their own
    quiet_start/end from Profile — that path is now read-only.  The
    existing quiet_start/end columns are kept (admin-managed only) so
    no data is lost; ownership flips, not the values.

    Idempotent: SQLite ALTER TABLE ADD COLUMN fails-but-doesn't-corrupt
    on re-run; caught + logged.
    """
    try:
        await conn.execute(
            "ALTER TABLE users ADD COLUMN dnd_enabled INTEGER NOT NULL DEFAULT 1"
        )
        await conn.commit()
        logger.info("Migration 100: added users.dnd_enabled (default ON)")
    except Exception as e:
        logger.info(
            "Migration 100: users.dnd_enabled likely already exists — %s", e,
        )


@_register("101_users_assigned_work_hours_id")
async def migrate_users_assigned_work_hours_id(conn) -> None:
    """Add ``users.assigned_work_hours_id`` — FK to a ``work_hours`` row.

    Lets admins assign a user to a NAMED schedule from the Working
    Hours catalog instead of typing custom hours into the per-user
    override.  Reads as "Bakhtinur uses Night Shift" rather than
    "Bakhtinur = 22:00-06:00", which is cleaner for the audit log and
    keeps the schedule list as the single source of truth.

    Backward-compatible: existing ``quiet_start`` / ``quiet_end`` values
    keep working as a fallback when ``assigned_work_hours_id`` is NULL
    (the DND precedence is: dnd_enabled → assigned schedule →
    free-form override → role-level → none).

    Nullable; no default so existing rows stay NULL → inherit role-level.
    Not a foreign-key constraint at the DB layer (SQLite would skip it
    anyway without ``PRAGMA foreign_keys=ON``); referential integrity
    is enforced at the endpoint by validating the schedule belongs to
    the caller's account before write.

    Idempotent: ALTER ADD COLUMN fails-but-doesn't-corrupt on re-run.
    """
    try:
        await conn.execute(
            "ALTER TABLE users ADD COLUMN assigned_work_hours_id INTEGER"
        )
        await conn.commit()
        logger.info("Migration 101: added users.assigned_work_hours_id")
    except Exception as e:
        logger.info(
            "Migration 101: users.assigned_work_hours_id likely already exists — %s", e,
        )


@_register("104_platform_audit_log")
async def migrate_platform_audit_log(conn) -> None:
    """Create ``platform_audit_log`` — system-tier event trail.

    Unlike the per-tenant ``audit_log`` (which requires an existing
    ``account_id`` and is shown inside the customer dashboard), this
    table records PLATFORM events: account creation (operator, web
    self-serve, or Telegram bot), suspend/unsuspend, deletion requests.
    ``account_id`` is nullable so events about not-yet-created or
    already-purged accounts still have a home.

    ``actor`` is a free-form identity string ("operator:12345",
    "self-serve", "bot:67890") — richer than a bare tg_id because the
    same human can act through different channels.
    """
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event       TEXT    NOT NULL,
                account_id  INTEGER,
                actor       TEXT    NOT NULL DEFAULT '',
                details     TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL
            )
            """
        )
        await conn.commit()
        logger.info("Migration 104: platform_audit_log table ready")
    except Exception as e:
        logger.error("Migration 104 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("111_metrics_daily_eod_readings")
async def migrate_metrics_daily_eod_readings(conn) -> None:
    """Add ``odometer_eod`` / ``engine_hours_eod`` to ``vehicle_metrics_daily``.

    The 5-minute ``vehicle_state_snapshot`` table is pruned at 7 days, so
    back-dated work orders could only resolve an odometer "as of" a date
    within the last week.  ``vehicle_metrics_daily`` is kept 730 days but
    only stored miles-per-day, not a cumulative odometer.  These two
    columns carry the end-of-day reading into the long-retention tier so
    the as-of lookup reaches ~2 years back (accruing forward from now;
    pre-existing daily rows stay NULL because their source snapshots are
    already pruned).

    Idempotent: each ADD COLUMN no-ops on the duplicate-column error.
    """
    for col in ("odometer_eod", "engine_hours_eod"):
        try:
            await conn.execute(
                f"ALTER TABLE vehicle_metrics_daily ADD COLUMN {col} REAL"
            )
            await conn.commit()
            logger.info("Migration 111: added vehicle_metrics_daily.%s", col)
        except Exception as e:
            logger.info(
                "Migration 111: vehicle_metrics_daily.%s likely exists — %s",
                col, e,
            )


@_register("112_driver_applications")
async def migrate_driver_applications(conn) -> None:
    """Driver-recruiting intake: application_links + driver_applications.

    ``application_links`` — one row per shareable recruiting link an
    account creates (the token in /apply/<token> resolves to the
    account).  Reusable: many applicants submit through one link.

    ``driver_applications`` — one row per submitted application.  Most of
    the 8-step form is stored as JSON blobs (the form data is naturally
    nested); a handful of columns are promoted out for searching/sorting
    (name, email, status) and SSN/DOB are encrypted-at-rest (infra.crypto
    → TEXT, not plaintext).  Uploaded docs live in the object store; only
    their ids are stored here (``docs_json``) — never base64 in the DB.

    Idempotent: CREATE TABLE / INDEX IF NOT EXISTS.
    """
    try:
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS application_links (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                token       TEXT    NOT NULL UNIQUE,
                label       TEXT    NOT NULL DEFAULT '',
                source      TEXT    NOT NULL DEFAULT '',
                created_by  INTEGER REFERENCES users(id),
                is_active   INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS driver_applications (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id        INTEGER NOT NULL REFERENCES accounts(id),
                link_token        TEXT    NOT NULL,
                reference         TEXT    NOT NULL,
                status            TEXT    NOT NULL DEFAULT 'submitted',
                -- promoted for list/search (the rest live in *_json)
                first_name        TEXT    NOT NULL DEFAULT '',
                last_name         TEXT    NOT NULL DEFAULT '',
                email             TEXT    NOT NULL DEFAULT '',
                phone             TEXT    NOT NULL DEFAULT '',
                city              TEXT    NOT NULL DEFAULT '',
                state             TEXT    NOT NULL DEFAULT '',
                cdl_state         TEXT    NOT NULL DEFAULT '',
                cdl_class         TEXT    NOT NULL DEFAULT '',
                position_type     TEXT    NOT NULL DEFAULT '',
                years_cdl         TEXT    NOT NULL DEFAULT '',
                -- encrypted PII (infra.crypto → base64 TEXT)
                dob_enc           TEXT,
                ssn_enc           TEXT,
                -- nested step data
                gate_json             TEXT NOT NULL DEFAULT '{}',
                personal_json         TEXT NOT NULL DEFAULT '{}',
                address_history_json  TEXT NOT NULL DEFAULT '[]',
                cdl_json              TEXT NOT NULL DEFAULT '{}',
                experience_json       TEXT NOT NULL DEFAULT '{}',
                employment_json       TEXT NOT NULL DEFAULT '[]',
                incidents_json        TEXT NOT NULL DEFAULT '{}',
                position_json         TEXT NOT NULL DEFAULT '{}',
                consents_json         TEXT NOT NULL DEFAULT '{}',
                docs_json             TEXT NOT NULL DEFAULT '{}',
                -- signature + certification audit trail
                sig_mode          TEXT NOT NULL DEFAULT '',
                sig_name          TEXT NOT NULL DEFAULT '',
                sig_date          TEXT NOT NULL DEFAULT '',
                sig_object_id     TEXT,
                disclosure_version TEXT NOT NULL DEFAULT '',
                -- lifecycle / recruiter
                recruiter_notes      TEXT NOT NULL DEFAULT '',
                reviewed_by          INTEGER REFERENCES users(id),
                converted_to_user_id INTEGER REFERENCES users(id),
                submit_ip            TEXT NOT NULL DEFAULT '',
                submitted_at         TEXT NOT NULL,
                created_at           TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_application_links_account
                ON application_links(account_id);
            CREATE INDEX IF NOT EXISTS idx_driver_apps_account_status
                ON driver_applications(account_id, status);
            CREATE INDEX IF NOT EXISTS idx_driver_apps_token
                ON driver_applications(link_token);
            """
        )
        await conn.commit()
        logger.info("Migration 112: application_links + driver_applications ready")
    except Exception as e:
        logger.error("Migration 112 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("113_invites_source_application")
async def migrate_invites_source_application(conn) -> None:
    """Link an invite back to the driver application it was minted from.

    Hiring an applicant (``/applications/applications/{id}/convert``) mints a
    driver invite, but the new user doesn't exist until that invite is
    REDEEMED.  Stamping the source application id on the invite lets
    ``redeem_invite`` complete the round-trip — set the application's
    ``converted_to_user_id`` once the driver actually onboards — so the
    record shows which application became which driver.

    Idempotent: ADD COLUMN no-ops on the duplicate-column error on re-run.
    """
    try:
        await conn.execute(
            "ALTER TABLE invites ADD COLUMN source_application_id INTEGER"
        )
        await conn.commit()
        logger.info("Migration 113: added invites.source_application_id")
    except Exception as e:
        logger.info("Migration 113: invites.source_application_id likely exists — %s", e)


@_register("114_recruitment_links_expires_at")
async def migrate_application_links_expires_at(conn) -> None:
    """Add ``application_links.expires_at`` (ISO-8601, nullable).

    Public apply links used to live forever until manually revoked — a
    leaked or stale-campaign link kept ingesting applicant PII unmonitored.
    ``expires_at`` lets a link auto-close; NULL means never expires, so
    pre-existing links are unaffected (backward compatible).
    ``resolve_recruitment_link`` rejects a token once it's past expiry.

    Idempotent: ADD COLUMN no-ops on the duplicate-column error on re-run.
    """
    try:
        await conn.execute(
            "ALTER TABLE application_links ADD COLUMN expires_at TEXT"
        )
        await conn.commit()
        logger.info("Migration 114: added application_links.expires_at")
    except Exception as e:
        logger.info("Migration 114: application_links.expires_at likely exists — %s", e)


@_register("115_recruitment_notifications")
async def migrate_application_notifications(conn) -> None:
    """In-app notifications + per-user channel prefs for recruiting.

    ``application_notifications`` is one row PER RECIPIENT (fan-out target
    is every account user holding ``can_manage_applications``), powering the
    dashboard channel + the unread badge.  ``application_notify_prefs``
    stores each user's chosen delivery channels (telegram / email /
    dashboard) — mirroring the ``digest_subscriptions.delivery_channels``
    comma-list pattern.  A missing prefs row means "all channels" (the
    sensible default for someone who can already see the applications).

    Idempotent: CREATE TABLE IF NOT EXISTS.
    """
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS application_notifications (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL REFERENCES accounts(id),
                user_id         INTEGER NOT NULL REFERENCES users(id),
                application_id  INTEGER REFERENCES driver_applications(id),
                reference       TEXT    NOT NULL DEFAULT '',
                kind            TEXT    NOT NULL DEFAULT 'application_submitted',
                title           TEXT    NOT NULL DEFAULT '',
                body            TEXT    NOT NULL DEFAULT '',
                is_read         INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT    NOT NULL
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_recruit_notif_inbox
                ON application_notifications(account_id, user_id, is_read, id)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS application_notify_prefs (
                user_id     INTEGER PRIMARY KEY REFERENCES users(id),
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                channels    TEXT    NOT NULL DEFAULT 'telegram,email,dashboard',
                updated_at  TEXT    NOT NULL
            )
        """)
        await conn.commit()
        logger.info("Migration 115: created application_notifications + application_notify_prefs")
    except Exception as e:
        logger.error("Migration 115 failed: %s", e, exc_info=True)
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


@_register("116_application_vetting_and_purge_fix")
async def migrate_application_vetting(conn) -> None:
    """Pre-hire vetting checklist + a retention/purge correctness fix.

    1. ``driver_applications.vetting_json`` — records that each FMCSA query
       (PSP / MVR / Clearinghouse, + optional drug/background) was run, by
       whom and when.  The 'approved' status gate enforces the required
       checks so the pipeline stage is a real compliance gate, not a label.

    2. Re-point ``application_notifications.application_id`` FK to
       ``ON DELETE CASCADE``.  The account purge (terminal step of the
       90-day FMCSA grace) deletes tenant tables by ``account_id`` in an
       arbitrary order; with the default RESTRICT, deleting
       ``driver_applications`` while a notification still referenced it
       would FAIL — leaving applicant PII un-purged past the legal window.
       CASCADE lets the application delete carry its notifications with it.

    Both steps idempotent.
    """
    try:
        await conn.execute(
            "ALTER TABLE driver_applications ADD COLUMN vetting_json TEXT NOT NULL DEFAULT ''"
        )
        await conn.commit()
        logger.info("Migration 116: added driver_applications.vetting_json")
    except Exception as e:
        logger.info("Migration 116: vetting_json likely exists — %s", e)
    try:
        await conn.execute(
            "ALTER TABLE application_notifications "
            "DROP CONSTRAINT IF EXISTS application_notifications_application_id_fkey"
        )
        await conn.execute(
            "ALTER TABLE application_notifications "
            "ADD CONSTRAINT application_notifications_application_id_fkey "
            "FOREIGN KEY (application_id) REFERENCES driver_applications(id) ON DELETE CASCADE"
        )
        await conn.commit()
        logger.info("Migration 116: application_notifications.application_id → ON DELETE CASCADE")
    except Exception as e:
        logger.info("Migration 116: FK cascade alter skipped — %s", e)


@_register("117_recruitment_link_view_count")
async def migrate_recruitment_link_view_count(conn) -> None:
    """``application_links.view_count`` — top-of-funnel counter for the
    per-link analytics (views → submissions → hires conversion).  Bumped
    by the public apply page; submissions/hires are derived from
    ``driver_applications`` at read time.  Idempotent ADD COLUMN."""
    try:
        await conn.execute(
            "ALTER TABLE application_links ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0"
        )
        await conn.commit()
        logger.info("Migration 117: added application_links.view_count")
    except Exception as e:
        logger.info("Migration 117: view_count likely exists — %s", e)


@_register("118_application_ssn_hash")
async def migrate_application_ssn_hash(conn) -> None:
    """``driver_applications.ssn_hash`` — a KEYED, per-account blind index
    of the SSN (HMAC, see infra.crypto.blind_index) for recruiter-side
    re-applicant detection.  It is one-way (the SSN is NOT recoverable from
    it) and never leaves the server.  Detection NEVER blocks the public
    form — it only flags prior matches for the recruiter.  Idempotent."""
    try:
        await conn.execute(
            "ALTER TABLE driver_applications ADD COLUMN ssn_hash TEXT"
        )
        await conn.commit()
        logger.info("Migration 118: added driver_applications.ssn_hash")
    except Exception as e:
        logger.info("Migration 118: ssn_hash likely exists — %s", e)
    try:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_driver_app_ssn_hash "
            "ON driver_applications(account_id, ssn_hash)"
        )
        await conn.commit()
        logger.info("Migration 118: created idx_driver_app_ssn_hash")
    except Exception as e:
        logger.info("Migration 118: ssn_hash index skipped — %s", e)


@_register("119_perm_recruit_to_manage_applications")
async def migrate_perm_recruit_to_manage_applications(conn) -> None:
    """Rename the permission flag ``can_recruit_applicants`` →
    ``can_manage_applications`` inside every stored per-account
    ``role_permissions`` override.

    The Applications feature was renamed off the recruiter-role word; the
    FeatureSet field + permission matrix now use ``can_manage_applications``.
    Existing accounts hold their grants as a JSON blob keyed by the OLD flag
    name, so without this rewrite a saved grant would be read under a key the
    code no longer knows and silently revert to the role default.  Fresh DBs
    have no such rows → no-op.  The physical recruitment_* tables keep their
    legacy names (renaming live tables fights schema.create_tables; not worth
    it for invisible names).
    """
    import json as _json
    try:
        cur = await conn.execute("SELECT id, permissions FROM role_permissions")
        rows = await cur.fetchall()
    except Exception as e:
        logger.info("Migration 119: role_permissions not present — %s", e)
        return
    changed = 0
    for r in rows:
        rid = r[0]
        try:
            perms = _json.loads(r[1] or "{}")
        except (ValueError, TypeError):
            continue
        if isinstance(perms, dict) and "can_recruit_applicants" in perms:
            perms["can_manage_applications"] = perms.pop("can_recruit_applicants")
            await conn.execute(
                "UPDATE role_permissions SET permissions = ? WHERE id = ?",
                (_json.dumps(perms), rid),
            )
            changed += 1
    await conn.commit()
    logger.info(
        "Migration 119: renamed can_recruit_applicants→can_manage_applications "
        "in %d role_permissions override(s)", changed,
    )


@_register("120_company_branding")
async def migrate_company_branding(conn) -> None:
    """Brand/identity fields on ``companies`` so each sub-company (a real
    carrier) carries its own logo + brand for the public application form
    and the DQ packet.  ``logo_object_id`` points at an object-store image;
    the rest are plain text.  Idempotent ADD COLUMN."""
    cols = [
        ("logo_object_id", "TEXT"),
        ("brand_color", "TEXT NOT NULL DEFAULT ''"),
        ("website", "TEXT NOT NULL DEFAULT ''"),
        ("phone", "TEXT NOT NULL DEFAULT ''"),
    ]
    for name, ddl in cols:
        try:
            await conn.execute(f"ALTER TABLE companies ADD COLUMN {name} {ddl}")
            await conn.commit()
            logger.info("Migration 120: added companies.%s", name)
        except Exception as e:
            logger.info("Migration 120: companies.%s likely exists — %s", name, e)


@_register("121_application_link_company")
async def migrate_application_link_company(conn) -> None:
    """``application_links.company_id`` — which sub-company (carrier) a
    recruiting link is for, so the public form + DQ packet brand to that
    carrier.  Nullable (NULL → generic/account brand, backward compatible).
    Idempotent ADD COLUMN."""
    try:
        await conn.execute(
            "ALTER TABLE application_links ADD COLUMN company_id INTEGER REFERENCES companies(id)"
        )
        await conn.commit()
        logger.info("Migration 121: added application_links.company_id")
    except Exception as e:
        logger.info("Migration 121: company_id likely exists — %s", e)


@_register("122_driver_application_company")
async def migrate_driver_application_company(conn) -> None:
    """``driver_applications.company_id`` — snapshots which carrier the
    applicant applied to (copied from the link at submit time, so it
    survives the link being revoked/re-pointed).  Drives the DQ packet's
    carrier identity (legal name + MC#/DOT#).  Nullable.  Idempotent."""
    try:
        await conn.execute(
            "ALTER TABLE driver_applications ADD COLUMN company_id INTEGER REFERENCES companies(id)"
        )
        await conn.commit()
        logger.info("Migration 122: added driver_applications.company_id")
    except Exception as e:
        logger.info("Migration 122: company_id likely exists — %s", e)


@_register("123_company_brand_content")
async def migrate_company_brand_content(conn) -> None:
    """Recruiter-editable apply-form content on a carrier: ``headline`` (a
    one-line pitch), ``perks`` (newline-separated selling points), and
    ``banner_object_id`` (a hero photo).  Cosmetic — surfaced on the public
    apply form only.  Idempotent ADD COLUMNs."""
    cols = [
        ("headline", "TEXT NOT NULL DEFAULT ''"),
        ("perks", "TEXT NOT NULL DEFAULT ''"),
        ("banner_object_id", "TEXT"),
    ]
    for name, ddl in cols:
        try:
            await conn.execute(f"ALTER TABLE companies ADD COLUMN {name} {ddl}")
            await conn.commit()
            logger.info("Migration 123: added companies.%s", name)
        except Exception as e:
            logger.info("Migration 123: companies.%s likely exists — %s", name, e)


@_register("124_company_requirements")
async def migrate_company_requirements(conn) -> None:
    """Per-carrier pre-qual gate thresholds for the apply form: minimum CDL
    experience (years), minimum age, and required CDL class.  Defaults match
    the old hardcoded gate (1 / 21 / A) so unbranded forms are unchanged.
    These adapt the gate's self-attestation question text only — no hard
    server enforcement.  Idempotent ADD COLUMNs."""
    cols = [
        ("req_experience_years", "INTEGER NOT NULL DEFAULT 1"),
        ("req_min_age", "INTEGER NOT NULL DEFAULT 21"),
        ("req_cdl_class", "TEXT NOT NULL DEFAULT 'A'"),
    ]
    for name, ddl in cols:
        try:
            await conn.execute(f"ALTER TABLE companies ADD COLUMN {name} {ddl}")
            await conn.commit()
            logger.info("Migration 124: added companies.%s", name)
        except Exception as e:
            logger.info("Migration 124: companies.%s likely exists — %s", name, e)


@_register("125_company_form_theme")
async def migrate_company_form_theme(conn) -> None:
    """Per-carrier apply-form base theme — 'light' (default) or 'dark' — so a
    carrier can match a dark website.  The carrier's accent colour tints the
    primary UI on top of whichever base.  Idempotent ADD COLUMN."""
    try:
        await conn.execute(
            "ALTER TABLE companies ADD COLUMN form_theme TEXT NOT NULL DEFAULT 'light'"
        )
        await conn.commit()
        logger.info("Migration 125: added companies.form_theme")
    except Exception as e:
        logger.info("Migration 125: form_theme likely exists — %s", e)


@_register("126_company_header_bg_colors")
async def migrate_company_header_bg_colors(conn) -> None:
    """Optional apply-form colours beyond the accent: ``header_color`` tints
    the hero band, ``bg_color`` tints the page background.  Empty → the base
    theme default.  Text on each is auto-contrasted client-side.  Idempotent."""
    cols = [
        ("header_color", "TEXT NOT NULL DEFAULT ''"),
        ("bg_color", "TEXT NOT NULL DEFAULT ''"),
    ]
    for name, ddl in cols:
        try:
            await conn.execute(f"ALTER TABLE companies ADD COLUMN {name} {ddl}")
            await conn.commit()
            logger.info("Migration 126: added companies.%s", name)
        except Exception as e:
            logger.info("Migration 126: companies.%s likely exists — %s", name, e)


@_register("127_company_heading_color")
async def migrate_company_heading_color(conn) -> None:
    """Optional apply-form heading colour — tints the big title + carrier name
    only (body/hints stay auto-contrasted).  Empty → base default.  Idempotent."""
    try:
        await conn.execute("ALTER TABLE companies ADD COLUMN heading_color TEXT NOT NULL DEFAULT ''")
        await conn.commit()
        logger.info("Migration 127: added companies.heading_color")
    except Exception as e:
        logger.info("Migration 127: heading_color likely exists — %s", e)


@_register("128_company_legal_compliance")
async def migrate_company_legal_compliance(conn) -> None:
    """Carrier legal/compliance details that fill the apply-form FMCSA/FCRA
    consent disclosures: mailing address, compliance email, and the
    background-check agency (CRA) name/address/phone/site.  Recruiter-filled
    BLANKS only — the legal language itself is fixed in code.  Idempotent."""
    cols = [
        ("legal_address", "TEXT NOT NULL DEFAULT ''"),
        ("compliance_email", "TEXT NOT NULL DEFAULT ''"),
        ("cra_name", "TEXT NOT NULL DEFAULT ''"),
        ("cra_address", "TEXT NOT NULL DEFAULT ''"),
        ("cra_phone", "TEXT NOT NULL DEFAULT ''"),
        ("cra_site", "TEXT NOT NULL DEFAULT ''"),
    ]
    for name, ddl in cols:
        try:
            await conn.execute(f"ALTER TABLE companies ADD COLUMN {name} {ddl}")
            await conn.commit()
            logger.info("Migration 128: added companies.%s", name)
        except Exception as e:
            logger.info("Migration 128: companies.%s likely exists — %s", name, e)


@_register("129_company_surface_color")
async def migrate_company_surface_color(conn) -> None:
    """``surface_color`` — the apply-form's base surface (card + page).  The
    form derives readable text / borders / muted fills from it (see
    surfaceThemeStyle), replacing the old light/dark toggle.  Empty → the
    default light theme.  Backfills existing dark-base carriers so they keep
    a dark look.  Idempotent."""
    try:
        await conn.execute("ALTER TABLE companies ADD COLUMN surface_color TEXT NOT NULL DEFAULT ''")
        await conn.commit()
        logger.info("Migration 129: added companies.surface_color")
    except Exception as e:
        logger.info("Migration 129: surface_color likely exists — %s", e)
    try:
        await conn.execute(
            "UPDATE companies SET surface_color = '#0a0a0a' "
            "WHERE form_theme = 'dark' AND (surface_color IS NULL OR surface_color = '')"
        )
        await conn.commit()
        logger.info("Migration 129: backfilled surface_color for dark-base carriers")
    except Exception as e:
        logger.info("Migration 129: surface_color backfill skipped — %s", e)


@_register("128_drop_derived_service_perms")
async def migrate_drop_derived_service_perms(conn) -> None:
    """Strip the now-DERIVED service flags from every stored
    ``role_permissions`` override: ``can_alerts_all``, ``can_alerts_vehicle``,
    ``can_ai_chat``, ``can_digest``.

    The Alerts inbox, the AI assistant, and the Reports hub (incl. its
    scheduled-report subscription) are no longer owner-toggled features —
    access is derived from the role's feature permissions at resolve time
    (capabilities/permissions/roles.derive_service_perms).  Any value stored
    under these keys is now ignored on read, so leaving them in the JSON blob
    would only mislead a future reader into thinking the toggle still matters.
    This removes them.  Fresh DBs have no such rows → no-op; the rewrite is
    idempotent (a second run finds nothing to strip).
    """
    import json as _json
    _DERIVED = ("can_alerts_all", "can_alerts_vehicle", "can_ai_chat", "can_digest")
    try:
        cur = await conn.execute("SELECT id, permissions FROM role_permissions")
        rows = await cur.fetchall()
    except Exception as e:
        logger.info("Migration 128: role_permissions not present — %s", e)
        return
    changed = 0
    for r in rows:
        rid = r[0]
        try:
            perms = _json.loads(r[1] or "{}")
        except (ValueError, TypeError):
            continue
        if isinstance(perms, dict) and any(k in perms for k in _DERIVED):
            for k in _DERIVED:
                perms.pop(k, None)
            await conn.execute(
                "UPDATE role_permissions SET permissions = ? WHERE id = ?",
                (_json.dumps(perms), rid),
            )
            changed += 1
    await conn.commit()
    logger.info(
        "Migration 128: dropped derived service flags "
        "(can_alerts_all/can_alerts_vehicle/can_ai_chat) from %d "
        "role_permissions override(s)", changed,
    )


@_register("130_user_preferences")
async def migrate_user_preferences(conn) -> None:
    """Create ``user_preferences`` for per-user opaque UI state.

    Backs the DataTable's cross-device auto-save (column visibility /
    order / pinning that follows the operator instead of being trapped
    in one browser's localStorage).  Same shape as ``account_settings``
    but keyed by ``user_id``.

    Fresh DBs pick this up via schema.create_tables(); existing
    deployments need this migration since the CREATE TABLE IF NOT
    EXISTS in schema.py is a no-op on already-initialised databases.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            key         TEXT    NOT NULL,
            value       TEXT    NOT NULL DEFAULT '',
            updated_at  TEXT    NOT NULL,
            PRIMARY KEY (user_id, key)
        )
    """)
    await conn.commit()
    logger.info("Migration 130: created user_preferences table")


@_register("131_vehicle_telemetry_unified")
async def migrate_vehicle_telemetry_unified(conn) -> None:
    """Merge ``vehicle_telemetry_hourly`` + ``vehicle_metrics_daily`` into
    ONE granularity-keyed warehouse table ``vehicle_telemetry``.

    The two aggregate tiers had nearly identical shapes but drifted names
    (``hour_utc``/``day_utc``; the hourly tier even carries a dead legacy
    ``top_speed_mph`` alongside ``max_speed_mph``) and forced a new table
    per grain.  One table keyed by ``granularity`` ('hourly'|'daily'|
    'weekly') ends that and lets the weekly tier exist with zero new DDL.

    Backfill copies BOTH old tables in.  The daily end-of-day odometer
    CANNOT be recomputed past the 7-day ``vehicle_state_snapshot`` horizon,
    so it must be COPIED, not rebuilt — the daily table is the durable EOD
    store (see [[eod_odometer_history]]).  The old tables are LEFT IN PLACE
    as a dead backup; a later migration drops them once this has baked.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS vehicle_telemetry (
            account_id        INTEGER NOT NULL,
            vehicle_id        TEXT    NOT NULL,
            vehicle_name      TEXT    NOT NULL DEFAULT '',
            granularity       TEXT    NOT NULL,
            bucket_start      TEXT    NOT NULL,
            miles             REAL    NOT NULL DEFAULT 0,
            drive_min         REAL    NOT NULL DEFAULT 0,
            idle_min          REAL    NOT NULL DEFAULT 0,
            max_speed_mph     REAL,
            avg_fuel_pct      REAL,
            harsh_event_count INTEGER NOT NULL DEFAULT 0,
            fault_count_eod   INTEGER NOT NULL DEFAULT 0,
            odometer_eod      REAL,
            engine_hours_eod  REAL,
            ingested_at       TEXT    NOT NULL DEFAULT '',
            PRIMARY KEY (account_id, vehicle_id, granularity, bucket_start)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vehicle_telemetry_grain "
        "ON vehicle_telemetry(account_id, granularity, bucket_start DESC)"
    )
    await conn.commit()

    # Backfill the hourly tier.  (avg_fuel_pct exists on the old hourly
    # table but was never populated — copied for completeness; stays NULL.)
    await conn.execute("""
        INSERT INTO vehicle_telemetry (
            account_id, vehicle_id, vehicle_name, granularity, bucket_start,
            miles, drive_min, idle_min, max_speed_mph, avg_fuel_pct,
            harsh_event_count, ingested_at
        )
        SELECT account_id, vehicle_id, vehicle_name, 'hourly', hour_utc,
               miles, drive_min, idle_min, max_speed_mph, avg_fuel_pct,
               harsh_event_count, ingested_at
          FROM vehicle_telemetry_hourly
        ON CONFLICT (account_id, vehicle_id, granularity, bucket_start) DO NOTHING
    """)
    # Backfill the daily tier (carries the long-retention EOD odometer).
    await conn.execute("""
        INSERT INTO vehicle_telemetry (
            account_id, vehicle_id, granularity, bucket_start,
            miles, drive_min, idle_min, max_speed_mph, avg_fuel_pct,
            harsh_event_count, fault_count_eod, odometer_eod, engine_hours_eod,
            ingested_at
        )
        SELECT account_id, vehicle_id, 'daily', day_utc,
               miles, drive_min, idle_min, max_speed_mph, avg_fuel_pct,
               harsh_event_count, fault_count_eod, odometer_eod, engine_hours_eod,
               ingested_at
          FROM vehicle_metrics_daily
        ON CONFLICT (account_id, vehicle_id, granularity, bucket_start) DO NOTHING
    """)
    await conn.commit()
    logger.info("Migration 131: vehicle_telemetry unified table created + backfilled")

    # RLS — match the protection vehicle_telemetry_hourly already has
    # (migration 057).  Gated by ENABLE_RLS, same shape as 057/103/105.
    import os
    if os.getenv("ENABLE_RLS", "0").strip() not in ("1", "true", "TRUE", "yes"):
        logger.info("Migration 131: ENABLE_RLS not set; RLS policy skipped")
        return
    try:
        await conn.execute("ALTER TABLE vehicle_telemetry ENABLE ROW LEVEL SECURITY")
        await conn.execute("ALTER TABLE vehicle_telemetry FORCE ROW LEVEL SECURITY")
        await conn.execute("DROP POLICY IF EXISTS tenant_isolation ON vehicle_telemetry")
        await conn.execute(
            """
            CREATE POLICY tenant_isolation ON vehicle_telemetry
            USING       (account_id::text = current_setting('app.account_id', true))
            WITH CHECK  (account_id::text = current_setting('app.account_id', true))
            """
        )
        await conn.commit()
        logger.info("Migration 131: tenant_isolation RLS applied to vehicle_telemetry")
    except Exception as e:
        logger.error("Migration 131: RLS apply on vehicle_telemetry failed — %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("132_drop_legacy_telemetry_tiers")
async def migrate_drop_legacy_telemetry_tiers(conn) -> None:
    """Drop the pre-merge aggregate tables now that 131 unified them into
    ``vehicle_telemetry``.

    GUARDED — only drops when ``vehicle_telemetry`` already holds at least
    as many rows per granularity as each old table.  This codebase has a
    history of migrations marking themselves *applied* after a partial
    failure (see 035 → 036), so a guard — not trust — is what makes the
    drop safe: if 131's backfill silently half-failed, the unified counts
    fall short, the guard trips, and the old tables are KEPT untouched.

    Designed to run in the SAME release right after 131 — no pruning has
    run yet, so the per-tier counts match exactly and the guard passes
    cleanly.  If the guard ever trips (incomplete backfill), the tables are
    left as the recoverable backup.  Backfill is provably lossless (old PKs
    map 1:1 into ``(account, vehicle, granularity, bucket_start)``), so the
    happy path is the expected one.
    """
    async def _count(sql: str):
        """Row count, or None when the table is missing (rolls back the
        aborted txn so the next query runs clean on Postgres)."""
        try:
            cur = await conn.execute(sql)
            rows = await cur.fetchall()
            return int(rows[0][0]) if rows and rows[0] and rows[0][0] is not None else 0
        except Exception:
            try:
                await conn.rollback()
            except Exception:
                pass
            return None

    old_hourly = await _count("SELECT COUNT(*) FROM vehicle_telemetry_hourly")
    old_daily = await _count("SELECT COUNT(*) FROM vehicle_metrics_daily")
    if old_hourly is None and old_daily is None:
        logger.info("Migration 132: legacy tiers already absent; nothing to drop")
        return

    new_hourly = await _count(
        "SELECT COUNT(*) FROM vehicle_telemetry WHERE granularity = 'hourly'"
    ) or 0
    new_daily = await _count(
        "SELECT COUNT(*) FROM vehicle_telemetry WHERE granularity = 'daily'"
    ) or 0

    # Guard: for each old table that still exists, unified must hold >= its rows.
    safe_hourly = (old_hourly is None) or (new_hourly >= old_hourly)
    safe_daily = (old_daily is None) or (new_daily >= old_daily)
    if not (safe_hourly and safe_daily):
        logger.warning(
            "Migration 132: KEEPING legacy tiers — unified row counts below "
            "the old tables (hourly new=%s old=%s, daily new=%s old=%s). The "
            "131 backfill looks incomplete; old tables preserved as backup.",
            new_hourly, old_hourly, new_daily, old_daily,
        )
        return

    for tbl in ("vehicle_telemetry_hourly", "vehicle_metrics_daily"):
        try:
            await conn.execute(f"DROP TABLE IF EXISTS {tbl}")
            await conn.commit()
            logger.info("Migration 132: dropped legacy table %s", tbl)
        except Exception as e:
            logger.error("Migration 132: failed to drop %s — %s", tbl, e)
            try:
                await conn.rollback()
            except Exception:
                pass


@_register("133_vehicles_field_provenance")
async def migrate_vehicles_field_provenance(conn) -> None:
    """Add ``vehicles.field_provenance`` — a JSON map ``{spec_field: source}``
    recording WHICH source last authoritatively set each spec field.

    Powers source-precedence merges (a lower-priority integration can't
    overwrite a higher-priority source's value) and operator pins (a field
    whose provenance is ``manual`` is never overwritten by a sync).  No
    backfill needed: the merge engine treats a MISSING entry as "owned by
    the row's existing ``source``", so existing rows behave correctly and
    provenance fills in as syncs run.  Idempotent (skips if the column is
    already present)."""
    try:
        await conn.execute(
            "ALTER TABLE vehicles ADD COLUMN field_provenance TEXT NOT NULL DEFAULT '{}'"
        )
        await conn.commit()
        logger.info("Migration 133: added vehicles.field_provenance")
    except Exception as e:
        logger.debug("vehicles.field_provenance skipped: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("134_data_conflicts")
async def migrate_data_conflicts(conn) -> None:
    """Cross-source data conflicts — when two integrations carry a DIFFERENT
    non-empty value for the same field, record it so an operator can review +
    resolve (Phase 2 / Layer 3).  Generic on ``(entity_type, entity_id)`` so
    work-orders / other domains can reuse the store; vehicles is the first
    consumer.  One open conflict per ``(account, entity_type, entity_id, field)``."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS data_conflicts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            entity_type     TEXT    NOT NULL,
            entity_id       INTEGER NOT NULL,
            field           TEXT    NOT NULL,
            current_value   TEXT    NOT NULL DEFAULT '',
            current_source  TEXT    NOT NULL DEFAULT '',
            incoming_value  TEXT    NOT NULL DEFAULT '',
            incoming_source TEXT    NOT NULL DEFAULT '',
            status          TEXT    NOT NULL DEFAULT 'open',
            detected_at     TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT '',
            resolved_by     INTEGER,
            resolved_value  TEXT,
            UNIQUE(account_id, entity_type, entity_id, field)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_conflicts_open "
        "ON data_conflicts(account_id, status)"
    )
    await conn.commit()
    logger.info("Migration 134: data_conflicts table created")

    # RLS — tenant-scoped, same gated pattern as 057/131.
    import os
    if os.getenv("ENABLE_RLS", "0").strip() not in ("1", "true", "TRUE", "yes"):
        logger.info("Migration 134: ENABLE_RLS not set; RLS policy skipped")
        return
    try:
        await conn.execute("ALTER TABLE data_conflicts ENABLE ROW LEVEL SECURITY")
        await conn.execute("ALTER TABLE data_conflicts FORCE ROW LEVEL SECURITY")
        await conn.execute("DROP POLICY IF EXISTS tenant_isolation ON data_conflicts")
        await conn.execute(
            """
            CREATE POLICY tenant_isolation ON data_conflicts
            USING       (account_id::text = current_setting('app.account_id', true))
            WITH CHECK  (account_id::text = current_setting('app.account_id', true))
            """
        )
        await conn.commit()
        logger.info("Migration 134: tenant_isolation RLS applied to data_conflicts")
    except Exception as e:
        logger.error("Migration 134: RLS apply on data_conflicts failed — %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("135_carrier_directory")
async def migrate_carrier_directory(conn) -> None:
    """Create ``carrier_profile`` — the recruiter Carrier Knowledge Base.

    An account-scoped, info-only directory of the EXTERNAL carriers the
    recruiting team works with: each row is a free-form profile (pre-qual
    criteria, presentation/sales sheet, process notes) stored as JSON in
    ``content``.  Maintained by ``recruiter_manager``, read by ``recruiter``.
    Deliberately separate from ``companies`` (which brands the apply form).

    Fresh DBs pick this up via schema.create_tables(); existing deployments
    need this migration (the CREATE TABLE IF NOT EXISTS in schema.py is a
    no-op on already-initialised databases).
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS carrier_profile (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id          INTEGER NOT NULL REFERENCES accounts(id),
            name                TEXT    NOT NULL,
            website             TEXT    NOT NULL DEFAULT '',
            video_url           TEXT    NOT NULL DEFAULT '',
            experience_summary  TEXT    NOT NULL DEFAULT '',
            content             TEXT    NOT NULL DEFAULT '{}',
            created_by          INTEGER NOT NULL DEFAULT 0,
            created_at          TEXT    NOT NULL,
            updated_at          TEXT    NOT NULL DEFAULT ''
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_carrier_profile_account "
        "ON carrier_profile(account_id)"
    )
    await conn.commit()
    logger.info("Migration 135: created carrier_profile table")

    # RLS — tenant-scoped, same gated pattern as 131/134.
    import os
    if os.getenv("ENABLE_RLS", "0").strip() not in ("1", "true", "TRUE", "yes"):
        logger.info("Migration 135: ENABLE_RLS not set; RLS policy skipped")
        return
    try:
        await conn.execute("ALTER TABLE carrier_profile ENABLE ROW LEVEL SECURITY")
        await conn.execute("ALTER TABLE carrier_profile FORCE ROW LEVEL SECURITY")
        await conn.execute("DROP POLICY IF EXISTS tenant_isolation ON carrier_profile")
        await conn.execute(
            """
            CREATE POLICY tenant_isolation ON carrier_profile
            USING       (account_id::text = current_setting('app.account_id', true))
            WITH CHECK  (account_id::text = current_setting('app.account_id', true))
            """
        )
        await conn.commit()
        logger.info("Migration 135: tenant_isolation RLS applied to carrier_profile")
    except Exception as e:
        logger.error("Migration 135: RLS apply on carrier_profile failed — %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("136_user_is_manager")
async def migrate_user_is_manager(conn) -> None:
    """Add ``users.is_manager`` and retire the ``recruiter_manager`` role.

    "Manager" is now a per-user seniority tier layered on the base role
    (capabilities/permissions/roles.MANAGER_GRANTS), NOT its own role.  This:

      1. adds the ``is_manager`` column (fresh DBs get it from schema.py;
         existing ones need this ALTER — the CREATE TABLE IF NOT EXISTS is a
         no-op once the table exists);
      2. folds any existing ``recruiter_manager`` USER back to ``recruiter``
         with ``is_manager = 1`` (the elevated access now rides the tier, not
         the role);
      3. drops the now-orphaned ``recruiter_manager`` ``role_permissions``
         rows (the manager delta is code-defined, not a stored role row).
    """
    try:
        await conn.execute(
            "ALTER TABLE users ADD COLUMN is_manager INTEGER NOT NULL DEFAULT 0"
        )
        await conn.commit()
        logger.info("Migration 136: added column users.is_manager")
    except Exception:
        pass  # column already exists

    # Fold recruiter_manager USERS → recruiter + is_manager (data migration).
    try:
        await conn.execute(
            "UPDATE users SET role = 'recruiter', is_manager = 1 "
            "WHERE role = 'recruiter_manager'"
        )
        await conn.commit()
        logger.info("Migration 136: folded recruiter_manager users → recruiter + is_manager")
    except Exception as e:
        logger.error("Migration 136: recruiter_manager user fold failed — %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass

    # Drop the orphaned per-account recruiter_manager permission rows — the
    # manager delta is now code-defined (MANAGER_GRANTS), not a stored role.
    try:
        await conn.execute(
            "DELETE FROM role_permissions WHERE role = 'recruiter_manager'"
        )
        await conn.commit()
        logger.info("Migration 136: dropped recruiter_manager role_permissions rows")
    except Exception as e:
        logger.error("Migration 136: role_permissions cleanup failed — %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("137_co_owners")
async def migrate_co_owners(conn) -> None:
    """Multiple owners with a protected PRIMARY owner.

    Adds ``users.is_primary_owner`` and marks each account's existing sole
    owner as primary (every account has exactly one owner today, so this is
    an exact backfill).  Also creates ``owner_promotion_codes`` — the
    email-code half of the two-factor co-owner promotion confirm.

    Co-owners have ``role='owner'`` + ``is_primary_owner=0``; only the
    primary owner may create/remove co-owners or do destructive account
    actions.
    """
    try:
        await conn.execute(
            "ALTER TABLE users ADD COLUMN is_primary_owner INTEGER NOT NULL DEFAULT 0"
        )
        await conn.commit()
        logger.info("Migration 137: added column users.is_primary_owner")
    except Exception:
        pass  # column already exists

    # Backfill: today's single owner-per-account IS the primary owner.
    try:
        await conn.execute(
            "UPDATE users SET is_primary_owner = 1 WHERE role = 'owner'"
        )
        await conn.commit()
        logger.info("Migration 137: marked existing owners as primary")
    except Exception as e:
        logger.error("Migration 137: primary-owner backfill failed — %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass

    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS owner_promotion_codes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id        INTEGER NOT NULL UNIQUE REFERENCES accounts(id),
                initiator_user_id INTEGER NOT NULL,
                target_user_id    INTEGER NOT NULL,
                code_hash         TEXT    NOT NULL,
                expires_at        TEXT    NOT NULL,
                created_at        TEXT    NOT NULL
            )
        """)
        await conn.commit()
        logger.info("Migration 137: created owner_promotion_codes table")
    except Exception as e:
        logger.error("Migration 137: owner_promotion_codes create failed — %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("138_application_drafts")
async def migrate_application_drafts(conn) -> None:
    """Create ``application_drafts`` — save-and-resume for the public apply
    form.

    One row per (link, applicant email): the in-progress form JSON is held
    ENCRYPTED (it's pre-consent PII) alongside a few clear columns that are
    the only thing recruiters may see (name, step progress, timestamps).
    ``resume_token`` is the emailed cross-device unlock (+ the email itself
    re-entered as the second factor); ``draft_secret`` is the in-session
    write credential so a stranger can't overwrite someone's draft.
    Drafts are short-lived: purged by retention after 14 days idle and
    deleted on submit.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS application_drafts (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id       INTEGER NOT NULL REFERENCES accounts(id),
            link_token       TEXT    NOT NULL,
            email            TEXT    NOT NULL,
            resume_token     TEXT    NOT NULL UNIQUE,
            draft_secret     TEXT    NOT NULL,
            first_name       TEXT    NOT NULL DEFAULT '',
            last_name        TEXT    NOT NULL DEFAULT '',
            step             INTEGER NOT NULL DEFAULT 0,
            steps_total      INTEGER NOT NULL DEFAULT 0,
            data_encrypted   TEXT    NOT NULL DEFAULT '',
            link_emailed_at  TEXT,
            reminder_sent_at TEXT,
            created_at       TEXT    NOT NULL,
            updated_at       TEXT    NOT NULL,
            UNIQUE(account_id, link_token, email)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_app_drafts_account "
        "ON application_drafts(account_id, updated_at)"
    )
    await conn.commit()
    logger.info("Migration 138: created application_drafts table")

    # RLS — tenant-scoped, same gated pattern as 131/134/135.
    import os
    if os.getenv("ENABLE_RLS", "0").strip() not in ("1", "true", "TRUE", "yes"):
        logger.info("Migration 138: ENABLE_RLS not set; RLS policy skipped")
        return
    try:
        await conn.execute("ALTER TABLE application_drafts ENABLE ROW LEVEL SECURITY")
        await conn.execute("ALTER TABLE application_drafts FORCE ROW LEVEL SECURITY")
        await conn.execute("DROP POLICY IF EXISTS tenant_isolation ON application_drafts")
        await conn.execute(
            """
            CREATE POLICY tenant_isolation ON application_drafts
            USING       (account_id::text = current_setting('app.account_id', true))
            WITH CHECK  (account_id::text = current_setting('app.account_id', true))
            """
        )
        await conn.commit()
        logger.info("Migration 138: tenant_isolation RLS applied to application_drafts")
    except Exception as e:
        logger.error("Migration 138: RLS apply on application_drafts failed — %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("139_loads")
async def migrate_loads(conn) -> None:
    """Create ``loads`` — the canonical load/shipment model (the Loads
    feature).  OUR Postgres is the SSOT: dispatchers/operators enter loads
    by hand (source='manual'), and a connected TMS (Datatruck) projects its
    orders in (source='datatruck', keyed by external_ref) — same inversion
    as the vehicles registry and work-orders module.

    ``field_provenance`` + ``source``/``external_ref`` ship NOW so the
    Datatruck projection phase is code-only (no second migration).  Driver /
    dispatcher link to ``users`` ids with a free-text name fallback for
    people who aren't 4truck users yet.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS loads (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id         INTEGER NOT NULL,
            load_number        TEXT    NOT NULL DEFAULT '',
            status             TEXT    NOT NULL DEFAULT 'upcoming',
            payment_status     TEXT    NOT NULL DEFAULT '',
            customer           TEXT    NOT NULL DEFAULT '',
            company_code       TEXT    NOT NULL DEFAULT '',
            pickup_location    TEXT    NOT NULL DEFAULT '',
            pickup_date        TEXT    NOT NULL DEFAULT '',
            delivery_location  TEXT    NOT NULL DEFAULT '',
            delivery_date      TEXT    NOT NULL DEFAULT '',
            driver_user_id     INTEGER,
            driver_name        TEXT    NOT NULL DEFAULT '',
            dispatcher_user_id INTEGER,
            dispatcher_name    TEXT    NOT NULL DEFAULT '',
            vehicle_unit       TEXT    NOT NULL DEFAULT '',
            trailer_unit       TEXT    NOT NULL DEFAULT '',
            total_rate         REAL,
            loaded_miles       REAL,
            empty_miles        REAL,
            driver_pay         REAL,
            other_costs        REAL,
            source             TEXT    NOT NULL DEFAULT 'manual',
            external_ref       TEXT    NOT NULL DEFAULT '',
            field_provenance   TEXT    NOT NULL DEFAULT '{}',
            notes              TEXT    NOT NULL DEFAULT '',
            is_active          INTEGER NOT NULL DEFAULT 1,
            created_at         TEXT    NOT NULL DEFAULT '',
            updated_at         TEXT    NOT NULL DEFAULT ''
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_loads_account_status "
        "ON loads(account_id, status)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_loads_account_pickup "
        "ON loads(account_id, pickup_date DESC)"
    )
    # One row per synced TMS order; manual rows have external_ref='' and
    # stay outside the constraint (partial index).
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_loads_external_ref "
        "ON loads(account_id, source, external_ref) WHERE external_ref <> ''"
    )
    await conn.commit()
    logger.info("Migration 139: loads table created")

    # RLS — tenant-scoped, same gated pattern as 131/134/138.
    import os
    if os.getenv("ENABLE_RLS", "0").strip() not in ("1", "true", "TRUE", "yes"):
        logger.info("Migration 139: ENABLE_RLS not set; RLS policy skipped")
        return
    try:
        await conn.execute("ALTER TABLE loads ENABLE ROW LEVEL SECURITY")
        await conn.execute("ALTER TABLE loads FORCE ROW LEVEL SECURITY")
        await conn.execute("DROP POLICY IF EXISTS tenant_isolation ON loads")
        await conn.execute(
            """
            CREATE POLICY tenant_isolation ON loads
            USING       (account_id::text = current_setting('app.account_id', true))
            WITH CHECK  (account_id::text = current_setting('app.account_id', true))
            """
        )
        await conn.commit()
        logger.info("Migration 139: tenant_isolation RLS applied to loads")
    except Exception as e:
        logger.error("Migration 139: RLS apply on loads failed — %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("139_link_auto_remind")
async def migrate_link_auto_remind(conn) -> None:
    """Per-link auto-remind policy for abandoned apply drafts.

    ``remind_every_hours`` (0 = off, the default — recruiters opt IN per
    link) sets the nudge cadence; ``remind_max`` caps how many automatic
    reminders one draft may ever receive (the no-infinite-sends guarantee).
    ``reminders_sent`` on the draft is that lifetime counter.
    """
    for table, ddl in (
        ("application_links", "ADD COLUMN remind_every_hours INTEGER NOT NULL DEFAULT 0"),
        ("application_links", "ADD COLUMN remind_max INTEGER NOT NULL DEFAULT 3"),
        ("application_drafts", "ADD COLUMN reminders_sent INTEGER NOT NULL DEFAULT 0"),
    ):
        try:
            await conn.execute(f"ALTER TABLE {table} {ddl}")
            await conn.commit()
        except Exception as e:
            logger.info("Migration 139: %s %s skipped (%s)", table, ddl.split()[2], e)
            try:
                await conn.rollback()
            except Exception:
                pass
    logger.info("Migration 139: link auto-remind columns ensured")


@_register("140_loads_seq_and_driver_pay_fix")
async def migrate_loads_seq_and_driver_pay_fix(conn) -> None:
    """Two follow-ups from the first live Datatruck loads sync.

    * ``loads.seq`` — a per-account sequential Load ID (1, 2, 3…), separate
      from ``load_number`` (the broker/BOL reference the dispatcher enters
      or the TMS carries).  Backfilled per account in insertion order.
    * Clear the ``driver_pay`` values a brief bad mapping stamped from
      ``trip.total_load_pay`` (which is the trip's REVENUE share, not
      driver earnings) — only rows the integration owns are touched;
      operator-pinned values (provenance 'manual') are preserved.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(loads)")
        cols = {r[1] for r in await cur.fetchall()}
        if "seq" not in cols:
            await conn.execute("ALTER TABLE loads ADD COLUMN seq INTEGER")
            await conn.commit()
            logger.info("Migration 140: added loads.seq")
        await conn.execute(
            """UPDATE loads SET seq = sub.rn
               FROM (SELECT id, ROW_NUMBER() OVER (
                         PARTITION BY account_id ORDER BY id) AS rn
                       FROM loads) sub
               WHERE loads.id = sub.id AND loads.seq IS NULL"""
        )
        await conn.execute(
            """UPDATE loads SET driver_pay = NULL
               WHERE source = 'datatruck'
                 AND field_provenance LIKE '%"driver_pay": "datatruck"%'"""
        )
        await conn.commit()
        logger.info("Migration 140: loads.seq backfilled + bad driver_pay cleared")
    except Exception as e:
        logger.error("Migration 140 failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("141_vehicles_datatruck_ref")
async def migrate_vehicles_datatruck_ref(conn) -> None:
    """Add ``vehicles.datatruck_ref`` — the Datatruck-side asset binding,
    completing the uniform identity pattern (our stable id + one link column
    per integration, like ``telematics_ref`` / ``users.datatruck_driver_id``).

    Match ladder becomes ref-first: an existing ref decides identity
    outright on every re-sync; the VIN → plate → unique-unit natural keys
    remain the DISCOVERY fallback that makes the first link.  No backfill —
    refs stamp organically as the next syncs match rows."""
    try:
        cur = await conn.execute("PRAGMA table_info(vehicles)")
        cols = {r[1] for r in await cur.fetchall()}
        if "datatruck_ref" not in cols:
            await conn.execute(
                "ALTER TABLE vehicles ADD COLUMN datatruck_ref TEXT NOT NULL DEFAULT ''"
            )
            await conn.commit()
            logger.info("Migration 141: added vehicles.datatruck_ref")
    except Exception as e:
        logger.error("Migration 141 failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("140_employer_verifications")
async def migrate_employer_verifications(conn) -> None:
    """Create ``application_employer_verifications`` — the §391.23 safety-
    performance-history investigation trail.

    One row per (application, prior employer) once a recruiter engages it:
    request sent when/where, attempts, and the outcome.  The row trail IS the
    good-faith documentation FMCSA requires when an old employer never
    responds.  Rows cascade away with their application.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS application_employer_verifications (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id     INTEGER NOT NULL REFERENCES accounts(id),
            application_id INTEGER NOT NULL REFERENCES driver_applications(id) ON DELETE CASCADE,
            employer_index INTEGER NOT NULL,
            employer_name  TEXT    NOT NULL DEFAULT '',
            employer_email TEXT    NOT NULL DEFAULT '',
            status         TEXT    NOT NULL DEFAULT 'pending',
            attempts       INTEGER NOT NULL DEFAULT 0,
            sent_at        TEXT,
            responded_at   TEXT,
            notes          TEXT    NOT NULL DEFAULT '',
            created_at     TEXT    NOT NULL,
            updated_at     TEXT    NOT NULL,
            UNIQUE(application_id, employer_index)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_emp_verif_account "
        "ON application_employer_verifications(account_id, application_id)"
    )
    await conn.commit()
    logger.info("Migration 140: created application_employer_verifications")


@_register("142_carrier_intake")
async def migrate_carrier_intake(conn) -> None:
    """Add the carrier self-fill invite columns to ``carrier_profile``.

    A recruiting manager can mint a tokenized public link (like the driver
    apply links) and send it to the external carrier, who fills in their own
    requirements/presentation sheet on apply.<apex>/carrier/<token>.  One
    active link per carrier: the token + expiry live on the profile row; a
    carrier submission stamps ``intake_submitted_at`` and raises the
    review-pending flag, which a manager's next save clears.

    Fresh DBs get the columns from schema.py; existing deployments need
    these ALTERs.  No index on ``intake_token`` — the table is small
    (dozens of carriers per account) and schema.py must not carry an index
    on a post-creation column (boot-order crash on upgraded DBs).
    """
    try:
        cur = await conn.execute("PRAGMA table_info(carrier_profile)")
        cols = {r[1] for r in await cur.fetchall()}
        adds = [
            ("intake_token", "TEXT NOT NULL DEFAULT ''"),
            ("intake_expires_at", "TEXT"),
            ("intake_email", "TEXT NOT NULL DEFAULT ''"),
            ("intake_invited_by", "INTEGER NOT NULL DEFAULT 0"),
            ("intake_submitted_at", "TEXT NOT NULL DEFAULT ''"),
            ("intake_review_pending", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for name, decl in adds:
            if name not in cols:
                await conn.execute(
                    f"ALTER TABLE carrier_profile ADD COLUMN {name} {decl}"
                )
        await conn.commit()
        logger.info("Migration 142: added carrier_profile intake columns")
    except Exception as e:
        logger.error("Migration 142 failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("143_load_line_items")
async def migrate_load_line_items(conn) -> None:
    """Create ``load_line_items`` — per-type extra pay & costs the TMS API
    can't provide (TONU / layover / bonus on the driver-pay side, tolls /
    lumper / detention on the expense side).  A row attaches to a load, or
    — for layover, which exists precisely because there was NO load — to a
    driver + date, with the responsible dispatcher stamped so the cost
    lands on their KPI."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS load_line_items (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id         INTEGER NOT NULL,
            load_id            INTEGER,
            driver_user_id     INTEGER,
            dispatcher_user_id INTEGER,
            kind               TEXT    NOT NULL,
            bucket             TEXT    NOT NULL DEFAULT 'expense',
            amount             REAL    NOT NULL,
            item_date          TEXT    NOT NULL DEFAULT '',
            notes              TEXT    NOT NULL DEFAULT '',
            created_by         INTEGER NOT NULL DEFAULT 0,
            created_at         TEXT    NOT NULL DEFAULT ''
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_load_line_items_load "
        "ON load_line_items(account_id, load_id)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_load_line_items_date "
        "ON load_line_items(account_id, item_date)"
    )
    await conn.commit()
    logger.info("Migration 143: load_line_items table created")

    # RLS — tenant-scoped, same gated pattern as 139_loads.
    import os
    if os.getenv("ENABLE_RLS", "0").strip() not in ("1", "true", "TRUE", "yes"):
        logger.info("Migration 143: ENABLE_RLS not set; RLS policy skipped")
        return
    try:
        await conn.execute("ALTER TABLE load_line_items ENABLE ROW LEVEL SECURITY")
        await conn.execute("ALTER TABLE load_line_items FORCE ROW LEVEL SECURITY")
        await conn.execute("DROP POLICY IF EXISTS tenant_isolation ON load_line_items")
        await conn.execute(
            """
            CREATE POLICY tenant_isolation ON load_line_items
            USING       (account_id::text = current_setting('app.account_id', true))
            WITH CHECK  (account_id::text = current_setting('app.account_id', true))
            """
        )
        await conn.commit()
        logger.info("Migration 143: tenant_isolation RLS applied to load_line_items")
    except Exception as e:
        logger.error("Migration 143: RLS apply on load_line_items failed — %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("144_load_settlement_columns")
async def migrate_load_settlement_columns(conn) -> None:
    """Add loads.other_pay + settlement_ref/settlement_status — the
    accessorial lump (total_other_pay, already inside total_rate; the
    split is informational) and which Datatruck settlement the load
    landed on.  Metadata only: the settlements themselves have no public
    API.  No backfill — values stamp on the next sync's refresh pass."""
    try:
        cur = await conn.execute("PRAGMA table_info(loads)")
        cols = {r[1] for r in await cur.fetchall()}
        added = []
        if "other_pay" not in cols:
            await conn.execute(
                "ALTER TABLE loads ADD COLUMN other_pay REAL"
            )
            added.append("other_pay")
        if "settlement_ref" not in cols:
            await conn.execute(
                "ALTER TABLE loads ADD COLUMN settlement_ref TEXT NOT NULL DEFAULT ''"
            )
            added.append("settlement_ref")
        if "settlement_status" not in cols:
            await conn.execute(
                "ALTER TABLE loads ADD COLUMN settlement_status TEXT NOT NULL DEFAULT ''"
            )
            added.append("settlement_status")
        if added:
            await conn.commit()
            logger.info("Migration 144: added loads columns %s", added)
    except Exception as e:
        logger.error("Migration 144 failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass



@_register("145_rename_payroll_flags_to_driver_pay")
async def migrate_rename_payroll_flags(conn) -> None:
    """Feature rename payroll → driver_pay: re-key the two permission
    flags inside stored role_permissions JSON so saved overrides keep
    working under the new flag names.  Idempotent; a missed row degrades
    gracefully (the role's code default still applies).  Physical table
    names (payroll_runs / payroll_run_items) stay as internal storage
    identifiers — the Billing→Subscription precedent (billing_* tables
    kept)."""
    import json as _json
    try:
        cur = await conn.execute("SELECT id, permissions FROM role_permissions")
        for r in await cur.fetchall():
            row = dict(r)
            try:
                perms = _json.loads(row.get("permissions") or "{}")
            except (TypeError, ValueError):
                continue
            changed = False
            for old, new in (
                ("can_payroll_admin", "can_driver_pay_admin"),
                ("can_payroll_view_own", "can_driver_pay_view_own"),
            ):
                if old in perms:
                    perms[new] = perms.pop(old)
                    changed = True
            if changed:
                await conn.execute(
                    "UPDATE role_permissions SET permissions = ? WHERE id = ?",
                    (_json.dumps(perms), row["id"]),
                )
        await conn.commit()
        logger.info("Migration 145: payroll→driver_pay permission flags re-keyed")
    except Exception as e:
        logger.error("Migration 145 flag rekey failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


@_register("146_work_orders_repair_priority")
async def migrate_work_orders_repair_priority(conn) -> None:
    """Add ``work_orders.repair_priority`` — the reason-for-repair class
    (``scheduled`` / ``non_scheduled`` / ``emergency``), the trucking
    equivalent of a VMRS reason code.  Lets the fleet split planned
    upkeep from unplanned firefighting when analysing spend.

    Existing rows default to '' (unclassified) — honest, since we can't
    retroactively know why a past repair happened; the operator sets it
    going forward.  Idempotent: ADD COLUMN no-ops on the duplicate-
    column error.
    """
    try:
        await conn.execute(
            "ALTER TABLE work_orders ADD COLUMN repair_priority TEXT NOT NULL DEFAULT ''"
        )
        await conn.commit()
        logger.info("Migration 146: added work_orders.repair_priority")
    except Exception as e:
        logger.info("Migration 146: work_orders.repair_priority likely exists — %s", e)


@_register("147_work_orders_complaint_cause_correction")
async def migrate_work_orders_ccc(conn) -> None:
    """Add ``complaint`` / ``cause`` / ``correction`` to ``work_orders``.

    The 3C repair-documentation standard (what the driver reported /
    what the shop found / what they did) — the format DOT audits and
    warranty claims expect.  All default '' so existing rows and
    integration-projected rows read as blank until filled.  Idempotent:
    each ADD COLUMN no-ops on the duplicate-column error, independently
    so a partial prior run still completes.
    """
    for col in ("complaint", "cause", "correction"):
        try:
            await conn.execute(
                f"ALTER TABLE work_orders ADD COLUMN {col} TEXT NOT NULL DEFAULT ''"
            )
            await conn.commit()
            logger.info("Migration 147: added work_orders.%s", col)
        except Exception as e:
            logger.info("Migration 147: work_orders.%s likely exists — %s", col, e)


@_register("148_work_order_parts_service_task")
async def migrate_work_order_parts_service_task(conn) -> None:
    """Add ``work_order_parts.service_task`` — the task-type slug each
    part line belongs to ('brakes', 'oil', 'custom_…'), sharing the
    maintenance task-type vocabulary.  The parts editor groups lines by
    this tag (task → parts hierarchy) and cost reports aggregate spend
    per task from it.  '' = untagged; existing rows stay untagged.

    Idempotent: ADD COLUMN no-ops on the duplicate-column error.
    """
    try:
        await conn.execute(
            "ALTER TABLE work_order_parts ADD COLUMN service_task TEXT NOT NULL DEFAULT ''"
        )
        await conn.commit()
        logger.info("Migration 148: added work_order_parts.service_task")
    except Exception as e:
        logger.info("Migration 148: work_order_parts.service_task likely exists — %s", e)


@_register("149_vendors_registry")
async def migrate_vendors_registry(conn) -> None:
    """Per-account vendor registry (master data) + ``work_orders.vendor_id``.

    Three steps, per docs/architecture/vendor-parts-master-data.md
    (Phase A; shape confirmed 2026-07-13):

    1. **Table** — ``vendors`` with a stored normalized ``name_key``
       and ``UNIQUE(account_id, name_key)`` so concurrent sync upserts
       can't create case-duplicates.  ``name_key`` is a plain column
       (not an expression index) so it works identically through the
       SQLite-compat layer and Postgres.
    2. **Link column** — nullable ``work_orders.vendor_id`` (app-level
       FK: plain INTEGER + index, consistent with the rest of the
       schema).  The ``vendor_name/address/phone`` snapshot columns on
       work_orders are NOT touched, ever — they remain the invoice
       truth; the id is the analytical spine.
    3. **Backfill** — per account, one vendor per distinct non-empty
       ``name_key`` across existing work orders (display name = most
       frequent casing, contact = most recent non-empty), then link
       ALL matching rows.  Deterministic, idempotent (ON CONFLICT DO
       NOTHING + only fills vendor_id IS NULL rows).

    Plus the standard tenant-isolation RLS block (gated on ENABLE_RLS),
    so the table is protected from birth.
    """
    import os

    # 1. Table + indexes.
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS vendors (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL,
                name        TEXT    NOT NULL,
                name_key    TEXT    NOT NULL,
                address     TEXT    NOT NULL DEFAULT '',
                phone       TEXT    NOT NULL DEFAULT '',
                email       TEXT    NOT NULL DEFAULT '',
                notes       TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL DEFAULT ''
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_vendors_account_name_key
                ON vendors(account_id, name_key);

            -- Merge tombstones: a merged-away vendor's name_key lands
            -- here pointing at the SURVIVOR, so the next Datatruck
            -- sync re-resolves to the winner instead of recreating
            -- the loser.  (Plan doc open-question #1 → resolved yes.)
            CREATE TABLE IF NOT EXISTS vendor_aliases (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL,
                name_key    TEXT    NOT NULL,
                vendor_id   INTEGER NOT NULL,
                created_at  TEXT    NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_vendor_aliases_key
                ON vendor_aliases(account_id, name_key);
        """)
        await conn.commit()
        logger.info("Migration 149: vendors + vendor_aliases tables ready")
    except Exception as e:
        logger.error("Migration 149 vendors table failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass
        return

    # 2. work_orders.vendor_id.
    try:
        await conn.execute(
            "ALTER TABLE work_orders ADD COLUMN vendor_id INTEGER"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_work_orders_vendor_id "
            "ON work_orders(account_id, vendor_id)"
        )
        await conn.commit()
        logger.info("Migration 149: added work_orders.vendor_id")
    except Exception as e:
        logger.info("Migration 149: work_orders.vendor_id likely exists — %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass

    # 3. Backfill: create + link.  Normalization here MUST match
    #    VendorsMixin._vendor_name_key (trim, collapse inner
    #    whitespace, casefold) — done in Python for dialect parity.
    try:
        cur = await conn.execute(
            "SELECT id, account_id, vendor_name, vendor_address, "
            "       vendor_phone, created_at "
            "FROM work_orders WHERE vendor_name <> '' AND vendor_id IS NULL"
        )
        rows = [dict(r) for r in await cur.fetchall()]
        if rows:
            def key_of(name: str) -> str:
                return " ".join((name or "").split()).casefold()

            # (account, key) → aggregate for display name + contact.
            agg: dict = {}
            for r in rows:
                k = (r["account_id"], key_of(r["vendor_name"]))
                a = agg.setdefault(k, {"casings": {}, "addr": "", "phone": "", "latest": ""})
                a["casings"][r["vendor_name"]] = a["casings"].get(r["vendor_name"], 0) + 1
                created = r.get("created_at") or ""
                if created >= a["latest"]:
                    if r.get("vendor_address"):
                        a["addr"] = r["vendor_address"]
                    if r.get("vendor_phone"):
                        a["phone"] = r["vendor_phone"]
                    a["latest"] = created

            now = __import__("datetime").datetime.utcnow().isoformat()
            for (account_id, nkey), a in agg.items():
                display = max(a["casings"].items(), key=lambda kv: kv[1])[0]
                await conn.execute(
                    "INSERT INTO vendors (account_id, name, name_key, address, "
                    " phone, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (account_id, name_key) DO NOTHING",
                    (account_id, display, nkey, a["addr"], a["phone"], now, now),
                )
            # Link every row by (account, key).
            vcur = await conn.execute("SELECT id, account_id, name_key FROM vendors")
            vmap = {(v["account_id"], v["name_key"]): v["id"]
                    for v in (dict(x) for x in await vcur.fetchall())}
            for r in rows:
                vid = vmap.get((r["account_id"], key_of(r["vendor_name"])))
                if vid:
                    await conn.execute(
                        "UPDATE work_orders SET vendor_id = ? WHERE id = ?",
                        (vid, r["id"]),
                    )
            await conn.commit()
            logger.info(
                "Migration 149: backfilled %d vendors, linked %d work orders",
                len(agg), len(rows),
            )
    except Exception as e:
        logger.error("Migration 149 backfill failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass

    # 4. RLS from birth (same gating + shape as migrations 057/103).
    if os.getenv("ENABLE_RLS", "0").strip() not in ("1", "true", "TRUE", "yes"):
        logger.info("Migration 149: ENABLE_RLS not set; vendors RLS skipped")
        return
    for tbl in ("vendors", "vendor_aliases"):
        try:
            await conn.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
            await conn.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
            await conn.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl}")
            await conn.execute(
                f"""
                CREATE POLICY tenant_isolation ON {tbl}
                USING       (account_id::text = current_setting('app.account_id', true))
                WITH CHECK  (account_id::text = current_setting('app.account_id', true))
                """
            )
            logger.info("Migration 149: RLS enabled on %s", tbl)
        except Exception as e:
            logger.warning("Migration 149: %s RLS skipped (%s)", tbl, e)


@_register("150_parts_catalog")
async def migrate_parts_catalog(conn) -> None:
    """Per-account parts catalog (master data) + ``work_order_parts.part_id``.

    Phase B of docs/architecture/vendor-parts-master-data.md — the same
    recipe migration 149 used for vendors: catalog table with a stored
    normalized ``name_key`` + per-account uniqueness, alias tombstones
    for merge+resync correctness, a nullable app-level FK on the line
    rows, a deterministic backfill that links ALL existing lines, and
    tenant RLS from birth.  ``part_name`` on line rows stays the
    invoice-truth snapshot forever.

    Note: ``work_order_parts`` itself has no ``account_id`` (legacy
    shape) — the backfill resolves the account through the parent
    work order, and the new tables do NOT copy that shape.
    """
    import os

    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS parts_catalog (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id   INTEGER NOT NULL,
                name         TEXT    NOT NULL,
                name_key     TEXT    NOT NULL,
                part_number  TEXT    NOT NULL DEFAULT '',
                notes        TEXT    NOT NULL DEFAULT '',
                created_at   TEXT    NOT NULL,
                updated_at   TEXT    NOT NULL DEFAULT ''
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_parts_catalog_account_key
                ON parts_catalog(account_id, name_key);

            CREATE TABLE IF NOT EXISTS part_aliases (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL,
                name_key    TEXT    NOT NULL,
                part_id     INTEGER NOT NULL,
                created_at  TEXT    NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_part_aliases_key
                ON part_aliases(account_id, name_key);
        """)
        await conn.commit()
        logger.info("Migration 150: parts_catalog + part_aliases ready")
    except Exception as e:
        logger.error("Migration 150 tables failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass
        return

    try:
        await conn.execute(
            "ALTER TABLE work_order_parts ADD COLUMN part_id INTEGER"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_work_order_parts_part_id "
            "ON work_order_parts(part_id)"
        )
        await conn.commit()
        logger.info("Migration 150: added work_order_parts.part_id")
    except Exception as e:
        logger.info("Migration 150: work_order_parts.part_id likely exists — %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass

    # Backfill: account resolved via the parent work order.  Display
    # name = most frequent casing; part_number = most recent non-empty.
    try:
        cur = await conn.execute(
            "SELECT p.id, p.part_name, p.part_number, w.account_id, "
            "       w.created_at "
            "FROM work_order_parts p "
            "JOIN work_orders w ON w.id = p.work_order_id "
            "WHERE p.part_name <> '' AND p.part_id IS NULL"
        )
        rows = [dict(r) for r in await cur.fetchall()]
        if rows:
            def key_of(name: str) -> str:
                return " ".join((name or "").split()).casefold()

            agg: dict = {}
            for r in rows:
                k = (r["account_id"], key_of(r["part_name"]))
                a = agg.setdefault(k, {"casings": {}, "pn": "", "latest": ""})
                a["casings"][r["part_name"]] = a["casings"].get(r["part_name"], 0) + 1
                created = r.get("created_at") or ""
                if created >= a["latest"]:
                    if r.get("part_number"):
                        a["pn"] = r["part_number"]
                    a["latest"] = created

            now = __import__("datetime").datetime.utcnow().isoformat()
            for (account_id, nkey), a in agg.items():
                display = max(a["casings"].items(), key=lambda kv: kv[1])[0]
                await conn.execute(
                    "INSERT INTO parts_catalog (account_id, name, name_key, "
                    " part_number, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (account_id, name_key) DO NOTHING",
                    (account_id, display, nkey, a["pn"], now, now),
                )
            pcur = await conn.execute(
                "SELECT id, account_id, name_key FROM parts_catalog"
            )
            pmap = {(p["account_id"], p["name_key"]): p["id"]
                    for p in (dict(x) for x in await pcur.fetchall())}
            for r in rows:
                pid = pmap.get((r["account_id"], key_of(r["part_name"])))
                if pid:
                    await conn.execute(
                        "UPDATE work_order_parts SET part_id = ? WHERE id = ?",
                        (pid, r["id"]),
                    )
            await conn.commit()
            logger.info(
                "Migration 150: backfilled %d catalog parts, linked %d lines",
                len(agg), len(rows),
            )
    except Exception as e:
        logger.error("Migration 150 backfill failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass

    if os.getenv("ENABLE_RLS", "0").strip() not in ("1", "true", "TRUE", "yes"):
        logger.info("Migration 150: ENABLE_RLS not set; RLS skipped")
        return
    for tbl in ("parts_catalog", "part_aliases"):
        try:
            await conn.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
            await conn.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
            await conn.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl}")
            await conn.execute(
                f"""
                CREATE POLICY tenant_isolation ON {tbl}
                USING       (account_id::text = current_setting('app.account_id', true))
                WITH CHECK  (account_id::text = current_setting('app.account_id', true))
                """
            )
            logger.info("Migration 150: RLS enabled on %s", tbl)
        except Exception as e:
            logger.warning("Migration 150: %s RLS skipped (%s)", tbl, e)


@_register("151_vendors_global_link")
async def migrate_vendors_global_link(conn) -> None:
    """Add ``vendors.global_vendor_id`` — the optional link from an
    account's private vendor record to its platform vendor_directory
    identity (Phase C1).  Nullable app-level FK; linking is explicit
    (operator/manager action), never automatic.
    """
    try:
        await conn.execute(
            "ALTER TABLE vendors ADD COLUMN global_vendor_id INTEGER"
        )
        await conn.commit()
        logger.info("Migration 151: added vendors.global_vendor_id")
    except Exception as e:
        logger.info("Migration 151: vendors.global_vendor_id likely exists — %s", e)


@_register("152_vehicle_inventory")
async def migrate_vehicle_inventory(conn) -> None:
    """Onboard inventory — what physically lives in each truck.

    Two tables: ``vehicle_inventory_items`` (one row per tracked item,
    anchored to the vehicles registry) and ``vehicle_inventory_events``
    (immutable accountability trail — every install/status/transfer/
    verify with the acting user AND the driver assigned to the truck at
    that moment).  RLS applied when ENABLE_RLS is on, same policy shape
    as every other tenant table.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_inventory_items (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id        INTEGER NOT NULL,
            vehicle_id        INTEGER NOT NULL,
            category          TEXT    NOT NULL,
            label             TEXT    NOT NULL DEFAULT '',
            identifier        TEXT    NOT NULL DEFAULT '',
            status            TEXT    NOT NULL DEFAULT 'installed',
            notes             TEXT    NOT NULL DEFAULT '',
            installed_at      TEXT    NOT NULL DEFAULT '',
            last_verified_at  TEXT,
            last_verified_by  INTEGER,
            is_active         INTEGER NOT NULL DEFAULT 1,
            created_at        TEXT    NOT NULL DEFAULT '',
            updated_at        TEXT    NOT NULL DEFAULT ''
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vehicle_inventory_vehicle "
        "ON vehicle_inventory_items(account_id, vehicle_id, is_active)"
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_inventory_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            item_id         INTEGER NOT NULL,
            event_type      TEXT    NOT NULL,
            from_status     TEXT,
            to_status       TEXT,
            from_vehicle_id INTEGER,
            to_vehicle_id   INTEGER,
            actor_user_id   INTEGER,
            driver_user_id  INTEGER,
            note            TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL DEFAULT ''
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vehicle_inventory_events_item "
        "ON vehicle_inventory_events(account_id, item_id)"
    )
    await conn.commit()
    logger.info("Migration 152: vehicle inventory tables created")

    import os
    if os.getenv("ENABLE_RLS", "0").strip() not in ("1", "true", "TRUE", "yes"):
        logger.info("Migration 152: ENABLE_RLS not set; RLS skipped")
        return
    for tbl in ("vehicle_inventory_items", "vehicle_inventory_events"):
        try:
            await conn.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
            await conn.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
            await conn.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl}")
            await conn.execute(
                f"""
                CREATE POLICY tenant_isolation ON {tbl}
                USING       (account_id::text = current_setting('app.account_id', true))
                WITH CHECK  (account_id::text = current_setting('app.account_id', true))
                """
            )
            logger.info("Migration 152: RLS enabled on %s", tbl)
        except Exception as e:
            logger.warning("Migration 152: %s RLS skipped (%s)", tbl, e)


@_register("153_work_order_labor")
async def migrate_work_order_labor(conn) -> None:
    """Labor as optional line items (Work Orders Tier-2 B1).

    One row per labor charge on an invoice — description, hours × rate
    (or a flat total), tagged with the same ``service_task`` vocabulary
    as part lines so cost reports can split labor per kind of work.

    Ships WITH ``account_id`` + RLS from birth (work_order_parts
    predates that rule — its shape is deliberately NOT copied).  When a
    work order has labor lines, ``work_orders.labor_cost`` becomes the
    derived sum (recomputed server-side on every line change); with no
    lines the manual scalar keeps working exactly as before.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS work_order_labor (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id    INTEGER NOT NULL,
            work_order_id INTEGER NOT NULL,
            service_task  TEXT    NOT NULL DEFAULT '',
            description   TEXT    NOT NULL DEFAULT '',
            hours         REAL    NOT NULL DEFAULT 0,
            rate          REAL    NOT NULL DEFAULT 0,
            total_cost    REAL    NOT NULL DEFAULT 0,
            created_at    TEXT    NOT NULL DEFAULT ''
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_work_order_labor_wo "
        "ON work_order_labor(account_id, work_order_id)"
    )
    await conn.commit()
    logger.info("Migration 153: work_order_labor created")

    import os
    if os.getenv("ENABLE_RLS", "0").strip() not in ("1", "true", "TRUE", "yes"):
        logger.info("Migration 153: ENABLE_RLS not set; RLS skipped")
        return
    try:
        await conn.execute("ALTER TABLE work_order_labor ENABLE ROW LEVEL SECURITY")
        await conn.execute("ALTER TABLE work_order_labor FORCE ROW LEVEL SECURITY")
        await conn.execute("DROP POLICY IF EXISTS tenant_isolation ON work_order_labor")
        await conn.execute(
            """
            CREATE POLICY tenant_isolation ON work_order_labor
            USING       (account_id::text = current_setting('app.account_id', true))
            WITH CHECK  (account_id::text = current_setting('app.account_id', true))
            """
        )
        logger.info("Migration 153: RLS enabled on work_order_labor")
    except Exception as e:
        logger.warning("Migration 153: work_order_labor RLS skipped (%s)", e)


@_register("154_work_orders_status_lifecycle_only")
async def migrate_work_orders_status_lifecycle_only(conn) -> None:
    """status carries LIFECYCLE only (draft|submitted|void); money truth
    lives solely in payment_status.

    The legacy status='paid' duplicated payment_status='paid' — two
    sources of money truth.  Backfill: such rows become submitted, and
    payment_status is promoted to 'paid' unless it already carries a
    more specific money state (partial/void — money truth wins).
    Data-only by design (no CHECK constraint this release; the Pydantic
    patterns are the write-side gate) and idempotent: after the first
    run no status='paid' rows remain.
    """
    await conn.execute(
        """
        UPDATE work_orders
        SET payment_status = CASE
                WHEN payment_status IN ('', 'unpaid') THEN 'paid'
                ELSE payment_status
            END,
            status = 'submitted'
        WHERE status = 'paid'
        """
    )
    await conn.commit()
    logger.info("Migration 154: work_orders.status narrowed to lifecycle-only")


@_register("155_parts_public_link")
async def migrate_parts_public_link(conn) -> None:
    """Link per-account catalog parts to the platform public parts
    catalog (part_directory) — the parts analog of
    ``vendors.global_vendor_id``.

    ``public_link_suppressed`` is the Unlink-honesty marker: the adopt
    fan-out re-fires on every entry activation / alias-add, so without
    a per-row suppression flag a user who unlinked a wrong match would
    be silently re-linked at the next fan-out.  Unlink sets it; an
    explicit re-link clears it; adopt's match predicate requires
    ``global_part_id IS NULL AND NOT public_link_suppressed``.
    """
    await conn.execute(
        "ALTER TABLE parts_catalog "
        "ADD COLUMN IF NOT EXISTS global_part_id INTEGER"
    )
    await conn.execute(
        "ALTER TABLE parts_catalog "
        "ADD COLUMN IF NOT EXISTS public_link_suppressed BOOLEAN NOT NULL DEFAULT FALSE"
    )
    await conn.commit()
    logger.info("Migration 155: parts_catalog public-link columns ready")


@_register("156_datatruck_derived_tax_backfill")
async def migrate_datatruck_derived_tax_backfill(conn) -> None:
    """Backfill the tax-and-fees amount on already-synced Datatruck
    work orders.

    Datatruck's OpenAPI never sent tax/fees as fields, so historical
    synced WOs stored ``tax_amount = 0`` while ``total_cost`` already
    carried Datatruck's tax-inclusive total — leaving the breakdown
    (parts + tax) short of the total.  The gap ``total_cost −
    parts_cost − labor_cost`` IS the combined tax + fees (same
    derivation the normalizer now applies going forward).

    Guarded so it's safe + idempotent:
      * only ``source = 'datatruck'`` rows,
      * only where ``tax_amount`` is still 0 (never clobber a value an
        operator or a newer sync already set),
      * only where the gap is positive (a discount/rounding WO where
        total < subtotal stays untouched — no negative tax),
      * only where line items exist (``parts_cost > 0``) — a WO with a
        total but no itemization is a lump sum we can't split, so
        attributing all of it to tax would be wrong,
      * REAL columns cast to numeric for ROUND (float ROUND has no
        2-arg form in Postgres).
    """
    await conn.execute(
        """
        UPDATE work_orders
        SET tax_amount = ROUND(
                (total_cost - parts_cost - labor_cost)::numeric, 2
            ),
            updated_at = updated_at
        WHERE source = 'datatruck'
          AND tax_amount = 0
          AND parts_cost > 0.005
          AND (total_cost - parts_cost - labor_cost) > 0.005
        """
    )
    await conn.commit()
    logger.info("Migration 156: Datatruck derived tax/fees backfilled")


@_register("157_work_orders_external_number")
async def migrate_work_orders_external_number(conn) -> None:
    """Add ``work_orders.external_number`` (the source system's
    human-readable reference, e.g. Datatruck "WO-00983" — distinct from
    ``external_id`` which is its internal numeric id) and backfill it
    for already-synced Datatruck rows from the ELT staging table.

    Data-only backfill, idempotent (only fills blank external_number on
    source='datatruck' rows).  The projection keeps it fresh going
    forward.
    """
    await conn.execute(
        "ALTER TABLE work_orders "
        "ADD COLUMN IF NOT EXISTS external_number TEXT NOT NULL DEFAULT ''"
    )
    await conn.execute(
        """
        UPDATE work_orders w
        SET external_number = d.number
        FROM datatruck_work_orders d
        WHERE w.source = 'datatruck'
          AND w.external_number = ''
          AND w.account_id = d.account_id
          AND w.external_id = d.external_id
          AND COALESCE(d.number, '') <> ''
        """
    )
    await conn.commit()
    logger.info("Migration 157: work_orders.external_number added + backfilled")


@_register("158_datatruck_stale_payment_ratchet")
async def migrate_datatruck_stale_payment_ratchet(conn) -> None:
    """One-time application of the payment ratchet to already-synced
    rows: work orders still 'unpaid' whose freshest staged payload
    shows a cleared balance become 'paid'.

    The projection used to seed payment on INSERT only, so 110 rows
    sat "Unpaid" forever after their upstream balance cleared.  The
    refresh path now ratchets forward on every sync; this catches the
    backlog (including rows that drifted out of the sync window).
    Idempotent; never touches paid/partial/void or manual rows.
    """
    await conn.execute(
        """
        UPDATE work_orders w
        SET payment_status = 'paid', updated_at = updated_at
        FROM datatruck_work_orders d
        WHERE w.source = 'datatruck'
          AND w.payment_status = 'unpaid'
          AND w.account_id = d.account_id
          AND w.external_id = d.external_id
          AND w.total_cost > 0.005
          AND COALESCE((d.payload::jsonb->>'balance')::numeric, 1) <= 0.005
        """
    )
    await conn.commit()
    logger.info("Migration 158: stale Datatruck payment statuses ratcheted to paid")
