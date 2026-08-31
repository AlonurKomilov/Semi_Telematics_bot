# Warehouse grain split — maintenance-window runbook

Owner-approved 2026-08-06 (advisor-endorsed). The physical layer takes
the grain names: `vehicle_state → vehicle_state_live`,
`vehicle_state_snapshot → vehicle_state_minute`, and
`vehicle_telemetry` splits into `vehicle_state_hour/_day/_week`
(the `granularity` column dies). The five `vehicle_state_*` surface
VIEWS are dropped — the tables take their exact names, so consumer
SQL keeps working unchanged. Deployed code (migration 188 + swept
machinery) must be on the box BEFORE the window.

Pre-window row counts (2026-08-06): hourly 133,730 · daily 7,014 ·
weekly 588 (total 141,332). Verify against the day's own totals.

## Window (full stop, ~2 minutes)

1. `make stop` — verify zero connections:
```sql
SELECT count(*) FROM pg_stat_activity
WHERE datname = current_database() AND pid <> pg_backend_pid();
```
2. Snapshot: `pg_dump "$DATABASE_URL" --schema-only -f /home/abcdev/backups/pre_grain_split_$(date +%s).sql`
3. Operator SQL — run `psql "$DATABASE_URL" -v ON_ERROR_STOP=1` and paste
   the block below IN ONE transaction:

```sql
BEGIN;
-- capture totals for the verification step
CREATE TEMP TABLE _pre AS
SELECT granularity, COUNT(*) AS n FROM warehouse.vehicle_telemetry GROUP BY 1;

DROP VIEW IF EXISTS warehouse.vehicle_timeline;
DROP VIEW IF EXISTS warehouse.vehicle_state_live;
DROP VIEW IF EXISTS warehouse.vehicle_state_minute;
DROP VIEW IF EXISTS warehouse.vehicle_state_hour;
DROP VIEW IF EXISTS warehouse.vehicle_state_day;
DROP VIEW IF EXISTS warehouse.vehicle_state_week;
DROP VIEW IF EXISTS warehouse.vehicle_health_live;
DROP VIEW IF EXISTS warehouse.vehicle_health_minute;
DROP VIEW IF EXISTS warehouse.vehicle_health_hour;
DROP VIEW IF EXISTS warehouse.vehicle_health_day;
DROP VIEW IF EXISTS warehouse.vehicle_health_week;

ALTER TABLE warehouse.vehicle_state          RENAME TO vehicle_state_live;
ALTER TABLE warehouse.vehicle_state_snapshot RENAME TO vehicle_state_minute;

CREATE TABLE warehouse.vehicle_state_hour
  (LIKE warehouse.vehicle_telemetry INCLUDING DEFAULTS);
ALTER TABLE warehouse.vehicle_state_hour DROP COLUMN granularity;
INSERT INTO warehouse.vehicle_state_hour
  SELECT account_id, vehicle_id, vehicle_name, bucket_start, miles,
         drive_min, idle_min, max_speed_mph, avg_fuel_pct,
         harsh_event_count, fault_count_eod, odometer_eod,
         engine_hours_eod, ingested_at, registry_id, source_ts,
         battery_min_v, battery_avg_v, oil_min_psi, oil_avg_psi,
         coolant_max_c, coolant_avg_c, rpm_avg, engine_load_avg_pct
  FROM warehouse.vehicle_telemetry WHERE granularity = 'hourly';
ALTER TABLE warehouse.vehicle_state_hour
  ADD PRIMARY KEY (account_id, vehicle_id, bucket_start);
CREATE INDEX idx_vehicle_state_hour_bucket
  ON warehouse.vehicle_state_hour (account_id, bucket_start DESC);

CREATE TABLE warehouse.vehicle_state_day
  (LIKE warehouse.vehicle_telemetry INCLUDING DEFAULTS);
ALTER TABLE warehouse.vehicle_state_day DROP COLUMN granularity;
INSERT INTO warehouse.vehicle_state_day
  SELECT account_id, vehicle_id, vehicle_name, bucket_start, miles,
         drive_min, idle_min, max_speed_mph, avg_fuel_pct,
         harsh_event_count, fault_count_eod, odometer_eod,
         engine_hours_eod, ingested_at, registry_id, source_ts,
         battery_min_v, battery_avg_v, oil_min_psi, oil_avg_psi,
         coolant_max_c, coolant_avg_c, rpm_avg, engine_load_avg_pct
  FROM warehouse.vehicle_telemetry WHERE granularity = 'daily';
ALTER TABLE warehouse.vehicle_state_day
  ADD PRIMARY KEY (account_id, vehicle_id, bucket_start);
CREATE INDEX idx_vehicle_state_day_bucket
  ON warehouse.vehicle_state_day (account_id, bucket_start DESC);

CREATE TABLE warehouse.vehicle_state_week
  (LIKE warehouse.vehicle_telemetry INCLUDING DEFAULTS);
ALTER TABLE warehouse.vehicle_state_week DROP COLUMN granularity;
INSERT INTO warehouse.vehicle_state_week
  SELECT account_id, vehicle_id, vehicle_name, bucket_start, miles,
         drive_min, idle_min, max_speed_mph, avg_fuel_pct,
         harsh_event_count, fault_count_eod, odometer_eod,
         engine_hours_eod, ingested_at, registry_id, source_ts,
         battery_min_v, battery_avg_v, oil_min_psi, oil_avg_psi,
         coolant_max_c, coolant_avg_c, rpm_avg, engine_load_avg_pct
  FROM warehouse.vehicle_telemetry WHERE granularity = 'weekly';
ALTER TABLE warehouse.vehicle_state_week
  ADD PRIMARY KEY (account_id, vehicle_id, bucket_start);
CREATE INDEX idx_vehicle_state_week_bucket
  ON warehouse.vehicle_state_week (account_id, bucket_start DESC);

-- VERIFY BEFORE DROP — counts must match or ROLLBACK:
SELECT (SELECT n FROM _pre WHERE granularity='hourly')
         = (SELECT COUNT(*) FROM warehouse.vehicle_state_hour)  AS hour_ok,
       (SELECT n FROM _pre WHERE granularity='daily')
         = (SELECT COUNT(*) FROM warehouse.vehicle_state_day)   AS day_ok,
       (SELECT n FROM _pre WHERE granularity='weekly')
         = (SELECT COUNT(*) FROM warehouse.vehicle_state_week)  AS week_ok;
-- all three must read 't'.  If not: ROLLBACK; investigate.

DROP TABLE warehouse.vehicle_telemetry;
COMMIT;
```

4. Recreate the views — boot ALSO does this via migration 188's
   convergence, so simplest: start the app (`make start`); migration
   188 sees the tables converged and rebuilds
   `vehicle_health_*` + `vehicle_timeline` if missing.
   (Or paste the view DDL from migration 188 by hand first.)
5. Verify after start:
```sql
SELECT to_regclass('warehouse.vehicle_state_hour'),
       to_regclass('warehouse.vehicle_telemetry');   -- table, NULL
SELECT COUNT(*) FROM warehouse.vehicle_timeline WHERE grain='hour';
```
6. Watch one build cycle (minute snapshot + next :05 hour roll).

## If you restarted without the window
Migration 188 converges at boot (WARNING logged, counts verified
before any drop — it aborts rather than lose a row). Snapshot and
verification then happened without you; run step 5 now.

## Rollback
Restore from the schema snapshot + the nightly R2 dump
(docs/runbooks/backups.md). The split is copy-then-drop; the COMMIT
only lands after count verification passes.
