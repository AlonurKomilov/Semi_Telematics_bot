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
    await migrate_processed_stripe_events_table(conn)
    await migrate_billing_invoices_table(conn)
    await migrate_subscription_comp_columns(conn)
    await migrate_comp_account_history_table(conn)
    await migrate_subscription_provider_item_ids(conn)
    await migrate_billing_usage_snapshot_active_columns(conn)
    await migrate_add_users_last_seen(conn)
    await migrate_create_user_sessions(conn)
    await migrate_create_account_persona_groups(conn)
    await migrate_add_accounts_alert_routing_mode(conn)
    # The next steps MUST be the last entries — each touches every
    # table that has an ``account_id`` column, so they need every
    # CREATE TABLE already applied.  Run order matters:
    #
    #   1. Renumber account IDs to the 10_000_001+ range.
    #   2. Insert the ``account-{id}/`` segment into stored file
    #      paths so each tenant's blobs live under its own subtree.
    await migrate_account_id_base_renumber(conn)
    await migrate_account_prefixed_file_paths(conn)
    # If an earlier deploy applied the path-prefix step before the
    # renumber, stored paths now carry the OLD account ID (e.g.
    # ``account-1/``) while ``accounts.id`` has been bumped (e.g.
    # ``10000001``).  Bump any low-ID prefix to its renumbered
    # equivalent.  Safe to run unconditionally — fast no-op when no
    # stale paths exist.
    await migrate_repair_stale_account_path_ids(conn)
    # Knowledge-base hygiene: drop the duplicate
    # ``(account_id, category)`` index that accumulated from two
    # migrations creating the same shape under different names, then
    # add a Postgres tsvector GIN index for fast title/description/
    # tags search.  Both steps no-op on re-run.
    await migrate_knowledge_base_indexes_and_fts(conn)
    # Several older tables store ``created_by`` / ``uploaded_by`` as
    # the user's Telegram ID (BIGINT).  That value can change — a user
    # who registered by email later links their Telegram, or a future
    # auth provider rotates the identifier — and when it does, the
    # original owner loses access to the rows they created.  Rewrite
    # every such column to the immutable ``users.id`` primary key.
    # Idempotent: fast no-op once every row already references a
    # users.id (re-runs touch zero rows).
    await migrate_backfill_user_id_ownership(conn)


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


async def migrate_create_user_sessions(conn) -> None:
    """Create ``user_sessions`` — one row per JWT minted via a login flow.

    Powers the dashboard's "Active sessions" panel and the operator
    console's per-user sessions expander.  Each row carries the JWT's
    ``jti`` (so a future revoke pass can deny-list a single device
    without invalidating the user's other tokens), a parsed device
    label, IP, expiry, and a throttled ``last_seen`` heartbeat written
    by the auth dependency on every authed request.
    """
    try:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_sessions'"
        )
        if await cur.fetchone():
            return
        await conn.execute(
            """CREATE TABLE user_sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                jti           TEXT    NOT NULL UNIQUE,
                device_label  TEXT    NOT NULL DEFAULT '',
                user_agent    TEXT    NOT NULL DEFAULT '',
                ip            TEXT    NOT NULL DEFAULT '',
                created_at    TEXT    NOT NULL,
                last_seen     TEXT    NOT NULL,
                expires_at    TEXT    NOT NULL,
                revoked_at    TEXT
            )"""
        )
        await conn.execute(
            "CREATE INDEX idx_user_sessions_user ON user_sessions(user_id, last_seen DESC)"
        )
        await conn.execute(
            "CREATE INDEX idx_user_sessions_jti ON user_sessions(jti)"
        )
        await conn.commit()
        logger.info("Created user_sessions table")
    except Exception as e:
        logger.error("user_sessions migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_add_users_last_seen(conn) -> None:
    """Add ``users.last_seen`` — last time the user authenticated against the
    API.  Stamped (throttled, ~once/min) by the auth dependency so the
    system console can show 'last seen X ago' without any client-side
    heartbeat.  Nullable: pre-existing rows are NULL until the user
    logs in again."""
    try:
        cur = await conn.execute("PRAGMA table_info(users)")
        cols = {r[1] for r in await cur.fetchall()}
        if "last_seen" not in cols:
            await conn.execute("ALTER TABLE users ADD COLUMN last_seen TEXT")
            await conn.commit()
            logger.info("Migration: added users.last_seen column")
    except Exception as e:
        logger.error("last_seen migration failed: %s", e)
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


async def migrate_processed_stripe_events_table(conn) -> None:
    """Create ``processed_stripe_events`` for webhook idempotency.

    Stripe retries any non-2xx response (and some 2xx if it doesn't
    receive the ack in time), so the same ``event.id`` can arrive
    several times.  ``handle_webhook`` INSERT-OR-IGNOREs into this
    table before processing; duplicates short-circuit with a fast 200
    and never re-mutate state.
    """
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS processed_stripe_events (
                event_id      TEXT    PRIMARY KEY,
                event_type    TEXT    NOT NULL,
                processed_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                account_id    INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_processed_stripe_events_processed_at
                ON processed_stripe_events(processed_at);
        """)
        await conn.commit()
        logger.info("processed_stripe_events table created/verified")
    except Exception as e:
        logger.debug("processed_stripe_events migration skipped: %s", e)


async def migrate_billing_invoices_table(conn) -> None:
    """Create ``billing_invoices`` — mirror of Stripe invoice records.

    Stripe is authoritative for the numbers; we mirror locally so the
    dashboard can list invoices without an API round-trip and so a
    payment-retry / failure can be correlated with the snapshot it
    belongs to.  ``provider_invoice_id`` is UNIQUE: the webhook handler
    INSERT-OR-IGNOREs so retries are no-ops.
    """
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS billing_invoices (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id                INTEGER NOT NULL REFERENCES accounts(id),
                provider                  TEXT    NOT NULL DEFAULT 'stripe',
                provider_invoice_id       TEXT    NOT NULL UNIQUE,
                provider_subscription_id  TEXT    NOT NULL DEFAULT '',
                provider_customer_id      TEXT    NOT NULL DEFAULT '',
                amount_due_cents          INTEGER NOT NULL DEFAULT 0,
                amount_paid_cents         INTEGER NOT NULL DEFAULT 0,
                currency                  TEXT    NOT NULL DEFAULT 'usd',
                status                    TEXT    NOT NULL DEFAULT '',
                period_start              TEXT,
                period_end                TEXT,
                hosted_invoice_url        TEXT    NOT NULL DEFAULT '',
                invoice_pdf_url           TEXT    NOT NULL DEFAULT '',
                paid_at                   TEXT,
                created_at                TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_billing_invoices_account
                ON billing_invoices(account_id, created_at DESC);
        """)
        await conn.commit()
        logger.info("billing_invoices table created/verified")
    except Exception as e:
        logger.debug("billing_invoices migration skipped: %s", e)


async def migrate_subscription_comp_columns(conn) -> None:
    """Add comp + grace tracking columns to ``subscriptions``.

    Adds: ``past_due_since`` (when did we enter past_due), ``is_comped``,
    ``comp_expires_at`` (REQUIRED when comped), ``comp_reason``,
    ``comp_granted_by``, ``comp_granted_at``.  Each column is added
    independently and skipped if already present so this migration is
    safe to run repeatedly.
    """
    spec = [
        ("past_due_since",  "TEXT"),
        ("is_comped",       "INTEGER NOT NULL DEFAULT 0"),
        ("comp_expires_at", "TEXT"),
        ("comp_reason",     "TEXT NOT NULL DEFAULT ''"),
        ("comp_granted_by", "INTEGER"),
        ("comp_granted_at", "TEXT"),
    ]
    try:
        cur = await conn.execute("PRAGMA table_info(subscriptions)")
        existing = {r[1] for r in await cur.fetchall()}
        for col, decl in spec:
            if col in existing:
                continue
            await conn.execute(f"ALTER TABLE subscriptions ADD COLUMN {col} {decl}")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_subscriptions_comp_expires "
            "ON subscriptions(is_comped, comp_expires_at)"
        )
        await conn.commit()
        logger.info("subscriptions comp columns added/verified")
    except Exception as e:
        logger.debug("subscriptions comp migration skipped: %s", e)


async def migrate_billing_usage_snapshot_active_columns(conn) -> None:
    """Add ``active_vehicles`` / ``inactive_vehicles`` to usage snapshots.

    The monthly snapshot job now records the active-vehicle count
    (vehicles signaling within the last 3 days) — the figure that
    actually drives the invoice.  ``vehicle_count`` (raw Samsara fleet)
    stays around for legacy dashboards.  Both new columns default to
    zero so historical rows project cleanly into the new schema.
    """
    spec = [
        ("active_vehicles",   "INTEGER NOT NULL DEFAULT 0"),
        ("inactive_vehicles", "INTEGER NOT NULL DEFAULT 0"),
    ]
    try:
        cur = await conn.execute("PRAGMA table_info(billing_usage_snapshots)")
        existing = {r[1] for r in await cur.fetchall()}
        for col, decl in spec:
            if col in existing:
                continue
            await conn.execute(
                f"ALTER TABLE billing_usage_snapshots ADD COLUMN {col} {decl}"
            )
        await conn.commit()
        logger.info("billing_usage_snapshots active/inactive columns added/verified")
    except Exception as e:
        logger.debug("billing_usage_snapshots active-columns migration skipped: %s", e)


async def migrate_subscription_provider_item_ids(conn) -> None:
    """Add ``provider_base_item_id`` + ``provider_extra_item_id`` columns.

    Stripe's two-line subscription pattern needs the individual item ids
    so ``sync_billing_quantity`` can PATCH only the extras quantity
    without touching the base.  Existing deploys carry empty strings
    until the next checkout completes.
    """
    spec = [
        ("provider_base_item_id",  "TEXT NOT NULL DEFAULT ''"),
        ("provider_extra_item_id", "TEXT NOT NULL DEFAULT ''"),
    ]
    try:
        cur = await conn.execute("PRAGMA table_info(subscriptions)")
        existing = {r[1] for r in await cur.fetchall()}
        for col, decl in spec:
            if col in existing:
                continue
            await conn.execute(f"ALTER TABLE subscriptions ADD COLUMN {col} {decl}")
        await conn.commit()
        logger.info("subscriptions provider_*_item_id columns added/verified")
    except Exception as e:
        logger.debug("subscriptions provider-item-id migration skipped: %s", e)


async def migrate_comp_account_history_table(conn) -> None:
    """Create the comp audit log table."""
    try:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS comp_account_history (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id    INTEGER NOT NULL REFERENCES accounts(id),
                action        TEXT    NOT NULL,
                expires_at    TEXT,
                reason        TEXT    NOT NULL DEFAULT '',
                actor_user_id INTEGER,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_comp_history_account
                ON comp_account_history(account_id, created_at DESC);
        """)
        await conn.commit()
        logger.info("comp_account_history table created/verified")
    except Exception as e:
        logger.debug("comp_account_history migration skipped: %s", e)


# ── Re-base account IDs to the 10_000_001+ range ────────────────────

ACCOUNT_ID_OFFSET = 10_000_000
ACCOUNT_ID_FLOOR = ACCOUNT_ID_OFFSET + 1  # 10_000_001 — minimum legitimate ID


async def migrate_account_id_base_renumber(conn) -> None:
    """Re-base every account_id from 1..N to 10_000_001..N+10M.

    Production-grade ID format: every tenant folder lines up as
    ``account-100000XX/`` (8 digits, fixed-width).  Avoids leaking the
    tenant count to customers and reserves the low range (1..9999999)
    for internal/system accounts forever.

    **Atomicity:** the migration acquires a raw asyncpg connection
    directly from the pool, starts a real transaction, and performs all
    UPDATEs inside it.  Done this way because the migration runner's
    ``conn`` is a pool-PROXY where each ``execute()`` acquires a fresh
    connection from the pool — ``SET LOCAL`` directives, constraint
    deferral, and BEGIN/COMMIT would otherwise apply to different
    sessions.  Failure rolls back the whole pass; success leaves the
    DB in a fully-renumbered state with no half-states observable.

    **FK enforcement:** every FK constraint referencing accounts(id)
    is altered to DEFERRABLE INITIALLY IMMEDIATE inside the transaction,
    then ``SET CONSTRAINTS ALL DEFERRED`` defers the checks until
    COMMIT.  This works without superuser perms (any owner of the
    constraint can ALTER it).  Constraints remain DEFERRABLE after
    the migration — they default to IMMEDIATE so normal enforcement
    is unchanged, but future ops needing to bulk-renumber can defer.

    **RLS:** ``SET LOCAL row_security = off`` bypasses any tenant
    isolation policies for the duration of this transaction.  Works
    if the connecting role owns the tables (which it does in
    practice — same role created them).

    **Idempotent:** the ``WHERE id < FLOOR`` filter makes re-runs
    no-ops once every account has been bumped.

    **Concurrency:** ``LOCK TABLE accounts IN ACCESS EXCLUSIVE MODE``
    serialises with any other writer; the rest of the system sees a
    consistent view throughout.  Run this in a maintenance window —
    concurrent INSERTs into accounts during the lock will block until
    the migration commits.
    """
    # The proxy carries a reference to the asyncpg pool underneath.
    pool = getattr(getattr(conn, "_pool", None), "_pool", None)
    if pool is None:
        # ``Database.initialize()`` always wires up the asyncpg pool
        # before running migrations, so reaching here means the
        # configuration is broken in a way that would silently leave
        # account IDs at 1..N and the next migration (077) would stamp
        # paths with the wrong IDs.  Fail loud rather than silently
        # skip — caller stack trace tells ops exactly where to look.
        raise RuntimeError(
            "Migration 076: asyncpg pool reference unavailable; this "
            "migration must run on Postgres with the pool initialised."
        )

    async with pool.acquire() as raw:
        # Check we actually need to do anything before locking.
        max_id_row = await raw.fetchrow(
            "SELECT COALESCE(MAX(id), 0) AS max_id FROM accounts"
        )
        max_id = int(max_id_row["max_id"]) if max_id_row else 0
        if max_id >= ACCOUNT_ID_FLOOR:
            logger.debug(
                "Migration 076: max(accounts.id)=%d already >= %d — skipping",
                max_id, ACCOUNT_ID_FLOOR,
            )
            return

        # RLS guard: under ENABLE_RLS=1 (migration 057), tenant tables
        # carry FORCE ROW LEVEL SECURITY which a non-BYPASSRLS role
        # cannot bypass with ``SET LOCAL row_security = off``.  If any
        # of the tables we're about to UPDATE has FORCE RLS active AND
        # the connecting role isn't BYPASSRLS, the bulk UPDATEs would
        # silently match zero rows and leave the DB in a tenancy-broken
        # state.  Refuse to proceed.
        rls_blockers = await raw.fetch("""
            SELECT n.nspname || '.' || c.relname AS qualified
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
              JOIN information_schema.columns col
                ON col.table_schema = n.nspname
               AND col.table_name   = c.relname
             WHERE col.column_name = 'account_id'
               AND n.nspname = 'public'
               AND c.relrowsecurity = true
               AND c.relforcerowsecurity = true
        """)
        if rls_blockers:
            bypass = await raw.fetchval(
                "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            if not bypass:
                tables = ", ".join(r["qualified"] for r in rls_blockers)
                raise RuntimeError(
                    f"Migration 076: cannot renumber under FORCE ROW LEVEL "
                    f"SECURITY without BYPASSRLS role.  Affected tables: "
                    f"{tables}.  Connect as a BYPASSRLS role (e.g. "
                    f"4truck_admin) or run this migration before flipping "
                    f"ENABLE_RLS=1."
                )

        async with raw.transaction():
            # Disable RLS for this tx — owner privilege suffices when
            # FORCE RLS isn't set; the guard above caught the FORCE case.
            try:
                await raw.execute("SET LOCAL row_security = off")
            except Exception as e:
                logger.warning(
                    "Migration 076: could not SET LOCAL row_security (%s); "
                    "continuing — RLS may filter UPDATEs if enabled", e,
                )

            # Lock accounts so no concurrent INSERT picks a number
            # below the new floor mid-migration.
            await raw.execute(
                "LOCK TABLE accounts IN ACCESS EXCLUSIVE MODE"
            )

            # Defer every FK constraint that points at accounts(id).
            # ALTERing each to DEFERRABLE lets SET CONSTRAINTS DEFERRED
            # apply.  The ALTER is a metadata-only operation; rows are
            # untouched.
            fk_rows = await raw.fetch("""
                SELECT n.nspname  AS schema_name,
                       c.relname  AS table_name,
                       con.conname AS constraint_name
                  FROM pg_constraint con
                  JOIN pg_class      c ON c.oid = con.conrelid
                  JOIN pg_namespace  n ON n.oid = c.relnamespace
                 WHERE con.contype = 'f'
                   AND con.confrelid = 'public.accounts'::regclass
            """)
            for fk in fk_rows:
                try:
                    await raw.execute(
                        f'ALTER TABLE "{fk["schema_name"]}"."{fk["table_name"]}" '
                        f'ALTER CONSTRAINT "{fk["constraint_name"]}" '
                        f'DEFERRABLE INITIALLY IMMEDIATE'
                    )
                except Exception as e:
                    # Some constraints (e.g. partition parent) may not
                    # be ALTER-able; log + continue.  If FK still fires
                    # the renumber UPDATE will raise, rolling back.
                    logger.warning(
                        "Migration 076: could not make %s deferrable (%s)",
                        fk["constraint_name"], e,
                    )

            await raw.execute("SET CONSTRAINTS ALL DEFERRED")

            # 1. Bump the accounts PK.
            await raw.execute(
                f"UPDATE accounts SET id = id + {ACCOUNT_ID_OFFSET} "
                f"WHERE id < {ACCOUNT_ID_FLOOR}"
            )

            # 2. Bump every account_id column in the public schema.
            #    information_schema sees every table that exists at
            #    this point in startup — and we run AFTER both
            #    schema.create_tables + platform_schema.create_tables
            #    + platform_migrations table-creators, so coverage is
            #    exhaustive.
            await raw.execute(f"""
                DO $$
                DECLARE
                    r RECORD;
                BEGIN
                    FOR r IN
                        SELECT table_schema, table_name
                          FROM information_schema.columns
                         WHERE column_name = 'account_id'
                           AND table_schema = 'public'
                           AND table_name <> 'accounts'
                    LOOP
                        EXECUTE format(
                            'UPDATE %I.%I SET account_id = account_id + {ACCOUNT_ID_OFFSET} '
                            'WHERE account_id IS NOT NULL '
                            'AND account_id < {ACCOUNT_ID_FLOOR}',
                            r.table_schema, r.table_name
                        );
                    END LOOP;
                END
                $$;
            """)

        # 3. Reset the sequence.  Runs OUTSIDE the renumber transaction
        # so a setval failure (perms / sequence renamed in weird ways)
        # doesn't roll back the renumber itself.  ``pg_get_serial_sequence``
        # returns the properly-quoted sequence name, and we pass it as
        # a regclass-cast bind param so it's safe against any quoting
        # edge case.
        try:
            seq_name = await raw.fetchval(
                "SELECT pg_get_serial_sequence('accounts', 'id')"
            ) or "public.accounts_id_seq"
            await raw.execute(
                "SELECT setval($1::regclass, "
                "GREATEST((SELECT COALESCE(MAX(id), 0) FROM accounts), $2))",
                seq_name, ACCOUNT_ID_OFFSET,
            )
        except Exception as e:
            logger.error(
                "Migration 076: could not reset accounts sequence (%s); "
                "next INSERT may receive a low ID — run manually: "
                "SELECT setval(pg_get_serial_sequence('accounts','id'), "
                "GREATEST((SELECT MAX(id) FROM accounts), %d))",
                e, ACCOUNT_ID_OFFSET,
            )

        logger.info(
            "Migration 076: re-based account IDs to %d+ range",
            ACCOUNT_ID_FLOOR,
        )


# ── Prefix stored file paths with account-{id}/ ─────────────────────


async def migrate_account_prefixed_file_paths(conn) -> None:
    """Insert ``account-{id}/`` after ``data/userdata/`` in stored paths.

    Must be called AFTER ``migrate_account_id_base_renumber`` so the
    inserted ID matches the renumbered ``accounts.id`` value (see the
    explicit call order in ``run_all``).

    Each column needs the row's account_id.  Some tables carry it
    directly (camera_checks, parking_events, maintenance_tasks,
    storage_sync_queue); others reach it via a join
    (pti_inspection_media → driver_inspections,
    work_order_attachments → work_orders).

    Idempotent: the ``NOT LIKE 'data/userdata/account-%'`` filter makes
    re-runs no-ops for rows that already carry the prefix.  Drive-backed
    file_path values (opaque Drive IDs without slashes) are untouched
    because they don't match ``LIKE 'data/userdata/%'``.

    Wrapped in a real asyncpg transaction so a per-table failure rolls
    back the whole pass — partial migrations are observable to ops via
    a clean ``logger.error``, never via silently half-written rows.
    """
    pool = getattr(getattr(conn, "_pool", None), "_pool", None)
    if pool is None:
        # Same reasoning as the renumber step: silently skipping leaves
        # stored paths without the account prefix, which the rest of
        # the system assumes is present.  Fail loud.
        raise RuntimeError(
            "Migration 077: asyncpg pool reference unavailable; this "
            "migration must run on Postgres with the pool initialised."
        )

    direct = [
        ("camera_checks",      "image_path"),
        ("parking_events",     "map_image_path"),
        ("maintenance_tasks",  "attachment_path"),
        ("storage_sync_queue", "local_path"),
    ]
    via_join = [
        # (table, column, join_table, on_self, on_parent, parent_account_col)
        ("pti_inspection_media",   "file_path",
         "driver_inspections", "inspection_id", "id", "account_id"),
        ("pti_inspection_media",   "local_path",
         "driver_inspections", "inspection_id", "id", "account_id"),
        ("work_order_attachments", "file_path",
         "work_orders",        "work_order_id", "id", "account_id"),
    ]

    async with pool.acquire() as raw:
        async with raw.transaction():
            # Bypass RLS for the duration of this migration so all
            # tenant rows are visible to the UPDATEs.
            try:
                await raw.execute("SET LOCAL row_security = off")
            except Exception as e:
                logger.debug(
                    "Migration 077: SET LOCAL row_security failed (%s)", e,
                )

            total = 0

            # Fail-fast on the first per-table error: once asyncpg
            # records an error inside a transaction, every subsequent
            # ``raw.execute`` raises ``InFailedSQLTransactionError``
            # with a generic "current transaction is aborted" message,
            # which would mask the real root cause and pad the log
            # with noise.  Break on the first failure so the
            # RuntimeError carries the actionable message.
            for table, col in direct:
                try:
                    result = await raw.execute(f"""
                        UPDATE {table}
                           SET {col} = 'data/userdata/account-' || account_id || '/'
                                    || substr({col}, length('data/userdata/') + 1)
                         WHERE {col} LIKE 'data/userdata/%'
                           AND {col} NOT LIKE 'data/userdata/account-%'
                           AND length({col}) > length('data/userdata/')
                    """)
                except Exception as e:
                    raise RuntimeError(
                        f"Migration 077: failed on {table}.{col} ({e})"
                    ) from e
                # asyncpg execute() returns command status like
                # 'UPDATE 5'; parse it for the rowcount.
                parts = (result or "").split()
                rowcount = int(parts[-1]) if parts and parts[-1].isdigit() else 0
                total += rowcount
                logger.info(
                    "Migration 077: %s.%s prefixed %d row(s)",
                    table, col, rowcount,
                )

            for table, col, jt, on_self, on_parent, parent_col in via_join:
                try:
                    result = await raw.execute(f"""
                        UPDATE {table} AS t
                           SET {col} = 'data/userdata/account-' || p.{parent_col} || '/'
                                    || substr(t.{col}, length('data/userdata/') + 1)
                          FROM {jt} AS p
                         WHERE t.{on_self} = p.{on_parent}
                           AND t.{col} LIKE 'data/userdata/%'
                           AND t.{col} NOT LIKE 'data/userdata/account-%'
                           AND length(t.{col}) > length('data/userdata/')
                    """)
                except Exception as e:
                    raise RuntimeError(
                        f"Migration 077: failed on {table}.{col} via {jt} ({e})"
                    ) from e
                parts = (result or "").split()
                rowcount = int(parts[-1]) if parts and parts[-1].isdigit() else 0
                total += rowcount
                logger.info(
                    "Migration 077: %s.%s (via %s) prefixed %d row(s)",
                    table, col, jt, rowcount,
                )

            logger.info("Migration 077: account-prefixed %d path(s)", total)


async def migrate_create_account_persona_groups(conn) -> None:
    """Create ``account_persona_groups`` — per-persona Telegram group
    chat for an account when ``accounts.alert_routing_mode`` is
    ``per_persona_groups``.

    One row per (account, persona) pair.  Unlike the legacy
    ``forum_groups``/``alert_routing`` pair (one forum chat + many topic
    threads), this table stores a *flat* group chat per persona —
    Dispatchers, Safety, Fleet, HR each get their own group.  Owner /
    Admin share the aggregate group.

    ``chat_id`` is the Telegram chat ID (negative for groups/supergroups).
    ``chat_title`` is purely advisory for the admin UI (group rename in
    Telegram won't auto-sync back).
    """
    try:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='account_persona_groups'"
        )
        if await cur.fetchone():
            return
        await conn.execute(
            """CREATE TABLE account_persona_groups (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL,
                persona     TEXT    NOT NULL,
                chat_id     BIGINT  NOT NULL,
                chat_title  TEXT    NOT NULL DEFAULT '',
                is_active   INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL,
                UNIQUE(account_id, persona)
            )"""
        )
        await conn.execute(
            "CREATE INDEX idx_account_persona_groups_account "
            "ON account_persona_groups(account_id)"
        )
        await conn.commit()
        logger.info("Created account_persona_groups table")
    except Exception as e:
        logger.error("account_persona_groups migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


async def migrate_add_accounts_alert_routing_mode(conn) -> None:
    """Add ``accounts.alert_routing_mode`` — opt-in switch between the
    legacy single-forum-with-topics routing (``single_group``, the
    default) and the per-persona flat-groups routing
    (``per_persona_groups``).

    Default ``single_group`` preserves existing-account behavior —
    nothing changes for any account until an operator flips the column
    and registers the persona groups in ``account_persona_groups``.
    """
    try:
        cur = await conn.execute("PRAGMA table_info(accounts)")
        cols = {r[1] for r in await cur.fetchall()}
        if "alert_routing_mode" not in cols:
            await conn.execute(
                "ALTER TABLE accounts ADD COLUMN alert_routing_mode "
                "TEXT NOT NULL DEFAULT 'single_group'"
            )
            await conn.commit()
            logger.info("Migration: added accounts.alert_routing_mode column")
    except Exception as e:
        logger.error("alert_routing_mode migration failed: %s", e)
        try:
            await conn.rollback()
        except Exception:
            pass


# ── Repair stale account-N/ path prefix after out-of-order deploys ──


async def migrate_repair_stale_account_path_ids(conn) -> None:
    """Bump ``account-N/`` to ``account-{N+10M}/`` in stored file paths.

    Idempotent repair pass that exists because an earlier deploy briefly
    ran the path-prefix migration (077) BEFORE the account renumber (076)
    — the path-prefix step therefore stamped paths with the OLD
    account_id (e.g. ``data/userdata/account-1/...``), then the renumber
    bumped ``accounts.id`` to ``10000001`` without revisiting the stored
    paths.  Result: paths point at a folder that doesn't exist on disk
    because the filesystem reshuffle moved files into ``account-10000001/``.

    The fix is mechanical: for any path containing ``account-N/`` where
    ``N < 10_000_001``, rewrite to ``account-{N+10_000_000}/``.  Same
    offset the renumber step uses, so the result lines up with the
    renumbered ``accounts.id`` and the moved-on-disk folders.

    Safe to run unconditionally: fast no-op when no stale paths exist
    (the regex test ``~ 'account-([1-9][0-9]{0,6})/'`` matches only
    1..9_999_999, never the 10M+ range).
    """
    pool = getattr(getattr(conn, "_pool", None), "_pool", None)
    if pool is None:
        raise RuntimeError(
            "Migration 078: asyncpg pool reference unavailable."
        )

    OFFSET = 10_000_000
    columns = [
        ("camera_checks",          "image_path"),
        ("parking_events",         "map_image_path"),
        ("maintenance_tasks",      "attachment_path"),
        ("storage_sync_queue",     "local_path"),
        ("pti_inspection_media",   "file_path"),
        ("pti_inspection_media",   "local_path"),
        ("work_order_attachments", "file_path"),
    ]

    async with pool.acquire() as raw:
        async with raw.transaction():
            try:
                await raw.execute("SET LOCAL row_security = off")
            except Exception:
                pass

            total = 0
            for table, col in columns:
                try:
                    # Extract the OLD numeric id from the path, add the
                    # offset, and swap the segment.  The WHERE clause
                    # restricts to paths whose embedded id is below the
                    # 10M floor — already-correct paths are skipped.
                    result = await raw.execute(f"""
                        UPDATE {table}
                           SET {col} = replace(
                               {col},
                               'data/userdata/account-'
                                   || substring({col} from 'account-([0-9]+)/')
                                   || '/',
                               'data/userdata/account-'
                                   || (substring({col} from 'account-([0-9]+)/')::bigint
                                       + {OFFSET})::text
                                   || '/'
                           )
                         WHERE {col} ~ 'data/userdata/account-[0-9]+/'
                           AND substring({col} from 'account-([0-9]+)/')::bigint < {OFFSET + 1}
                    """)
                except Exception as e:
                    raise RuntimeError(
                        f"Migration 078: failed on {table}.{col} ({e})"
                    ) from e
                parts = (result or "").split()
                rowcount = int(parts[-1]) if parts and parts[-1].isdigit() else 0
                total += rowcount
                if rowcount:
                    logger.info(
                        "Migration 078: %s.%s repaired %d row(s)",
                        table, col, rowcount,
                    )

            logger.info(
                "Migration 078: repaired %d stale account-prefixed path(s)",
                total,
            )


# ── Knowledge-base index hygiene + full-text search ─────────────────


async def migrate_knowledge_base_indexes_and_fts(conn) -> None:
    """Tighten the knowledge_base table for production scale.

    Two parts, both idempotent:

    1. ``idx_kb_account`` and ``idx_kb_account_cat`` are functionally
       identical — both ``(account_id, category)`` btrees.  Keep the
       longer-named one and drop the duplicate so writes don't pay
       double the index-maintenance cost.
    2. Add a GIN ``tsvector`` index across title + description + tags
       so search is sub-millisecond even at 10k+ articles.  Falls back
       gracefully on non-Postgres (no-op) — the storage layer's ILIKE
       query still works without the FTS index, it just scans the
       table sequentially.

    Reads no DB credentials and runs through the regular proxy
    connection.  Both statements use ``IF EXISTS`` / ``IF NOT EXISTS``
    so re-runs are silent.
    """
    try:
        # Drop the duplicate index.  Both names appear in production
        # because two migrations created the same shape at different
        # times.  Keep idx_kb_account (the older name) and remove the
        # newer one — either works, picked to match existing logs.
        await conn.execute("DROP INDEX IF EXISTS idx_kb_account_cat")
        await conn.commit()
    except Exception as e:
        logger.debug("Migration 079: dropping duplicate index skipped (%s)", e)

    # Try to add the FTS GIN index.  ``IF NOT EXISTS`` keeps re-runs
    # silent.  Postgres-only — the ``USING GIN`` syntax is not portable
    # to SQLite but tests use Postgres exclusively per conftest.py.
    try:
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_kb_fts
                ON knowledge_base
              USING GIN (to_tsvector('english',
                            coalesce(title,'')
                            || ' ' || coalesce(description,'')
                            || ' ' || coalesce(tags,'')))
        """)
        await conn.commit()
        logger.info("Migration 079: FTS GIN index created")
    except Exception as e:
        # Not Postgres OR the table doesn't exist yet on a brand-new
        # install (rare race — platform_schema creates it first, then
        # platform_migrations runs).  Either way, harmless.
        logger.debug("Migration 079: FTS index skipped (%s)", e)


# ── Backfill: rewrite ownership columns from telegram_id to users.id ──


async def migrate_backfill_user_id_ownership(conn) -> None:
    """Rewrite ownership columns from telegram_id to ``users.id``.

    Background
    ----------
    Tables like ``knowledge_base.created_by``,
    ``pti_inspection_media.uploaded_by``, and
    ``work_order_attachments.uploaded_by`` historically stored a
    user's Telegram ID (BIGINT) as the row's owner.  That value can
    change at runtime — a user who registered by email later links
    their Telegram, or a future auth provider rotates the identifier
    — and when it does the original owner loses access to the rows
    they created.

    What this migration does
    ------------------------
    For each column in the list below, look up the row's current
    ``created_by`` / ``uploaded_by`` value as a ``users.telegram_id``
    and rewrite it to that user's ``users.id``.  Rows that don't
    resolve (deleted users, orphan IDs) become ``0`` so they remain
    visible but visibly unowned — easy to spot in an ops audit.

    Idempotent
    ----------
    Each UPDATE skips rows whose value already matches a ``users.id``
    (``NOT EXISTS (SELECT 1 FROM users WHERE id = t.col)``).  Running
    the migration twice touches zero rows on the second pass.

    Reversibility
    -------------
    The original telegram_id is lost on rewrite.  Before running this
    against a non-trivial production dataset, snapshot the affected
    columns first::

        CREATE TABLE _ownership_backfill_snapshot AS
            SELECT 'knowledge_base.created_by' AS source, id, created_by
              FROM knowledge_base
             UNION ALL ...
    """
    pool = getattr(getattr(conn, "_pool", None), "_pool", None)
    if pool is None:
        raise RuntimeError(
            "Migration 080: asyncpg pool reference unavailable."
        )

    # (table, column) pairs.  Each column currently stores a raw
    # telegram_id and is used to record row ownership.  Verified by
    # querying information_schema for BIGINT created_by / uploaded_by
    # columns in the public schema — anything else either uses a proper
    # INTEGER FK already (modern style) or stores audit-trail data
    # (e.g. ``audit_log.user_id``) which we intentionally leave alone.
    columns = [
        ("knowledge_base",         "created_by"),
        ("work_orders",            "created_by"),
        ("work_order_attachments", "uploaded_by"),
        ("pti_inspection_media",   "uploaded_by"),
        ("maintenance_tasks",      "created_by"),
        ("fuel_entries",           "created_by"),
        ("platform_geofences",     "created_by"),
        ("work_hours",             "created_by"),
        ("custom_poi_layers",      "created_by"),
    ]

    async with pool.acquire() as raw:
        async with raw.transaction():
            try:
                await raw.execute("SET LOCAL row_security = off")
            except Exception:
                pass

            total = 0
            for table, col in columns:
                try:
                    # Three classes of rows in each table:
                    #   1. col already equals a users.id (already migrated)
                    #   2. col equals a users.telegram_id → rewrite to that user's id
                    #   3. col matches neither → set to 0 (unknown owner)
                    #
                    # We detect class 1 with a NOT EXISTS guard so the
                    # UPDATE only touches rows that aren't already on
                    # the right side.
                    result = await raw.execute(f"""
                        UPDATE {table} AS t
                           SET {col} = COALESCE(
                                   (SELECT u.id FROM users u
                                     WHERE u.telegram_id = t.{col}
                                     LIMIT 1),
                                   0)
                         WHERE {col} IS NOT NULL
                           AND {col} <> 0
                           AND NOT EXISTS (
                               SELECT 1 FROM users u
                                WHERE u.id = t.{col}
                           )
                    """)
                    parts = (result or "").split()
                    rowcount = int(parts[-1]) if parts and parts[-1].isdigit() else 0
                    total += rowcount
                    if rowcount:
                        logger.info(
                            "Migration 080: %s.%s rewrote %d row(s)",
                            table, col, rowcount,
                        )
                except Exception as e:
                    # Table doesn't exist (some are tenant-only) — log
                    # and continue.  The outer try/except for the
                    # whole transaction would roll everything back on
                    # failure, but a missing tenant table is benign
                    # since it has no rows to migrate.
                    logger.debug(
                        "Migration 080: skipping %s.%s (%s)", table, col, e,
                    )

            logger.info(
                "Migration 080: backfilled %d ownership row(s) across %d table(s)",
                total, len(columns),
            )
