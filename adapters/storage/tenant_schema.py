"""DDL for tenant tables — isolated per account.

Each tenant gets its own SQLite file. Tables still contain account_id
for backward compatibility during migration, but once fully migrated the
column becomes redundant (isolation by file).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def create_tables(conn) -> None:
    """Create tenant tables and indexes."""

    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS companies (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            code            TEXT    NOT NULL,
            display_name    TEXT    NOT NULL DEFAULT '',
            samsara_api_key TEXT    NOT NULL,
            active_days     INTEGER NOT NULL DEFAULT 30,
            is_active       INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT    NOT NULL,
            UNIQUE(account_id, code)
        );

        CREATE TABLE IF NOT EXISTS maintenance_tasks (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id          INTEGER NOT NULL,
            company_code        TEXT    NOT NULL DEFAULT '',
            vehicle_id          TEXT    NOT NULL DEFAULT '',
            vehicle_name        TEXT    NOT NULL DEFAULT '',
            task_type           TEXT    NOT NULL DEFAULT 'custom',
            description         TEXT    NOT NULL DEFAULT '',
            due_date            TEXT,
            due_miles           REAL,
            status              TEXT    NOT NULL DEFAULT 'pending',
            created_by          INTEGER NOT NULL,
            created_at          TEXT    NOT NULL,
            completed_at        TEXT,
            recur_interval_days INTEGER,
            recur_interval_miles REAL,
            last_odometer       REAL
        );

        CREATE TABLE IF NOT EXISTS fuel_entries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
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

        CREATE TABLE IF NOT EXISTS account_settings (
            account_id  INTEGER NOT NULL,
            key         TEXT    NOT NULL,
            value       TEXT    NOT NULL DEFAULT '',
            updated_at  TEXT    NOT NULL,
            PRIMARY KEY (account_id, key)
        );

        CREATE TABLE IF NOT EXISTS alert_acknowledgments (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id       INTEGER NOT NULL,
            alert_type       TEXT    NOT NULL DEFAULT 'fault',
            vehicle_id       TEXT    NOT NULL DEFAULT '',
            vehicle_name     TEXT    NOT NULL DEFAULT '',
            alert_key        TEXT    NOT NULL DEFAULT '',
            message_id       INTEGER NOT NULL DEFAULT 0,
            chat_id          INTEGER NOT NULL DEFAULT 0,
            sent_to          INTEGER NOT NULL DEFAULT 0,
            acknowledged_by  INTEGER,
            acknowledged_at  TEXT,
            status           TEXT    NOT NULL DEFAULT 'active',
            created_at       TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS alert_history (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id        INTEGER NOT NULL,
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

        CREATE TABLE IF NOT EXISTS dnd_alert_queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            telegram_id     INTEGER NOT NULL,
            alert_type      TEXT    NOT NULL DEFAULT 'fault',
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            alert_text      TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL,
            delivered       INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL,
            user_id     INTEGER,
            action      TEXT    NOT NULL,
            target_type TEXT    NOT NULL DEFAULT '',
            target_id   TEXT    NOT NULL DEFAULT '',
            details     TEXT    NOT NULL DEFAULT '',
            created_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS camera_checks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            vehicle_id      TEXT    NOT NULL DEFAULT '',
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            camera_type     TEXT    NOT NULL DEFAULT '',
            status          TEXT    NOT NULL DEFAULT '',
            obstruction     TEXT    NOT NULL DEFAULT '',
            alignment       TEXT    NOT NULL DEFAULT '',
            quality         TEXT    NOT NULL DEFAULT '',
            summary         TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS parking_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            vehicle_id      TEXT    NOT NULL DEFAULT '',
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            company_code    TEXT    NOT NULL DEFAULT '',
            latitude        REAL    NOT NULL DEFAULT 0,
            longitude       REAL    NOT NULL DEFAULT 0,
            address         TEXT    NOT NULL DEFAULT '',
            first_stopped   TEXT    NOT NULL DEFAULT '',
            duration_hours  REAL    NOT NULL DEFAULT 0,
            location_class  TEXT    NOT NULL DEFAULT 'unknown',
            alert_level     TEXT    NOT NULL DEFAULT 'none',
            ai_analysis     TEXT    NOT NULL DEFAULT '',
            map_image_path  TEXT    NOT NULL DEFAULT '',
            resolved        INTEGER NOT NULL DEFAULT 0,
            last_checked    TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS work_hours (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL,
            label       TEXT    NOT NULL DEFAULT '',
            start_hour  INTEGER NOT NULL DEFAULT 6,
            end_hour    INTEGER NOT NULL DEFAULT 18,
            target_role TEXT    NOT NULL DEFAULT 'all',
            created_by  INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS digest_subscriptions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            frequency   TEXT    NOT NULL DEFAULT 'daily',
            send_hour   INTEGER NOT NULL DEFAULT 7,
            timezone    TEXT    NOT NULL DEFAULT 'UTC',
            report_type TEXT    NOT NULL DEFAULT 'faults',
            is_active   INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL,
            UNIQUE(user_id)
        );

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

        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_companies_account_id
            ON companies(account_id);
        CREATE INDEX IF NOT EXISTS idx_maintenance_account
            ON maintenance_tasks(account_id, status);
        CREATE INDEX IF NOT EXISTS idx_fuel_entries_account
            ON fuel_entries(account_id, vehicle_name);
        CREATE INDEX IF NOT EXISTS idx_alert_ack_pending
            ON alert_acknowledgments(acknowledged_at, status);
        CREATE INDEX IF NOT EXISTS idx_alert_history_active
            ON alert_history(account_id, alert_type, vehicle_id, chat_id, status);
        CREATE INDEX IF NOT EXISTS idx_dnd_queue_pending
            ON dnd_alert_queue(telegram_id, delivered);
        CREATE INDEX IF NOT EXISTS idx_audit_account
            ON audit_log(account_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_digest_subs_active
            ON digest_subscriptions(is_active, send_hour);
        CREATE INDEX IF NOT EXISTS idx_camera_checks_account
            ON camera_checks(account_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_parking_events_active
            ON parking_events(account_id, resolved);
        CREATE INDEX IF NOT EXISTS idx_work_hours_account
            ON work_hours(account_id);
        CREATE INDEX IF NOT EXISTS idx_kb_account_cat
            ON knowledge_base(account_id, category);
        CREATE INDEX IF NOT EXISTS idx_kb_pinned
            ON knowledge_base(account_id, pinned);
    """)
    await conn.commit()
