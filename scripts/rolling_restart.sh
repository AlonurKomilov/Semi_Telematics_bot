#!/usr/bin/env bash
#
# Rolling restart of the 4truck app services.
#
# Rolls one unit at a time with a health gate between each, so a bad
# deploy is caught while the API is still serving the previous code:
#
#     pre-flight  →  queue  →  bot  →  API
#     ^^^^^^^^^^     ^^^^^     ^^^     ^^^
#     import check   invisible  ~15s    zero downtime (SIGHUP reload)
#
# Order is least-visible first ON PURPOSE.  If the queue or the bot
# fails to come back, we abort BEFORE touching the API — users keep
# getting served by the old workers instead of a 502.
#
# The pre-flight import check exists because SIGHUP cannot fail safely
# on its own: gunicorn retires the healthy workers before the new ones
# have proven they can import.  See preflight_import() below.
#
# Redis and Postgres are never touched.  They hold state, not code:
# restarting them to deploy a code change gains nothing and costs the
# JWT denylist, the ARQ queue, the APScheduler lock and staged acks.
#
# Callers: `make restart` / `make restart-clean`, which run all the
# preparation (deps, frontend builds, nginx) BEFORE invoking this, while
# the old services are still up.
#
# Usage:
#   scripts/rolling_restart.sh [--dry-run]

set -uo pipefail

# Wall-clock start.  Every step reports its own elapsed time so it is
# obvious where a slow deploy actually goes — the settle windows are
# deliberate waiting, not hangs, and without timings they read the same.
T_START=$SECONDS

API=4truck-api
BOT=4truck-bot
QUEUE=4truck-queue

ENV_FILE=".env"
ENV_STAMP=".env-applied"

# Derive the health URL the same way gunicorn.conf.py derives its bind:
# GUNICORN_BIND wins, else API_PORT, else 8000.  Hardcoding 8000 here
# would make every health check false-negative on a custom port.
api_port() {
	local bind port
	bind=$(sed -n 's/^GUNICORN_BIND=//p' "$ENV_FILE" 2>/dev/null | tail -1 | tr -d '"'\'' ')
	if [ -n "$bind" ]; then
		echo "${bind##*:}"
		return
	fi
	port=$(sed -n 's/^API_PORT=//p' "$ENV_FILE" 2>/dev/null | tail -1 | tr -d '"'\'' ')
	echo "${port:-8000}"
}

HEALTH_URL="http://127.0.0.1:$(api_port)/api/health"

# How long to wait for a unit to report active after a restart.
ACTIVATE_TIMEOUT=45
# How long to keep watching a unit AFTER it goes active, to catch a
# crash-on-boot that systemd would otherwise silently paper over with
# Restart=always.  The bot has historically crashed ~5s into boot, so
# a gate that stops at "is-active" would report a false success.
QUEUE_SETTLE=5
BOT_SETTLE=15
# Gunicorn boots 17 workers in ~2s; allow generous headroom.
API_RELOAD_TIMEOUT=45

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

# ── helpers ──────────────────────────────────────────────────

run() {
	if [ "$DRY_RUN" -eq 1 ]; then
		echo "      [dry-run] $*"
	else
		"$@"
	fi
}

# A unit counts as present if it is enabled OR merely running.  Checking
# is-enabled alone (the convention elsewhere in the Makefile) would make
# a loaded-but-not-enabled unit silently skip while we still printed
# "rolling restart complete" — the worst possible lie after a deploy.
installed() {
	systemctl is-enabled "$1" >/dev/null 2>&1 && return 0
	systemctl is-active "$1" >/dev/null 2>&1 && return 0
	return 1
}

# Poll the API health endpoint until it answers, or give up.
wait_for_health() {
	local timeout="$1" waited=0
	while [ "$waited" -lt "$timeout" ]; do
		curl -sf "$HEALTH_URL" >/dev/null 2>&1 && return 0
		sleep 1
		waited=$((waited + 1))
	done
	return 1
}

# ── Pre-flight: can the new code even be imported? ───────────
#
# This is the gate that SIGHUP cannot provide.  Gunicorn's reload()
# spawns new workers and then IMMEDIATELY calls manage_workers(), which
# SIGTERMs the oldest (i.e. the healthy, currently-serving) workers down
# to the worker count — before any new worker has finished importing the
# app.  If a new worker then dies with WORKER_BOOT_ERROR/APP_LOAD_ERROR,
# reap_workers() raises HaltServer, the arbiter calls halt() -> stop(),
# and stop() closes the listening socket outright.  systemd's
# Restart=always then re-runs it every 5s into the same failure.
#
# In other words: for code that will not import, a "graceful reload" is
# not graceful at all — it is a crash loop with the port shut. The only
# way to keep that promise honest is to refuse to touch anything until
# the code has been proven importable in a throwaway process, exactly
# the way each gunicorn worker imports it after fork.
# Load .env WITHOUT executing it.
#
# Sourcing (`. ./.env`) would run the file as shell, so any $(...) or
# backticks in it execute as whoever runs the deploy — and this user has
# full sudo.  Neither of the two things that normally read this file
# behaves that way: systemd's EnvironmentFile and python-dotenv both
# parse KEY=VALUE literally.  The deploy script must not be the odd one
# out, especially while .env is group-writable.
#
# So: split on the first '=', validate the key, strip one layer of
# matching quotes (what systemd EnvironmentFile does), and assign the
# value literally.  `export "$k=$v"` does not re-evaluate the value.
load_env_literally() {
	local line key val
	while IFS= read -r line || [ -n "$line" ]; do
		line=${line%$'\r'}
		case "$line" in ''|'#'*) continue ;; esac
		case "$line" in *=*) ;; *) continue ;; esac
		key=${line%%=*}
		val=${line#*=}
		key=${key#export }
		# Regex, not a glob: `[A-Za-z0-9_]*` as a shell PATTERN means
		# "one class char followed by anything", so it happily accepts
		# `bad-key`.  Anchored regex is what actually validates here.
		[[ $key =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
		case "$val" in
			\"*\") val=${val#\"}; val=${val%\"} ;;
			\'*\') val=${val#\'}; val=${val%\'} ;;
		esac
		export "$key=$val"
	done < "$1"
}

preflight_import() {
	local out rc
	out=$( ( [ -f "./$ENV_FILE" ] && load_env_literally "./$ENV_FILE"; \
		ENABLE_API=1 ENABLE_BOT=0 ENABLE_SCHEDULER=0 \
		timeout 120 python3 -c "
import importlib, sys
for mod, attr in (('interfaces.api.app', 'app'),
                  ('capabilities.jobs.worker', 'WorkerSettings')):
    m = importlib.import_module(mod)
    if not hasattr(m, attr):
        sys.exit(f'{mod} has no attribute {attr!r}')
" ) 2>&1 )
	rc=$?
	if [ "$rc" -ne 0 ]; then
		echo ""
		echo "   ❌ Pre-flight import FAILED — the new code does not load."
		echo ""
		echo "$out" | tail -25 | sed 's/^/      /'
		echo ""
		echo "   Nothing was restarted.  All three services are still running"
		echo "   the previous, working code.  Fix the error above and re-run."
		exit 1
	fi
}

nrestarts() {
	local n
	n=$(systemctl show "$1" -p NRestarts --value 2>/dev/null)
	echo "${n:-0}"
}

main_pid() {
	local p
	p=$(systemctl show "$1" -p MainPID --value 2>/dev/null)
	echo "${p:-0}"
}

worker_pids() {
	local mp
	mp=$(main_pid "$1")
	if [ -z "$mp" ] || [ "$mp" = "0" ]; then
		echo ""
		return
	fi
	pgrep -P "$mp" 2>/dev/null | sort -n | tr '\n' ' '
}

abort() {
	echo ""
	echo "   ❌ $1"
	echo ""
	echo "   Rolling restart ABORTED.  Services not yet rolled are still"
	echo "   running the previous code — in particular the API is still"
	echo "   serving.  Investigate, then either re-run 'make restart' or"
	echo "   fall back to: make stop && make clean && make start"
	exit 1
}

# Restart a unit, wait for it to go active, then keep watching it for
# `settle` seconds to be sure it does not crash-loop behind systemd's
# Restart=always.
roll_unit() {
	local svc="$1" settle="$2" label="$3"
	local before_restarts waited t0 t_active

	if ! installed "$svc"; then
		echo "   ⏭  $svc — not installed as a systemd unit, skipped"
		return 0
	fi

	t0=$SECONDS
	before_restarts=$(nrestarts "$svc")
	echo "   🔄 $svc — restarting ($label)..."
	run sudo systemctl restart "$svc"

	if [ "$DRY_RUN" -eq 1 ]; then
		echo "      [dry-run] would wait for active, then settle ${settle}s"
		return 0
	fi

	waited=0
	while [ "$waited" -lt "$ACTIVATE_TIMEOUT" ]; do
		systemctl is-active "$svc" >/dev/null 2>&1 && break
		sleep 1
		waited=$((waited + 1))
	done
	if ! systemctl is-active "$svc" >/dev/null 2>&1; then
		abort "$svc did not become active within ${ACTIVATE_TIMEOUT}s."
	fi
	t_active=$((SECONDS - t0))

	# Active is not the same as healthy — watch for a crash-on-boot.
	sleep "$settle"
	if ! systemctl is-active "$svc" >/dev/null 2>&1; then
		abort "$svc went down again ${settle}s after starting (crash on boot)."
	fi
	if [ "$(nrestarts "$svc")" != "$before_restarts" ]; then
		abort "$svc crashed and was auto-restarted during boot (NRestarts went $before_restarts → $(nrestarts "$svc")).  Check its log before deploying."
	fi

	# Split the two numbers on purpose: "up in 3s" is the real recovery
	# time, the rest is the settle window we chose to wait out.
	echo "   ✅ $svc — up in ${t_active}s, stable through ${settle}s watch  [$((SECONDS - t0))s]"
}

# ── 0. preconditions ─────────────────────────────────────────

if ! installed "$API" && ! installed "$BOT" && ! installed "$QUEUE"; then
	echo "❌ No 4truck systemd units are installed."
	echo "   Rolling restart needs systemd.  For the nohup dev setup use:"
	echo "      make stop && make start"
	exit 1
fi

echo "🔄 Rolling restart — services stay up, one unit at a time"
[ "$DRY_RUN" -eq 1 ] && echo "   (dry run — nothing will be changed)"

# Every step below needs sudo.  Say so once, up front, instead of
# stalling on an invisible password prompt three minutes in — and fail
# fast when there is no tty to prompt on (cron, CI, an orchestrator).
if [ "$DRY_RUN" -eq 0 ] && ! sudo -n true 2>/dev/null; then
	if [ -t 0 ]; then
		echo "   🔑 sudo password will be requested (systemctl needs it)."
	else
		echo "   ❌ sudo needs a password and there is no terminal to ask on."
		echo "      Run this from an interactive shell so sudo can prompt."
		echo "      (Pre-caching with 'sudo -v' also works, but this account"
		echo "      has full sudo, so a warm cache means anyone at that"
		echo "      terminal has root for the next ~15 minutes.)"
		exit 1
	fi
fi

# ── 0. pre-flight — prove the code imports before touching anything ──

if [ "$DRY_RUN" -eq 1 ]; then
	echo "   🧪 [dry-run] would verify the new code imports before rolling"
else
	echo "   🧪 Pre-flight: verifying the new code imports..."
	t_pre=$SECONDS
	preflight_import
	echo "   ✅ Pre-flight OK — API app and queue worker both import  [$((SECONDS - t_pre))s]"
fi

# ── 1. queue — invisible to users, jobs wait in Redis ────────

roll_unit "$QUEUE" "$QUEUE_SETTLE" "in-flight jobs drain first"

# ── 2. bot — brief Telegram polling gap, updates are re-delivered ──

roll_unit "$BOT" "$BOT_SETTLE" "scheduler + Telegram polling"

# ── 3. API — SIGHUP reload, zero dropped requests ────────────
#
# A changed .env cannot be picked up by SIGHUP (systemd only reads
# EnvironmentFile on a full start), so detect that and fall back.

if ! installed "$API"; then
	echo "   ⏭  $API — not installed as a systemd unit, skipped"
else
	env_changed=0
	if [ -f "$ENV_FILE" ]; then
		if [ ! -f "$ENV_STAMP" ] || [ "$ENV_FILE" -nt "$ENV_STAMP" ]; then
			env_changed=1
		fi
	fi

	can_reload=$(systemctl show "$API" -p CanReload --value 2>/dev/null)

	if [ "$env_changed" -eq 1 ] || [ "$can_reload" != "yes" ]; then
		t_api_restart=$SECONDS
		if [ "$env_changed" -eq 1 ]; then
			echo "   ℹ️  .env changed since last deploy — SIGHUP cannot re-read it."
			roll_unit "$API" 5 "full restart to pick up new environment"
			run touch "$ENV_STAMP"
		else
			echo "   ⚠️  $API has no ExecReload — falling back to a full restart."
			echo "      Run 'make install-api' once to enable zero-downtime reloads."
			roll_unit "$API" 5 "full restart"
		fi
		API_MODE=restart
		# is-active alone is too weak for the API: Type=simple means the
		# arbiter reports active while its workers may still be failing,
		# so confirm the port actually answers before declaring success.
		if [ "$DRY_RUN" -eq 0 ]; then
			t_health=$SECONDS
			wait_for_health 30 || \
				abort "$API restarted but $HEALTH_URL never answered."
			API_DOWN=$((SECONDS - t_api_restart))
			echo "   ✅ $API — restarted and health OK  [$((SECONDS - t_health))s to answer]"
		fi
	else
		API_MODE=reload
		t_api=$SECONDS
		before_workers=$(worker_pids "$API")
		before_main=$(main_pid "$API")
		echo "   🔄 $API — reloading (SIGHUP, no dropped connections)..."
		run sudo systemctl reload "$API"

		if [ "$DRY_RUN" -eq 1 ]; then
			echo "      [dry-run] would wait for all $(echo "$before_workers" | wc -w) workers to rotate"
		else
			# Reload succeeded only once EVERY old worker is gone and
			# the arbiter (MainPID) survived.  Health staying 200
			# throughout proves nothing on its own — the old workers
			# answer it happily while new ones fail to boot.
			waited=0
			rotated=0
			while [ "$waited" -lt "$API_RELOAD_TIMEOUT" ]; do
				now_workers=$(worker_pids "$API")
				overlap=0
				for p in $before_workers; do
					case " $now_workers " in *" $p "*) overlap=1 ;; esac
				done
				if [ "$overlap" -eq 0 ] && [ -n "$now_workers" ]; then
					rotated=1
					break
				fi
				sleep 1
				waited=$((waited + 1))
			done

			# Distinguish the two failure shapes — they need very
			# different reactions from whoever is reading this.
			if [ "$(main_pid "$API")" != "$before_main" ]; then
				abort "$API arbiter died during reload and systemd restarted it (MainPID $before_main → $(main_pid "$API")).  This is gunicorn's HaltServer path: the listening socket was closed, so there WAS a brief outage, and it will repeat every ${API_RELOAD_TIMEOUT}s if the code is still broken.  Check api.log now."
			fi

			[ "$rotated" -eq 1 ] || \
				abort "$API workers did not fully rotate within ${API_RELOAD_TIMEOUT}s.  The arbiter is alive and old workers are still serving, so the site is up — but the new code is not live.  Check api.log."

			curl -sf "$HEALTH_URL" >/dev/null 2>&1 || \
				abort "$API reloaded but $HEALTH_URL is not responding."

			echo "   ✅ $API — reloaded, $(echo "$before_workers" | wc -w) workers rotated, health OK (0 requests dropped)  [$((SECONDS - t_api))s]"
		fi
	fi
fi

# ── done ─────────────────────────────────────────────────────

echo ""
if [ "$DRY_RUN" -eq 1 ]; then
	echo "✅ Dry run complete — no changes made."
else
	echo "✅ Rolling restart complete in $((SECONDS - T_START))s."
	if [ "${API_MODE:-skipped}" = "reload" ]; then
		echo "   API downtime: 0s — the elapsed time above is the deliberate"
		echo "   settle windows, during which the site stayed up throughout."
	elif [ "${API_MODE:-skipped}" = "restart" ]; then
		echo "   API downtime: ~${API_DOWN:-?}s — this run took the full-restart"
		echo "   path, not the zero-downtime reload.  The reason is printed above."
	fi
	echo "   Redis + Postgres untouched (state preserved)."
fi
