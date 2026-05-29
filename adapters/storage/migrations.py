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

        from capabilities.iam.permissions import ROLE_PERMISSIONS

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
                notes                    TEXT    NOT NULL DEFAULT '',
                created_by               BIGINT  NOT NULL,
                created_at               TEXT    NOT NULL,
                updated_at               TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_work_orders_vehicle
                ON work_orders(account_id, vehicle_name, service_date DESC);
            CREATE INDEX IF NOT EXISTS idx_work_orders_status
                ON work_orders(account_id, status);

            CREATE TABLE IF NOT EXISTS work_order_parts (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                work_order_id       INTEGER NOT NULL,
                part_name           TEXT    NOT NULL DEFAULT '',
                part_number         TEXT    NOT NULL DEFAULT '',
                quantity            REAL    NOT NULL DEFAULT 1,
                unit_cost           REAL    NOT NULL DEFAULT 0,
                total_cost          REAL    NOT NULL DEFAULT 0,
                warranty_months     INTEGER NOT NULL DEFAULT 0,
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
