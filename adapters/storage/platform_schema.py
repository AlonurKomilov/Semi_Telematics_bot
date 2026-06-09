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
            payroll_enabled     INTEGER NOT NULL DEFAULT 0,
            coaching_enabled    INTEGER NOT NULL DEFAULT 0,
            timezone            TEXT    NOT NULL DEFAULT 'America/New_York',
            alert_routing_mode  TEXT    NOT NULL DEFAULT 'single_group',
            created_at          TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id     BIGINT  NOT NULL UNIQUE,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            role            TEXT    NOT NULL DEFAULT 'fleet',
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
            dnd_enabled     INTEGER NOT NULL DEFAULT 1,
            assigned_work_hours_id INTEGER,
            timezone        TEXT    NOT NULL DEFAULT 'America/New_York',
            language        TEXT    NOT NULL DEFAULT 'en',
            last_shift_report TEXT,
            email           TEXT,
            password_hash   TEXT,
            samsara_driver_id TEXT,
            last_seen       TEXT,
            UNIQUE(account_id, email)
        );

        CREATE TABLE IF NOT EXISTS invites (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT    NOT NULL UNIQUE,
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            role        TEXT    NOT NULL DEFAULT 'fleet',
            truck_num   TEXT,
            created_by  INTEGER NOT NULL REFERENCES users(id),
            expires_at  TEXT    NOT NULL,
            used_by     INTEGER REFERENCES users(id),
            created_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS authorized_chats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            chat_id     BIGINT  NOT NULL,
            chat_title  TEXT    NOT NULL DEFAULT '',
            added_by    INTEGER NOT NULL REFERENCES users(id),
            is_active   INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL,
            UNIQUE(account_id, chat_id)
        );

        CREATE TABLE IF NOT EXISTS ai_usage (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id         INTEGER NOT NULL REFERENCES accounts(id),
            user_id            BIGINT  NOT NULL DEFAULT 0,
            model              TEXT    NOT NULL DEFAULT '',
            request_type       TEXT    NOT NULL DEFAULT '',
            prompt_tokens      INTEGER NOT NULL DEFAULT 0,
            reply_tokens       INTEGER NOT NULL DEFAULT 0,
            thinking_tokens    INTEGER NOT NULL DEFAULT 0,
            total_tokens       INTEGER NOT NULL DEFAULT 0,
            -- Router telemetry — nullable so legacy rows stay valid.
            latency_ms         INTEGER,
            error_type         TEXT,
            tool_success_count INTEGER,
            role               TEXT,
            -- Heuristic sub-class of free-form ``question`` prompts:
            -- lookup / analysis / comparison / summary /
            -- troubleshooting / other.  Lets the router pick the
            -- model that's best for *this kind* of question.
            prompt_category    TEXT,
            -- Implicit dissatisfaction: TRUE when the user re-asked
            -- within ~30 s of seeing this row's response.  Scorer
            -- treats (1 - reask_rate) as a satisfaction component.
            had_reask          BOOLEAN NOT NULL DEFAULT FALSE,
            -- Explicit positive: TRUE when the user clicked the
            -- thumbs-up icon on this response.  The only upweighting
            -- signal in the scoring stack.
            had_thumbs_up      BOOLEAN NOT NULL DEFAULT FALSE,
            -- Optional follow-up after thumbs-down: WHY the user
            -- disliked the answer.  Categories: inaccurate /
            -- off_topic / incomplete / hallucinated / vague /
            -- unjust_refusal / other.  NULL when the user skipped
            -- the form.  ``had_reask`` still flips on bare
            -- thumbs-down — these columns are supplementary.
            feedback_reason    TEXT,
            feedback_note      TEXT,
            created_at         TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_users_telegram_id
            ON users(telegram_id);
        CREATE INDEX IF NOT EXISTS idx_invites_code
            ON invites(code);
        CREATE INDEX IF NOT EXISTS idx_authorized_chats_chat_id
            ON authorized_chats(chat_id);
        CREATE INDEX IF NOT EXISTS idx_ai_usage_account
            ON ai_usage(account_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_ai_usage_router
            ON ai_usage(account_id, model, created_at);
        -- idx_ai_usage_category lives in the migration only — on
        -- existing installs the prompt_category column doesn't exist
        -- until the ALTER TABLE runs, so creating the index here would
        -- fail before the migration has a chance to add the column.

        CREATE TABLE IF NOT EXISTS ai_chat_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            user_id    BIGINT  NOT NULL,
            role       TEXT    NOT NULL,
            text       TEXT    NOT NULL,
            created_at TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ai_chat_history_user
            ON ai_chat_history(account_id, user_id, created_at);

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
            pinned           INTEGER NOT NULL DEFAULT 0,
            created_by       BIGINT  NOT NULL DEFAULT 0,
            creator_name     TEXT    NOT NULL DEFAULT '',
            approved         INTEGER NOT NULL DEFAULT 1,
            view_count       INTEGER NOT NULL DEFAULT 0,
            helpful_count    INTEGER NOT NULL DEFAULT 0,
            unhelpful_count  INTEGER NOT NULL DEFAULT 0,
            last_viewed_at   TEXT    NOT NULL DEFAULT '',
            updated_at       TEXT    NOT NULL DEFAULT '',
            created_at       TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS knowledge_feedback (
            article_id INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            helpful    INTEGER NOT NULL,
            created_at TEXT    NOT NULL,
            PRIMARY KEY (article_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS knowledge_bookmarks (
            article_id INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            created_at TEXT    NOT NULL,
            PRIMARY KEY (article_id, user_id)
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
            updated_by      BIGINT  NOT NULL DEFAULT 0,
            updated_at      TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, role, company_id)
        );
        CREATE INDEX IF NOT EXISTS idx_role_perms_account
            ON role_permissions(account_id);
        CREATE INDEX IF NOT EXISTS idx_role_perms_lookup
            ON role_permissions(account_id, role, company_id);

        CREATE TABLE IF NOT EXISTS role_ai_guidance (
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            role        TEXT    NOT NULL,
            guidance    TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL DEFAULT '',
            PRIMARY KEY (account_id, role)
        );
        CREATE INDEX IF NOT EXISTS idx_role_ai_guidance_account
            ON role_ai_guidance(account_id);

        CREATE TABLE IF NOT EXISTS driver_trucks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            truck_num   TEXT    NOT NULL,
            is_primary  INTEGER NOT NULL DEFAULT 0,
            assigned_by BIGINT  NOT NULL DEFAULT 0,
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
            assigned_by BIGINT  NOT NULL DEFAULT 0,
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
            -- Stripe subscription_item ids for the two-line plan
            -- (``base`` tier price + ``extras`` per-vehicle price).
            -- ``sync_billing_quantity`` patches the extras item when
            -- the active-vehicle count changes; the base item never
            -- changes quantity.  Empty for stub-provider accounts.
            provider_base_item_id  TEXT NOT NULL DEFAULT '',
            provider_extra_item_id TEXT NOT NULL DEFAULT '',
            provider_data       TEXT    NOT NULL DEFAULT '{}',
            -- JSON blob for provider-specific fields
            trial_ends_at       TEXT,
            current_period_start TEXT,
            current_period_end   TEXT,
            canceled_at         TEXT,
            -- Set when status flips to ``past_due``; cleared when we
            -- recover to active.  Used by enforcement to compute
            -- whether grace has elapsed.
            past_due_since      TEXT,
            -- Comp ("100% Special Discount") flag — see comp_account_history
            -- for the audit trail.  ``comp_expires_at`` is REQUIRED whenever
            -- ``is_comped = 1``; the API refuses to grant comp without it.
            is_comped           INTEGER NOT NULL DEFAULT 0,
            comp_expires_at     TEXT,
            comp_reason         TEXT    NOT NULL DEFAULT '',
            comp_granted_by     INTEGER,
            comp_granted_at     TEXT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_subscriptions_account
            ON subscriptions(account_id);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_status
            ON subscriptions(status);
        -- NOTE: ``idx_subscriptions_comp_expires`` (on the new
        -- ``is_comped`` + ``comp_expires_at`` columns) is created by
        -- ``migrate_subscription_comp_columns`` instead of here.
        -- ``CREATE TABLE IF NOT EXISTS`` is a no-op on upgrade, so the
        -- comp columns don't actually exist until the ALTER TABLE
        -- migration runs.  Putting the index here used to crash boot
        -- with ``UndefinedColumnError: is_comped`` on any pre-existing
        -- deployment.

        -- Comp account audit log.  Every grant / renew / revoke /
        -- expire writes a row so we can answer "when did account X
        -- become comped, who approved it, why, and how many times has
        -- it been renewed?" months later.  Append-only.
        CREATE TABLE IF NOT EXISTS comp_account_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            action      TEXT    NOT NULL,
            -- 'granted' | 'renewed' | 'revoked' | 'expired'
            expires_at  TEXT,
            reason      TEXT    NOT NULL DEFAULT '',
            actor_user_id INTEGER,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_comp_history_account
            ON comp_account_history(account_id, created_at DESC);

        -- Monthly usage snapshots — one row per account per billing period.
        -- Scheduler writes a snapshot on the 1st of each month (or on demand).
        -- Immutable once written; Stripe metered billing reads from here.
        CREATE TABLE IF NOT EXISTS billing_usage_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            period_start    TEXT    NOT NULL,
            period_end      TEXT    NOT NULL,
            -- Raw Samsara fleet count at snapshot time.  Kept for
            -- backwards compatibility with legacy reports; the new
            -- ``active_vehicles`` / ``inactive_vehicles`` pair is what
            -- the bill is actually computed from.
            vehicle_count   INTEGER NOT NULL DEFAULT 0,
            active_vehicles    INTEGER NOT NULL DEFAULT 0,
            inactive_vehicles  INTEGER NOT NULL DEFAULT 0,
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

        -- Webhook idempotency.  Stripe retries any non-2xx response (and
        -- some 2xx if it doesn't receive the ack in time), so the same
        -- event.id can arrive several times.  We INSERT-OR-IGNORE on
        -- this table before processing — duplicates short-circuit with
        -- a fast 200 and never re-mutate state.  TTL pruning is fine
        -- (Stripe doesn't retry after ~3 days), but we keep all rows
        -- for now so the audit trail of every webhook we received is
        -- intact — adds <1MB/year for a typical small fleet.
        CREATE TABLE IF NOT EXISTS processed_stripe_events (
            event_id      TEXT    PRIMARY KEY,
            event_type    TEXT    NOT NULL,
            processed_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            account_id    INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_processed_stripe_events_processed_at
            ON processed_stripe_events(processed_at);

        -- Persisted invoice records.  Stripe is authoritative for the
        -- numbers (we never compute these client-side), but mirroring
        -- them locally lets the dashboard list invoices without an API
        -- round-trip, and lets us correlate a payment retry / failure
        -- with the snapshot it belongs to.  ``provider_invoice_id`` is
        -- UNIQUE so the webhook handler can INSERT-OR-IGNORE on retry.
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
            -- 'paid', 'open', 'uncollectible', 'void' — mirror of Stripe.
            period_start              TEXT,
            period_end                TEXT,
            hosted_invoice_url        TEXT    NOT NULL DEFAULT '',
            invoice_pdf_url           TEXT    NOT NULL DEFAULT '',
            paid_at                   TEXT,
            created_at                TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_billing_invoices_account
            ON billing_invoices(account_id, created_at DESC);

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

        CREATE TABLE IF NOT EXISTS account_persona_groups (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL,
            persona     TEXT    NOT NULL,
            chat_id     BIGINT  NOT NULL,
            chat_title  TEXT    NOT NULL DEFAULT '',
            is_active   INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL,
            UNIQUE(account_id, persona)
        );
        CREATE INDEX IF NOT EXISTS idx_account_persona_groups_account
            ON account_persona_groups(account_id);
    """)
    await conn.commit()
