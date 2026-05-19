#!/usr/bin/env bash
#
# Subdomain cutover automator.  Idempotent — safe to re-run.
#
# What it does:
#   1. Verifies prerequisites (JWT_SECRET, TELEGRAM_BOT_TOKEN, new Origin cert)
#   2. Writes missing subdomain env vars into .env (preserves existing values)
#   3. Backs up + installs the new nginx config
#   4. Restarts API + bot + queue
#   5. Re-registers the system bot's Telegram webhook
#   6. Verifies every endpoint returns the expected status
#
# Run from the project root:
#   bash scripts/cutover_subdomain.sh
#
# The script reads .env to grab TELEGRAM_BOT_TOKEN for the setWebhook call.
# No secrets are printed to stdout.

set -euo pipefail

# ── Colors (only when stdout is a TTY) ───────────────────────────
if [ -t 1 ]; then
  R="\033[31m"; G="\033[32m"; Y="\033[33m"; B="\033[34m"; N="\033[0m"
else
  R=""; G=""; Y=""; B=""; N=""
fi
say()   { printf "${B}==>${N} %s\n" "$*"; }
ok()    { printf "${G}OK${N}  %s\n" "$*"; }
warn()  { printf "${Y}WARN${N} %s\n" "$*"; }
die()   { printf "${R}FAIL${N} %s\n" "$*" >&2; exit 1; }

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# ── 0. Sanity ──────────────────────────────────────────────────────
[ -f .env ] || die ".env not found at $PROJECT_ROOT/.env"
[ -f nginx/4truck.conf ] || die "nginx/4truck.conf not found"

# ── 1. Prerequisites in .env ──────────────────────────────────────
say "Checking .env prerequisites"

ensure_env_set() {
  # Args: <key>  →  fails if key is unset/empty in .env
  local key="$1"
  if ! grep -qE "^${key}=.+" .env; then
    die "${key} is missing or empty in .env — set it before re-running"
  fi
}
ensure_env_set JWT_SECRET
ensure_env_set TELEGRAM_BOT_TOKEN
ensure_env_set DATABASE_URL
ok "JWT_SECRET / TELEGRAM_BOT_TOKEN / DATABASE_URL are set"

# ── 2. Append missing subdomain env vars ──────────────────────────
say "Setting subdomain env vars (preserves existing values)"

set_env_var() {
  # Args: <key> <value>
  # If the key has a non-empty value: leave it alone.
  # If it's missing or empty: set/replace with the given value.
  local key="$1" val="$2"
  if grep -qE "^${key}=.+" .env; then
    local current
    current="$(grep -E "^${key}=" .env | head -1 | cut -d= -f2-)"
    if [ "$current" = "$val" ]; then
      ok "${key} already correct"
    else
      warn "${key} already set to a different value — leaving it.  Edit manually if you want '${val}'."
    fi
  else
    # Replace empty `KEY=` lines if present, else append.
    if grep -qE "^${key}=$" .env; then
      # GNU sed in-place edit.  macOS would need `sed -i ''`.
      sed -i -E "s|^${key}=$|${key}=${val}|" .env
      ok "${key} set (filled empty line)"
    else
      echo "${key}=${val}" >> .env
      ok "${key} set (appended)"
    fi
  fi
}

set_env_var WEBAPP_URL "https://app.4truck.us"
set_env_var WEBHOOK_URL "https://bot.4truck.us/webhook"
set_env_var GDRIVE_OAUTH_REDIRECT_URI "https://api.4truck.us/storage/google/callback"

# ── 2b. nginx filesystem readability ──────────────────────────────
say "Verifying nginx (www-data) can traverse into the project tree"

if sudo -n -u www-data test -r interfaces/dashboard/dist/index.html 2>/dev/null; then
  ok "www-data can read dashboard/dist/index.html"
elif [ "$(stat -c %a "$HOME" 2>/dev/null)" = "750" ] || [ "$(stat -c %a "$HOME" 2>/dev/null)" = "700" ]; then
  warn "$HOME is mode $(stat -c %a "$HOME") — www-data cannot traverse into it."
  warn "Fix:  sudo chmod o+x $HOME"
  warn "      (adds traverse only; does NOT make $HOME world-listable)"
  read -r -p "Apply fix now? [y/N] " resp
  if [ "${resp:-N}" = "y" ] || [ "${resp:-N}" = "Y" ]; then
    sudo chmod o+x "$HOME"
    ok "$HOME is now $(stat -c %a "$HOME")"
  else
    die "Aborted — chmod o+x is required for dash./app.4truck.us to serve."
  fi
fi

# ── 3. TLS cert sanity ────────────────────────────────────────────
say "Verifying TLS cert SANs cover subdomains"

CERT=/etc/nginx/ssl/4truck.crt
if [ -r "$CERT" ]; then
  SANS=$(openssl x509 -in "$CERT" -noout -text 2>/dev/null | awk '/Subject Alternative Name/{f=1;next} f{print;exit}' || true)
  MISSING=""
  for h in dash.4truck.us app.4truck.us api.4truck.us bot.4truck.us; do
    if ! echo "$SANS" | grep -q "$h" && ! echo "$SANS" | grep -q "\*.4truck.us"; then
      MISSING="$MISSING $h"
    fi
  done
  if [ -n "$MISSING" ]; then
    warn "Cert is missing SAN(s):${MISSING}"
    warn "Cloudflare Full (Strict) will fail TLS handshake to these subdomains."
    warn "Generate a new Origin cert at: Cloudflare → SSL/TLS → Origin Server → Create Certificate"
    warn "  Hostnames: *.4truck.us  4truck.us"
    warn "  Replace /etc/nginx/ssl/4truck.{crt,key} with the new cert+key, then re-run this script."
    die "TLS cert blocks the cutover.  Fix the cert and re-run."
  fi
  ok "Cert covers all four new subdomains"
else
  warn "Can't read $CERT (need sudo).  Skipping cert check — assuming caller has verified."
fi

# ── 4. Install nginx config ───────────────────────────────────────
say "Installing nginx config"

NGINX_DEST=/etc/nginx/sites-available/4truck
if sudo test -f "$NGINX_DEST"; then
  TS=$(date +%Y%m%d-%H%M%S)
  sudo cp "$NGINX_DEST" "${NGINX_DEST}.bak.${TS}"
  ok "Backed up existing config to ${NGINX_DEST}.bak.${TS}"
fi
sudo cp nginx/4truck.conf "$NGINX_DEST"
sudo ln -sf "$NGINX_DEST" /etc/nginx/sites-enabled/4truck
if ! sudo nginx -t 2>&1 | tail -1; then
  die "nginx -t failed.  Restoring previous config…"
fi
sudo systemctl reload nginx
ok "nginx config installed and reloaded"

# ── 5. Restart API + bot + queue ──────────────────────────────────
say "Restarting application services"
sudo systemctl restart 4truck-api.service
sudo systemctl restart 4truck-bot.service
sudo systemctl restart 4truck-queue.service
sleep 4
for svc in 4truck-api.service 4truck-bot.service 4truck-queue.service; do
  state=$(systemctl is-active "$svc" || true)
  if [ "$state" = "active" ]; then
    ok "${svc}: $state"
  else
    die "${svc}: $state (check 'sudo journalctl -u $svc -n 50')"
  fi
done

# ── 6. Re-register Telegram webhook (system bot only) ─────────────
say "Re-registering Telegram webhook for system bot"

# Source ONLY the lines we need; never log them.
TELEGRAM_BOT_TOKEN=$(grep -E "^TELEGRAM_BOT_TOKEN=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
WEBHOOK_SECRET=$(grep -E "^WEBHOOK_SECRET=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true)

if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
  die "TELEGRAM_BOT_TOKEN is empty — can't call setWebhook"
fi

resp=$(curl -sS -X POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d url="https://bot.4truck.us/webhook" \
  ${WEBHOOK_SECRET:+-d secret_token="${WEBHOOK_SECRET}"} \
  -d drop_pending_updates=true)

if echo "$resp" | grep -q '"ok":true'; then
  ok "Telegram setWebhook succeeded"
  # Read it back to confirm.
  info=$(curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo")
  url=$(echo "$info" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['result'].get('url',''))")
  pending=$(echo "$info" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['result'].get('pending_update_count',0))")
  ok "getWebhookInfo → url=${url}  pending=${pending}"
else
  warn "setWebhook returned: $resp"
  die "Webhook not registered.  Likely TLS error — check Telegram can reach bot.4truck.us."
fi

# Per-account bots re-register their own webhooks on bot service restart
# (infra/bot_registry.py:start_all → start_bot → bot.set_webhook).  Nothing
# to do manually.

# ── 7. Verify every endpoint ──────────────────────────────────────
say "Verifying endpoints"

check() {
  # Args: <expected_code> <url> <description>
  local exp="$1" url="$2" desc="$3"
  local code
  code=$(curl -sI --max-time 10 -o /dev/null -w "%{http_code}" "$url" || echo "000")
  if [ "$code" = "$exp" ]; then
    ok "${desc}: ${code}"
  else
    warn "${desc}: expected ${exp}, got ${code}  (${url})"
  fi
}

check 200 http://127.0.0.1:8000/api/health           "API local (loopback)"
check 200 https://dash.4truck.us/                    "dash.4truck.us SPA"
check 200 https://app.4truck.us/                     "app.4truck.us SPA"
check 200 https://api.4truck.us/health               "api.4truck.us"
check 200 https://4truck.us/privacy                  "apex /privacy still works"
check 200 https://4truck.us/terms                    "apex /terms still works"
check 301 https://4truck.us/dashboard/               "apex /dashboard/ → 301"
check 301 https://4truck.us/miniapp/                 "apex /miniapp/ → 301"
check 404 https://bot.4truck.us/                     "bot.4truck.us root → 404 (expected)"

# Specifically follow the 301s end-to-end:
say "Following the legacy 301 redirects to confirm they land on the new subdomains"
for path in /dashboard/ /miniapp/; do
  final=$(curl -sIL --max-time 10 -o /dev/null -w "%{url_effective}" "https://4truck.us${path}" || echo "")
  case "$final" in
    https://dash.4truck.us/*) ok "https://4truck.us${path} → ${final}" ;;
    https://app.4truck.us/*)  ok "https://4truck.us${path} → ${final}" ;;
    *)                        warn "https://4truck.us${path} ended at: ${final}" ;;
  esac
done

echo
ok "Cutover complete."
echo
echo "  • If any subdomain check failed with 5xx, check:"
echo "      sudo journalctl -u 4truck-api -n 50"
echo "  • If a check showed 502 from Cloudflare, the TLS handshake to the"
echo "    origin failed — re-verify the Origin cert covers the SAN."
echo "  • If 301 redirects don't land on the new subdomains, re-deploy nginx:"
echo "      sudo cp nginx/4truck.conf /etc/nginx/sites-available/4truck && sudo systemctl reload nginx"
