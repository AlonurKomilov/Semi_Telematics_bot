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

    # Migration: rename the recruiting tables to the feature name
    # (applications).  The feature/code/API/UI are all "applications";
    # these 3 physical tables kept the legacy "recruitment_*" names.
    # Postgres-aware (information_schema — the organizations rename above
    # uses sqlite_master, which is a no-op on Postgres).  Guarded so it
    # only fires when the old table exists and the new one does not, so it
    # never collides with the CREATE TABLE IF NOT EXISTS below.
    for _old, _new in (
        ("recruitment_links", "application_links"),
        ("recruitment_notifications", "application_notifications"),
        ("recruitment_notify_prefs", "application_notify_prefs"),
    ):
        try:
            has_old = await (await conn.execute(
                f"SELECT 1 FROM information_schema.tables WHERE table_name = '{_old}'"
            )).fetchone()
            if not has_old:
                continue  # already migrated (or fresh DB) — nothing to rename
            has_new = await (await conn.execute(
                f"SELECT 1 FROM information_schema.tables WHERE table_name = '{_new}'"
            )).fetchone()
            if has_new:
                # Orphan recovery: a CREATE-before-rename race on deploy can
                # leave an EMPTY new table beside the data-bearing old one.
                # Drop it ONLY if empty, then rename the real table in; if it
                # has rows, leave both and warn (needs a human merge).
                row = await (await conn.execute(f"SELECT COUNT(*) FROM {_new}")).fetchone()
                new_rows = (row[0] if row else 0) or 0
                if new_rows:
                    logger.warning(
                        "Both %s and non-empty %s exist — skipping rename "
                        "(manual merge required)", _old, _new)
                    continue
                await conn.execute(f"DROP TABLE {_new}")
            await conn.execute(f"ALTER TABLE {_old} RENAME TO {_new}")
            await conn.commit()
            logger.info("Renamed table %s → %s", _old, _new)
        except Exception as e:
            logger.debug(f"table rename check {_old}: {e}")

    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            name                 TEXT    NOT NULL,
            slug                 TEXT    NOT NULL UNIQUE,
            tier                 TEXT    NOT NULL DEFAULT 'free',
            is_active            INTEGER NOT NULL DEFAULT 1,
            timezone             TEXT    NOT NULL DEFAULT 'America/New_York',
            suspended_at         TEXT,
            suspended_reason     TEXT,
            suspended_by         BIGINT,
            deleted_at           TEXT,
            delete_requested_by  BIGINT,
            purge_at             TEXT,
            created_at           TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS companies (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            code            TEXT    NOT NULL,
            display_name    TEXT    NOT NULL DEFAULT '',
            samsara_api_key TEXT    NOT NULL,
            active_days     INTEGER NOT NULL DEFAULT 30,
            mc_number       TEXT    NOT NULL DEFAULT '',
            usdot_number    TEXT    NOT NULL DEFAULT '',
            is_active       INTEGER NOT NULL DEFAULT 1,
            logo_object_id  TEXT,
            brand_color     TEXT    NOT NULL DEFAULT '',
            website         TEXT    NOT NULL DEFAULT '',
            phone           TEXT    NOT NULL DEFAULT '',
            headline        TEXT    NOT NULL DEFAULT '',
            perks           TEXT    NOT NULL DEFAULT '',
            banner_object_id TEXT,
            req_experience_years INTEGER NOT NULL DEFAULT 1,
            req_min_age          INTEGER NOT NULL DEFAULT 21,
            req_cdl_class        TEXT    NOT NULL DEFAULT 'A',
            form_theme           TEXT    NOT NULL DEFAULT 'light',
            surface_color        TEXT    NOT NULL DEFAULT '',
            header_color         TEXT    NOT NULL DEFAULT '',
            bg_color             TEXT    NOT NULL DEFAULT '',
            heading_color        TEXT    NOT NULL DEFAULT '',
            legal_address        TEXT    NOT NULL DEFAULT '',
            compliance_email     TEXT    NOT NULL DEFAULT '',
            cra_name             TEXT    NOT NULL DEFAULT '',
            cra_address          TEXT    NOT NULL DEFAULT '',
            cra_phone            TEXT    NOT NULL DEFAULT '',
            cra_site             TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL,
            UNIQUE(account_id, code)
        );

        CREATE TABLE IF NOT EXISTS carrier_profile (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id          INTEGER NOT NULL REFERENCES accounts(id),
            name                TEXT    NOT NULL,
            website             TEXT    NOT NULL DEFAULT '',
            video_url           TEXT    NOT NULL DEFAULT '',
            experience_summary  TEXT    NOT NULL DEFAULT '',
            content             TEXT    NOT NULL DEFAULT '{}',
            created_by          INTEGER NOT NULL DEFAULT 0,
            created_at          TEXT    NOT NULL,
            updated_at          TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_carrier_profile_account
            ON carrier_profile(account_id);

        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id BIGINT  NOT NULL UNIQUE,
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            role        TEXT    NOT NULL DEFAULT 'fleet',
            truck_num   TEXT,
            alerts_on   INTEGER NOT NULL DEFAULT 0,
            is_active   INTEGER NOT NULL DEFAULT 1,
            -- Manager tier: a per-user seniority layered on the base role
            -- (see capabilities/permissions/roles.MANAGER_GRANTS).  0 =
            -- employee, 1 = manager of their role.
            is_manager  INTEGER NOT NULL DEFAULT 0,
            -- Primary (main) owner of the account — the one un-demotable
            -- owner who alone can create/remove co-owners and do destructive
            -- account actions.  Set for the account creator; co-owners have
            -- role='owner' but is_primary_owner=0.
            is_primary_owner INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL
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
            created_at  TEXT    NOT NULL,
            -- NULL = active, populated = revoked-at this instant.
            -- See migration 087.  Filter ``WHERE revoked_at IS NULL``
            -- in get_invite / list_invites / redeem_invite to keep
            -- revoked codes out of every redemption surface.
            revoked_at  TEXT,
            -- Email-channel columns (migration 088).  See the
            -- migration docstring for the lifecycle invariants:
            --   sent_to_email NULL → link-channel invite.
            --   sent_to_email NOT NULL + email_sent_at IS NULL → SMTP
            --     accepted but the relay refused (operator sees
            --     "Created but email failed").
            --   sent_to_email is encrypted at rest via infra.crypto
            --     when ENCRYPTION_KEY is set.
            sent_to_email     TEXT,
            email_sent_at     TEXT,
            email_send_count  INTEGER NOT NULL DEFAULT 0,
            -- Bounce / complaint state (migration 097).
            -- ``resend_email_id``: Resend HTTP API per-send identifier
            --   used as primary lookup key in the bounce-webhook
            --   handler; refusing recipient-fallback closes cross-
            --   account hijack.
            -- ``email_bounced_at`` flips on hard bounce, soft-cap
            --   reached, OR set+cleared by a subsequent delivered.
            -- ``email_bounce_reason`` is encrypted-at-rest (relays
            --   echo recipient address verbatim in failure text).
            -- ``email_bounce_type`` 'hard' | 'soft' | 'complaint'.
            -- ``email_soft_bounce_count`` defers the badge until
            --   >=3 soft bounces — most clear self in hours.
            -- ``email_complained_at`` distinct from bounce: operator
            --   UX flags but doesn't auto-revoke (recipient one-
            --   click is silent + destructive).
            resend_email_id          TEXT,
            email_bounced_at         TEXT,
            email_bounce_reason      TEXT,
            email_bounce_type        TEXT,
            email_soft_bounce_count  INTEGER NOT NULL DEFAULT 0,
            email_complained_at      TEXT
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
            last_odometer   REAL,
            status          TEXT    NOT NULL DEFAULT 'pending',
            created_by      BIGINT  NOT NULL,
            created_at      TEXT    NOT NULL,
            updated_at      TEXT,
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
            created_by      BIGINT  NOT NULL,
            created_at      TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS digest_subscriptions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            frequency   TEXT    NOT NULL DEFAULT 'daily',
            send_hour   INTEGER NOT NULL DEFAULT 7,
            timezone    TEXT    NOT NULL DEFAULT 'UTC',
            report_type TEXT    NOT NULL DEFAULT 'faults',
            -- Comma-separated list of delivery channels: 'telegram',
            -- 'email', or both ('telegram,email').  Default preserves
            -- pre-2026-06 behaviour for existing subscribers.  Email
            -- delivery additionally requires users.email_verified=1.
            delivery_channels TEXT NOT NULL DEFAULT 'telegram',
            is_active   INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL,
            -- Multi-schedule: one row per (user, report_type) so a user
            -- can stack Faults Daily + Fuel Weekly + Health Monthly
            -- independently.  Pre-2026-06 the constraint was
            -- UNIQUE(user_id) which capped each user at one row.
            UNIQUE(user_id, report_type)
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

        -- Per-account custom maintenance task types (operator-defined
        -- dropdown options for the maintenance scheduler).  Queried by
        -- adapters/storage/maintenance.py; UNIQUE(account_id, value)
        -- backs the idempotent upsert (ON CONFLICT (account_id, value)).
        CREATE TABLE IF NOT EXISTS maintenance_custom_task_types (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            value       TEXT    NOT NULL,
            label       TEXT    NOT NULL,
            created_by  INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL DEFAULT '',
            updated_at  TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, value)
        );
        CREATE INDEX IF NOT EXISTS idx_maint_custom_types_account
            ON maintenance_custom_task_types(account_id);

        -- account_settings: per-account key/value store for feature flags,
        -- AI model preferences, pillar caps, etc.  Isolation is enforced
        -- by the account_id predicate on every read/write (same pattern
        -- as the rest of the tenant tables).
        CREATE TABLE IF NOT EXISTS account_settings (
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            key         TEXT    NOT NULL,
            value       TEXT    NOT NULL DEFAULT '',
            updated_at  TEXT    NOT NULL,
            PRIMARY KEY (account_id, key)
        );

        -- user_preferences: per-user opaque KV for UI state (DataTable
        -- layouts, last-used filters, etc.).  Lives here instead of
        -- localStorage so an operator's column hides / order / pinning
        -- follow them across devices.  Values are TEXT — the frontend
        -- serialises JSON in/out.  Keys are opaque (e.g.
        -- "table.maintenance-tasks.visibility") so the backend doesn't
        -- need to learn every UI shape.
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            key         TEXT    NOT NULL,
            value       TEXT    NOT NULL DEFAULT '',
            updated_at  TEXT    NOT NULL,
            PRIMARY KEY (user_id, key)
        );

        CREATE TABLE IF NOT EXISTS alert_acknowledgments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            alert_type      TEXT    NOT NULL DEFAULT 'fault',
            vehicle_id      TEXT    NOT NULL DEFAULT '',
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            alert_key       TEXT    NOT NULL DEFAULT '',
            message_id      BIGINT  NOT NULL DEFAULT 0,
            chat_id         BIGINT  NOT NULL DEFAULT 0,
            sent_to         BIGINT  NOT NULL DEFAULT 0,
            acknowledged_by BIGINT,
            acknowledged_at TEXT,
            status          TEXT    NOT NULL DEFAULT 'active',
            created_at      TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS platform_audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event       TEXT    NOT NULL,
            account_id  INTEGER,
            actor       TEXT    NOT NULL DEFAULT '',
            details     TEXT    NOT NULL DEFAULT '',
            created_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS account_deletion_codes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id   INTEGER NOT NULL UNIQUE REFERENCES accounts(id),
            user_id      INTEGER NOT NULL,
            code_hash    TEXT    NOT NULL,
            expires_at   TEXT    NOT NULL,
            created_at   TEXT    NOT NULL
        );

        -- Pending co-owner promotion (email-code half of the two-factor
        -- confirm).  One pending promotion per account (UNIQUE); binds the
        -- primary owner who initiated it to the target being promoted.
        CREATE TABLE IF NOT EXISTS owner_promotion_codes (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id        INTEGER NOT NULL UNIQUE REFERENCES accounts(id),
            initiator_user_id INTEGER NOT NULL,
            target_user_id    INTEGER NOT NULL,
            code_hash         TEXT    NOT NULL,
            expires_at        TEXT    NOT NULL,
            created_at        TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS application_links (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            token       TEXT    NOT NULL UNIQUE,
            label       TEXT    NOT NULL DEFAULT '',
            source      TEXT    NOT NULL DEFAULT '',
            created_by  INTEGER REFERENCES users(id),
            is_active   INTEGER NOT NULL DEFAULT 1,
            view_count  INTEGER NOT NULL DEFAULT 0,
            company_id  INTEGER REFERENCES companies(id),
            created_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS driver_applications (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id        INTEGER NOT NULL REFERENCES accounts(id),
            link_token        TEXT    NOT NULL,
            reference         TEXT    NOT NULL,
            status            TEXT    NOT NULL DEFAULT 'submitted',
            first_name        TEXT    NOT NULL DEFAULT '',
            last_name         TEXT    NOT NULL DEFAULT '',
            email             TEXT    NOT NULL DEFAULT '',
            phone             TEXT    NOT NULL DEFAULT '',
            city              TEXT    NOT NULL DEFAULT '',
            state             TEXT    NOT NULL DEFAULT '',
            cdl_state         TEXT    NOT NULL DEFAULT '',
            cdl_class         TEXT    NOT NULL DEFAULT '',
            position_type     TEXT    NOT NULL DEFAULT '',
            years_cdl         TEXT    NOT NULL DEFAULT '',
            dob_enc           TEXT,
            ssn_enc           TEXT,
            gate_json             TEXT NOT NULL DEFAULT '{}',
            personal_json         TEXT NOT NULL DEFAULT '{}',
            address_history_json  TEXT NOT NULL DEFAULT '[]',
            cdl_json              TEXT NOT NULL DEFAULT '{}',
            experience_json       TEXT NOT NULL DEFAULT '{}',
            employment_json       TEXT NOT NULL DEFAULT '[]',
            incidents_json        TEXT NOT NULL DEFAULT '{}',
            position_json         TEXT NOT NULL DEFAULT '{}',
            consents_json         TEXT NOT NULL DEFAULT '{}',
            docs_json             TEXT NOT NULL DEFAULT '{}',
            sig_mode          TEXT NOT NULL DEFAULT '',
            sig_name          TEXT NOT NULL DEFAULT '',
            sig_date          TEXT NOT NULL DEFAULT '',
            sig_object_id     TEXT,
            disclosure_version TEXT NOT NULL DEFAULT '',
            vetting_json         TEXT NOT NULL DEFAULT '',
            ssn_hash             TEXT,
            recruiter_notes      TEXT NOT NULL DEFAULT '',
            reviewed_by          INTEGER REFERENCES users(id),
            converted_to_user_id INTEGER REFERENCES users(id),
            submit_ip            TEXT NOT NULL DEFAULT '',
            company_id           INTEGER REFERENCES companies(id),
            submitted_at         TEXT NOT NULL,
            created_at           TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS application_notifications (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            user_id         INTEGER NOT NULL REFERENCES users(id),
            application_id  INTEGER REFERENCES driver_applications(id) ON DELETE CASCADE,
            reference       TEXT    NOT NULL DEFAULT '',
            kind            TEXT    NOT NULL DEFAULT 'application_submitted',
            title           TEXT    NOT NULL DEFAULT '',
            body            TEXT    NOT NULL DEFAULT '',
            is_read         INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_app_notif_inbox
            ON application_notifications(account_id, user_id, is_read, id);

        CREATE TABLE IF NOT EXISTS application_notify_prefs (
            user_id     INTEGER PRIMARY KEY REFERENCES users(id),
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            channels    TEXT    NOT NULL DEFAULT 'telegram,email,dashboard',
            updated_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            user_id     BIGINT,
            action      TEXT    NOT NULL,
            target_type TEXT    NOT NULL DEFAULT '',
            target_id   TEXT    NOT NULL DEFAULT '',
            details     TEXT    NOT NULL DEFAULT '',
            created_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ai_usage (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            user_id         BIGINT  NOT NULL DEFAULT 0,
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
            ON alert_acknowledgments(acknowledged_at, status);
        CREATE INDEX IF NOT EXISTS idx_audit_account
            ON audit_log(account_id, created_at);

        CREATE TABLE IF NOT EXISTS dnd_alert_queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            telegram_id     BIGINT  NOT NULL,
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
            chat_id           BIGINT  NOT NULL,
            message_id        BIGINT  NOT NULL DEFAULT 0,
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
            updated_by      BIGINT  NOT NULL DEFAULT 0,
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

        -- ── Scorecards (composite scoring engine) ──────────
        CREATE TABLE IF NOT EXISTS score_rules (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            rule_id         TEXT    NOT NULL,
            label           TEXT    NOT NULL DEFAULT '',
            category        TEXT    NOT NULL DEFAULT '',
            kind            TEXT    NOT NULL DEFAULT 'penalty',
            points          INTEGER NOT NULL DEFAULT 0,
            cap             INTEGER,
            enabled         INTEGER NOT NULL DEFAULT 1,
            -- pillar tag + per-rule curve anchors.
            -- All nullable so older overrides round-trip unchanged.
            pillar          TEXT    NOT NULL DEFAULT '',
            curve_x_zero    REAL,
            curve_x_max     REAL,
            curve_y_max     INTEGER,
            updated_at      TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, rule_id)
        );
        CREATE INDEX IF NOT EXISTS idx_score_rules_account
            ON score_rules(account_id, enabled);

        CREATE TABLE IF NOT EXISTS score_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            subject_type    TEXT    NOT NULL DEFAULT 'driver',
            subject_id      TEXT    NOT NULL DEFAULT '',
            rule_id         TEXT    NOT NULL DEFAULT '',
            points          INTEGER NOT NULL DEFAULT 0,
            occurred_at     TEXT    NOT NULL DEFAULT '',
            evidence_type   TEXT    NOT NULL DEFAULT '',
            evidence_id     TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_score_events_subject
            ON score_events(account_id, subject_type, subject_id, occurred_at);

        CREATE TABLE IF NOT EXISTS daily_scorecard_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            snapshot_date   TEXT    NOT NULL,
            subject_type    TEXT    NOT NULL DEFAULT 'driver',
            subject_id      TEXT    NOT NULL DEFAULT '',
            subject_name    TEXT    NOT NULL DEFAULT '',
            total_score     INTEGER NOT NULL DEFAULT 0,
            window_days     INTEGER NOT NULL DEFAULT 7,
            breakdown_json  TEXT    NOT NULL DEFAULT '',
            source          TEXT    NOT NULL DEFAULT 'live',
            created_at      TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, snapshot_date, subject_type, subject_id)
        );
        CREATE INDEX IF NOT EXISTS idx_scorecard_snapshots_lookup
            ON daily_scorecard_snapshots(account_id, subject_type, subject_id, snapshot_date);

        -- ── Datatruck TMS sync (adapters/storage/datatruck/) ───────
        -- ELT shape: promoted columns for filtering/joins + raw JSON
        -- payload so un-promoted upstream fields survive locally.
        -- UNIQUE(account_id, external_id) makes sync upserts
        -- idempotent per upstream record.
        CREATE TABLE IF NOT EXISTS datatruck_drivers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            external_id     TEXT    NOT NULL,
            first_name      TEXT    NOT NULL DEFAULT '',
            last_name       TEXT    NOT NULL DEFAULT '',
            display_name    TEXT    NOT NULL DEFAULT '',
            phone           TEXT    NOT NULL DEFAULT '',
            email           TEXT    NOT NULL DEFAULT '',
            status          TEXT    NOT NULL DEFAULT '',
            payload         TEXT    NOT NULL DEFAULT '{}',
            first_seen_at   TEXT    NOT NULL DEFAULT '',
            synced_at       TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, external_id)
        );
        CREATE INDEX IF NOT EXISTS idx_datatruck_drivers_account
            ON datatruck_drivers(account_id, synced_at);

        CREATE TABLE IF NOT EXISTS datatruck_trucks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            external_id     TEXT    NOT NULL,
            unit_number     TEXT    NOT NULL DEFAULT '',
            plate_number    TEXT    NOT NULL DEFAULT '',
            vin             TEXT    NOT NULL DEFAULT '',
            make            TEXT    NOT NULL DEFAULT '',
            model           TEXT    NOT NULL DEFAULT '',
            year            INTEGER,
            status          TEXT    NOT NULL DEFAULT '',
            owner_name      TEXT    NOT NULL DEFAULT '',
            operator_name   TEXT    NOT NULL DEFAULT '',
            odometer        REAL,
            payload         TEXT    NOT NULL DEFAULT '{}',
            first_seen_at   TEXT    NOT NULL DEFAULT '',
            synced_at       TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, external_id)
        );
        CREATE INDEX IF NOT EXISTS idx_datatruck_trucks_account
            ON datatruck_trucks(account_id, synced_at);
        CREATE INDEX IF NOT EXISTS idx_datatruck_trucks_unit
            ON datatruck_trucks(account_id, unit_number);

        CREATE TABLE IF NOT EXISTS datatruck_trailers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            external_id     TEXT    NOT NULL,
            unit_number     TEXT    NOT NULL DEFAULT '',
            plate_number    TEXT    NOT NULL DEFAULT '',
            vin             TEXT    NOT NULL DEFAULT '',
            make            TEXT    NOT NULL DEFAULT '',
            model           TEXT    NOT NULL DEFAULT '',
            year            INTEGER,
            status          TEXT    NOT NULL DEFAULT '',
            payload         TEXT    NOT NULL DEFAULT '{}',
            first_seen_at   TEXT    NOT NULL DEFAULT '',
            synced_at       TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, external_id)
        );
        CREATE INDEX IF NOT EXISTS idx_datatruck_trailers_account
            ON datatruck_trailers(account_id, synced_at);

        CREATE TABLE IF NOT EXISTS datatruck_orders (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id          INTEGER NOT NULL,
            external_id         TEXT    NOT NULL,
            order_number        TEXT    NOT NULL DEFAULT '',
            status              TEXT    NOT NULL DEFAULT '',
            pickup_date         TEXT    NOT NULL DEFAULT '',
            delivery_date       TEXT    NOT NULL DEFAULT '',
            origin              TEXT    NOT NULL DEFAULT '',
            destination         TEXT    NOT NULL DEFAULT '',
            driver_external_id  TEXT    NOT NULL DEFAULT '',
            truck_external_id   TEXT    NOT NULL DEFAULT '',
            total_rate          REAL,
            payload             TEXT    NOT NULL DEFAULT '{}',
            first_seen_at       TEXT    NOT NULL DEFAULT '',
            synced_at           TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, external_id)
        );
        CREATE INDEX IF NOT EXISTS idx_datatruck_orders_account
            ON datatruck_orders(account_id, synced_at);
        CREATE INDEX IF NOT EXISTS idx_datatruck_orders_status
            ON datatruck_orders(account_id, status, pickup_date);

        CREATE TABLE IF NOT EXISTS datatruck_work_orders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            external_id     TEXT    NOT NULL,
            number          TEXT    NOT NULL DEFAULT '',
            status          TEXT    NOT NULL DEFAULT '',
            vehicle_unit    TEXT    NOT NULL DEFAULT '',
            opened_at       TEXT    NOT NULL DEFAULT '',
            closed_at       TEXT    NOT NULL DEFAULT '',
            total_cost      REAL,
            payload         TEXT    NOT NULL DEFAULT '{}',
            first_seen_at   TEXT    NOT NULL DEFAULT '',
            synced_at       TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, external_id)
        );
        CREATE INDEX IF NOT EXISTS idx_datatruck_work_orders_account
            ON datatruck_work_orders(account_id, synced_at);

        -- ── Vehicle registry (adapters/storage/vehicles_registry.py) ──
        -- The single source of truth for vehicles, owned in OUR DB.
        -- Integrations (Samsara live state, Datatruck TMS) ENRICH rows
        -- here; they don't define the fleet.  A vehicle exists because
        -- the account added it (source='manual') or an integration
        -- synced it (source='samsara'|'datatruck').  Trailers and
        -- not-yet-telemetered trucks live here with no live-state match.
        -- UNIQUE(account_id, company_code, unit_number) mirrors
        -- vehicle_state's key so the live-state enrichment join is 1:1.
        CREATE TABLE IF NOT EXISTS vehicles (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            company_code    TEXT    NOT NULL DEFAULT '',
            unit_number     TEXT    NOT NULL,
            vehicle_type    TEXT    NOT NULL DEFAULT 'truck',
            vin             TEXT    NOT NULL DEFAULT '',
            plate_number    TEXT    NOT NULL DEFAULT '',
            make            TEXT    NOT NULL DEFAULT '',
            model           TEXT    NOT NULL DEFAULT '',
            year            INTEGER,
            status          TEXT    NOT NULL DEFAULT 'active',
            source          TEXT    NOT NULL DEFAULT 'manual',
            telematics_ref  TEXT    NOT NULL DEFAULT '',
            notes           TEXT    NOT NULL DEFAULT '',
            is_active       INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT '',
            UNIQUE(account_id, company_code, unit_number)
        );
        CREATE INDEX IF NOT EXISTS idx_vehicles_account
            ON vehicles(account_id, is_active);
        CREATE INDEX IF NOT EXISTS idx_vehicles_type
            ON vehicles(account_id, vehicle_type);
    """)
    await conn.commit()
