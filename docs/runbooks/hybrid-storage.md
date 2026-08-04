# Hybrid Storage rollout — disk-primary, cloud-async per-tenant tiering

The hybrid backend gives every account two layers of storage:

* **Disk** (the server's local cache) — fast, always works, never has an
  OAuth token to expire.  Writes land here in ~10 ms.
* **Google Drive** (the customer's own Drive via BYO OAuth) — durable,
  per-tenant, off-server.  A background worker pushes bytes from disk
  to Drive within ~60 s of upload and then deletes the local copy.

Every file goes through the same lifecycle.  An expired Drive token
never blocks a driver upload; quota-exhausted local disk never
silently swallows files.  Per-account quotas + a circuit breaker keep
one stuck account from cascading.

This rollout shipped as Phases 1–6 (migrations 070, code under
`adapters/storage/storage_sync.py`, `adapters/storage/object_store_hybrid.py`,
`capabilities/object_store/sync_worker.py`, route additions in
`interfaces/api/routes/storage.py`, dashboard panels under
`interfaces/dashboard/src/pages/admin/`).

---

## Architecture

```
                  Driver / fleet upload
                        │
                        ▼
        ┌─────────────────────────────────────────┐
        │  Upload endpoint                        │
        │  1. quota gate (_check_quota_or_413)    │
        │  2. store.put → disk (HybridObjectStore)│
        │  3. attach_inspection_media row         │
        │  4. track_for_sync → queue row + state  │
        │     = 'local'                            │
        └─────────────────┬───────────────────────┘
                          │  (returns 200 in <500ms)
                          ▼
                  object_store_sync_queue
              (DB outbox — survives restarts)
                          │
                          ▼  every 60 s (or kicked)
        ┌─────────────────────────────────────────┐
        │  sync_pending_storage worker            │
        │  1. claim_pending_sync (SKIP LOCKED +   │
        │     lease push 5 min)                   │
        │  2. group by account                    │
        │  3. build Drive client ONCE / account   │
        │  4. for each row: disk.get → drive.put  │
        │     → set_media_storage_state(remote)   │
        │     → disk.delete → mark_sync_succeeded │
        └─────────────────┬───────────────────────┘
                          │
                          ▼
                  Customer's Google Drive
              inspections/{acct}/{ins_id}/   (same path as on disk)
```

Reads (`HybridObjectStore.get`) check disk first, then Drive — the
caller never sees the tier transition.  Once the sync worker has
uploaded a file and removed the local copy, the media row's
`file_path` is the Drive file ID; the PTI read helper
(`_media_bytes` in `interfaces/api/routes/inspections.py`) prefers
`store.get_by_id(drive_id)` for those rows so reads survive any
folder reshuffling the user does in their Drive UI.

---

## Per-account rollout

The migration runs at boot and is purely additive — every existing
account keeps its current backend (`disk` or `gdrive`).  Flipping an
account into the hybrid model is a single setting change.

### 1. Confirm Drive is connected for the account

The hybrid only drains its queue if Drive is reachable.  Confirm
via the storage admin page (Settings → Storage → "Connected as
…@…") or directly:

```bash
psql "$DATABASE_URL" -c "
  SELECT account_id, key, length(value) AS len
    FROM account_settings
   WHERE account_id = <ACCT>
     AND key IN ('storage.gdrive.refresh_token',
                 'storage.gdrive.user_email',
                 'storage.gdrive.root_folder_id')
   ORDER BY key"
```

If `object_store.gdrive.refresh_token` is empty or the saved token is
expired, the queue will just accumulate locally until the operator
reconnects Drive.  That's a feature, not a bug — files never get
lost.

### 2. Flip the account to hybrid

From a Python REPL on the box:

```python
from adapters.storage import Database
db = Database(); await db.initialize()
await db.set_account_storage_backend(<ACCT>, "hybrid")
await db.close()
```

The `set_account_storage_backend` helper also invalidates the per-
account `ObjectStore` cache, so the next upload immediately uses the
new backend without a service restart.

### 3. (Optional) set a per-account quota override

Default quota is 5 GB.  Override per account:

```python
await db.set_account_disk_quota(<ACCT>, 10 * 1024**3)   # 10 GB
```

### 4. Verify

* Open the Storage admin page (`/admin/storage`).  The new
  **StorageHealthCard** should show "Hybrid · Drive connected as
  …@…" and a quota gauge at 0 %.
* Drop a test photo into a PTI inspection.  Within ~60 s the
  pending count should tick up to 1, then drain to 0 as the worker
  pushes it to Drive.
* The driver photo's `storage_state` will progress `local → syncing
  → remote` and the file_path in `pti_inspection_media` flips from
  the disk URL to the Drive file ID.

---

## Env vars (worker tuning)

| Variable | Default | What it controls |
|---|---|---|
| `SYNC_WORKER_BATCH_SIZE` | `8` | Rows claimed per pass.  Higher = more parallelism per tick.  Raise on a high-traffic instance; lower if Drive returns 429s. |
| `SYNC_WORKER_ACCOUNT_CONCURRENCY` | `4` | Parallel uploads per account inside a single pass.  Google's per-user 1000-requests/100-sec quota gives plenty of headroom; raise if you've already raised batch size. |

No restart needed for the lease itself — the worker job is registered
in `interfaces/bot/scheduler.py` with `seconds=60`, `max_instances=1`,
`coalesce=True`.

---

## What to monitor

`GET /storage/health` (per-account) returns the canonical metrics.
Scrape-friendly shape (already wired):

```json
{
  "backend": "hybrid",
  "drive":   { "connected": true, "email": "…" },
  "quota":   { "used_bytes": 1234, "quota_bytes": 5368709120, "percent": 0.02 },
  "queue":   { "pending": 0, "stuck": 0 },
  "media":   { "local": 0, "syncing": 0, "remote": 247, "stuck": 0 }
}
```

Alerts to wire when you stand up Prometheus / Grafana later:

* **`queue.stuck > 0`** for any account → Drive needs operator action
  (token, quota, perms).  Surface on the admin's main dashboard.
* **`queue.pending > 50`** sustained for >10 min → worker behind;
  raise concurrency or check Drive latency.
* **`quota.percent > 80`** → soft alert.  At 100 % the upload endpoint
  returns 413 with the structured `account_storage_full` detail.

---

## Failure modes & recovery

| Failure | Symptom | Recovery |
|---|---|---|
| Drive refresh token expired (Testing-mode 7-day expiry, etc.) | Health card shows "Drive disconnected" or `queue.stuck` ticks up with `error_code: token_expired`.  File table shows red ⚠ rows with last_error = "invalid_grant…" | Settings → Storage → **Connect Google Drive** (re-runs OAuth, mints a fresh refresh token).  Then **Retry all stuck** from the file table — one click re-enqueues every parked row. |
| Drive whole-account quota full | `queue.stuck` with `error_code: quota_exceeded` | Customer frees Drive space.  Then **Retry all stuck**.  Worker's circuit breaker means we don't keep slamming Google in the meantime. |
| Drive folder permission revoked | `error_code: forbidden` | Customer regrants access via the OAuth re-consent, OR moves the root folder back into their `My Drive`.  Then **Retry all stuck**. |
| Transient Drive outage / 5xx | Rows tick `attempts` upward; ladder is 30 s → 2 m → 10 m → 1 h → 6 h → 24 h | Wait.  Auto-recovers.  After 6 failed attempts a row is parked as `stuck` and the operator gets a visible row to retry manually. |
| Server crash mid-upload | File is partially on disk; queue row may or may not exist | The Phase 3 worker's lease (5 min) handles in-flight rows automatically: lease expires → row re-claims on the next tick.  A truly orphan disk file (no queue row) is a silent leak but doesn't block anything — clean up with a manual sweep if it ever happens. |
| Server crash mid-sync | Queue row's `attempts` already bumped; file still on disk | Lease expires (5 min) → next worker claims it → resumes.  Idempotent: GDrive `put` updates-in-place on same-name uploads. |
| Local disk full on the server | New uploads start failing with the `account_storage_full` 413.  Worker keeps draining existing rows. | Increase quotas or free disk.  No file loss — uploads are rejected at the gate, not corrupted. |
| Orphan queue row (file vanished from disk) | Worker marks the row stuck with `error_code: missing` | Investigate (manual disk wipe?).  Delete the queue row + media row if the file is truly gone. |

---

## Scaling to N workers (10k accounts)

Phase 3's worker uses `SELECT … FOR UPDATE SKIP LOCKED` + a 5-minute
lease, so adding a second worker process is a one-liner:

```bash
# systemd unit (4truck-sync-worker.service)
[Service]
EnvironmentFile=/etc/4truck/env
Environment=SYNC_WORKER_BATCH_SIZE=16
Environment=SYNC_WORKER_ACCOUNT_CONCURRENCY=8
ExecStart=/opt/4truck/venv/bin/python -m capabilities.object_store.standalone_worker
```

(The codebase ships the worker inside the bot's APScheduler today
because that's sufficient for hundreds of accounts.  When you need
to scale past one server, peel the worker out into a dedicated
process — no code changes, just import `sync_pending_storage` and
loop with `asyncio.sleep(60)`.)

For 10k+ accounts:

1. **Storage tier**: move `data/` to NFS / EFS so any worker can read
   any account's files.  Code unchanged — `DiskObjectStore._root` just
   resolves to a different path.
2. **DB tier**: pgBouncer in front of Postgres in transaction-pooling
   mode.  Indexes already in place (`idx_object_store_sync_queue_due`,
   `idx_object_store_sync_queue_account_due`).
3. **Drive quotas**: 1B/day per OAuth project covers 10k accounts ×
   50 uploads/day.  If you grow past that, run multiple OAuth projects
   and round-robin the env var per account / per worker.

---

## Rollback

Hybrid is opt-in per account.  To revert an account to the prior
behaviour:

```python
# Back to pure-disk (no cloud sync — files stay on the server)
await db.set_account_storage_backend(<ACCT>, "disk")

# Back to pure-gdrive (writes go directly to Drive; no local cache)
await db.set_account_storage_backend(<ACCT>, "gdrive")
```

The change takes effect on the next request.  Existing queue rows for
the account remain in the queue — drop them manually if you want to
abandon pending syncs:

```sql
DELETE FROM object_store_sync_queue WHERE account_id = <ACCT>;
```

Files already in Drive stay in Drive.  Files only on disk stay on
disk (still readable through whichever backend the account now uses,
because the media row's `file_path` already points at the disk URL
for those rows).

---

## Migration safety

Migration `070_hybrid_storage_foundation` is **strictly additive**:

* Adds `local_path`, `remote_path`, `storage_state`, `sha256` columns
  to `pti_inspection_media` with safe defaults (`storage_state` defaults
  to `'remote'` so every legacy row is treated as already-settled —
  the worker ignores them).
* Creates the `object_store_sync_queue` table from scratch.
* Adds two indexes.

There's no data backfill, no destructive change, no requirement that
old rows be migrated.  Roll forward freely; roll back by skipping
hybrid as a backend choice.
