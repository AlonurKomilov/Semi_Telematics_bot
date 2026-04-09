"""DDL for platform tables — shared across all tenants.

Platform tables store auth/identity data needed for login,
account management, and system-wide operations.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def create_tables(conn) -> None:
    """Create platform tables and indexes."""

    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            slug        TEXT    NOT NULL UNIQUE,
            tier        TEXT    NOT NULL DEFAULT 'free',
            is_active   INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id     INTEGER NOT NULL UNIQUE,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            role            TEXT    NOT NULL DEFAULT 'fleet_manager',
            department      TEXT    NOT NULL DEFAULT 'general',
            truck_num       TEXT,
            display_name    TEXT    NOT NULL DEFAULT '',
            alerts_on       INTEGER NOT NULL DEFAULT 0,
            is_active       INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT    NOT NULL,
            alert_faults    INTEGER NOT NULL DEFAULT 1,
            alert_health    INTEGER NOT NULL DEFAULT 1,
            alert_fuel      INTEGER NOT NULL DEFAULT 1,
            alert_geofence  INTEGER NOT NULL DEFAULT 1,
            alert_events    INTEGER NOT NULL DEFAULT 1,
            alert_parking   INTEGER NOT NULL DEFAULT 1,
            alert_camera    INTEGER NOT NULL DEFAULT 1,
            ai_fault        INTEGER NOT NULL DEFAULT 0,
            ai_health       INTEGER NOT NULL DEFAULT 0,
            ai_fuel         INTEGER NOT NULL DEFAULT 0,
            ai_events       INTEGER NOT NULL DEFAULT 0,
            ai_parking      INTEGER NOT NULL DEFAULT 0,
            quiet_start     TEXT,
            quiet_end       TEXT,
            timezone        TEXT    NOT NULL DEFAULT 'America/New_York',
            language        TEXT    NOT NULL DEFAULT 'en',
            last_shift_report TEXT,
            email           TEXT    UNIQUE,
            password_hash   TEXT
        );

        CREATE TABLE IF NOT EXISTS invites (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT    NOT NULL UNIQUE,
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            role        TEXT    NOT NULL DEFAULT 'fleet_manager',
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

        CREATE INDEX IF NOT EXISTS idx_users_telegram_id
            ON users(telegram_id);
        CREATE INDEX IF NOT EXISTS idx_invites_code
            ON invites(code);
        CREATE INDEX IF NOT EXISTS idx_authorized_chats_chat_id
            ON authorized_chats(chat_id);
        CREATE INDEX IF NOT EXISTS idx_ai_usage_account
            ON ai_usage(account_id, created_at);
    """)
    await conn.commit()
