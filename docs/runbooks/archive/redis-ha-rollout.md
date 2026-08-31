# Redis HA (Sentinel) rollout runbook

Replaces the single Redis instance with a Sentinel topology so the cache, ARQ queue, scheduler lock, and rate-limiter survive node failure.

## Architecture

```
              +---------------+      +---------------+
              | sentinel-1    |      | sentinel-2    |
              | (quorum vote) |      | (quorum vote) |
              +-------+-------+      +-------+-------+
                      \\                    /
                       \\                  /
                        +----------------+
                        | sentinel-3     |
                        | (quorum vote)  |
                        +----+-----------+
                             |
              +--------------+--------------+
              | master (writes + reads)     |
              |  failover target: replica-1 |
              +--+---------------------+----+
                 |                     |
        +--------+-------+    +--------+-------+
        | replica-1      |    | replica-2      |
        | (read-only)    |    | (read-only)    |
        +----------------+    +----------------+
```

3 sentinels are the **minimum quorum**: any 2 must agree before failover.  2 replicas give one spare even during failover.

## Code landed (already merged)

- [infra/cache.py](../../infra/cache.py) `init_redis()` — picks Sentinel mode when `REDIS_SENTINELS` is set, falls back to direct `REDIS_URL` otherwise.  No code-side changes for callers; the same `_pool` is used either way.
- [capabilities/jobs/worker.py](../../capabilities/jobs/worker.py) ARQ `WorkerSettings.redis_settings` — Sentinel-aware via the same env vars.
- The scheduler distributed lock (`infra.cache.acquire_lock("scheduler:global")`) automatically picks up Sentinel because it routes through the cache pool above.

## Rollout

### Stage 0 — set up the Sentinel topology

You need three machines or three containers running:
- 1 Redis master
- 2 Redis replicas (`replicaof <master-host> <master-port>`)
- 3 Sentinel daemons watching the master

For development, the easiest is docker-compose:

```yaml
# docker-compose.sentinel.yml — drop into the project root.
services:
  redis-master:
    image: redis:7-alpine
    command: redis-server --requirepass devpass --masterauth devpass --port 6379
    ports: ["6379:6379"]

  redis-replica-1:
    image: redis:7-alpine
    command: >
      redis-server
        --requirepass devpass --masterauth devpass --port 6380
        --replicaof redis-master 6379
    ports: ["6380:6380"]
    depends_on: [redis-master]

  redis-replica-2:
    image: redis:7-alpine
    command: >
      redis-server
        --requirepass devpass --masterauth devpass --port 6381
        --replicaof redis-master 6379
    ports: ["6381:6381"]
    depends_on: [redis-master]

  sentinel-1:
    image: redis:7-alpine
    command: >
      sh -c 'echo "
        sentinel monitor mymaster redis-master 6379 2
        sentinel auth-pass mymaster devpass
        sentinel down-after-milliseconds mymaster 5000
        sentinel failover-timeout mymaster 10000
        sentinel parallel-syncs mymaster 1
        port 26379
      " > /etc/sentinel.conf && redis-sentinel /etc/sentinel.conf'
    ports: ["26379:26379"]
    depends_on: [redis-master]

  sentinel-2:
    image: redis:7-alpine
    command: >
      sh -c 'echo "
        sentinel monitor mymaster redis-master 6379 2
        sentinel auth-pass mymaster devpass
        sentinel down-after-milliseconds mymaster 5000
        sentinel failover-timeout mymaster 10000
        sentinel parallel-syncs mymaster 1
        port 26380
      " > /etc/sentinel.conf && redis-sentinel /etc/sentinel.conf'
    ports: ["26380:26380"]
    depends_on: [redis-master]

  sentinel-3:
    image: redis:7-alpine
    command: >
      sh -c 'echo "
        sentinel monitor mymaster redis-master 6379 2
        sentinel auth-pass mymaster devpass
        sentinel down-after-milliseconds mymaster 5000
        sentinel failover-timeout mymaster 10000
        sentinel parallel-syncs mymaster 1
        port 26381
      " > /etc/sentinel.conf && redis-sentinel /etc/sentinel.conf'
    ports: ["26381:26381"]
    depends_on: [redis-master]
```

For production, deploy on three separate VMs (one master/replica/sentinel per machine, ideally across availability zones), or use a managed offering (AWS ElastiCache for Redis with replication group, Upstash, Redis Cloud).

### Stage 1 — point the application at Sentinel

Add to `.env`:

```
# Sentinel topology — comma-separated host:port (port defaults to 26379)
REDIS_SENTINELS=sentinel-1:26379,sentinel-2:26380,sentinel-3:26381

# Master name as declared in sentinel.conf
REDIS_MASTER_NAME=mymaster

# Auth + DB still come from REDIS_URL.  The host part is ignored in
# Sentinel mode — Sentinel resolves the current master itself.
REDIS_URL=redis://:devpass@unused/0
```

Restart the services:

```bash
sudo systemctl restart 4truck-api 4truck-bot 4truck-queue
```

Watch for the log line:

```
Redis connected via Sentinel (master=mymaster, sentinels=3, max_conn=50)
```

If you see `Sentinel mode failed (...) — falling back to in-memory`, check:
- All sentinels are reachable on TCP
- `REDIS_MASTER_NAME` matches `sentinel monitor <name>` exactly
- The password in `REDIS_URL` matches `auth-pass mymaster`

### Stage 2 — verify failover

```bash
# Stop the master container/process.
docker compose -f docker-compose.sentinel.yml stop redis-master

# Wait ~30s.  Sentinels promote a replica.
sleep 35

# Check the application — it should reconnect to the new master.
curl -sI http://127.0.0.1:8000/api/health | head -1
# → HTTP/1.1 200 OK

# Inspect any sentinel:
redis-cli -p 26379 -a devpass sentinel get-master-addr-by-name mymaster
# → 1) "redis-replica-1"
#   2) "6380"
```

The ARQ worker logs will briefly show `ConnectionError` during the failover window — expected; arq's built-in retry handles it.

Restart the old master and let it become a replica:

```bash
docker compose -f docker-compose.sentinel.yml start redis-master
```

### Stage 3 — operational dashboards

Add to your Grafana / monitoring:

```promql
# Master flip detection — counts how often Sentinel changes the master.
rate(redis_master_failover_total[5m]) > 0

# Replica lag — alerts if any replica is more than 5s behind.
redis_master_repl_offset - redis_slave_repl_offset > 5000000
```

## Known caveats

### Rate-limit accuracy during failover

The slowapi rate-limit counters live in Redis.  During the ~30s failover window:
- Requests may briefly succeed past the limit (failover is read-as-fast; counter resets)
- Or briefly be over-limited if the replica's snapshot is slightly stale

This is acceptable — abuse detection across a 30s gap isn't a security-meaningful regression.

### ARQ queue persistence

ARQ's job queue is RDB-persisted by default.  If a job is enqueued just before master fails, and the replica's RDB snapshot lags, the job can be lost.  For critical jobs, use `arq.func(..., timeout=..., max_tries=...)` so the producer-side retry catches it.

### Single-region deployment

This topology is single-region HA.  For multi-region disaster recovery you'd add Redis replication across regions and Sentinel monitoring across both — outside the scope of Tier 4 Step 3.

## Rollback

```bash
# 1. Unset the sentinel env vars in .env (or comment them out)
sed -i 's/^REDIS_SENTINELS=/#REDIS_SENTINELS=/' .env
sed -i 's/^REDIS_MASTER_NAME=/#REDIS_MASTER_NAME=/' .env

# 2. Set REDIS_URL to the (former) master directly
REDIS_URL=redis://:devpass@127.0.0.1:6379/0

# 3. Restart services
sudo systemctl restart 4truck-api 4truck-bot 4truck-queue
```

The cache code falls back to the direct path automatically when `REDIS_SENTINELS` is empty.
