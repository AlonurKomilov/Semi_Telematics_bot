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
ACTIVATE_TIMEOUT=${ACTIVATE_TIMEOUT:-45}
# How long to keep watching a unit AFTER it goes active, to catch a
# crash-on-boot that systemd would otherwise silently paper over with
# Restart=always.  The bot has historically crashed ~5s into boot, so
# a gate that stops at "is-active" would report a false success.
#
# Overridable from the environment: on a quiet box 15s of watching is
# 15s of waiting for nothing, and the operator is better placed than
# this script to judge that.  Defaults stay conservative.
QUEUE_SETTLE=${QUEUE_SETTLE:-5}
BOT_SETTLE=${BOT_SETTLE:-15}

# How long to allow for the API's workers to come up.
#
# NOT ~2s, which is what "Listening at" in api.log suggests — that line
# only means the arbiter bound the socket.  A worker is not serving
# until it logs "Application startup complete".  Connections accepted
# into the backlog before that just sit there.
#
# Observed 65-87s per worker across several boots, but that was measured
# on a box running at load average ~19 on 8 cores; the import itself is
# only ~12s of CPU, so most of that was waiting for a scheduler slot
# rather than anything the app does.  On an idle box expect far less.
# The budget is sized for the bad case on purpose: too tight and it
# aborts a perfectly healthy deploy, which is the worse failure.
API_RELOAD_TIMEOUT=180
# Same reasoning for the health probe after a full API restart.
API_HEALTH_TIMEOUT=180

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

# What actually happened to each service, for the closing summary.  The
# target is called "restart" but the API is RELOADED, not restarted —
# leaving the reader to reconcile the command name with the log.
VERB_QUEUE="not touched"
VERB_BOT="not touched"
VERB_API="not touched"

# ── helpers ──────────────────────────────────────────────────

run() {
	if [ "$DRY_RUN" -eq 1 ]; then
		echo "      [dry-run] $*"
	else
		"$@"
	fi
}

# ── in-place progress ────────────────────────────────────────
#
# Repeating counters rewrite ONE line instead of appending a new one per
# tick.  A 60s worker rotation was emitting 20 near-identical lines, and
# with five such loops in a run the real events (what restarted, what
# failed) drowned in their own progress reports.
#
# Only when stdout is a terminal.  Piped to a file or a CI log, carriage
# returns render as garbage, so there we fall back to appending — but
# throttled, since nobody is watching a log file live.
IS_TTY=0
[ -t 1 ] && IS_TTY=1
NONTTY_EVERY=15   # seconds between appended lines when not a terminal

# $1 = message, $2 = seconds elapsed (used only to throttle non-TTY output)
progress() {
	if [ "$IS_TTY" -eq 1 ]; then
		# \033[K clears to end of line so a shorter message cannot leave
		# fragments of the longer one behind it.
		printf "\r      → %s\033[K" "$1"
	elif [ $(( ${2:-0} % NONTTY_EVERY )) -eq 0 ]; then
		echo "      → $1"
	fi
}

# A transient line at the SAME indent as the results it will be replaced
# by, so "🔄 <svc> — restarting…" occupies the row that "✅ <svc> — up
# in 10s…" then takes over.  The in-progress line is scaffolding: useful
# while the work runs, redundant the moment the result states the same
# service and more besides.  Keeping both left three dead 🔄 lines in
# every run.
#
# Non-TTY still emits it once — a log file needs to record that the
# restart was attempted, not just that it succeeded.
status_line() {
	# A dry run has no progress to overwrite — its "[dry-run] …" lines
	# are permanent, so a transient row underneath them would be merged
	# into by the next echo.
	if [ "$DRY_RUN" -eq 1 ]; then
		echo "   $1"
	elif [ "$IS_TTY" -eq 1 ]; then
		printf "\r   %s\033[K" "$1"
	elif [ $(( ${2:-0} % NONTTY_EVERY )) -eq 0 ]; then
		echo "   $1"
	fi
}

# Close an in-place line.  With an argument it becomes the final text for
# that line; without one it just terminates it.
progress_done() {
	if [ "$IS_TTY" -eq 1 ]; then
		if [ -n "${1:-}" ]; then
			printf "\r      → %s\033[K\n" "$1"
		else
			printf "\r\033[K"
		fi
	elif [ -n "${1:-}" ]; then
		echo "      → $1"
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
#
# Reports progress rather than blocking silently: a bare "Checking
# health..." followed by 20 seconds of nothing is indistinguishable from
# a hang, and gives you nothing to act on when it does time out.  The
# endpoint reports per-subsystem state ({"status","db","redis"}), so on
# success we echo which parts are healthy and on failure we show the last
# body we got — that is usually the difference between "API still
# booting" and "API is up but Postgres is not".
wait_for_health() {
	local timeout="$1" waited=0 body=""
	progress "polling $HEALTH_URL (up to ${timeout}s)" 0
	while [ "$waited" -lt "$timeout" ]; do
		body=$(curl -sf --max-time 3 "$HEALTH_URL" 2>/dev/null)
		if [ -n "$body" ]; then
			progress_done
			return 0
		fi
		sleep 1
		waited=$((waited + 1))
		progress "no response yet (${waited}/${timeout}s) — API still booting" "$waited"
	done
	progress_done
	LAST_HEALTH_BODY="$body"
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

# Importing the API app pulls in most of the codebase and takes several
# seconds.  Emit a line per module as it lands, so the wait is legible
# and a genuinely stuck import is attributable to a specific module
# rather than to "pre-flight".
#
# Progress lines are tagged and filtered out of the stream; everything
# (including the app's own log noise) still goes to a temp file so the
# failure path can show the real traceback.
preflight_import() {
	local out_file rc
	out_file=$(mktemp)
	(
		[ -f "./$ENV_FILE" ] && load_env_literally "./$ENV_FILE"
		ENABLE_API=1 ENABLE_BOT=0 ENABLE_SCHEDULER=0 \
		timeout 120 python3 -u -c "
import importlib, sys, time
for mod, attr in (('interfaces.api.app', 'app'),
                  ('capabilities.jobs.worker', 'WorkerSettings')):
    print(f'@@ importing {mod} ...', flush=True)
    t0 = time.monotonic()
    m = importlib.import_module(mod)
    if not hasattr(m, attr):
        sys.exit(f'{mod} has no attribute {attr!r}')
    print(f'@@ ok {mod} ({time.monotonic() - t0:.1f}s)', flush=True)
print('@@ done', flush=True)
"
	) >"$out_file" 2>&1 &
	local py_pid=$!

	# ONE reader, in the foreground.
	#
	# This was a `tail | grep | sed` pipeline plus a separate heartbeat
	# subshell — two processes writing to the same terminal.  That ruled
	# out in-place updates entirely: a carriage-return counter and an
	# independently-arriving module line overwrite each other's row.
	#
	# Reading the file here instead means one writer, so the ticking
	# counter can rewrite its own line and each finished module can close
	# that line with its real timing.  It also stops on the `@@ done`
	# marker rather than on process exit — the interpreter spends ~11s in
	# teardown after the last import, and keying off liveness printed
	# "still importing…" after both modules had already reported ok.
	# One transient row for the whole phase, same rule as the services.
	#
	# The per-module timings were printed on success and then said
	# nothing the "✅ Code imports cleanly … [20s]" line did not: 18.6s
	# and 0.1s sum to the total it already reports.  They earn their
	# space only when something goes wrong, so on failure the module
	# that was mid-import is named and the full traceback follows.
	local shown=0 t0=$SECONDS cur="" failed_at="" total ln
	while :; do
		total=$(grep -c '^@@' "$out_file" 2>/dev/null)
		total=${total:-0}
		while [ "$shown" -lt "$total" ]; do
			shown=$((shown + 1))
			ln=$(grep '^@@' "$out_file" 2>/dev/null | sed -n "${shown}p")
			case "$ln" in
				"@@ importing "*)
					cur=${ln#@@ importing }
					cur=${cur% ...}
					failed_at="$cur"   # last module entered, for the error path
					t0=$SECONDS
					;;
				"@@ ok "*)
					cur=""
					failed_at=""
					;;
			esac
		done
		grep -q '^@@ done' "$out_file" 2>/dev/null && break
		kill -0 "$py_pid" 2>/dev/null || break
		[ -n "$cur" ] && status_line \
			"🧪 Pre-flight — importing $cur … $((SECONDS - t0))s (slow here = busy box, not broken code)" \
			"$((SECONDS - t0))"
		sleep 1
	done
	progress_done

	wait "$py_pid"; rc=$?

	if [ "$rc" -ne 0 ]; then
		echo ""
		if [ -n "$failed_at" ]; then
			echo "   ❌ Pre-flight FAILED while importing $failed_at"
		else
			echo "   ❌ Pre-flight import FAILED — the new code does not load."
		fi
		echo ""
		grep -v '^@@ ' "$out_file" | tail -25 | sed 's/^/      /'
		echo ""
		echo "   Nothing was restarted.  All three services are still running"
		echo "   the previous, working code.  Fix the error above and re-run."
		rm -f "$out_file"
		exit 1
	fi
	rm -f "$out_file"
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
	status_line "🔄 $svc — restarting…" 0
	run sudo systemctl restart "$svc"

	if [ "$DRY_RUN" -eq 1 ]; then
		echo "      [dry-run] would wait for active, then watch ${settle}s"
		return 0
	fi

	# Everything from here to the result is TRANSIENT: it rewrites one
	# line, and that line is erased when the service is done.  What
	# survives is the pair that still means something afterwards —
	#
	#     🔄 4truck-bot — restarting…
	#     ✅ 4truck-bot — up in 12s, stable through 15s watch  [29s]
	#
	# "active after 12s" and "15/15s clean" are worth watching live and
	# worth nothing once the ✅ line states the same facts; keeping them
	# meant three services left a dozen dead lines behind them.
	waited=0
	while [ "$waited" -lt "$ACTIVATE_TIMEOUT" ]; do
		systemctl is-active "$svc" >/dev/null 2>&1 && break
		sleep 1
		waited=$((waited + 1))
		status_line "🔄 $svc — restarting… waiting for systemd (${waited}/${ACTIVATE_TIMEOUT}s)" "$waited"
	done
	if ! systemctl is-active "$svc" >/dev/null 2>&1; then
		progress_done
		abort "$svc did not become active within ${ACTIVATE_TIMEOUT}s."
	fi
	t_active=$((SECONDS - t0))

	# Active is not the same as healthy.  Poll ACROSS the settle window
	# rather than sleeping through it and checking once at the end: that
	# aborts as soon as a crash happens instead of up to ${settle}s
	# later, and it turns the longest silent stretch of the deploy into
	# visible progress.
	local elapsed=0 chunk
	while [ "$elapsed" -lt "$settle" ]; do
		status_line "🔄 $svc — active after ${t_active}s, watching for crash-on-boot ${elapsed}/${settle}s" "$elapsed"
		chunk=$((settle - elapsed))
		[ "$chunk" -gt 3 ] && chunk=3
		sleep "$chunk"
		elapsed=$((elapsed + chunk))
		if ! systemctl is-active "$svc" >/dev/null 2>&1; then
			progress_done
			abort "$svc went down ${elapsed}s after starting (crash on boot)."
		fi
		if [ "$(nrestarts "$svc")" != "$before_restarts" ]; then
			progress_done
			abort "$svc crashed and was auto-restarted ${elapsed}s into boot (NRestarts went $before_restarts → $(nrestarts "$svc")).  Check its log before deploying."
		fi
	done
	progress_done   # erase the transient line

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
		echo "   🔑 sudo will prompt shortly — run via 'make restart' to be"
		echo "      asked up front instead of mid-roll."
	else
		echo "   ❌ sudo needs a password and there is no terminal to ask on."
		echo "      Run this from an interactive shell so sudo can prompt."
		exit 1
	fi
fi

# ── 0a. host state — explains a slow deploy while it is happening ────
#
# Reports only.  The heavy consumers on this box are development agents;
# this script must never signal them.  It exists so that "why did the
# restart take 136s?" is answerable from the deploy output itself rather
# than from a dashboard opened after the fact — the app import is ~12s
# of CPU, so on a saturated box the wall time is contention, not code.

python3 scripts/host_snapshot.py --title "Server before deploy" 2>/dev/null \
	|| echo "   📊 (snapshot unavailable)"
echo ""
HOST_LOAD_BEFORE=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo "?")

# ── 0b. pre-flight — prove the code imports before touching anything ──

if [ "$DRY_RUN" -eq 1 ]; then
	echo "   🧪 Pre-flight — [dry-run] would verify the code imports before rolling"
else
	t_pre=$SECONDS
	preflight_import
	PREFLIGHT_SECS=$((SECONDS - t_pre))
	echo "   ✅ Code imports cleanly — safe to roll  [${PREFLIGHT_SECS}s]"
fi

# ── 1. queue — invisible to users, jobs wait in Redis ────────

roll_unit "$QUEUE" "$QUEUE_SETTLE" "in-flight jobs drain first"
VERB_QUEUE="restarted"

# ── 2. bot — brief Telegram polling gap, updates are re-delivered ──

roll_unit "$BOT" "$BOT_SETTLE" "scheduler + Telegram polling"
VERB_BOT="restarted"

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
		VERB_API="restarted"
		# is-active alone is too weak for the API: Type=simple means the
		# arbiter reports active while its workers may still be failing,
		# so confirm the port actually answers before declaring success.
		if [ "$DRY_RUN" -eq 0 ]; then
			t_health=$SECONDS
			wait_for_health "$API_HEALTH_TIMEOUT" || \
				abort "$API restarted but $HEALTH_URL never answered within ${API_HEALTH_TIMEOUT}s.  Last body: ${LAST_HEALTH_BODY:-<none>}"
			API_DOWN=$((SECONDS - t_api_restart))
			echo "   ✅ $API — restarted and health OK  [$((SECONDS - t_health))s to answer]"
		fi
	else
		API_MODE=reload
		VERB_API="reloaded"
		t_api=$SECONDS
		before_workers=$(worker_pids "$API")
		before_main=$(main_pid "$API")
		status_line "🔄 $API — reloading (SIGHUP: the socket stays open throughout)" 0
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
			total_old=$(echo "$before_workers" | wc -w)
			progress "retiring $total_old old workers, booting replacements" 0
			while [ "$waited" -lt "$API_RELOAD_TIMEOUT" ]; do
				now_workers=$(worker_pids "$API")
				overlap=0
				remaining=0
				for p in $before_workers; do
					case " $now_workers " in
						*" $p "*) overlap=1; remaining=$((remaining + 1)) ;;
					esac
				done
				if [ "$overlap" -eq 0 ] && [ -n "$now_workers" ]; then
					rotated=1
					break
				fi
				sleep 1
				waited=$((waited + 1))
				progress "$((total_old - remaining))/$total_old rotated, $remaining still draining (${waited}/${API_RELOAD_TIMEOUT}s)" "$waited"
			done
			progress_done

			# Distinguish the two failure shapes — they need very
			# different reactions from whoever is reading this.
			if [ "$(main_pid "$API")" != "$before_main" ]; then
				abort "$API arbiter died during reload and systemd restarted it (MainPID $before_main → $(main_pid "$API")).  This is gunicorn's HaltServer path: the listening socket was closed, so there WAS a brief outage, and it will repeat every ${API_RELOAD_TIMEOUT}s if the code is still broken.  Check api.log now."
			fi

			[ "$rotated" -eq 1 ] || \
				abort "$API workers did not fully rotate within ${API_RELOAD_TIMEOUT}s.  The arbiter is alive and old workers are still serving, so the site is up — but the new code is not live.  Check api.log."

			# Old workers are gone by now, but a NEW one is not
			# necessarily serving yet — importing this app takes ~45s,
			# so this wait is the long pole of the whole deploy.  It
			# used to be a bare `curl` with no --max-time, which blocked
			# here in silence for 48s with the last progress line 45s
			# stale; from the outside that is indistinguishable from a
			# hang.  wait_for_health polls with a per-request timeout and
			# reports as it goes.
			t_ready=$SECONDS
			status_line "🔄 $API — workers rotated, waiting for one to answer" 0
			wait_for_health "$API_HEALTH_TIMEOUT" || \
				abort "$API reloaded but $HEALTH_URL never answered within ${API_HEALTH_TIMEOUT}s.  Last body: ${LAST_HEALTH_BODY:-<none>}"
			api_ready_gap=$((SECONDS - t_ready))

			echo "   ✅ $API — reloaded, $(echo "$before_workers" | wc -w) workers rotated, health OK  [$((SECONDS - t_api))s]"
			# Be precise rather than flattering: nothing was refused, but
			# if the old workers exited before a new one could answer,
			# requests arriving in that gap waited on the socket backlog.
			if [ "$api_ready_gap" -gt 2 ]; then
				echo "      ℹ️  ${api_ready_gap}s of that was the new workers importing"
				echo "         (reported once more in the summary below)."
			fi
		fi
	fi
fi

# ── done ─────────────────────────────────────────────────────

echo ""
if [ "$DRY_RUN" -eq 1 ]; then
	echo "✅ Dry run complete — nothing was changed."
	echo "   Run 'make restart' to apply this for real."
else
	# Close on STATE, not prose.
	#
	# The previous ending went straight from the elapsed time into four
	# paragraphs about queue gaps and import cost, and never answered the
	# only question that matters at the end of a deploy: is everything
	# running right now?  `make start` used to end with "All services
	# healthy" and losing that was a regression.  State first, then the
	# commentary that explains it.
	# Fetch health FIRST: it is what lets redis and postgres be reported
	# as verified rather than asserted.  The endpoint returns per-subsystem
	# state ({"status","db","redis"}), so the app itself confirms it can
	# reach both — a stronger signal than this script poking at them, and
	# the only honest basis for printing "running" next to them.
	health_body=$(curl -sf --max-time 3 "$HEALTH_URL" 2>/dev/null)
	case "$health_body" in
		*'"redis":"ok"'*) redis_mark="✅"; redis_state="running" ;;
		"")               redis_mark="?" ; redis_state="unverified" ;;
		*)                redis_mark="❌"; redis_state="UNREACHABLE" ;;
	esac
	case "$health_body" in
		*'"db":"ok"'*) db_mark="✅"; db_state="running" ;;
		"")            db_mark="?" ; db_state="unverified" ;;
		*)             db_mark="❌"; db_state="UNREACHABLE" ;;
	esac

	echo "   Services"
	for svc_verb in "$API|$VERB_API" "$BOT|$VERB_BOT" "$QUEUE|$VERB_QUEUE"; do
		svc=${svc_verb%%|*}
		verb=${svc_verb#*|}
		if systemctl is-active "$svc" >/dev/null 2>&1; then
			printf "      ✅ %-14s %-12s %s\n" "$svc" "running" "$verb"
		else
			printf "      ❌ %-14s %-12s %s\n" "$svc" "NOT RUNNING" "$verb"
		fi
	done
	printf "      %s %-14s %-12s %s\n" "$redis_mark" "redis" "$redis_state" "not touched (holds state)"
	printf "      %s %-14s %-12s %s\n" "$db_mark" "postgres" "$db_state" "not touched (holds state)"
	echo ""

	if [ -n "$health_body" ]; then
		echo "   Health   $health_body"
	else
		echo "   Health   ❌ $HEALTH_URL not answering — check api.log"
	fi
	echo ""

	echo "   API impact"
	if [ "${API_MODE:-skipped}" = "reload" ]; then
		if [ "${api_ready_gap:-0}" -gt 2 ]; then
			# Do NOT cite PREFLIGHT_SECS as the cause here.  Pre-flight
			# times ONE process importing alone; the reload starts all 17
			# workers importing at once, and measured fork→ready per
			# worker was ~74s against a 17s pre-flight.  Pairing the two
			# numbers invited the obvious question — why is the gap
			# longer than the import? — and could not answer it.
			echo "      ~${api_ready_gap}s where requests queued (nothing refused —"
			echo "      the socket stayed open). This is the new workers"
			echo "      importing; on a quieter box it shrinks."
		else
			echo "      No downtime. The elapsed time above is the deliberate"
			echo "      settle windows, during which the site stayed up."
		fi
	elif [ "${API_MODE:-skipped}" = "restart" ]; then
		echo "      ~${API_DOWN:-?}s offline — this run took the full-restart path,"
		echo "      not the reload. The reason is printed above."
	fi
	echo ""

	python3 scripts/host_snapshot.py --title "Server after deploy" 2>/dev/null \
		|| echo "   📊 (snapshot unavailable)"

	echo ""
	echo "✅ Rolling restart complete in $((SECONDS - T_START))s"

	# Drop the sudo session IF the deploy is what created it.  This
	# account has full (ALL:ALL) sudo, so leaving a warm timestamp behind
	# means anyone at this terminal has passwordless root for the next
	# ~15 minutes.  A session that already existed is left alone — it
	# belongs to whatever the operator was doing before.
	if [ -f .sudo-owned ]; then
		rm -f .sudo-owned
		sudo -k 2>/dev/null || true
	fi
fi
