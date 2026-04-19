"""DDL statements — CREATE TABLE / INDEX for every table."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def create_tables(conn) -> None:
    """Create all tables and indexes.  Called during Database.initialize()."""

    # Migration: rename old 'organizations' table → 'companies'
    try:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='organizations'"
        )
        if await cur.fetchone():
            await conn.execute("ALTER TABLE organizations RENAME TO companies")
            await conn.execute("DROP INDEX IF EXISTS idx_orgs_account_id")
            await conn.commit()
            logger.info("Migrated table organizations → companies")
    except Exception as e:
        logger.debug(f"Table migration check: {e}")

    # Migration: add thinking_tokens column to ai_usage
    try:
        cur = await conn.execute("PRAGMA table_info(ai_usage)")
        cols = {r[1] for r in await cur.fetchall()}
        if "ai_usage" not in {r[0] for r in (await (await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")).fetchall())}:
            pass  # table doesn't exist yet — CREATE TABLE handles it
        elif "thinking_tokens" not in cols:
            await conn.execute(
                "ALTER TABLE ai_usage ADD COLUMN thinking_tokens INTEGER NOT NULL DEFAULT 0"
            )
            await conn.commit()
            logger.info("Added thinking_tokens column to ai_usage")
    except Exception as e:
        logger.debug(f"ai_usage migration check: {e}")

    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            slug        TEXT    NOT NULL UNIQUE,
            tier        TEXT    NOT NULL DEFAULT 'free',
            is_active   INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS companies (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            code            TEXT    NOT NULL,
            display_name    TEXT    NOT NULL DEFAULT '',
            samsara_api_key TEXT    NOT NULL,
            active_days     INTEGER NOT NULL DEFAULT 30,
            is_active       INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT    NOT NULL,
            UNIQUE(account_id, code)
        );

        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL UNIQUE,
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            role        TEXT    NOT NULL DEFAULT 'fleet',
            department  TEXT    NOT NULL DEFAULT 'general',
            truck_num   TEXT,
            alerts_on   INTEGER NOT NULL DEFAULT 0,
            is_active   INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS invites (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT    NOT NULL UNIQUE,
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            role        TEXT    NOT NULL DEFAULT 'fleet',
            department  TEXT    NOT NULL DEFAULT 'general',
            truck_num   TEXT,
            created_by  INTEGER NOT NULL REFERENCES users(id),
            expires_at  TEXT    NOT NULL,
            used_by     INTEGER REFERENCES users(id),
            created_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS authorized_chats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            chat_id     INTEGER NOT NULL,
            chat_title  TEXT    NOT NULL DEFAULT '',
            added_by    INTEGER NOT NULL REFERENCES users(id),
            is_active   INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL,
            UNIQUE(account_id, chat_id)
        );

        CREATE TABLE IF NOT EXISTS maintenance_tasks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            company_code    TEXT    NOT NULL DEFAULT '',
            vehicle_id      TEXT    NOT NULL DEFAULT '',
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            task_type       TEXT    NOT NULL DEFAULT 'custom',
            description     TEXT    NOT NULL DEFAULT '',
            due_date        TEXT,
            due_miles       REAL,
            status          TEXT    NOT NULL DEFAULT 'pending',
            created_by      INTEGER NOT NULL,
            created_at      TEXT    NOT NULL,
            completed_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS fuel_entries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            company_code    TEXT    NOT NULL DEFAULT '',
            vehicle_id      TEXT    NOT NULL DEFAULT '',
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            gallons         REAL    NOT NULL DEFAULT 0,
            price_per_gallon REAL   NOT NULL DEFAULT 0,
            total_cost      REAL    NOT NULL DEFAULT 0,
            odometer_miles  REAL    NOT NULL DEFAULT 0,
            date            TEXT    NOT NULL,
            created_by      INTEGER NOT NULL,
            created_at      TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS digest_subscriptions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            frequency   TEXT    NOT NULL DEFAULT 'daily',
            send_hour   INTEGER NOT NULL DEFAULT 7,
            timezone    TEXT    NOT NULL DEFAULT 'UTC',
            report_type TEXT    NOT NULL DEFAULT 'faults',
            is_active   INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL,
            UNIQUE(user_id)
        );

        CREATE INDEX IF NOT EXISTS idx_users_telegram_id
            ON users(telegram_id);
        CREATE INDEX IF NOT EXISTS idx_companies_account_id
            ON companies(account_id);
        CREATE INDEX IF NOT EXISTS idx_invites_code
            ON invites(code);
        CREATE INDEX IF NOT EXISTS idx_authorized_chats_chat_id
            ON authorized_chats(chat_id);
        CREATE INDEX IF NOT EXISTS idx_maintenance_account
            ON maintenance_tasks(account_id, status);
        CREATE INDEX IF NOT EXISTS idx_fuel_entries_account
            ON fuel_entries(account_id, vehicle_name);
        CREATE INDEX IF NOT EXISTS idx_digest_subs_active
            ON digest_subscriptions(is_active, send_hour);

        CREATE TABLE IF NOT EXISTS account_settings (
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            key         TEXT    NOT NULL,
            value       TEXT    NOT NULL DEFAULT '',
            updated_at  TEXT    NOT NULL,
            PRIMARY KEY (account_id, key)
        );

        CREATE TABLE IF NOT EXISTS alert_acknowledgments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            alert_type      TEXT    NOT NULL DEFAULT 'fault',
            vehicle_id      TEXT    NOT NULL DEFAULT '',
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            alert_key       TEXT    NOT NULL DEFAULT '',
            message_id      INTEGER NOT NULL DEFAULT 0,
            chat_id         INTEGER NOT NULL DEFAULT 0,
            sent_to         INTEGER NOT NULL DEFAULT 0,
            acknowledged_by INTEGER,
            acknowledged_at TEXT,
            escalation_level INTEGER NOT NULL DEFAULT 0,
            next_escalation TEXT,
            created_at      TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            user_id     INTEGER,
            action      TEXT    NOT NULL,
            target_type TEXT    NOT NULL DEFAULT '',
            target_id   TEXT    NOT NULL DEFAULT '',
            details     TEXT    NOT NULL DEFAULT '',
            created_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ai_usage (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            user_id         INTEGER NOT NULL DEFAULT 0,
            model           TEXT    NOT NULL DEFAULT '',
            request_type    TEXT    NOT NULL DEFAULT '',
            prompt_tokens   INTEGER NOT NULL DEFAULT 0,
            reply_tokens    INTEGER NOT NULL DEFAULT 0,
            thinking_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens    INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_ai_usage_account
            ON ai_usage(account_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_alert_ack_pending
            ON alert_acknowledgments(acknowledged_at, next_escalation);
        CREATE INDEX IF NOT EXISTS idx_audit_account
            ON audit_log(account_id, created_at);

        CREATE TABLE IF NOT EXISTS dnd_alert_queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            telegram_id     INTEGER NOT NULL,
            alert_type      TEXT    NOT NULL DEFAULT 'fault',
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            alert_text      TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL,
            delivered        INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_dnd_queue_pending
            ON dnd_alert_queue(telegram_id, delivered);

        CREATE TABLE IF NOT EXISTS alert_history (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id        INTEGER NOT NULL REFERENCES accounts(id),
            alert_type        TEXT    NOT NULL,
            vehicle_id        TEXT    NOT NULL,
            vehicle_name      TEXT    NOT NULL DEFAULT '',
            chat_id           INTEGER NOT NULL,
            message_id        INTEGER NOT NULL DEFAULT 0,
            occurrence_count  INTEGER NOT NULL DEFAULT 1,
            first_seen        TEXT    NOT NULL,
            last_seen         TEXT    NOT NULL,
            last_detail       TEXT    NOT NULL DEFAULT '',
            status            TEXT    NOT NULL DEFAULT 'active'
        );
        CREATE INDEX IF NOT EXISTS idx_alert_history_active
            ON alert_history(account_id, alert_type, vehicle_id, chat_id, status);

        CREATE TABLE IF NOT EXISTS role_permissions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            role            TEXT    NOT NULL,
            company_id      INTEGER,
            permissions     TEXT    NOT NULL DEFAULT '{}',
            updated_by      INTEGER NOT NULL DEFAULT 0,
            updated_at      TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, role, company_id)
        );
        CREATE INDEX IF NOT EXISTS idx_role_perms_account
            ON role_permissions(account_id);
        CREATE INDEX IF NOT EXISTS idx_role_perms_lookup
            ON role_permissions(account_id, role, company_id);

        CREATE TABLE IF NOT EXISTS driver_trucks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            truck_num   TEXT    NOT NULL,
            is_primary  INTEGER NOT NULL DEFAULT 0,
            assigned_by INTEGER NOT NULL DEFAULT 0,
            assigned_at TEXT    NOT NULL DEFAULT ''
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_driver_trucks_user_truck
            ON driver_trucks(user_id, truck_num);
        CREATE INDEX IF NOT EXISTS idx_driver_trucks_user
            ON driver_trucks(user_id);

        CREATE TABLE IF NOT EXISTS user_companies (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            company_id  INTEGER NOT NULL REFERENCES companies(id),
            assigned_by INTEGER NOT NULL DEFAULT 0,
            assigned_at TEXT    NOT NULL DEFAULT ''
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_user_companies_user_company
            ON user_companies(user_id, company_id);
        CREATE INDEX IF NOT EXISTS idx_user_companies_user
            ON user_companies(user_id);
    """)
    await conn.commit()
