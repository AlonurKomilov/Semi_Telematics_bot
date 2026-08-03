# Warehouse schema move — maintenance-window runbook

Owner-approved 2026-08-03 (advisor pre-flight passed). Moves the 13
warehouse tables + the `vehicle_timeline` view into a dedicated
`warehouse` schema and normalizes 4 names. One window, ~5 minutes,
full stop of the app. The deployed code (search_path + swept names +
migration 183) must already be on the box BEFORE the window.

## Rename map

Same name, new schema: vehicle_state, vehicle_state_snapshot,
vehicle_telemetry, vehicle_health_snapshot, vehicle_fault_snapshot,
vehicle_fault_detail, safety_event_log, geofence_definitions,
ingest_runs, vehicle_timeline (view).

Renamed: driver_efficiency_daily → driver_efficiency ·
aggregate_weather_snapshot → weather_snapshot ·
aggregate_efficiency_snapshot → efficiency_snapshot ·
warehouse_ingest_orphans → ingest_orphans.

## Window procedure

**1. Stop everything** (owner): bot + API + workers fully down — the
1-minute ingest must not write mid-move. Verify nothing is connected:

```sql
SELECT count(*) FROM pg_stat_activity
WHERE datname = current_database() AND pid <> pg_backend_pid();
```

**2. Snapshot** (rollback insurance; schema only, seconds):

```bash
pg_dump "$DATABASE_URL" --schema-only -f /tmp/pre_schema_move_$(date +%s).sql
```

**3. Operator SQL** (as a superuser/owner role, one psql session):

```sql
BEGIN;
CREATE SCHEMA IF NOT EXISTS warehouse;
-- every role that queries these tables needs USAGE; adjust role names
-- to what \du shows (app role + any BYPASSRLS admin/readonly role):
GRANT USAGE ON SCHEMA warehouse TO PUBLIC;

ALTER TABLE public.vehicle_state                 SET SCHEMA warehouse;
ALTER TABLE public.vehicle_state_snapshot        SET SCHEMA warehouse;
ALTER TABLE public.vehicle_telemetry             SET SCHEMA warehouse;
ALTER TABLE public.vehicle_health_snapshot       SET SCHEMA warehouse;
ALTER TABLE public.vehicle_fault_snapshot        SET SCHEMA warehouse;
ALTER TABLE public.vehicle_fault_detail          SET SCHEMA warehouse;
ALTER TABLE public.safety_event_log              SET SCHEMA warehouse;
ALTER TABLE public.geofence_definitions          SET SCHEMA warehouse;
ALTER TABLE public.ingest_runs                   SET SCHEMA warehouse;
ALTER TABLE public.driver_efficiency_daily       SET SCHEMA warehouse;
ALTER TABLE public.aggregate_weather_snapshot    SET SCHEMA warehouse;
ALTER TABLE public.aggregate_efficiency_snapshot SET SCHEMA warehouse;
ALTER TABLE public.warehouse_ingest_orphans      SET SCHEMA warehouse;
ALTER VIEW  public.vehicle_timeline              SET SCHEMA warehouse;

ALTER TABLE warehouse.driver_efficiency_daily       RENAME TO driver_efficiency;
ALTER TABLE warehouse.aggregate_weather_snapshot    RENAME TO weather_snapshot;
ALTER TABLE warehouse.aggregate_efficiency_snapshot RENAME TO efficiency_snapshot;
ALTER TABLE warehouse.warehouse_ingest_orphans      RENAME TO ingest_orphans;
COMMIT;
```

Indexes, sequences, and RLS policies travel with each table
automatically — nothing else to move.

**4. Verify** (same session):

```sql
-- all 14 objects present in warehouse:
SELECT n, to_regclass('warehouse.' || n) IS NOT NULL AS ok
FROM unnest(ARRAY['vehicle_state','vehicle_state_snapshot',
 'vehicle_telemetry','vehicle_health_snapshot','vehicle_fault_snapshot',
 'vehicle_fault_detail','safety_event_log','geofence_definitions',
 'ingest_runs','driver_efficiency','weather_snapshot',
 'efficiency_snapshot','ingest_orphans','vehicle_timeline']) AS n;
-- RLS policies came along (compare to the pre-move count):
SELECT count(*) FROM pg_policies WHERE schemaname = 'warehouse';
-- the view still answers:
SELECT 1 FROM warehouse.vehicle_timeline LIMIT 1;
-- billing's activity source still answers:
SELECT count(*) FROM warehouse.vehicle_state;
```

**5. Start the app** on the deployed code (owner). Boot runs migration
183, whose `to_regclass` guards see everything already moved → no-op,
records applied.

**6. Watch one cycle**: minute snapshot lands, ingest watchdog quiet,
live map + billing page load. Then re-run
`python3 -m scripts.backfill_registry_id --account 10000001`
(clears identity-NULL tier rows written since the cascade fix).

**7. Backup check** (before closing the window): if any backup job
uses `pg_dump -n public`, add `-n warehouse` — otherwise the warehouse
silently stops being backed up.

## Rollback

Reverse the SQL (same statements, schemas/names swapped) and restart
on the previous commit. Renames/moves are metadata-only — no data is
copied in either direction.

## Do NOT

- Run the sweep/deploy and the SQL in the other order (old code +
  moved tables = every warehouse query fails).
- Edit migrations 177–182 (they legitimately create the OLD names on
  fresh installs; 183 converges them).
- Move anything else (Datatruck ELT, parking, inventory stay public).
