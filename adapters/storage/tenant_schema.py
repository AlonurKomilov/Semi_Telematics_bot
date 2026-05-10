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
            created_by          BIGINT  NOT NULL,
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
            created_by      BIGINT  NOT NULL,
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
            message_id       BIGINT  NOT NULL DEFAULT 0,
            chat_id          BIGINT  NOT NULL DEFAULT 0,
            sent_to          BIGINT  NOT NULL DEFAULT 0,
            acknowledged_by  BIGINT,
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
            -- chat_id kept for backwards compat but not used in queries; always 0
            chat_id           BIGINT  NOT NULL DEFAULT 0,
            -- message_id not used; per-subscriber tracking is in alert_acknowledgments
            message_id        BIGINT  NOT NULL DEFAULT 0,
            occurrence_count  INTEGER NOT NULL DEFAULT 1,
            first_seen        TEXT    NOT NULL,
            last_seen         TEXT    NOT NULL,
            last_detail       TEXT    NOT NULL DEFAULT '',
            status            TEXT    NOT NULL DEFAULT 'active',
            -- Severity is the single source of truth across bot / dashboard /
            -- mini-app.  Was previously re-derived client-side from alert_type
            -- (and frequently disagreed with the bot's per-type formatter).
            severity          TEXT    NOT NULL DEFAULT 'warning',
            -- Snapshot of the truck's location at first fire so the
            -- dashboard + mini-app rows can show "📍 Mojave Freeway, CA"
            -- without an extra Samsara round-trip.
            location          TEXT    NOT NULL DEFAULT '',
            -- Re-escalation tracking: how many "UNACKNOWLEDGED" reminders
            -- have been pushed and when the last one was sent.  Used by
            -- re_escalate_critical_alerts to enforce the max-attempts cap
            -- and exponential backoff between reminders.
            reescalate_count        INTEGER NOT NULL DEFAULT 0,
            reescalate_last_sent_at TEXT,
            UNIQUE(account_id, alert_type, vehicle_id)
        );
        CREATE INDEX IF NOT EXISTS idx_alert_history_active_sort
            ON alert_history(account_id, status, severity, last_seen DESC);

        -- Per-alert mute window. While ``muted_until`` is in the future,
        -- pipeline.send_alert skips Telegram delivery for the targeted
        -- alert_history row.  Surfaced via "🔇 Mute 7d" button on the
        -- Telegram alert keyboard / dashboard alert card.
        CREATE TABLE IF NOT EXISTS alert_mutes (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id        INTEGER NOT NULL,
            alert_history_id  INTEGER NOT NULL,
            muted_by          BIGINT  NOT NULL,
            scope             TEXT    NOT NULL DEFAULT 'all_recipients',
            recipient_id      BIGINT,
            reason            TEXT    NOT NULL DEFAULT '',
            muted_until       TEXT    NOT NULL,
            created_at        TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_alert_mutes_active
            ON alert_mutes(account_id, alert_history_id, muted_until);

        CREATE TABLE IF NOT EXISTS dnd_alert_queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            telegram_id     BIGINT  NOT NULL,
            alert_type      TEXT    NOT NULL DEFAULT 'fault',
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            alert_text      TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL,
            delivered       INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL,
            user_id     BIGINT,
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
            created_by  BIGINT  NOT NULL DEFAULT 0,
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
            created_by      BIGINT  NOT NULL DEFAULT 0,
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
        -- Composite for the per-vehicle bulk lookups in send_alert
        -- (get_active_vehicle_acks_bulk, get_info_alert_acks_bulk).
        -- Without this, a fleet-wide alert burst falls back to a scan
        -- of acknowledged_at, ordering tens of thousands of rows.
        CREATE INDEX IF NOT EXISTS idx_alert_ack_acct_veh
            ON alert_acknowledgments(account_id, vehicle_id, status);
        CREATE INDEX IF NOT EXISTS idx_alert_history_active
            ON alert_history(account_id, alert_type, vehicle_id, status);
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

        CREATE INDEX IF NOT EXISTS idx_kb_account_cat
            ON knowledge_base(account_id, category);
        CREATE INDEX IF NOT EXISTS idx_kb_pinned
            ON knowledge_base(account_id, pinned);
        CREATE INDEX IF NOT EXISTS idx_platform_geofences_account
            ON platform_geofences(account_id, is_active);

        -- ── Custom (per-tenant) POI layers ──────────────────────────
        --   Admins can register their own map overlay layers in addition
        --   to the hardcoded built-ins (fuel_station, weigh_station ...).
        --   source_type=overpass: query stored in overpass_query column,
        --                         executed via the same Overpass pipeline.
        --   source_type=csv:      static points stored in custom_poi_points.
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

        -- ── Driver Scorecards (composite scoring engine) ───────────
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
            -- Audit Option C: pillar tag + per-rule curve anchors.
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

        -- ── Phase C: telemetry warehouse ────────────────────────────
        -- Stops Samsara from being a per-request dependency.  Filled by
        -- ``capabilities/telemetry/ingestor.py`` on a scheduler; routes
        -- read from here instead of hitting Samsara live.  Live GPS map
        -- still goes direct \u2014 see plan.md C4.

        -- Current snapshot, one row per vehicle, OVERWRITTEN every cycle.
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
        );
        CREATE INDEX IF NOT EXISTS idx_vehicle_state_company
            ON vehicle_state(account_id, company_code);
        CREATE INDEX IF NOT EXISTS idx_vehicle_state_name
            ON vehicle_state(account_id, vehicle_name);

        -- Append-only event log; idempotent on samsara_event_id.
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
        );
        CREATE INDEX IF NOT EXISTS idx_safety_event_log_occurred
            ON safety_event_log(account_id, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_safety_event_log_vehicle
            ON safety_event_log(account_id, vehicle_id, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_safety_event_log_vehicle_name
            ON safety_event_log(account_id, vehicle_name, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_safety_event_log_driver
            ON safety_event_log(account_id, driver_id, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_safety_event_log_type
            ON safety_event_log(account_id, event_type, occurred_at DESC);

        -- One row per (driver, day).  Upsert on each ingest.
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
        );
        CREATE INDEX IF NOT EXISTS idx_driver_eff_daily_day
            ON driver_efficiency_daily(account_id, day);

        -- Hourly roll-up per vehicle, last 90 days.
        CREATE TABLE IF NOT EXISTS vehicle_telemetry_hourly (
            account_id          INTEGER NOT NULL,
            vehicle_id          TEXT    NOT NULL,
            hour_utc            TEXT    NOT NULL,
            miles               REAL    NOT NULL DEFAULT 0,
            drive_min           REAL    NOT NULL DEFAULT 0,
            idle_min            REAL    NOT NULL DEFAULT 0,
            max_speed_mph       REAL    NOT NULL DEFAULT 0,
            harsh_event_count   INTEGER NOT NULL DEFAULT 0,
            ingested_at         TEXT    NOT NULL DEFAULT '',
            PRIMARY KEY (account_id, vehicle_id, hour_utc)
        );
        CREATE INDEX IF NOT EXISTS idx_vehicle_tel_hourly_hour
            ON vehicle_telemetry_hourly(account_id, hour_utc);

        -- Phase 2 — current vehicle-health snapshot, one row per vehicle,
        -- OVERWRITTEN every cycle (similar to vehicle_state).  Stores the
        -- full enriched per-vehicle dict from ``client.get_vehicle_health()``
        -- as ``raw_json`` so readers return the live shape unchanged.
        CREATE TABLE IF NOT EXISTS vehicle_health_snapshot (
            vehicle_id      TEXT    NOT NULL PRIMARY KEY,
            account_id      INTEGER NOT NULL,
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            company_code    TEXT    NOT NULL DEFAULT '',
            alert_count     INTEGER NOT NULL DEFAULT 0,
            raw_json        TEXT    NOT NULL DEFAULT '',
            captured_at     TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_vehicle_health_company
            ON vehicle_health_snapshot(account_id, company_code);
        CREATE INDEX IF NOT EXISTS idx_vehicle_health_name
            ON vehicle_health_snapshot(account_id, vehicle_name);

        -- Phase 2 — current per-vehicle fault snapshot, one row per
        -- *faulted* vehicle (non-faulted vehicles have no row).
        -- Replaces direct calls to ``client.get_vehicles_with_faults()``
        -- and ``client.get_critical_faults()``.  ``raw_json`` holds the
        -- full live-shape vehicle dict (including ``_dtcs`` / ``_lights``)
        -- so readers return the live shape unchanged.
        CREATE TABLE IF NOT EXISTS vehicle_fault_snapshot (
            vehicle_id      TEXT    NOT NULL PRIMARY KEY,
            account_id      INTEGER NOT NULL,
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            company_code    TEXT    NOT NULL DEFAULT '',
            dtc_count       INTEGER NOT NULL DEFAULT 0,
            has_critical    INTEGER NOT NULL DEFAULT 0,
            raw_json        TEXT    NOT NULL DEFAULT '',
            captured_at     TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_vehicle_fault_company
            ON vehicle_fault_snapshot(account_id, company_code);
        CREATE INDEX IF NOT EXISTS idx_vehicle_fault_critical
            ON vehicle_fault_snapshot(account_id, has_critical);

        -- Per-DTC detail with active/cleared lifecycle.  Phase 3 alerting
        -- replaces its Redis ``_known_faults`` dedup with a query on
        -- ``cleared_at IS NULL``, making the DB the dedup SSOT.
        CREATE TABLE IF NOT EXISTS vehicle_fault_detail (
            account_id      INTEGER NOT NULL,
            vehicle_id      TEXT    NOT NULL,
            dtc_id          TEXT    NOT NULL,
            spn             INTEGER,
            fmi             INTEGER,
            description     TEXT    NOT NULL DEFAULT '',
            severity        TEXT    NOT NULL DEFAULT '',
            observed_at     TEXT    NOT NULL DEFAULT '',
            cleared_at      TEXT,
            raw_json        TEXT    NOT NULL DEFAULT '',
            PRIMARY KEY (account_id, vehicle_id, dtc_id)
        );
        CREATE INDEX IF NOT EXISTS idx_fault_detail_active
            ON vehicle_fault_detail(account_id, vehicle_id, cleared_at);
        CREATE INDEX IF NOT EXISTS idx_fault_detail_observed
            ON vehicle_fault_detail(account_id, observed_at DESC);

        -- Phase 2 — fleet weather (ambient temp / baro), per-vehicle
        -- snapshot OVERWRITTEN every cycle.  Mirrors vehicle_health_snapshot.
        CREATE TABLE IF NOT EXISTS fleet_weather_snapshot (
            vehicle_id      TEXT    NOT NULL PRIMARY KEY,
            account_id      INTEGER NOT NULL,
            vehicle_name    TEXT    NOT NULL DEFAULT '',
            company_code    TEXT    NOT NULL DEFAULT '',
            temp_f          REAL,
            raw_json        TEXT    NOT NULL DEFAULT '',
            captured_at     TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_fleet_weather_company
            ON fleet_weather_snapshot(account_id, company_code);

        -- Phase 2 — combined vehicle+driver efficiency (windowed view).
        -- Stored per-window so the reader can serve the most recent
        -- snapshot for any (days, company) pair without re-fetching.
        CREATE TABLE IF NOT EXISTS fleet_efficiency_snapshot (
            account_id      INTEGER NOT NULL,
            window_days     INTEGER NOT NULL,
            company_code    TEXT    NOT NULL DEFAULT '',
            payload_json    TEXT    NOT NULL DEFAULT '',
            captured_at     TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT '',
            PRIMARY KEY (account_id, window_days, company_code)
        );

        -- Phase 4 — geofence definitions cache (rarely changing).
        -- Snapshot table OVERWRITTEN on each ingest cycle.
        CREATE TABLE IF NOT EXISTS geofence_definitions (
            geofence_id     TEXT    NOT NULL PRIMARY KEY,
            account_id      INTEGER NOT NULL,
            company_code    TEXT    NOT NULL DEFAULT '',
            name            TEXT    NOT NULL DEFAULT '',
            geofence_type   TEXT    NOT NULL DEFAULT '',
            raw_json        TEXT    NOT NULL DEFAULT '',
            captured_at     TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_geofence_definitions_company
            ON geofence_definitions(account_id, company_code);

        -- ── Payroll (Pay-for-Performance) ─────────────────────────
        -- Per-account configurable bonus rules.  ``kind`` selects how
        -- the rule is evaluated:
        --   'score_threshold'  — triggers when driver's avg score over
        --                        ``period_days`` is >= ``score_min``.
        --   'incident_count'   — triggers when count of safety events
        --                        of ``event_type`` over period is
        --                        <= ``max_count``.
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
            created_by      BIGINT  NOT NULL DEFAULT 0,
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

        -- ── Coaching (Auto Coaching) ──────────────────────────────
        -- Per-account topic catalogue.  Seeded with defaults by
        -- ``tenant_migrations.migrate_seed_coaching_topics``.
        CREATE TABLE IF NOT EXISTS coaching_topics (
            account_id       INTEGER NOT NULL,
            key              TEXT    NOT NULL,
            label            TEXT    NOT NULL DEFAULT '',
            default_message  TEXT    NOT NULL DEFAULT '',
            active           INTEGER NOT NULL DEFAULT 1,
            updated_at       TEXT    NOT NULL DEFAULT '',
            PRIMARY KEY (account_id, key)
        );

        -- Coaching rules — score-threshold or incident-count triggers.
        CREATE TABLE IF NOT EXISTS coaching_rules (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            name            TEXT    NOT NULL,
            kind            TEXT    NOT NULL DEFAULT 'score_threshold',
            score_max       REAL,
            event_type      TEXT,
            min_count       INTEGER,
            period_days     INTEGER NOT NULL DEFAULT 7,
            topic_key       TEXT    NOT NULL DEFAULT '',
            severity        TEXT    NOT NULL DEFAULT 'medium',
            message         TEXT    NOT NULL DEFAULT '',
            active          INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT    NOT NULL DEFAULT '',
            updated_at      TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_coaching_rules_account
            ON coaching_rules(account_id, active);

        -- One row per assigned coaching action.  ``rule_id`` is NULL
        -- for manual assignments.  Idempotency: engine refuses to
        -- create another pending row for the same (driver, topic_key)
        -- within ``period_days``.
        CREATE TABLE IF NOT EXISTS coaching_assignments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER NOT NULL,
            driver_id       TEXT    NOT NULL,
            rule_id         INTEGER,
            topic_key       TEXT    NOT NULL DEFAULT '',
            severity        TEXT    NOT NULL DEFAULT 'medium',
            reason          TEXT    NOT NULL DEFAULT '',
            status          TEXT    NOT NULL DEFAULT 'pending',
            assigned_by     BIGINT  NOT NULL DEFAULT 0,
            assigned_at     TEXT    NOT NULL DEFAULT '',
            due_at          TEXT,
            acknowledged_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_coaching_assignments_account
            ON coaching_assignments(account_id, status);
        CREATE INDEX IF NOT EXISTS idx_coaching_assignments_driver
            ON coaching_assignments(account_id, driver_id, status);

        CREATE TABLE IF NOT EXISTS coaching_acknowledgments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_id   INTEGER NOT NULL REFERENCES coaching_assignments(id),
            driver_id       TEXT    NOT NULL,
            acked_at        TEXT    NOT NULL DEFAULT '',
            note            TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_coaching_acks_assignment
            ON coaching_acknowledgments(assignment_id);
    """)
    await conn.commit()
