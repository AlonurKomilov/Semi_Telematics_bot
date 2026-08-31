# Phase 2 Rollout — Multi-process gunicorn

Splits the API server out of the legacy single-process `python run.py`
model into N gunicorn workers. The bot + scheduler stay in their own
single-instance process so Telegram polling + APScheduler global lock
keep working unchanged.

**Expected impact:** API CPU saturation rises from one core to all
cores; sustained RPS ceiling rises from ~10 → ~500 (rough — depends on
endpoint mix).

**Zero data-model changes.** Everything is process-level wiring +
configuration. Rollback is a one-line `ExecStart` revert + restart.

---

## Architecture before vs after

### Before
```
┌────────────────── 1 process ──────────────────┐
│  python run.py                                │
│  ├── uvicorn (single-worker, 1 CPU)           │
│  ├── Telegram bot polling                     │
│  └── APScheduler (warehouse jobs, alerts, …)  │
└───────────────────────────────────────────────┘
              ▲
              │
        nginx :443
```

### After
```
┌──── 4truck-api.service ─────┐  ┌── 4truck-bot.service ──┐
│  gunicorn arbiter           │  │  python run.py          │
│  ├── uvicorn worker #1      │  │  ├── Telegram bot       │
│  ├── uvicorn worker #2      │  │  └── APScheduler        │
│  ├── uvicorn worker #3      │  │     (single instance)   │
│  └── … N workers            │  │                         │
└─────────────────────────────┘  └─────────────────────────┘
              ▲                              ▲
              │ shared Redis + DB            │
        nginx :443                    Telegram polling
```

Key invariants:
- **API workers are stateless.** Anything that must survive across
  requests lives in Redis (auth tokens, SWR cache, alert dedup) or
  the DB (chat history, scorecards).
- **Bot+scheduler runs as exactly one process.** APScheduler also has
  a Redis distributed lock as a belt-and-braces safeguard.
- **No worker writes to disk** for anything that another worker reads.
- **Each worker has its own Redis pool, asyncpg pool, aiohttp session.**
  These connection objects cannot be shared across forks.

---

## Pre-flight checks

### 1. Confirm gunicorn is installed
```bash
python3 -c "import gunicorn; print(gunicorn.__version__)"
# expect: 22.0.0 or newer
```
If missing: `pip install -r requirements.txt`.

### 2. Boot one gunicorn worker locally
Smoke-test the lifespan + module-level `app` shim before touching prod:
```bash
cd /home/abcdev/projects/Semi_Telematics_bot
GUNICORN_WORKERS=1 \
  python3 -m gunicorn -c gunicorn.conf.py interfaces.api.app:app
# expect:
#   gunicorn starting: bind=0.0.0.0:8000 workers=1 ...
#   Started server process
#   API lifespan: initialised platform (gunicorn worker mode)
#   Application startup complete.
```
Hit `curl localhost:8000/api/health` — expect 200.
SIGTERM the process (`Ctrl-C`); expect:
```
   API lifespan: shut down platform
   Worker exiting (pid: ...)
```
If the lifespan startup line never prints, the issue is import order
in `interfaces/api/app.py:_lifespan`.

### 3. Audit shared state on the running deployment
Anything new that has been added to a module-level dict / `TTLCache` /
asyncio primitive **after** Phase 2 lands needs to either:
- Live in the worker process (bot/scheduler) only, OR
- Move to Redis.

Run:
```bash
grep -rn "^_[a-z_]*: \(dict\|list\|set\|TTLCache\|Optional\|asyncio\)\b" \
  --include="*.py" infra/ adapters/ capabilities/ interfaces/api/ \
  | grep -v test
```
Compare against `docs/runbooks/multiprocess-rollout.md`'s "audited safe"
list. New entries that aren't in the safe list need triage.

### 4. Confirm nginx config is valid
```bash
sudo nginx -t
```
Phase 2 adds `proxy_http_version 1.1` + `proxy_set_header Connection ""`
to every `/api/*` location. Without these the upstream `keepalive 32`
directive does nothing.

---

## Rollout

### Stage 1 — staging
1. Pull the Phase 2 branch on staging
2. `pip install -r requirements.txt` (installs gunicorn)
3. `sudo cp 4truck-api.service /etc/systemd/system/`
   `sudo cp 4truck-bot.service /etc/systemd/system/`
4. `sudo systemctl daemon-reload`
5. `sudo systemctl restart 4truck-bot 4truck-api`
6. Watch:
   ```bash
   sudo journalctl -u 4truck-api -f | head -50
   ```
   Expect N "gunicorn worker forked" lines (where N = `GUNICORN_WORKERS`,
   default `2*CPU+1`), each followed by "API lifespan: initialised platform".
7. Hit `/api/health` from outside; smoke-test `/dashboard/` SPA.
8. Check `nginx -t && systemctl reload nginx` to apply the new
   upstream + proxy headers.

### Stage 2 — production canary
Same steps. The blast radius is: while the API is restarting (5-15s),
in-flight requests drop. Mitigated by:
- `graceful_timeout = 30s` in gunicorn.conf.py — workers finish in-
  flight requests before SIGKILL.
- Frontend's `apiJSONSlow` retries via React Query.

Schedule the restart at low-traffic hours.

### Stage 3 — production
```bash
git pull
pip install -r requirements.txt
sudo cp 4truck-api.service 4truck-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart 4truck-bot
sudo systemctl restart 4truck-api
# nginx config already deployed from earlier step
sudo nginx -t && sudo systemctl reload nginx
```

---

## Verification

### Workers are running
```bash
ps -ef | grep gunicorn
# expect: 1 master + N workers (N = GUNICORN_WORKERS)
```

Each worker is a separate process with its own PID — visible in the
gunicorn arbiter logs:
```bash
sudo journalctl -u 4truck-api -n 100 | grep "worker forked"
```

### Lifespan ran for every worker
```bash
sudo journalctl -u 4truck-api -n 200 | grep "API lifespan"
# expect N "initialised platform" lines
```

### Load is balanced across workers
After ~1 minute of dashboard traffic:
```bash
sudo journalctl -u 4truck-api -n 500 | grep -oP 'pid=\d+' | sort | uniq -c
# expect roughly even distribution across worker PIDs
```

### Keepalive is working
```bash
ss -tn state established '( dport = :8000 or sport = :8000 )' | wc -l
# expect close to N × keepalive (= N × 32 connections held open)
```
If this is near zero under load, the `proxy_http_version 1.1` /
`Connection ""` headers aren't applied.

### Bot + scheduler still single-instance
```bash
ps -ef | grep "python.*run.py" | grep -v grep
# expect exactly ONE row
```

### Scheduler global lock still held
```bash
redis-cli -p 8002 GET lock:scheduler:global
# expect: "1"
```

---

## Monitoring (24h after rollout)

| Signal | Where | Healthy |
|---|---|---|
| `4truck-api` worker restarts | `journalctl -u 4truck-api \| grep "worker exited"` | ≤ N per max_requests cycle (≈ every few hours) |
| Gunicorn worker memory | `ps -o pid,rss,cmd -p $(pgrep -f uvicorn.workers)` | stays bounded; max_requests recycles before leak grows |
| RPS per worker | gunicorn access log | roughly even across PIDs |
| `lock:scheduler:global` heartbeat | Redis | always present |
| 504 rate | nginx access log `\b504\b` | < 0.01 % (down from baseline) |
| `lock:scheduler:global` flapping | `journalctl -u 4truck-bot \| grep "lock"` | one acquire on startup, no further activity |

---

## Rollback

If anything breaks:

```bash
# Revert API service to legacy single-process
sudo cp 4truck-api.service.bak /etc/systemd/system/4truck-api.service 2>/dev/null \
  || cat <<'EOF' | sudo tee /etc/systemd/system/4truck-api.service
[Unit]
Description=4truck API Server (legacy single-process)
After=network-online.target

[Service]
Type=simple
User=abcdev
WorkingDirectory=/home/abcdev/projects/Semi_Telematics_bot
ExecStart=/usr/bin/python3 /home/abcdev/projects/Semi_Telematics_bot/run.py
EnvironmentFile=/home/abcdev/projects/Semi_Telematics_bot/.env
Environment=ENABLE_API=1
Environment=ENABLE_BOT=0
Environment=ENABLE_SCHEDULER=0
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl restart 4truck-api
```

`run.py` still uses `uvicorn` directly (no gunicorn dependency), so the
rollback path is fully insulated from the new code paths.

The lifespan handler is **idempotent** — `_lifespan` checks
`infra.startup.tenant_registry is None` before calling `initialize()`,
so calling `create_api()` from `run.py` (which initialises platform
itself) doesn't double-init.

---

## Capacity expectation

| Setting | Before | After |
|---|---|---|
| API workers | 1 | `2*CPU+1` (default 9 on 4-core box) |
| Concurrent requests served | ~50 (single-loop limit) | ~500+ |
| CPU utilisation under load | ~12% (1/8 cores) | ~80%+ |
| p99 under burst | spikes 5-10s | < 2s |
| Per-request overhead | uvicorn loop | gunicorn IPC + uvicorn loop (~1 ms extra) |

---

## Tuning

All knobs live in `gunicorn.conf.py` and are env-overridable:

| Env var | Default | When to tune |
|---|---|---|
| `GUNICORN_WORKERS` | `2*CPU+1` | Bigger box → more workers (linear) |
| `GUNICORN_TIMEOUT` | 90s | Match nginx `proxy_read_timeout` |
| `GUNICORN_KEEPALIVE` | 75s | Match nginx upstream `keepalive_timeout` |
| `GUNICORN_MAX_REQUESTS` | 1000 | Lower if memory grows; raise if recycle blips show |
| `GUNICORN_MAX_REQUESTS_JITTER` | 100 | Always > 0 so workers don't all recycle together |
| `GUNICORN_GRACEFUL_TIMEOUT` | 30s | Lengthen if heavy endpoints (PDF gen) leave requests in flight at SIGTERM |

---

## Known limitations after Phase 2

- **Single-host today.** N workers fix the per-CPU bottleneck but they
  all share one box. True 10k scale needs k8s replicas across hosts —
  the gunicorn config above ports cleanly (just remove the
  `127.0.0.1:8000` upstream and put the load balancer in front).
- **Per-worker caches** (Samsara `_client_cache`, AI `_chat_histories`
  hot cache) are warm per-worker. Cold-cache cost is paid once per
  worker per key, not once per cluster — Phase 1 SWR layer + the
  DB-backed chat history make this acceptable.
- **APScheduler still in-process** with the bot. Phase 3 (ARQ queue)
  moves heavy jobs out of the worker.
- **No graceful nginx config reload on worker restarts** —
  `keepalive_requests 1000` recycles connections naturally, so the
  blip during `systemctl restart 4truck-api` is the only window where
  ongoing connections drop.
