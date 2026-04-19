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
        # If the DDL already has the composite constraint, skip
        if "UNIQUE(account_id, email)" in ddl.replace(" ", ""):
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
        from database.platform_schema import create_tables as _unused  # noqa: F401

        # We need to re-run create_tables which uses CREATE IF NOT EXISTS
        # Since we renamed the old table, it will create a fresh 'users'
        from database import platform_schema
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

        from permissions import ROLE_PERMISSIONS
        from database import Role

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
