# RLS rollout runbook

Postgres Row-Level Security on every tenant-scoped table.  After the
cutover, the database itself filters every query to
`account_id = current_setting('app.account_id')`; a missing predicate or
unset GUC returns zero rows instead of cross-tenant data.

## Code landed (already merged)

- [adapters/storage/core.py](../../adapters/storage/core.py): `Database.with_account(account_id)` context manager — sets `app.account_id` for the block, restores on exit.
- [adapters/storage/migrations.py](../../adapters/storage/migrations.py) migration **057_enable_rls_tenant_tables** — enables RLS + creates `tenant_isolation` policies on 41 tables.  **Gated by `ENABLE_RLS` env var (default off).**
- [interfaces/api/deps.py](../../interfaces/api/deps.py) `get_tenant_db` — wraps every API request in `with_account(user["account_id"])`.
- [infra/isolation.py](../../infra/isolation.py) `run_account_job(..., tenant_db=...)` — sets `app.account_id` for the duration of each scheduler-job iteration.
- All 13 scheduler-job call sites updated to pass `tenant_db=` (alerting/faults, health, fuel, cameras, events, maintenance×4, auto_reports, driver_expiry, driver_samsara_sync, geofences).
- [scripts/sql/rls_admin_role.sql](../../scripts/sql/rls_admin_role.sql) — creates the `4truck_admin` BYPASSRLS role for future cross-tenant query paths (currently unused but reserved).

## Staged rollout

### Stage 0 — pre-flight (no changes to production)

```bash
# Confirm the migration is present and idempotent (re-running is a no-op).
grep -n '057_enable_rls' adapters/storage/migrations.py

# Confirm ENABLE_RLS is NOT in .env yet (or is set to 0).
grep -E '^ENABLE_RLS=' .env || echo "(not set — default off, good)"
```

### Stage 1 — apply the admin-role grant (one-time, by a DBA)

```bash
# Run as a Postgres superuser (the application's DB user doesn't have CREATEROLE).
sudo -u postgres psql -d 4truck -f scripts/sql/rls_admin_role.sql
```

The script is idempotent.  Creates the `4truck_admin` role (NOLOGIN, BYPASSRLS) and grants it the same table+sequence permissions as the application user.  Re-running it just verifies the role exists.

### Stage 2 — first deploy with RLS enabled but operator BYPASSRLS (no behavior change)

This stage verifies the migration applies cleanly and code paths don't break, with the SAFETY of operator-bypass still on.

```bash
# 1. As DBA, grant BYPASSRLS to the application user temporarily:
sudo -u postgres psql -d 4truck -c "ALTER ROLE \"4truck\" BYPASSRLS;"

# 2. Enable RLS in env and restart so migration 057 runs.
echo 'ENABLE_RLS=1' >> .env
sudo systemctl restart 4truck-api 4truck-bot 4truck-queue

# 3. Watch the logs — migration 057 should say:
#    "Migration 057: RLS enabled on N table(s), skipped 0 (missing/error)"
sudo journalctl -u 4truck-bot -n 100 | grep -E 'Migration 057|RLS'

# 4. Smoke test: hit the dashboard, the miniapp, and several API endpoints.
#    Because the application user still has BYPASSRLS, behavior is
#    unchanged — RLS policies exist but the user bypasses them.
curl -sI https://dash.4truck.us/api/health
curl -sI https://api.4truck.us/v1/health

# 5. Bake 24-48 hours.  Watch error_log for any policy-related surprises.
sudo -u postgres psql -d 4truck -c "
  SELECT COUNT(*) FROM error_log
  WHERE created_at > NOW() - INTERVAL '24 hours'
    AND (details LIKE '%policy%' OR details LIKE '%row-level%');
"
```

### Stage 3 — flip enforcement on (revoke BYPASSRLS from the application user)

This is the moment RLS actually filters queries.  Any code path that forgets to set the GUC will start returning zero rows.

```bash
# 1. Final check: every account has a coherent app.account_id setter
#    on its hot path.  Look for cron jobs / scripts that don't go
#    through run_account_job or get_tenant_db.
grep -rn "tenant\._db\|database\._db" adapters/ capabilities/ interfaces/ infra/ | grep -v "with_account\|run_account_job" | head -20

# 2. Revoke BYPASSRLS from the application user.
sudo -u postgres psql -d 4truck -c "ALTER ROLE \"4truck\" NOBYPASSRLS;"

# 3. Immediately verify with a known account:
sudo -u postgres psql -d 4truck -c "
  SET ROLE \"4truck\";
  SELECT set_config('app.account_id', '1', false);
  SELECT COUNT(*) FROM maintenance_tasks;  -- should equal account 1's count
  SELECT set_config('app.account_id', '', false);
  SELECT COUNT(*) FROM maintenance_tasks;  -- should be 0 (GUC unset)
"

# 4. If counts look right, hit the production dashboard and verify.
# 5. If a tenant reports "no data showing," the most likely cause is
#    a code path missing its with_account.  Re-grant BYPASSRLS via
#    ALTER ROLE ... BYPASSRLS to immediately restore, then debug.
```

### Stage 4 — bake one week

After 7 days of clean operation with `4truck` as `NOBYPASSRLS`, the rollout is complete.

## Rollback

At any stage:

```bash
# Re-grant BYPASSRLS to restore the previous behavior immediately.
sudo -u postgres psql -d 4truck -c "ALTER ROLE \"4truck\" BYPASSRLS;"
# Reload services to clear any pool-cached connections that hit policies.
sudo systemctl restart 4truck-api 4truck-bot 4truck-queue
```

This takes effect for the next connection acquired by the app — no need to rerun the migration or drop policies.

To fully disable RLS:

```sql
-- DROP all policies (the migration's CREATE POLICY is `IF NOT EXISTS`-style
-- via DROP POLICY IF EXISTS first, so re-running the migration restores them).
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN SELECT schemaname, tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY', r.schemaname, r.tablename);
    EXECUTE format('ALTER TABLE %I.%I NO FORCE ROW LEVEL SECURITY', r.schemaname, r.tablename);
  END LOOP;
END $$;
```

## Known caveats

### Single-writer GUC race

Currently `Database._db` is a single shared asyncpg connection (the Tier 4 Phase 2 pool refactor is **not yet done**).  Two concurrent requests' `with_account` calls can interleave:

```
  Request A:  SET app.account_id = '1'
  Request A:  SELECT ...  ← runs under account 1's policy ✓
  Request B:  SET app.account_id = '2'  ← B reaches the same conn
  Request A:  SELECT ...  ← now runs under account 2's policy ✗
```

In practice, the writer-lock in `_PgConnection` serializes every `execute()`, so within a single uninterrupted block (no `await` boundaries between two queries) the GUC is stable.  Between awaits, request B can take the lock and reset the GUC.

**Mitigations applied:**
- Scheduler jobs that operate on multiple tenants iterate ONE AT A TIME (sequential `for acc in accounts: await run_account_job(...)`); they never have two `with_account`s in flight.
- API requests fully complete within a single FastAPI handler invocation — there's no parallelism within a single dependent chain.

**Where the race would bite:** if you add a new code path that starts a long-running background task in the middle of a request without re-establishing the GUC.  The Tier 4 Phase 2 pool refactor eliminates this entirely.

### Migration 057 + fresh databases

A fresh database that hasn't yet run migrations 001-056 might fail migration 057 because some of the 41 tables don't yet exist.  The migration logs each missing table and continues, so the rollout doesn't hard-fail — but the missing tables won't have RLS until they're created AND migration 057 is re-applied.

If a `_schema_versions` entry for `057_enable_rls_tenant_tables` already exists, re-applying requires manually deleting that row first:

```sql
DELETE FROM _schema_versions WHERE version = '057_enable_rls_tenant_tables';
```

Then restart the application — migration 057 will re-run and pick up the newly-created tables.
