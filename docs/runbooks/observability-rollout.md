# Phase 6 Rollout — Prometheus metrics + OpenTelemetry tracing

Adds end-to-end observability to everything Phases 1-5 already built:

| Layer | What we now see |
|---|---|
| API requests | RPS, p50/p95/p99 latency, error rate per route |
| Samsara API | latency + status counter per upstream endpoint |
| SWR cache | fresh/stale/miss/computed ratio + single-flight collapse count |
| Scoring service | per-stage histogram (`prepare_companies`, `signals_gather`, `efficiency_fetch`, `scoring_loop`, `total`) |
| ARQ jobs | per-function duration + failure rate; queue-depth gauge |
| OTel traces | nginx → FastAPI → asyncpg → Samsara → Redis (when `OTEL_EXPORTER_OTLP_ENDPOINT` is set) |

Everything is **optional** — when `prometheus-client` / OTel libs aren't
installed, the app boots identically and the metrics surfaces are
no-ops. **Zero application changes** at deploy time; Prometheus and
Grafana run alongside the existing stack.

---

## What ships

| File | Purpose |
|---|---|
| [infra/observability.py](../../infra/observability.py) | Metric definitions + `init_observability(app)` + ARQ queue-depth poller. Stub-pattern means domain code calls `record_X(...)` unconditionally. |
| [interfaces/api/app.py](../../interfaces/api/app.py) | `init_observability(app)` mounted in `create_api`; lifespan kicks off `poll_arq_queue_depth()` background task. |
| [adapters/samsara/client.py](../../adapters/samsara/client.py) | `_get(...)` wraps every Samsara call in latency timing + status label. |
| [infra/cache.py](../../infra/cache.py) | `get_or_compute` records each path: fresh / stale / miss / computed; in-process single-flight bumps a counter. |
| [capabilities/scoring/service.py](../../capabilities/scoring/service.py) | `_mark()` now emits both the structured log line AND a Prometheus histogram observation. |
| [capabilities/jobs/functions.py](../../capabilities/jobs/functions.py) | Each ARQ function records duration + status (success/failed). |
| [observability/prometheus/prometheus.yml](../../observability/prometheus/prometheus.yml) | Scrape config — single static target; swap to `kubernetes_sd_configs` for k8s. |
| [observability/alerts/4truck.yml](../../observability/alerts/4truck.yml) | 8 alert rules across API, Samsara, cache, ARQ, scoring. |
| [observability/grafana/dashboards/4truck-overview.json](../../observability/grafana/dashboards/4truck-overview.json) | One single-pane Grafana dashboard with 10 panels covering everything above. |
| [observability/docker-compose.yml](../../observability/docker-compose.yml) | Local dev stack — Prometheus + Grafana auto-provisioned. |

---

## Pre-flight

### 1. Install deps
```bash
pip install -r requirements.txt
python3 -c "import prometheus_client; print(prometheus_client.__version__)"
# expect: 0.20.x or newer
```

### 2. Smoke-boot the API and hit `/metrics`
```bash
GUNICORN_WORKERS=1 python3 -m gunicorn -c gunicorn.conf.py interfaces.api.app:app &
sleep 3
curl -s http://localhost:8000/metrics | head -20
# expect: Prometheus exposition format (HELP / TYPE / metric lines)
```

### 3. Generate a few requests, look for our custom metrics
```bash
curl -s http://localhost:8000/api/health > /dev/null
curl -s http://localhost:8000/metrics | grep -E "samsara_|cache_|scorecard_|arq_"
```
You'll see metric **definitions** even before any traffic — Prometheus
declares them at module load.

### 4. Verify the lifespan kicked off the ARQ poller
```bash
curl -s http://localhost:8000/metrics | grep arq_queue_depth
# expect at least one line; value will be 0 if the queue is empty
```

---

## Local dev — full stack via docker compose

```bash
cd observability
docker compose up -d
docker compose ps
# expect: 4truck-prometheus + 4truck-grafana both Up
```

Open:
- Prometheus targets: <http://localhost:9090/targets> — `4truck-api` should be UP
- Prometheus rules:   <http://localhost:9090/rules>   — 8 alert rules loaded
- Grafana:            <http://localhost:3000>          (admin/admin) — `4truck — Operational Overview` dashboard pre-provisioned

Hit the API a few times, then watch the Grafana panels populate within
30 seconds (default scrape + query refresh).

---

## Production rollout

### Stage 1 — origin
1. Pull Phase 6 branch on production host
2. `pip install -r requirements.txt`
3. `sudo systemctl restart 4truck-api 4truck-bot 4truck-queue`
4. Verify `/metrics` reachable from the same host:
   ```bash
   curl -s http://127.0.0.1:8000/metrics | head -5
   ```

### Stage 2 — Prometheus
Choose one:

**a) Self-hosted Prometheus + Grafana** (small fleet — single host):
```bash
cd /home/abcdev/projects/Semi_Telematics_bot/observability
docker compose up -d
# port 9090 (Prometheus) + 3000 (Grafana) — open ports in nginx if you
# want them exposed via the public domain.
```

**b) Managed (Grafana Cloud / Datadog / Honeycomb)**:
- Add `remote_write` to `prometheus.yml`:
  ```yaml
  remote_write:
    - url: https://prometheus-prod-XX.grafana.net/api/prom/push
      basic_auth: {username: ..., password: ...}
  ```
- For OTel traces, set in `.env`:
  ```bash
  OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io
  OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=$HC_API_KEY
  OTEL_SERVICE_NAME=4truck-api
  ```
- Restart `4truck-api`. The OTel section of `init_observability()`
  picks up the env vars and starts shipping spans automatically.

### Stage 3 — alerts
1. Stand up Alertmanager (managed or self-hosted)
2. Edit `prometheus.yml` → uncomment `alertmanager:9093` target
3. POST `http://prometheus:9090/-/reload` to hot-reload alert rules
4. Test by setting an alert threshold low temporarily (e.g.
   `APIErrorRateHigh` to `> 0.0001`) and verify the page fires

---

## Verification

### Custom metrics show up
```bash
curl -s http://localhost:8000/metrics | grep -E "^# HELP" | grep -E "(samsara|cache|scorecard|arq)"
# expect:
#   # HELP samsara_request_seconds Latency of Samsara API calls in seconds
#   # HELP samsara_requests_total Total Samsara API calls
#   # HELP cache_get_or_compute_total SWR cache result by status
#   # HELP cache_single_flight_collapsed_total ...
#   # HELP scorecard_stage_seconds ...
#   # HELP arq_job_seconds ...
#   # HELP arq_queue_depth ...
```

### Cardinality is bounded
```bash
# Total label combinations across our custom metrics:
curl -s http://localhost:8000/metrics | grep -E "^(samsara_|cache_|scorecard_|arq_)" | wc -l
# expect: < 500 lines after a day of traffic
```
If this grows unbounded, audit which label is leaking high-cardinality
values (likely `endpoint` if a parameter is being included).

### Per-route latency breakdown works
After hitting the API:
```bash
curl -s http://localhost:8000/metrics | grep 'http_request_duration_seconds_bucket{handler="/api/safety/scorecards/composite"' | head
```

### Grafana dashboard loads
Open `4truck — Operational Overview` — every panel should render data
within 30 seconds of starting traffic.

---

## Alert rules summary

| Rule | Condition | Severity | Duration |
|---|---|---|---|
| `APIErrorRateHigh` | 5xx rate > 1% | critical | 5m |
| `APIp99LatencyHigh` | p99 > 5s | warning | 10m |
| `APIDown` | scrape target unreachable | critical | 2m |
| `Samsara5xxRateHigh` | upstream 5xx > 5% | warning | 10m |
| `SamsaraTimeoutRateHigh` | timeouts > 1 req/s | warning | 5m |
| `ScorecardCacheMissRateHigh` | miss rate > 30% | warning | 30m |
| `ARQQueueDepthHigh` | queue > 1000 | warning | 10m |
| `ARQJobFailureRateHigh` | fail rate > 5% | warning | 15m |
| `ScorecardSignalsGatherSlow` | p95 stage > 10s | warning | 15m |

Edit thresholds in [observability/alerts/4truck.yml](../../observability/alerts/4truck.yml).
After editing, hot-reload Prometheus:
```bash
curl -X POST http://localhost:9090/-/reload
```

---

## Rollback

Phase 6 is **purely additive** — disabling it doesn't break anything.

### Soft rollback — disable observability without uninstalling deps
```bash
echo 'OBSERVABILITY_ENABLED=0' >> .env
sudo systemctl restart 4truck-api
# /metrics returns 404; OTel exporter stops; metric stubs no-op.
```

### Hard rollback — uninstall deps
```bash
pip uninstall -y prometheus-client prometheus-fastapi-instrumentator \
  opentelemetry-api opentelemetry-sdk \
  opentelemetry-instrumentation-fastapi \
  opentelemetry-instrumentation-aiohttp-client \
  opentelemetry-instrumentation-asyncpg \
  opentelemetry-instrumentation-redis \
  opentelemetry-exporter-otlp-proto-http
sudo systemctl restart 4truck-api
# observability.py auto-detects missing imports and the app boots clean.
```

---

## Tuning

| Env var | Default | Purpose |
|---|---|---|
| `OBSERVABILITY_ENABLED` | `1` | Master kill switch |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | Set to a Honeycomb / Tempo / Jaeger URL to enable tracing. Leave unset for prod-without-tracing. |
| `OTEL_SERVICE_NAME` | `4truck-api` | Service name in trace UI |
| `DEPLOYMENT_ENV` | `production` | Trace resource label |
| `APP_VERSION` | `1.0.0` | Trace resource label |

### Trace sampling
Default 100% (every request traced). For high-volume production set:
```bash
OTEL_TRACES_SAMPLER=parentbased_traceidratio
OTEL_TRACES_SAMPLER_ARG=0.01      # 1% sampling
```

### Per-process pool tuning for Prometheus
Each gunicorn worker exposes its own `/metrics`. Prometheus scrapes
each, so multi-worker setups produce per-worker metrics
**aggregated by Prometheus**, not by gunicorn. No special config —
just point `prometheus.yml` at every worker port (or use file_sd
discovery on the gunicorn unix-socket layout).

---

## What this phase does NOT add

- **Log aggregation** (Loki/Splunk/CloudWatch) — still file-based today.
  Easy add: ship `journalctl -u 4truck-*` to your log backend.
- **Real-User-Monitoring** (RUM) — frontend latency / Core Web Vitals.
  Add Cloudflare RUM or Sentry browser SDK separately.
- **Synthetic uptime monitoring** — none in-band; rely on the
  `APIDown` Prometheus alert + nginx health checks.

---

## Wrapping up the 6-phase scale-out

Phase 6 closes the loop on Phases 1-5 — every metric we now collect
either validates a Phase 1-5 win (cache hit ratio, scorecard timing,
ARQ queue depth) or surfaces failures we couldn't see before
(Samsara 5xx rate, dual-write divergence, queue starvation).

Phase rollout sequence (recap):

| # | Phase | Status | Validated by |
|---|---|---|---|
| 1 | Warehouse `WAREHOUSE_READS_ENABLED=1` | infra ready | `samsara_requests_total` should drop ~80% post-flag |
| 2 | Multi-process gunicorn | infra ready | `http_requests_total` distributed across worker PIDs |
| 3 | ARQ background queue | infra ready | `arq_queue_depth` gauge + `arq_job_seconds` histogram |
| 4 | Cloudflare CDN | infra ready | (edge metrics in CF analytics, origin sees `/dashboard/assets/*` drop to ~5%) |
| 5 | PostgreSQL migration | 5a infra ready | `pg_adapter:untranslated` log lines + `dualwrite` counters |
| 6 | Observability | this commit | All of the above visible in Grafana |
