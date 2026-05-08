# Phase 3 Rollout — ARQ background job queue

Adds an asyncio-native job queue (Redis-backed, no extra infra) so:

* the API can fire off heavy work in the background and return fast,
* a nightly cron pre-warms the SWR scorecards cache before users wake
  up so the first request of the morning is a cache hit,
* future async features (PDF gen, large CSV exports, deferred email
  reports) have a real runtime instead of `asyncio.create_task` inside
  the request loop.

Zero data-model changes. New runtime: a dedicated `4truck-queue.service`
process. APScheduler stays in the bot process unchanged — ARQ is for
new work and selectively-migrated jobs, not a forklift replacement.

---

## Architecture after Phase 3

```
┌──── 4truck-api ────┐    ┌──── 4truck-bot ────┐    ┌──── 4truck-queue ────┐
│  gunicorn N×       │    │  Telegram polling  │    │  ARQ worker (× M)    │
│  uvicorn workers   │    │  APScheduler       │    │  (precompute, future │
│                    │    │  (warehouse jobs,  │    │   PDF gen, exports)  │
│  enqueue() ───────────────┐                  │    │                      │
└────────────────────┘    │ │                  │    │ ←── BLPOP from queue │
                          │ └────────────────────┘    └──────────────────────┘
                          ▼
                ┌──── Redis ────┐
                │  arq:queue    │
                │  arq:job:*    │
                │  arq:result:* │
                └───────────────┘
```

Workers and producers find each other through Redis only — no direct
links, no shared filesystem, no service discovery. Add or remove worker
processes (or whole pods) at any time without touching the API or bot.

---

## What ships with Phase 3

| Component | File | Purpose |
|---|---|---|
| Job-queue client | [infra/jobs.py](../../infra/jobs.py) | `init_jobs()`, `enqueue()`, `get_job_status()`. Graceful no-op when arq isn't installed. |
| Worker settings | [capabilities/jobs/worker.py](../../capabilities/jobs/worker.py) | `WorkerSettings` (registered functions, cron jobs, lifecycle hooks). |
| Job functions | [capabilities/jobs/functions.py](../../capabilities/jobs/functions.py) | `ping`, `precompute_scorecards`, `fanout_precompute_scorecards`. |
| systemd unit | [4truck-queue.service](../../4truck-queue.service) | Runs `python3 -m arq capabilities.jobs.worker.WorkerSettings`. |
| API endpoints | [interfaces/api/routes/admin.py](../../interfaces/api/routes/admin.py) | `GET /admin/jobs/{job_id}` + `POST /admin/jobs/prewarm-scorecards`. |
| Startup wiring | [infra/startup.py](../../infra/startup.py) | Pool lifecycle alongside Redis cache. |

---

## Pre-flight

### 1. Install arq
```bash
pip install -r requirements.txt
python3 -c "import arq; print(arq.__version__)"
# expect: 0.26.x or newer
```

### 2. Smoke-boot the worker locally
```bash
cd /home/abcdev/projects/Semi_Telematics_bot
python3 -m arq capabilities.jobs.worker.WorkerSettings
# expect:
#   ARQ worker startup complete
#   Starting worker for 3 functions: ping, precompute_scorecards, fanout_precompute_scorecards
#   Scheduled cron jobs: prewarm_scorecards_morning
```
Leave it running.

### 3. Enqueue the ping job from a separate shell
```bash
cd /home/abcdev/projects/Semi_Telematics_bot
python3 -c "
import asyncio
from infra import jobs

async def main():
    await jobs.init_jobs()
    job = await jobs.enqueue('ping')
    print('enqueued:', job.job_id)
    await asyncio.sleep(1)
    print('status:', await jobs.get_job_status(job.job_id))
    await jobs.close_jobs()

asyncio.run(main())
"
# expect: status.status == 'complete', status.result == 'pong'
```
If you see `enqueue() skipped — queue unavailable`, Redis isn't
reachable from this shell — same Redis the API uses, check `REDIS_URL`.

### 4. Confirm the API can enqueue too
With the worker still running:
```bash
curl -X POST -H "Authorization: Bearer $JWT" \
  https://4truck.us/api/admin/jobs/prewarm-scorecards
# returns: {"job_id": "...", "function": "precompute_scorecards", ...}

# Poll status:
curl -H "Authorization: Bearer $JWT" \
  https://4truck.us/api/admin/jobs/<job_id>
# expect: status moves through queued → in_progress → complete
```

When `status: complete` returns, hit the dashboard scorecards page —
should be a cache hit (sub-100ms) because the precompute landed the
result in the SWR cache.

---

## Rollout

### Stage 1 — staging
```bash
git pull && pip install -r requirements.txt
sudo cp 4truck-queue.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now 4truck-queue
sudo systemctl restart 4truck-api 4truck-bot     # picks up new infra.startup wiring
journalctl -u 4truck-queue -f
# expect "ARQ worker startup complete" + idle
```

Run the ping smoke test above to verify end-to-end.

### Stage 2 — production
Same commands. Blast radius: adding a new service unit is non-disruptive.
Restarting the API + bot causes a brief blip (gracefully handled by the
gunicorn `graceful_timeout` from Phase 2).

### Stage 3 — wait for the 06:00 cron
Next morning at 06:00 UTC, watch:
```bash
journalctl -u 4truck-queue -f --since "06:00"
# expect:
#   fanout_precompute_scorecards start days=7
#   fanout_precompute_scorecards done enqueued=N total_accounts=N
#   precompute_scorecards start acct=1 days=7 ...
#   precompute_scorecards done acct=1 in 4321ms
#   ... one per account, in parallel up to ARQ_MAX_JOBS ...
```

First user to hit `/dashboard/safety/scorecards` after 06:00 should
land in the SWR cache (look for absence of `scorecard.timing total=Xms`
INFO line — that line only fires on cold-path > 1s).

---

## Verification

### Worker is consuming jobs
```bash
ps -ef | grep "arq capabilities.jobs"
# expect: 1 process per 4truck-queue unit instance

journalctl -u 4truck-queue -n 50 --no-pager | grep -E "ping|precompute|fanout"
```

### Queue depth is healthy
```bash
redis-cli -p 8002 LLEN arq:queue
# expect: ≤ ARQ_MAX_JOBS during normal operation; transient spikes
# during fanout are fine (workers drain in seconds)
```

### Cron is registered
```bash
redis-cli -p 8002 KEYS 'arq:cron:*'
# expect: arq:cron:prewarm_scorecards_morning
```

### Job-status endpoint works
```bash
# Last few jobs (any status):
redis-cli -p 8002 KEYS 'arq:result:*' | head -5
# Pick one:
curl -H "Authorization: Bearer $JWT" \
  https://4truck.us/api/admin/jobs/<job_id> | jq
```

---

## Monitoring

| Signal | Where | Healthy |
|---|---|---|
| Worker process up | `systemctl is-active 4truck-queue` | `active` |
| Queue depth | `redis-cli LLEN arq:queue` | `0`–`ARQ_MAX_JOBS` (drains in seconds) |
| Cron firing | `journalctl -u 4truck-queue --since "06:00" \| grep fanout` | one entry per day |
| Job failures | `journalctl -u 4truck-queue -n 1000 \| grep -i 'error\|FAILED'` | rare; ARQ retries up to `ARQ_MAX_TRIES` |
| Pre-warm effectiveness | morning `scorecard.timing total=X` lines | minimal — cache is warm |

---

## Rollback

The queue is **optional infrastructure**. To roll back, just stop the
worker — `enqueue()` becomes a logged no-op and the API continues to
serve cold-path scorecard requests synchronously (same as before
Phase 3).

```bash
sudo systemctl stop 4truck-queue
sudo systemctl disable 4truck-queue
# optional: remove the unit
sudo rm /etc/systemd/system/4truck-queue.service
sudo systemctl daemon-reload
```

The `infra/jobs.py` lazy-init is graceful: API endpoints that try to
`enqueue()` without a worker get `None` back and log a warning. No
500s, no broken UX (other than the morning cache miss).

---

## Tuning

All knobs env-overridable on `4truck-queue.service`:

| Env var | Default | When to tune |
|---|---|---|
| `ARQ_MAX_JOBS` | 10 | Concurrent jobs per worker. Bigger box → higher. CPU-bound jobs → keep low. I/O-bound → can go to 50+. |
| `ARQ_JOB_TIMEOUT` | 300s | Largest expected job duration × 2. PDF gen ~30s; precompute ~10s. |
| `ARQ_MAX_TRIES` | 3 | Failed jobs retry with exponential backoff. Reduce to 1 for non-idempotent work. |
| `ARQ_QUEUE_NAME` | `arq:queue` | Override per-tenant if you want isolation between heavy + light queues. |
| `ARQ_REDIS_URL` | `REDIS_URL` | Point at a separate Redis instance to isolate queue from cache. |

**Horizontal scaling:** drop a second `4truck-queue.service` instance
on a different host. They self-coordinate via Redis BLPOP — zero
configuration. You can also vary `ARQ_MAX_JOBS` per host depending on
machine size.

---

## Adding a new background job

1. Define the function in `capabilities/jobs/functions.py`:
   ```python
   async def my_job(ctx: dict, account_id: int, ...) -> dict:
       # heavy work here
       return {"ok": True}
   ```
2. Register it in `capabilities/jobs/worker.py:WorkerSettings.functions`.
3. Restart `4truck-queue` so the worker picks up the new function.
4. Enqueue from anywhere:
   ```python
   from infra import jobs
   job = await jobs.enqueue("my_job", account_id=7)
   ```
5. (Optional) Add a status route under `/admin/jobs/...` if the
   dashboard needs to poll it.

For cron-style scheduling, add `arq.cron(my_job, hour=N, minute=N)` to
the `cron_jobs` list in `WorkerSettings`. ARQ ensures only one worker
fires the cron each tick.

---

## Why ARQ over Celery/RQ

- **Asyncio-native** — handlers are `async def`, no thread-pool layer
  between the queue and our existing `await tenant.read_one(...)`.
- **Redis-only** — we already run Redis for caching and rate limits.
  No RabbitMQ/Kafka to operate.
- **Tiny code surface** — `arq` is ~3k LoC. Easier to debug than Celery.
- **Cron built in** — no separate `celery beat` process.
- **Graceful BLPOP shutdown** — workers drain in-flight jobs on SIGTERM.

The trade-off: no eventing/streaming/retries-with-backoff-curves like
Celery offers. We don't need them yet.

---

## Known gaps after Phase 3

- **No queue UI** — `/admin/jobs/{id}` is JSON-only. A dashboard "Jobs"
  page is one quick React component on top of this endpoint.
- **No per-tenant scoping inside the job** — admins of tenant A can
  see job IDs of tenant B if they know the IDs. ARQ doesn't natively
  scope jobs; we restrict to `can_manage_account` to limit blast
  radius. If multi-tenant job UX becomes a thing, stamp the requesting
  tenant on enqueue and check it on lookup.
- **No retry visibility** — failed jobs log + retry but no aggregated
  view in the admin UI. Phase 6 (Prometheus) covers this with a
  `arq_jobs_failed_total` counter.
- **Risk-summary endpoint still synchronous** — it works fine sync
  (build + render takes 1-3s). Convert later if dashboards need to
  fire 10+ exports concurrently.
