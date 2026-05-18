"""Schema migrations for platform database tables.

These run after platform_schema.create_tables() and add columns/indexes
introduced after the initial multi-tenant schema was created.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def run_all(conn) -> None:
    """Execute every platform migration in order."""
    await migrate_email_unique_per_account(conn)
    await migrate_add_bot_columns(conn)
    await migrate_rename_fleet_manager_role(conn)
    await migrate_knowledge_base_to_platform(conn)
    await migrate_seed_role_permissions(conn)
    await migrate_seed_driver_trucks(conn)
    await migrate_user_companies_table(conn)
    await migrate_billing_tables(conn)
    await migrate_ai_chat_history(conn)
    await migrate_add_role_ai_guidance(conn)
    await migrate_add_error_log(conn)
    await migrate_add_payroll_enabled_flag(conn)
    await migrate_add_coaching_enabled_flag(conn)
    await migrate_add_users_samsara_driver_id(conn)
    await migrate_create_payroll_tables(conn)
    # Driver Module migrations
    await migrate_add_driver_profile_columns(conn)
    await migrate_create_driver_vehicle_assignments(conn)
    await migrate_create_driver_documents(conn)
    await migrate_add_account_storage_quota(conn)
    await migrate_backfill_driver_vehicle_assignments(conn)
    await migrate_create_driver_document_notifications(conn)
    await migrate_create_driver_future_tables(conn)
    await migrate_add_account_timezone(conn)


async def migrate_ai_chat_history(conn) -> None:
    """Create ai_chat_history table if it doesn't exist yet.

    Idempotent — no-op when table already exists (e.g. fresh installs that
    picked it up from platform_schema.create_tables()).
    """
    try:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ai_chat_history'"
        )
        if await cur.fetchone():
            return  # already exists

        await conn.execute(
            """CREATE TABLE ai_chat_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                role       TEXT    NOT NULL,
                text       TEXT    NOT NULL,
                created_at TEXT    NOT NULL
            )"""
        )
        await conn.execute(
            "CREATE INDEX idx_ai_chat_history_user"
            " ON ai_chat_history(account_id, user_id, created_at)"
        )
        await conn.commit()
        logger.info("Created ai_chat_history table")
    except Exception as e:
        logger.error("ai_chat_history migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_email_unique_per_account(conn) -> None:
    """Change email from globally UNIQUE to UNIQUE(account_id, email).

    SQLite doesn't support DROP CONSTRAINT, so we check the table DDL
    and rebuild if needed.
    """
    try:
        cur = await conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        )
        row = await cur.fetchone()
        if not row:
            return  # table doesn't exist yet — CREATE TABLE handles it

        ddl = row[0] or ""
        # If the DDL already has the composite constraint, skip.
        # Strip ALL whitespace from both sides — earlier code kept a space in
        # the needle while stripping only spaces from the DDL, so any DDL
        # rendered with tabs/newlines bypassed the guard. That bug, combined
        # with a partial INSERT failure on the rebuild path, repeatedly wiped
        # the users table.
        if "UNIQUE(account_id,email)" in "".join(ddl.split()):
            return
        # Only proceed if there's a bare UNIQUE on email
        if "email" not in ddl:
            return

        logger.info("Migrating users.email from UNIQUE to UNIQUE(account_id, email)")

        await conn.execute("ALTER TABLE users RENAME TO _users_old")
        # Recreate with the composite constraint — copy full column list
        cur2 = await conn.execute("PRAGMA table_info(_users_old)")
        cols = [r[1] for r in await cur2.fetchall()]
        col_list = ", ".join(cols)

        # Build new CREATE TABLE from platform_schema (import at call time to
        # avoid circular imports at module level)
        from adapters.storage.platform_schema import create_tables as _unused  # noqa: F401

        # We need to re-run create_tables which uses CREATE IF NOT EXISTS
        # Since we renamed the old table, it will create a fresh 'users'
        from adapters.storage import platform_schema
        await platform_schema.create_tables(conn)

        await conn.execute(
            f"INSERT INTO users ({col_list}) SELECT {col_list} FROM _users_old"
        )
        await conn.execute("DROP TABLE _users_old")
        await conn.commit()
        logger.info("Migrated users.email to UNIQUE(account_id, email)")
    except Exception as e:
        logger.error(f"email uniqueness migration failed: {e}")
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_add_bot_columns(conn) -> None:
    """Add bot_token_encrypted, bot_username, webhook_secret to accounts table.

    Idempotent — skips if columns already exist.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(accounts)")
        existing = {r[1] for r in await cur.fetchall()}

        new_cols = [
            ("bot_token_encrypted", "TEXT"),
            ("bot_username", "TEXT NOT NULL DEFAULT ''"),
            ("webhook_secret", "TEXT NOT NULL DEFAULT ''"),
        ]
        for col_name, col_def in new_cols:
            if col_name not in existing:
                await conn.execute(
                    f"ALTER TABLE accounts ADD COLUMN {col_name} {col_def}"
                )
                logger.info("Added accounts.%s column", col_name)

        await conn.commit()
    except Exception as e:
        logger.error(f"Bot columns migration failed: {e}")
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_rename_fleet_manager_role(conn) -> None:
    """Rename role 'fleet_manager' to 'fleet' in users and invites tables.

    Idempotent — no-op if no fleet_manager rows exist.
    """
    try:
        for table in ("users", "invites"):
            cur = await conn.execute(
                f"UPDATE {table} SET role = 'fleet' WHERE role = 'fleet_manager'"
            )
            if cur.rowcount:
                logger.info("Renamed %d fleet_manager → fleet in %s", cur.rowcount, table)
        await conn.commit()
    except Exception as e:
        logger.error(f"Fleet manager rename migration failed: {e}")
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_knowledge_base_to_platform(conn) -> None:
    """Ensure knowledge_base table exists in platform DB with new columns.

    Adds target_role and creator_name columns if missing.
    Migrates old visibility values (role names) to new private/public model.
    Idempotent.
    """
    try:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_base'"
        )
        if not (await cur.fetchone()):
            # Table will be created by platform_schema.create_tables()
            return

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

        # Migrate old role-based visibility to new private/public model:
        # Old 'all' → private (visible to everyone in the account)
        # Old role values → private with that role as target_role
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
        logger.info("Knowledge base platform migration complete")
    except Exception as e:
        logger.error(f"Knowledge base platform migration failed: {e}")
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_seed_role_permissions(conn) -> None:
    """Seed role_permissions table with defaults for every existing account.

    Idempotent — only inserts rows that don't already exist.
    Uses the hardcoded ROLE_PERMISSIONS as factory defaults.
    """
    import json
    from dataclasses import asdict
    try:
        # Check table exists
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
                # Skip if already seeded (company_id=NULL for account-wide)
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
            logger.info(
                "Seeded %d role_permissions rows for %d accounts",
                inserted, len(accounts),
            )
    except Exception as e:
        logger.error(f"Role permissions seed migration failed: {e}")
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_seed_driver_trucks(conn) -> None:
    """Migrate existing users.truck_num into the driver_trucks junction table."""
    try:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='driver_trucks'"
        )
        if not (await cur.fetchone()):
            return

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


async def migrate_user_companies_table(conn) -> None:
    """Create user_companies table if it doesn't exist (for pre-Phase-6 databases)."""
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_companies (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                company_id  INTEGER NOT NULL REFERENCES companies(id),
                assigned_by INTEGER NOT NULL DEFAULT 0,
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


async def migrate_billing_tables(conn) -> None:
    """Create subscriptions and billing_usage_snapshots tables (idempotent).

    These tables are also created by platform_schema.create_tables() for fresh
    databases.  This migration ensures existing databases get them.
    """
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id               INTEGER NOT NULL UNIQUE REFERENCES accounts(id),
                tier                     TEXT    NOT NULL DEFAULT 'free',
                status                   TEXT    NOT NULL DEFAULT 'active',
                vehicle_count            INTEGER NOT NULL DEFAULT 0,
                base_vehicles            INTEGER NOT NULL DEFAULT 10,
                monthly_base_usd         INTEGER NOT NULL DEFAULT 0,
                extra_vehicle_cents      INTEGER NOT NULL DEFAULT 0,
                billing_email            TEXT    NOT NULL DEFAULT '',
                provider                 TEXT    NOT NULL DEFAULT 'stub',
                provider_customer_id     TEXT    NOT NULL DEFAULT '',
                provider_subscription_id TEXT    NOT NULL DEFAULT '',
                provider_data            TEXT    NOT NULL DEFAULT '{}',
                trial_ends_at            TEXT,
                current_period_start     TEXT,
                current_period_end       TEXT,
                canceled_at              TEXT,
                created_at               TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at               TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_subscriptions_account
                ON subscriptions(account_id);
            CREATE INDEX IF NOT EXISTS idx_subscriptions_status
                ON subscriptions(status);

            CREATE TABLE IF NOT EXISTS billing_usage_snapshots (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id       INTEGER NOT NULL REFERENCES accounts(id),
                period_start     TEXT    NOT NULL,
                period_end       TEXT    NOT NULL,
                vehicle_count    INTEGER NOT NULL DEFAULT 0,
                user_count       INTEGER NOT NULL DEFAULT 0,
                ai_queries       INTEGER NOT NULL DEFAULT 0,
                extra_vehicles   INTEGER NOT NULL DEFAULT 0,
                amount_due_cents INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(account_id, period_start)
            );
            CREATE INDEX IF NOT EXISTS idx_billing_snapshots_account
                ON billing_usage_snapshots(account_id, period_start);
        """)
        await conn.commit()
        logger.info("Billing tables created/verified")
    except Exception as e:
        logger.debug("billing_tables migration skipped: %s", e)


async def migrate_add_role_ai_guidance(conn) -> None:
    """Create role_ai_guidance table for per-account AI behaviour overrides."""
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS role_ai_guidance (
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                role        TEXT    NOT NULL,
                guidance    TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL DEFAULT '',
                PRIMARY KEY (account_id, role)
            );
            CREATE INDEX IF NOT EXISTS idx_role_ai_guidance_account
                ON role_ai_guidance(account_id);
        """)
        await conn.commit()
        logger.info("role_ai_guidance table created/verified")
    except Exception as e:
        logger.debug("role_ai_guidance migration skipped: %s", e)


async def migrate_add_error_log(conn) -> None:
    """Create error_log table for built-in error reporter."""
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS error_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                source      TEXT    NOT NULL,
                job_name    TEXT,
                account_id  INTEGER,
                error_type  TEXT    NOT NULL,
                error_msg   TEXT    NOT NULL,
                traceback   TEXT,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_error_log_created
                ON error_log(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_error_log_source
                ON error_log(source, created_at DESC);
        """)
        await conn.commit()
        logger.info("error_log table created/verified")
    except Exception as e:
        logger.debug("error_log migration skipped: %s", e)


async def migrate_add_payroll_enabled_flag(conn) -> None:
    """Add accounts.payroll_enabled ( P4P kill-switch). Default OFF."""
    try:
        cur = await conn.execute("PRAGMA table_info(accounts)")
        cols = {r[1] for r in await cur.fetchall()}
        if "payroll_enabled" not in cols:
            await conn.execute(
                "ALTER TABLE accounts ADD COLUMN payroll_enabled INTEGER NOT NULL DEFAULT 0"
            )
            await conn.commit()
            logger.info("Migration: added accounts.payroll_enabled column")
    except Exception as e:
        logger.error("payroll_enabled flag migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_add_coaching_enabled_flag(conn) -> None:
    """Add accounts.coaching_enabled ( Auto Coaching kill-switch). Default OFF."""
    try:
        cur = await conn.execute("PRAGMA table_info(accounts)")
        cols = {r[1] for r in await cur.fetchall()}
        if "coaching_enabled" not in cols:
            await conn.execute(
                "ALTER TABLE accounts ADD COLUMN coaching_enabled INTEGER NOT NULL DEFAULT 0"
            )
            await conn.commit()
            logger.info("Migration: added accounts.coaching_enabled column")
    except Exception as e:
        logger.error("coaching_enabled flag migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_add_users_samsara_driver_id(conn) -> None:
    """Add users.samsara_driver_id — explicit Telegram-user ↔ Samsara-driver
    binding. Required for /coaching/me and /payroll/me; without it those
    endpoints can't safely return per-driver data because the prior heuristic
    (most-recent safety event by truck) would leak data after vehicle reassignment.
    Default NULL: existing rows must be linked by an admin before the driver
    can use the self-service endpoints."""
    try:
        cur = await conn.execute("PRAGMA table_info(users)")
        cols = {r[1] for r in await cur.fetchall()}
        if "samsara_driver_id" not in cols:
            await conn.execute(
                "ALTER TABLE users ADD COLUMN samsara_driver_id TEXT"
            )
            await conn.commit()
            logger.info("Migration: added users.samsara_driver_id column")
    except Exception as e:
        logger.error("samsara_driver_id migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


# ── Driver Module ─────────────────────────────────────────────────
#
# Extends the existing ``users`` table (drivers stay a special role,
# not a separate table) with profile columns: CDL details, medical
# card expiry, hire date, contact info.  Adds two new tables:
# ``driver_vehicle_assignments`` (single source of truth for
# driver-vehicle mapping, with history) and ``driver_documents``
# (per-driver document store with expiration tracking).  Adds
# storage-quota columns on ``accounts`` so the local fallback object
# store has a cap.  All migrations are idempotent.


_DRIVER_PROFILE_COLUMNS: tuple[tuple[str, str], ...] = (
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


async def migrate_add_driver_profile_columns(conn) -> None:
    """Add the 11 driver-profile columns to users.

    All nullable so non-driver rows are unaffected.  Drivers will
    populate them via the admin UI; the columns also seed the
    expiration-alert scheduler (CDL / Medical Card).
    """
    try:
        cur = await conn.execute("PRAGMA table_info(users)")
        cols = {r[1] for r in await cur.fetchall()}
        added = 0
        for name, sqltype in _DRIVER_PROFILE_COLUMNS:
            if name not in cols:
                await conn.execute(
                    f"ALTER TABLE users ADD COLUMN {name} {sqltype}"
                )
                added += 1
        if added:
            await conn.commit()
            logger.info("Migration: added %d driver-profile column(s) to users", added)
    except Exception as e:
        logger.error("driver-profile columns migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_create_driver_vehicle_assignments(conn) -> None:
    """Create driver_vehicle_assignments — the single source of truth
    for who drives what.

    Replaces ``driver_trucks`` (kept as legacy alias one release).
    Preserves history: when a driver moves off a vehicle the row
    sticks around with ``unassigned_at`` set.  ``vehicle_name`` is
    the Samsara name (matches alerts + scorecard lookups);
    ``vehicle_id`` is the Samsara vehicle_id cached for fast joins.
    """
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
        logger.info("Migration: created driver_vehicle_assignments table")
    except Exception as e:
        logger.error("driver_vehicle_assignments migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_create_driver_documents(conn) -> None:
    """Create driver_documents — per-driver document store with
    expiration tracking.

    Files are stored via the existing ``ObjectStore`` protocol (Google
    Drive when the account has it linked, local disk fallback otherwise).
    ``object_key`` is the path inside the bucket; ``drive_file_id`` is
    the Google Drive file id cached for fast re-fetch.  ``status``
    transitions ``active`` → ``expired`` on the day of expiration via
    the daily document-expiration scheduler.
    """
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
        logger.info("Migration: created driver_documents table")
    except Exception as e:
        logger.error("driver_documents migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_add_account_storage_quota(conn) -> None:
    """Add storage_quota_bytes / storage_used_bytes to accounts.

    Quota applies only when the account uses the LOCAL disk fallback;
    accounts with Google Drive connected don't hit this cap because
    their Drive enforces its own quota.  Default 500 MB is enough for
    a small fleet's documents + camera images without hitting the
    cap; configurable per-account via the admin storage-settings route.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(accounts)")
        cols = {r[1] for r in await cur.fetchall()}
        added = 0
        if "storage_quota_bytes" not in cols:
            # 524288000 = 500 * 1024 * 1024 = 500 MB default cap.
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
            logger.info("Migration: added %d storage-quota column(s) to accounts", added)
    except Exception as e:
        logger.error("storage-quota migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_backfill_driver_vehicle_assignments(conn) -> None:
    """Seed driver_vehicle_assignments from existing data.

    Two sources, in order:
      1. ``driver_trucks`` — the previous junction table
      2. ``users.truck_num`` — the legacy single-truck column

    Both are preserved (no deletes); this is a one-way ADDITIVE backfill
    so existing readers keep working during the transition.  Re-runs
    are safe — only inserts rows that don't already have a matching
    active assignment.
    """
    try:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='driver_vehicle_assignments'"
        )
        if not (await cur.fetchone()):
            return  # table not created yet; will be picked up on next start

        now = __import__("datetime").datetime.utcnow().isoformat()
        inserted = 0

        # 1) driver_trucks → driver_vehicle_assignments (1:1)
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

        # 2) users.truck_num for any driver still missing an active row
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
            logger.info(
                "Migration: backfilled %d row(s) into driver_vehicle_assignments",
                inserted,
            )
    except Exception as e:
        logger.error("driver_vehicle_assignments backfill failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_create_driver_document_notifications(conn) -> None:
    """Per-(doc, bucket) ledger for the daily expiration scheduler.

    ``bucket_days`` is the threshold the doc had crossed when the alert
    fired (30 / 14 / 7 / 1 / 0 for "expired today").  The composite PK
    is the dedup hook — re-running the scheduler the same day or even
    weeks later won't re-fire an already-sent bucket for the same doc.
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
        logger.info("Migration: created driver_document_notifications table")
    except Exception as e:
        logger.error("driver_document_notifications migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_create_payroll_tables(conn) -> None:
    """Create payroll tables on the unified Database.

    Historically these lived on ``TenantDB`` and were created by
    ``tenant_schema.create_tables``.  After the platform/tenant DB
    unification they need an explicit migration so existing tenants
    pick them up on the next startup.

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
        logger.info("Migration: created payroll tables")
    except Exception as e:
        logger.error("Payroll-tables migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_create_driver_future_tables(conn) -> None:
    """Foundation tables for upcoming driver-facing modules.

    Schema-only, no read/write paths yet — the full features land in
    follow-on PRs.  Tables created now so:

    * Migration ordering stays simple (these three roll out together).
    * Adapter stubs in ``capabilities/drivers/`` can be filled in
      without another schema migration.
    * Reporting + scorecards can start joining against them
      incrementally.

    Tables:
      * ``driver_inspections`` — DOT pre/post-trip + annual + roadside
      * ``driver_trainings``   — completed courses with expirations
      * ``driver_hos_status``  — cached Samsara HOS / duty-status
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
        logger.info("Migration: created driver_inspections / driver_trainings / driver_hos_status")
    except Exception as e:
        logger.error("Driver future-tables migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_add_account_timezone(conn) -> None:
    """Add ``accounts.timezone`` for the account-level timezone default.

    Per-user ``users.timezone`` becomes the optional override; this
    column is the account-wide default that admins set via Settings.
    Cron jobs and display formatting both consult an effective-tz
    resolver that falls back through user → account → ``UTC``.

    Idempotent — ALTER TABLE only when the column is missing.
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
        logger.info("Migration: added accounts.timezone column")
    except Exception as e:
        logger.error("accounts.timezone migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass
