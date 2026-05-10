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
    """Create custom_poi_layers + custom_poi_points tables (legacy DB).

    Mirrors the same migration in tenant_migrations.py so the single-DB
    Database class also exposes the storage backing for custom POI layers.
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
    """Add pillar + curve columns to score_rules (Audit Option C).

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
    """Create the telemetry warehouse tables on the legacy single-DB layout.

    Phase C (multi-tenant) put these in ``tenant_schema.create_tables`` so
    fresh tenant DBs get them; the legacy ``LegacyRouter`` path that uses
    a single ``Database`` instance shared across the platform was missing
    them entirely.  Without this migration, ``ingest_vehicle_state`` (and
    every consumer that reads the warehouse) crashes with
    ``'Database' object has no attribute 'upsert_vehicle_state'`` /
    ``relation "vehicle_state" does not exist``.

    DDL kept identical to ``tenant_schema.py`` (incl. ``odometer_time``)
    so a tenant migrating to multi-DB sees the same shape.
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
    """Add vehicle_state.odometer_time for legacy DBs that ran 028 before
    the column was added to the canonical tenant_schema.  No-op when the
    column is already present (Postgres ALTER TABLE IF NOT EXISTS form)."""
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
