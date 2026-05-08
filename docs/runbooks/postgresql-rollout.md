# Phase 5 Rollout — SQLite → PostgreSQL migration

The biggest scale-out step. SQLite-per-tenant is fine for hundreds of
tenants but not for 10k+ — concurrent writes serialise on the per-file
WAL lock, no read replicas, no real backups, no schema-per-tenant
PITR. PostgreSQL fixes all of those.

**Effort:** 3-4 weeks · **Risk:** high · **Payoff:** unlocks the 10k
tenant ceiling, real point-in-time backups, read replicas, online
schema changes.

**Sequencing:** three sub-phases, each independently rollback-able.

---

## Architecture goal

```
Today:                                        After Phase 5:
┌── 1 SQLite file per tenant ──┐              ┌── PostgreSQL primary ──┐
│  /var/lib/4truck/             │              │  4truck_platform       │
│  ├── platform.db (shared)     │              │  4truck_tenants        │
│  ├── tenant_1.db              │              │    (schema-per-tenant) │
│  ├── tenant_2.db              │     ────►    └────────────┬───────────┘
│  └── tenant_N.db              │                           │
└──────────────────────────────┘              ┌─────────────▼───────────┐
                                              │  PostgreSQL read replica │
                                              │  (hot reads scale out)   │
                                              └──────────────────────────┘
```

Phase 5 ships the SQL-translation, dual-write, and migration plumbing.
The actual cutover decision is yours — flip the env flag when you're
ready.

---

## What ships in this commit (5a infrastructure)

| Component | File | Purpose |
|---|---|---|
| pg_adapter SQL translator (extended) | [adapters/storage/pg_adapter.py](../../adapters/storage/pg_adapter.py) | Now translates: `datetime('now', '-N days')` literal + parameterised forms; `date('now', ...)` same; `datetime(col)` / `date(col)` column-coercion; `INSERT OR IGNORE` → `INSERT ... ON CONFLICT DO NOTHING`. Logs warnings for `INSERT OR REPLACE`, `json_extract`, `julianday`, `strftime`, `printf`, `WITHOUT ROWID`, `random()` so dual-write surfaces them in prod logs. |
| Migration script (rebuilt table list) | [scripts/export_sqlite_to_postgres.py](../../scripts/export_sqlite_to_postgres.py) | TENANT_TABLES rewritten — was 12 tables, now 37 (covers audit, alerts, scoring, coaching, payroll, warehouse). Idempotent — re-runs upsert. |
| DualWriteCore shim | [adapters/storage/dualwrite.py](../../adapters/storage/dualwrite.py) | Wraps a primary (aiosqlite) + secondary (pg_adapter) connection pair. Mirrors writes; reads still go to primary unless `DB_READ_BACKEND=pg`. Counters surface drift. |
| Diagnostic endpoint | [interfaces/api/routes/admin.py](../../interfaces/api/routes/admin.py) | `GET /admin/dualwrite-status` — flag values + per-process counters. |

The pre-existing pieces from earlier work that 5b will activate:
- `pg_adapter._PgPool` / `open_pg_pool()` — async asyncpg pool wrapper
- `tenant_router.py` — already routes per-account when `DATABASE_URL` is set
- `tenant_migrations.py` / `platform_migrations.py` — schema migrations (already idempotent)

---

## Sub-phase 5a — Compatibility audit (week 1)

Goal: prove the existing SQL works on PG end-to-end with zero code
changes inside mixins. The translator does all the lifting.

### Steps

1. **Provision a PG instance** (RDS / Neon / Supabase or self-hosted):
   ```bash
   createdb 4truck_test
   # set in .env.test:
   #   DATABASE_URL=postgresql://4truck:secret@localhost/4truck_test
   ```

2. **Enable untranslated-pattern warnings** so any SQL the adapter
   passes through unchanged shows up in logs:
   ```bash
   export PG_ADAPTER_DEBUG=1
   ```

3. **Run the existing pytest suite against PG** by setting
   `DATABASE_URL` and running:
   ```bash
   DATABASE_URL=postgresql://localhost/4truck_test \
     PG_ADAPTER_DEBUG=1 \
     python3 -m pytest tests/ -x 2>&1 | tee pg-test.log
   ```
   Grep for `pg_adapter:untranslated` lines — each is a SQL pattern
   that may need extending the translator. Fix forward in
   `_sqlite_to_pg_sql`.

4. **Run the migration script in --dry-run** against a copy of one
   production tenant DB:
   ```bash
   python3 scripts/export_sqlite_to_postgres.py \
     --pg-url $DATABASE_URL \
     --sqlite-platform /var/lib/4truck/platform.db \
     --sqlite-tenant /var/lib/4truck/tenant_42.db \
     --tenant-id 42 \
     --dry-run
   ```
   Expect the script to print row counts per table without writing.

5. **Run for-real, then validate row counts match exactly**:
   ```bash
   python3 scripts/export_sqlite_to_postgres.py \
     --pg-url $DATABASE_URL \
     --sqlite-platform /var/lib/4truck/platform.db \
     --sqlite-tenant /var/lib/4truck/tenant_42.db \
     --tenant-id 42

   # Compare:
   for table in users accounts companies alert_history score_events; do
     sq=$(sqlite3 /var/lib/4truck/tenant_42.db "SELECT COUNT(*) FROM $table")
     pg=$(psql $DATABASE_URL -tAc "SELECT COUNT(*) FROM tenant_42.$table")
     echo "$table  sqlite=$sq  pg=$pg  $([ $sq = $pg ] && echo OK || echo MISMATCH)"
   done
   ```

6. **Performance smoke-test** — 10 hottest endpoints, k6/wrk against
   both a SQLite copy and the PG copy:
   ```bash
   wrk -t4 -c50 -d30s -H "Authorization: Bearer $JWT" \
     https://test.4truck.us/api/safety/scorecards/composite
   ```
   PG p99 must be within 2× SQLite baseline. If higher, profile the
   slow query (`EXPLAIN ANALYZE`) and add indexes.

### Validation

- ✅ All 720 pytest tests pass with `DATABASE_URL=postgresql://...`
- ✅ Per-table row counts match exactly for one tenant
- ✅ No `pg_adapter:untranslated` lines in the test log
- ✅ p99 within 2× SQLite for the 10 hot endpoints

### Rollback for 5a
Read-only experiment — nothing rolls back. Drop the test PG database
when done.

---

## Sub-phase 5b — Dual-write phase (weeks 2-3)

Goal: production runs every write to both SQLite (primary) and PG
(secondary) for 7 days; reads stay on SQLite. Drift logged.

### Architecture

```
write path:     mixin.execute(SQL, params)
                       ▼
                DualWriteConnection
                       ├── primary  → aiosqlite      ← caller sees this result
                       └── secondary→ pg_adapter     ← shadow; failure logged but never raised
                                                      counter: dualwrite:writes / writes_failed
read path:      mixin.execute(SELECT, params)
                       ▼
                DualWriteConnection
                       ├── primary  → aiosqlite      ← caller sees these rows
                       └── secondary→ pg_adapter     ← run if DB_READ_BACKEND=pg
                                                      counter: dualwrite:reads / reads_diverged
```

### Steps

1. **Wire DualWriteConnection into infra/platform.py**. The wrapper is
   ready in `adapters/storage/dualwrite.py`; the platform initializer
   needs to:
   - open both connections per-tenant when `DB_DUAL_WRITE=1`
   - return a `DualWriteConnection(primary, secondary)` instead of the
     bare aiosqlite connection

   This is a 20-line change in `infra/platform.py` — landed under a
   feature flag so the default behaviour is unchanged.

2. **Enable on staging first**:
   ```bash
   echo 'DB_DUAL_WRITE=1' >> .env
   echo 'DATABASE_URL=postgresql://staging-pg/4truck' >> .env
   echo 'PG_ADAPTER_DEBUG=1' >> .env
   sudo systemctl restart 4truck-api 4truck-bot 4truck-queue
   ```

3. **Watch logs for 24h**:
   ```bash
   journalctl -u 4truck-api -f | grep -E "dualwrite:|pg_adapter:"
   ```
   Expect:
   - **Zero** `dualwrite:secondary-failed` lines for the first hour
   - Some `dualwrite:divergent-rowcount` lines initially (timing
     skew between primary and secondary writes); should reduce to
     zero after 5 min
   - Zero `pg_adapter:untranslated` lines (any seen → fix the
     translator and redeploy)

4. **Backfill historical data** with the migration script, per tenant:
   ```bash
   python3 scripts/export_sqlite_to_postgres.py \
     --pg-url $DATABASE_URL \
     --sqlite-platform /var/lib/4truck/platform.db \
     --sqlite-tenant /var/lib/4truck/tenant_${id}.db \
     --tenant-id ${id}
   ```

5. **Validate read parity** by flipping `DB_READ_BACKEND=pg` for one
   tenant via a per-tenant override in `infra/platform.py` (or for the
   whole staging deploy):
   ```bash
   echo 'DB_READ_BACKEND=pg' >> .env.staging
   sudo systemctl restart 4truck-api
   ```
   Hit `/api/admin/dualwrite-status` — expect `reads > 0`,
   `reads_diverged == 0`. If reads diverge, log the SQL from the
   warning line and fix forward.

6. **Run dual-write in production for 7 days**. Daily check:
   ```bash
   curl -s -H "Authorization: Bearer $JWT" \
     https://4truck.us/api/admin/dualwrite-status | jq .metrics
   # expect: writes_failed=0, reads_diverged=0
   ```

### Validation
- ✅ 7 days of dual-write with `writes_failed == 0`
- ✅ All tenants backfilled, row counts match
- ✅ `DB_READ_BACKEND=pg` on staging for 24h with `reads_diverged == 0`

### Rollback for 5b
```bash
# Disable dual-write — primary still writes to SQLite as before
sed -i '/^DB_DUAL_WRITE=/d' .env
sed -i '/^DB_READ_BACKEND=/d' .env
sudo systemctl restart 4truck-api 4truck-bot 4truck-queue
```
Zero data loss — SQLite was always the primary. PG state is discarded.

---

## Sub-phase 5c — Cutover + cleanup (week 4)

Goal: PG becomes the source of truth. SQLite files retained 30 days
for emergency rollback.

### Steps

1. **Final backfill pass** to capture any rows written to SQLite-only
   between the last backfill and the cutover:
   ```bash
   for db in /var/lib/4truck/tenant_*.db; do
     id=$(basename $db .db | cut -d_ -f2)
     python3 scripts/export_sqlite_to_postgres.py \
       --pg-url $DATABASE_URL \
       --sqlite-tenant $db --tenant-id $id
   done
   ```

2. **Flip read backend globally**:
   ```bash
   sed -i 's/^DB_READ_BACKEND=.*/DB_READ_BACKEND=pg/' .env
   sudo systemctl restart 4truck-api
   ```
   Watch for 1h. Spot-check `/api/admin/dualwrite-status`,
   `scorecard.timing` lines — both should look identical to before.

3. **Flip write primary** by switching the platform initializer to
   pass `pg_adapter` as primary and aiosqlite as secondary:
   ```bash
   sed -i 's/^DB_WRITE_PRIMARY=.*/DB_WRITE_PRIMARY=pg/' .env
   sudo systemctl restart 4truck-api 4truck-bot 4truck-queue
   ```
   Now writes hit PG first; SQLite shadows for 30 days as a safety net.

4. **Run for 7 days with PG as primary, SQLite as secondary**. If
   anything looks wrong, flip back to SQLite primary instantly:
   ```bash
   sed -i 's/^DB_WRITE_PRIMARY=.*/DB_WRITE_PRIMARY=sqlite/' .env
   sudo systemctl restart 4truck-api
   ```

5. **Decommission SQLite** after 30 days:
   ```bash
   sed -i '/^DB_DUAL_WRITE=/d' .env
   sudo systemctl restart 4truck-api 4truck-bot 4truck-queue
   # Move SQLite files to cold storage:
   tar czf /backup/sqlite-pre-pg-$(date +%F).tar.gz /var/lib/4truck/
   # Keep tar for 90 more days minimum.
   ```

### Validation
- ✅ 7 days with PG primary + zero `dualwrite:secondary-failed` in
  reverse direction (SQLite shadow)
- ✅ Per-tenant row counts in PG match the last SQLite snapshot
- ✅ All hot endpoints have p99 within 2× of pre-cutover baseline
- ✅ `pg_dump` based backups working (verify by restoring to a test
  instance)

---

## Tuning post-cutover

### Connection pool sizing
Default `_PgPool(min_size=2, max_size=10)` is conservative. For
production with N gunicorn workers across M hosts:
```python
asyncpg.Pool(min_size=10, max_size=50)  # per-process
```
Total connections: M hosts × N workers × max_size. Stay under PG's
`max_connections` (default 100 for RDS small) by adding **PgBouncer**
in transaction-pooling mode in front.

### Indexes
The translator preserves `CREATE INDEX IF NOT EXISTS` directly. Audit
slow queries via:
```sql
SELECT schemaname, relname, n_live_tup, seq_scan, idx_scan
  FROM pg_stat_user_tables
  WHERE seq_scan > idx_scan AND n_live_tup > 1000
  ORDER BY seq_scan DESC LIMIT 20;
```

### Read replicas
For read-heavy endpoints (`/api/safety/scorecards/composite`,
`/api/fleet/overview`), point reads at a replica via:
```python
# in pg_adapter, accept (writer_dsn, reader_dsn) tuple
DATABASE_URL=postgresql://primary/...
DATABASE_READ_URL=postgresql://replica/...
```
SWR cache (Phase 1) absorbs most of the load already; replicas
are insurance for the long tail.

---

## Rollback at each sub-phase

| Sub-phase | Rollback action | Data risk |
|---|---|---|
| 5a (audit) | drop test PG DB | none — read-only |
| 5b (dual-write) | unset `DB_DUAL_WRITE` + restart | none — SQLite was primary |
| 5c step 2 (read flip) | unset `DB_READ_BACKEND=pg` + restart | none — both still in sync |
| 5c step 3 (write flip) | reset `DB_WRITE_PRIMARY=sqlite` + restart | minor — any writes during PG-primary window need re-mirroring (the dual-write shim handles this) |
| 5c step 5 (decommission) | reload SQLite files from `/backup/sqlite-pre-pg-*.tar.gz` | full restore — last 30 days lost; recoverable from PG backups |

---

## Known limitations after Phase 5

- **No online schema changes yet.** Adding a column still requires a
  brief write-blocker. Move to `pg_repack` or zero-downtime migration
  patterns when schema cadence increases.
- **No automated PITR backup pipeline.** Set up RDS automated
  snapshots / continuous WAL archiving via `pgBackRest` separately.
- **No multi-region failover.** Single-primary today. Phase 6
  observability surfaces failover signals; actually implementing
  failover is out of scope.
- **PgBouncer not yet in front.** Required before traffic exceeds
  ~50 RPS sustained per app worker. The pool sizing in Phase 2
  (`max_size=50` × N workers) will hit `max_connections` without it.
