#!/usr/bin/env bash
#
# Redis Sentinel cutover — moves the project from the single-node
# Redis on port 8002 to the Sentinel topology defined in
# docker-compose.sentinel.yml.
#
# Idempotent and resumable.  Safe to dry-run first via:
#   bash scripts/redis_sentinel_cutover.sh check
#
# Full cutover (writes .env, restarts services):
#   bash scripts/redis_sentinel_cutover.sh apply

set -euo pipefail

if [ -t 1 ]; then
  R="\033[31m"; G="\033[32m"; Y="\033[33m"; B="\033[34m"; N="\033[0m"
else
  R=""; G=""; Y=""; B=""; N=""
fi
say()  { printf "${B}==>${N} %s\n" "$*"; }
ok()   { printf "${G}OK${N}  %s\n" "$*"; }
warn() { printf "${Y}WARN${N} %s\n" "$*"; }
die()  { printf "${R}FAIL${N} %s\n" "$*" >&2; exit 1; }

MODE="${1:-check}"
case "$MODE" in
  check|apply) ;;
  *) die "Usage: $0 {check|apply}" ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -f docker-compose.sentinel.yml ] || die "docker-compose.sentinel.yml not found"
[ -f .env ] || die ".env not found"

# ── 1. Pre-flight ──────────────────────────────────────────────────
say "Pre-flight checks"

if ! command -v docker-compose >/dev/null && ! docker compose version >/dev/null 2>&1; then
  die "docker-compose (v1) or 'docker compose' (v2) not installed"
fi

if command -v docker-compose >/dev/null; then
  DC="docker-compose"
else
  DC="docker compose"
fi
ok "Using compose driver: $DC"

# ── 2. Existing Redis state (informational) ────────────────────────
say "Current Redis (8002) state"
if redis-cli -p 8002 ping 2>/dev/null | grep -q PONG; then
  arq_jobs=$(redis-cli -p 8002 ZCARD arq:queue 2>/dev/null || echo "0")
  total_keys=$(redis-cli -p 8002 DBSIZE 2>/dev/null || echo "0")
  ok "  reachable | ARQ queue depth=${arq_jobs} | total keys=${total_keys}"
  if [ "$arq_jobs" != "0" ] && [ "$arq_jobs" != "" ]; then
    warn "  ARQ has pending jobs — they will NOT be migrated (transient cache pattern)."
    warn "  Either drain the queue before cutover or accept losing in-flight jobs."
  fi
else
  warn "Redis on 8002 not reachable — that's fine, just informational"
fi

# ── 3. Bring up the Sentinel topology (containers) ─────────────────
say "Starting Sentinel topology (6 containers)"
if [ "$MODE" = "apply" ]; then
  $DC -f docker-compose.sentinel.yml up -d
  ok "  containers started"
  sleep 4
else
  echo "  (DRY-RUN — would run: $DC -f docker-compose.sentinel.yml up -d)"
fi

# ── 4. Verify topology health ──────────────────────────────────────
say "Verifying Sentinel sees a master"
if [ "$MODE" = "apply" ]; then
  for sentinel_port in 26379 26380 26381; do
    addr=$(redis-cli -p "$sentinel_port" -a 4truck-redis-pass --no-auth-warning \
             sentinel get-master-addr-by-name mymaster 2>/dev/null | head -1 || true)
    if [ -n "$addr" ]; then
      ok "  sentinel ${sentinel_port}: master = ${addr}:..."
    else
      warn "  sentinel ${sentinel_port}: master not reachable (will retry)"
    fi
  done
fi

# ── 5. Update .env (idempotent — only adds if missing) ─────────────
say "Updating .env"

ensure_env_kv() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" .env; then
    current=$(grep -E "^${key}=" .env | head -1 | cut -d= -f2-)
    if [ "$current" = "$val" ]; then
      ok "  ${key} already correct"
    else
      warn "  ${key} already set to a different value — leaving it.  Edit manually if needed."
    fi
  else
    if [ "$MODE" = "apply" ]; then
      echo "${key}=${val}" >> .env
      ok "  appended ${key}"
    else
      echo "  (DRY-RUN — would append: ${key}=${val})"
    fi
  fi
}

ensure_env_kv "REDIS_SENTINELS" "127.0.0.1:26379,127.0.0.1:26380,127.0.0.1:26381"
ensure_env_kv "REDIS_MASTER_NAME" "mymaster"

# Update REDIS_URL to carry the auth + db only (the host is ignored in
# Sentinel mode but the password + db are taken from this URL).
if grep -qE "^REDIS_URL=" .env; then
  current_url=$(grep -E "^REDIS_URL=" .env | head -1 | cut -d= -f2-)
  if echo "$current_url" | grep -q "4truck-redis-pass"; then
    ok "  REDIS_URL already carries Sentinel password"
  else
    warn "  REDIS_URL points at the OLD single-node Redis."
    warn "  In Sentinel mode the URL only carries auth + db.  Recommended:"
    warn "    REDIS_URL=redis://:4truck-redis-pass@unused/0"
    warn "  (Leaving in place — adjust manually if you want Sentinel auth from REDIS_URL.)"
  fi
fi

# ── 6. Restart services ────────────────────────────────────────────
if [ "$MODE" = "apply" ]; then
  say "Restarting services to pick up Sentinel config"
  sudo systemctl restart 4truck-api 4truck-bot 4truck-queue
  sleep 4
  for svc in 4truck-api 4truck-bot 4truck-queue; do
    state=$(systemctl is-active "$svc" || true)
    if [ "$state" = "active" ]; then
      ok "  $svc: $state"
    else
      die "$svc: $state — check 'sudo journalctl -u $svc -n 50'"
    fi
  done
  # Confirm the bot picked up Sentinel mode.
  echo
  say "Checking logs for Sentinel connection"
  sudo journalctl -u 4truck-bot -n 50 --no-pager 2>/dev/null | grep -E "Redis connected via Sentinel|Redis connected:|Sentinel mode" | tail -3 || \
    warn "  No matching log line yet — give it 10s and check 'sudo journalctl -u 4truck-bot -n 50'"
fi

echo
if [ "$MODE" = "apply" ]; then
  ok "Cutover applied.  Verify failover works:"
  echo "    docker stop 4truck-redis-master   # kill the master"
  echo "    sleep 35 && redis-cli -p 26379 -a 4truck-redis-pass --no-auth-warning sentinel get-master-addr-by-name mymaster"
  echo "    # should report a replica address (failover succeeded)"
  echo "    docker start 4truck-redis-master  # restart — it becomes a replica"
else
  ok "Dry-run complete.  Re-run with 'apply' to commit:"
  echo "    bash scripts/redis_sentinel_cutover.sh apply"
fi
