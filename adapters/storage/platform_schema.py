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
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT    NOT NULL,
            slug                TEXT    NOT NULL UNIQUE,
            tier                TEXT    NOT NULL DEFAULT 'free',
            is_active           INTEGER NOT NULL DEFAULT 1,
            bot_token_encrypted TEXT,
            bot_username        TEXT    NOT NULL DEFAULT '',
            webhook_secret      TEXT    NOT NULL DEFAULT '',
            created_at          TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id     INTEGER NOT NULL UNIQUE,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            role            TEXT    NOT NULL DEFAULT 'fleet',
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
            email           TEXT,
            password_hash   TEXT,
            UNIQUE(account_id, email)
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
            created_by      INTEGER NOT NULL DEFAULT 0,
            creator_name    TEXT    NOT NULL DEFAULT '',
            approved        INTEGER NOT NULL DEFAULT 1,
            updated_at      TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kb_account_cat
            ON knowledge_base(account_id, category);
        CREATE INDEX IF NOT EXISTS idx_kb_pinned
            ON knowledge_base(account_id, pinned);
        CREATE INDEX IF NOT EXISTS idx_kb_visibility
            ON knowledge_base(visibility, target_role);
        CREATE INDEX IF NOT EXISTS idx_kb_approved
            ON knowledge_base(approved);

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

        -- ── Billing ───────────────────────────────────────────────
        -- One row per account.  Stripe IDs stored here; provider_data holds
        -- any extra JSON (e.g. Stripe subscription object fields) so we don't
        -- need schema changes when we add Stripe fields.
        CREATE TABLE IF NOT EXISTS subscriptions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id          INTEGER NOT NULL UNIQUE REFERENCES accounts(id),
            tier                TEXT    NOT NULL DEFAULT 'free',
            status              TEXT    NOT NULL DEFAULT 'active',
            -- 'active' | 'trialing' | 'past_due' | 'canceled' | 'unpaid'
            vehicle_count       INTEGER NOT NULL DEFAULT 0,
            -- billable vehicles (synced from Samsara vehicle count)
            base_vehicles       INTEGER NOT NULL DEFAULT 10,
            -- vehicles included in flat monthly fee (no per-vehicle charge)
            monthly_base_usd    INTEGER NOT NULL DEFAULT 0,
            -- flat fee in cents  (starter=4900, pro=9900)
            extra_vehicle_cents INTEGER NOT NULL DEFAULT 0,
            -- per-vehicle cents above base (starter=299, pro=299)
            billing_email       TEXT    NOT NULL DEFAULT '',
            provider            TEXT    NOT NULL DEFAULT 'stub',
            -- 'stub' | 'stripe'
            provider_customer_id  TEXT  NOT NULL DEFAULT '',
            provider_subscription_id TEXT NOT NULL DEFAULT '',
            provider_data       TEXT    NOT NULL DEFAULT '{}',
            -- JSON blob for provider-specific fields
            trial_ends_at       TEXT,
            current_period_start TEXT,
            current_period_end   TEXT,
            canceled_at         TEXT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_subscriptions_account
            ON subscriptions(account_id);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_status
            ON subscriptions(status);

        -- Monthly usage snapshots — one row per account per billing period.
        -- Scheduler writes a snapshot on the 1st of each month (or on demand).
        -- Immutable once written; Stripe metered billing reads from here.
        CREATE TABLE IF NOT EXISTS billing_usage_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            period_start    TEXT    NOT NULL,
            period_end      TEXT    NOT NULL,
            vehicle_count   INTEGER NOT NULL DEFAULT 0,
            user_count      INTEGER NOT NULL DEFAULT 0,
            ai_queries      INTEGER NOT NULL DEFAULT 0,
            extra_vehicles  INTEGER NOT NULL DEFAULT 0,
            -- vehicles above base_vehicles for that period
            amount_due_cents INTEGER NOT NULL DEFAULT 0,
            -- total for the period (base + extras) in cents
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(account_id, period_start)
        );
        CREATE INDEX IF NOT EXISTS idx_billing_snapshots_account
            ON billing_usage_snapshots(account_id, period_start);
    """)
    await conn.commit()
