# Phase 1 Rollout — `WAREHOUSE_READS_ENABLED=1`

Switches dashboard reads from live Samsara to the local warehouse tables
that the ingestor refreshes every 60s–30min. Expected impact: hot
endpoint p50 drops from 800-2000 ms → 50-100 ms; Samsara API call
volume drops by ~80%.

The flag is **per-process** (read at startup from `WAREHOUSE_READS_ENABLED`).
Setting it in `.env` and restarting the API + worker pods is the rollout.
Unsetting + restart is the rollback. **Zero data loss in either direction.**

---

## Pre-flight (run before you flip anything)

### 1. Confirm the ingestor is running
The warehouse pods write all 9 tables. Verify in the worker pod logs:

```bash
journalctl -u 4truck-bot -n 200 | grep "warehouse_"
# should show heartbeat lines from APScheduler every 60s for vehicle_state,
# every 5m for safety_events / vehicle_health, every 2m for vehicle_faults, etc.
```

If you don't see them, the scheduler isn't running. Verify
`ENABLE_SCHEDULER=1` is set on the worker process and restart.

### 2. Inspect warehouse contents per tenant
Hit the new diagnostic endpoint **as an account-admin user** of each
tenant you plan to enable. Returns row counts + last-seen timestamp
per table:

```bash
curl -s -H "Authorization: Bearer $JWT" \
  https://4truck.us/api/admin/warehouse-status | jq
```

Healthy response (every table populated, recent timestamps):

```json
{
  "account_id": 7,
  "warehouse_reads_enabled": false,
  "tables": [
    {"table": "vehicle_state",             "rows": 84, "last_seen": "2026-05-07T11:34:12Z"},
    {"table": "vehicle_health_snapshot",   "rows": 84, "last_seen": "2026-05-07T11:30:01Z"},
    {"table": "vehicle_fault_snapshot",    "rows":  6, "last_seen": "2026-05-07T11:32:18Z"},
    {"table": "fleet_weather_snapshot",    "rows": 84, "last_seen": "2026-05-07T11:25:00Z"},
    {"table": "fleet_efficiency_snapshot", "rows": 84, "last_seen": "2026-05-07T11:00:00Z"},
    {"table": "safety_event_log",          "rows": 273,"last_seen": "2026-05-07T11:33:40Z"},
    {"table": "driver_efficiency_daily",   "rows":  6, "last_seen": "2026-05-06"},
    {"table": "vehicle_telemetry_hourly",  "rows":210, "last_seen": "2026-05-07T11:00:00Z"},
    {"table": "geofence_definitions",      "rows": 12, "last_seen": "2026-05-07T11:00:00Z"}
  ],
  "summary": {"total": 9, "populated": 9, "empty": 0}
}
```

If `summary.empty > 0` for any tenant you plan to enable, run the
backfill (next step). The reader gracefully falls back to live Samsara
on empty tables, but every empty table is one less endpoint that
benefits from the rollout.

### 3. (If needed) Run the backfill
Idempotent — safe to run repeatedly:

```bash
# All active accounts (slow first run, ~30s per account):
python -m scripts.backfill_warehouse

# One account only (fastest validation path):
python -m scripts.backfill_warehouse --account 7

# Skip the slow paginated Samsara endpoints (smoke test):
python -m scripts.backfill_warehouse --account 7 --skip-events --skip-efficiency
```

Re-hit `/api/admin/warehouse-status` after backfill. Every table should
now have rows and a recent `last_seen`.

### 4. Sanity-compare warehouse vs live for one tenant
Run identical queries against both backends in a python REPL:

```python
import asyncio
from infra import config
from infra.startup import initialize, shutdown

async def main():
    await initialize()
    from capabilities.telemetry.service import get_vehicle_health
    config.WAREHOUSE_READS_ENABLED = False
    live = await get_vehicle_health(7)
    config.WAREHOUSE_READS_ENABLED = True
    wh = await get_vehicle_health(7)
    print(f"live={len(live)}  warehouse={len(wh)}")
    # vehicle_id sets should match within ~5 trucks (warehouse is up to 5min stale)
    print(set(v['name'] for v in live) ^ set(v['name'] for v in wh))
    await shutdown()

asyncio.run(main())
```

A diff > 5% on row counts or large symmetric difference on names means
the ingestor isn't keeping up — investigate before flipping the flag.

---

## Rollout

### Stage 1 — single-tenant staging
1. Set `WAREHOUSE_READS_ENABLED=1` in the staging `.env`
2. `make restart` (or `systemctl restart 4truck-api`)
3. Watch staging for 1 hour:
   - `journalctl -u 4truck-api -f | grep -E "scorecard.timing|samsara"`
   - Expect: `scorecard.timing` lines drop from `total=2000-30000ms` to `total=50-300ms`
   - Expect: `samsara client GET /fleet/...` drops by ~80%

### Stage 2 — production canary (one tenant)
The flag is global per-pod, so a true canary requires either:
- **Option A:** Run a separate API pod with the flag set; route one tenant's traffic to it via nginx `map $http_authorization` (heavy)
- **Option B:** Just flip globally and accept a brief blast radius (recommended)

We've validated graceful fallback on empty tables, so Option B is the right
trade-off. Schedule the flip during low-traffic hours.

### Stage 3 — full production rollout
1. Set `WAREHOUSE_READS_ENABLED=1` in production `.env`
2. `systemctl restart 4truck-api 4truck-bot` (zero-downtime requires
   gunicorn + multiple workers — see Phase 2; until then expect ~5s
   blip)
3. Tail logs for 1 hour as in Stage 1
4. Hit `/api/admin/warehouse-status` from a few tenants — confirm
   `warehouse_reads_enabled: true`

---

## Monitoring (24h after rollout)

| Signal | Where | Healthy range |
|---|---|---|
| `scorecard.timing total=Xms` | API logs (INFO when ≥1s) | < 500ms p95 |
| Samsara API call rate | external dashboard or count `GET /fleet/...` log lines | down ~80% from baseline |
| `/api/admin/warehouse-status` per tenant | hit weekly | `summary.empty == 0` |
| `last_seen` per table | warehouse-status | within ingestor cadence (60s for state, 5m for health, hourly for efficiency) |
| User-reported "data feels stale" | support tickets | zero |

---

## Rollback

If anything looks wrong:

```bash
# 1. Comment out or remove WAREHOUSE_READS_ENABLED in .env
sed -i 's/^WAREHOUSE_READS_ENABLED=1/# WAREHOUSE_READS_ENABLED=1/' .env

# 2. Restart the API
systemctl restart 4truck-api

# 3. Verify the flag is off
curl -s -H "Authorization: Bearer $JWT" \
  https://4truck.us/api/admin/warehouse-status | jq .warehouse_reads_enabled
# expect: false
```

Reads flip back to live Samsara instantly. **No data loss** — the
warehouse tables stay populated and the ingestor keeps running, so the
flag can be flipped on again at any time without re-doing the backfill.

---

## What this rollout does NOT change

- **Live Samsara API** is still the source of truth — the warehouse is
  a derived cache, refreshed by the ingestor
- **No data semantics change** — the warehouse_reader returns the same
  shape as the live client, just from local rows
- **Cold/empty tables auto-fallback** to live Samsara, so no endpoint
  goes "dark" if a tenant's warehouse is missing
- **No DB migration required** — schema was deployed in Phase C and
  has been live for ages; we're just flipping which path the readers
  take

---

## Known gaps after Phase 1

These hot paths are still fully live-Samsara even with the flag on, and
will be picked up in Phase 2/3:

| Endpoint | Why still live | When to fix |
|---|---|---|
| `/admin/users/.../avatar` | image bytes, not warehoused | Phase 4 (CDN) |
| `/maintenance/odometer/...` | uses `_get_paginated_history` directly | Future |
| `client.get_vehicle_detail` | per-vehicle drilldown, not warehoused | Future |

These are minority traffic; the bulk of dashboard requests will hit the
warehouse after the flag flip.
