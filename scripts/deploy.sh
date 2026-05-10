#!/usr/bin/env bash
# 4truck deploy script — runs the privileged steps that finish the
# UI/UX/audit + ops cleanup landed in commit 7359727.
#
# Prerequisite: this script needs sudo (systemctl, cp into /etc/nginx,
# nginx reload).  Run it from your terminal — NOT from automation.
#
# Idempotent: each step checks current state and skips if already done.
#
# Usage:
#     ./scripts/deploy.sh                    # full deploy
#     ./scripts/deploy.sh --skip-queue       # skip ARQ worker install
#     ./scripts/deploy.sh --skip-nginx       # skip nginx reload
#     ./scripts/deploy.sh --dry-run          # print commands, run nothing

set -euo pipefail

REPO=/home/abcdev/projects/Semi_Telematics_bot
SKIP_QUEUE=0
SKIP_NGINX=0
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --skip-queue) SKIP_QUEUE=1 ;;
    --skip-nginx) SKIP_NGINX=1 ;;
    --dry-run)    DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Unknown flag: $arg"; exit 1 ;;
  esac
done

run() {
  echo "  + $*"
  if [ "$DRY_RUN" -eq 0 ]; then "$@"; fi
}

step() { echo; echo "── $* ───────────────────────────────────────"; }

# ── 0. Pre-flight checks ──────────────────────────────────────
step "0. Pre-flight"
cd "$REPO"
run git status --short | head -3 || true
run python3 -c "import gunicorn, arq, prometheus_client; print('deps ok')"
run test -f interfaces/miniapp/dist/index.html
run test -f interfaces/dashboard/dist/index.html

# ── 1. Free port 8000 (kill any manual run.py squatting on it) ─
step "1. Reclaim port 8000 from any manual run.py"
SQUATTERS=$(ss -tnlp 2>/dev/null | awk '/:8000 / {print $0}' | grep -oP 'pid=\K[0-9]+' | sort -u || true)
if [ -n "$SQUATTERS" ]; then
  for pid in $SQUATTERS; do
    cmd=$(ps -p "$pid" -o cmd= 2>/dev/null || true)
    case "$cmd" in
      *"python3 run.py"*|*"run.py"*)
        echo "  → killing manual run.py (PID $pid: $cmd)"
        run kill "$pid" || true
        sleep 1
        ;;
      *gunicorn*)
        echo "  → leaving gunicorn alone (PID $pid) — systemctl will restart it"
        ;;
      *)
        echo "  ! unknown process on :8000 (PID $pid: $cmd) — please review"
        echo "    If safe to terminate: kill $pid"
        ;;
    esac
  done
else
  echo "  port 8000 is free"
fi

# ── 2. Install nginx config (favicon 204 + asset cache rules) ──
if [ "$SKIP_NGINX" -eq 0 ]; then
  step "2. Install nginx config + reload"
  run sudo cp "$REPO/nginx/4truck.conf" /etc/nginx/sites-available/4truck
  if [ ! -e /etc/nginx/sites-enabled/4truck ]; then
    run sudo ln -s /etc/nginx/sites-available/4truck /etc/nginx/sites-enabled/4truck
  fi
  run sudo nginx -t
  run sudo systemctl reload nginx
fi

# ── 3. Install new 4truck-{api,bot} units, retire legacy unit ───
# Legacy `semi-telematics-bot.service` ran `run.py` with all flags
# enabled (single-process bot+sched+API on :8000). The new layout
# splits API into a multi-worker gunicorn unit and keeps bot+sched
# in a single-instance unit. If the legacy unit is still active,
# stop+disable it before installing the replacements so port 8000
# transfers cleanly.
step "3. Install/refresh systemd units (api + bot + queue)"

LEGACY_UNIT=semi-telematics-bot.service
if systemctl list-unit-files --no-legend "$LEGACY_UNIT" 2>/dev/null | grep -q .; then
  if systemctl is-active --quiet "$LEGACY_UNIT"; then
    echo "  → stopping legacy $LEGACY_UNIT (replaced by 4truck-api + 4truck-bot)"
    run sudo systemctl stop "$LEGACY_UNIT"
  fi
  if systemctl is-enabled --quiet "$LEGACY_UNIT" 2>/dev/null; then
    run sudo systemctl disable "$LEGACY_UNIT"
  fi
fi

# Copy unit files (idempotent — overwrite on every run so edits flow through)
for unit in 4truck-api.service 4truck-bot.service; do
  run sudo cp "$REPO/$unit" /etc/systemd/system/$unit
done
run sudo systemctl daemon-reload
run sudo systemctl enable 4truck-api 4truck-bot

# ── 4. Start API + bot (DB migrations auto-run on API boot) ─────
step "4. Restart API + bot services"
run sudo systemctl restart 4truck-api 4truck-bot
sleep 4
run sudo systemctl is-active 4truck-api
run sudo systemctl is-active 4truck-bot

# ── 5. Optional: install + start ARQ job-queue worker ──────────
if [ "$SKIP_QUEUE" -eq 0 ]; then
  step "5. Install ARQ job-queue worker"
  run sudo cp "$REPO/4truck-queue.service" /etc/systemd/system/4truck-queue.service
  run sudo systemctl daemon-reload
  run sudo systemctl enable --now 4truck-queue
  sleep 1
  run sudo systemctl is-active 4truck-queue
fi

# ── 6. Smoke tests ─────────────────────────────────────────────
step "6. Smoke tests"
run curl -sk -o /dev/null -w "GET /api/health → %{http_code}\n"      https://4truck.us/api/health
run curl -sk -o /dev/null -w "GET /favicon.ico → %{http_code}\n"     https://4truck.us/favicon.ico
run curl -sk -o /dev/null -w "GET /miniapp/    → %{http_code}\n"     https://4truck.us/miniapp/
run curl -sk -o /dev/null -w "GET /dashboard/  → %{http_code}\n"     https://4truck.us/dashboard/

step "Deploy complete"
echo
echo "Next steps (data-side, not part of this script):"
echo "  • Link drivers to Samsara IDs:   python3 scripts/link_samsara_drivers.py --account-id <ID>"
echo "  • Enable payroll for an account: UPDATE accounts SET payroll_enabled=1  WHERE id=<ID>;"
echo "  • Enable coaching for an account: UPDATE accounts SET coaching_enabled=1 WHERE id=<ID>;"
echo "  • Push commit to GitHub:         git push origin main"
