# 4truck — convenience targets
# ─────────────────────────────────────────

SERVICE       = 4truck-bot
SERVICE_API   = 4truck-api
SERVICE_QUEUE = 4truck-queue
# The full set of systemd units `make start/stop/restart` should cover.
# Order matters for stop: queue first (in-flight job drain), then bot
# (shutdown handler stops scheduler cleanly), then API.  Start order is
# the reverse: API up first so workers can enqueue immediately, then
# bot, then queue (so it picks up pending work).
APP_SERVICES_START = $(SERVICE_API) $(SERVICE) $(SERVICE_QUEUE)
APP_SERVICES_STOP  = $(SERVICE_QUEUE) $(SERVICE) $(SERVICE_API)
PID_FILE = .bot.pid
LOG_FILE = bot.log

# Bare `make` shows help.  Without this it ran the FIRST target in the
# file — which is `install`, i.e. a privileged systemd installer.  The
# likeliest accidental keystroke was also the most destructive one.
.DEFAULT_GOAL := help

.PHONY: help start stop restart restart-clean restart-dry \
       restart-api restart-bot restart-queue status logs install sudo-preflight prep-banner \
       clean clean-frontend clean-all \
       start-queue stop-queue \
       test test-cov test-fast test-watch \
       docker-build docker-up docker-down docker-logs docker-restart \
       nginx-install nginx-test nginx-status nginx-sync-if-needed ports \
       redis-start redis-stop redis-create redis-cli metrics \
       build dashboard-build dashboard-build-if-needed miniapp-build miniapp-build-if-needed \
       system-dashboard-build system-dashboard-build-if-needed \
       deps-install-if-needed

# ── help ─────────────────────────────────────────────

## List the common commands, then every target with its description.
##
## This file carries ~150 `##` doc comments in the conventional
## self-documenting format, and for a long time nothing rendered them —
## so `make restart-dry` (the safe way to preview a deploy) was
## undiscoverable unless you read the Makefile.  The first block below
## is hand-ordered because "what do I run to deploy?" should not require
## scanning an alphabetical list.
help:
	@echo "4truck — common tasks"
	@echo ""
	@echo "  make restart          deploy: services stay up, rolled one at a time"
	@echo "  make restart-clean    same, preceded by a Python bytecode clean"
	@echo "  make restart-dry      preview a deploy — changes nothing"
	@echo "  make status           are the services up, and is the API healthy?"
	@echo "  make metrics          server CPU / memory / disk, and what is using them"
	@echo "  make logs             follow the bot log"
	@echo ""
	@echo "  Cold path (takes the API down for ~90s — prefer 'make restart'):"
	@echo "    make stop && make clean && make start"
	@echo ""
	@echo "All targets:"
	@awk ' \
		/^##/ { if (doc == "" && length($$0) > 3) doc = substr($$0, 4); next } \
		/^[a-zA-Z0-9_.-]+:/ { \
			if (doc != "") { \
				split($$0, part, ":"); \
				printf "  %-26s %s\n", part[1], doc; \
				doc = ""; \
			} \
			next; \
		} \
		{ doc = "" } \
	' $(MAKEFILE_LIST)

# ── systemd-aware targets (preferred) ────────────────

## Install as systemd service (auto-start on boot + auto-restart on crash)
install:
	@bash install-service.sh

## Install Python deps only when requirements.txt changed since the
## last successful install (stamp file .deps-installed).  Makes
## `make start` self-sufficient after a pull that adds a dependency —
## without paying a pip resolve on every restart.  PEP 668 boxes
## (system Python) need --break-system-packages; try plain pip first
## so venv/container setups keep their normal path.
deps-install-if-needed:
	@STAMP=.deps-installed; \
	if [ ! -f "$$STAMP" ] || [ requirements.txt -nt "$$STAMP" ]; then \
		echo "   📦 requirements.txt changed — installing Python deps..."; \
		if pip install -q -r requirements.txt 2>/dev/null \
			|| pip install -q --break-system-packages -r requirements.txt; then \
			touch "$$STAMP"; \
			echo "   ✅ Python deps installed"; \
		else \
			echo "   ❌ pip install failed — fix before starting services"; \
			exit 1; \
		fi; \
	else \
		echo "   📦 Python deps unchanged — nothing to install"; \
	fi

## Start all services: Redis + bot/API (systemd or nohup fallback)
start: prep-banner deps-install-if-needed dashboard-build-if-needed miniapp-build-if-needed system-dashboard-build-if-needed
	@echo "🚀 Starting 4truck services..."
	@# ── 0. Clear stale Python bytecode so live source is always used ──
	@find . -path ./.git -prune -o -name '*.pyc' -delete 2>/dev/null; true
	@# ── 1. Redis ──
	@# Redis holds state, not code.  Start it if it happens to be down,
	@# but NEVER recreate it: a fresh container attaches a fresh empty
	@# volume, and the app would come up on a blank Redis without
	@# complaining.  Failing loudly is the correct behaviour here.
	@if docker ps --format '{{.Names}}' | grep -qx $(REDIS_CONTAINER); then \
		echo "   ·  Redis already running on port 8002"; \
	elif docker ps -a --format '{{.Names}}' | grep -qx $(REDIS_CONTAINER); then \
		if docker start $(REDIS_CONTAINER) >/dev/null 2>&1; then \
			echo "   ✅ Redis started on port 8002 (existing container, volume intact)"; \
		else \
			echo "   ❌ Redis container '$(REDIS_CONTAINER)' failed to start."; \
			echo "      Something else may be holding port 8002:"; \
			ss -tlnp 2>/dev/null | grep ':8002 ' || true; \
			echo "      NOT recreating it — a new container attaches an EMPTY volume"; \
			echo "      and would drop the JWT denylist and every queued job."; \
			exit 1; \
		fi; \
	else \
		echo "   ❌ Redis container '$(REDIS_CONTAINER)' does not exist."; \
		echo "      Refusing to create one automatically (new container = new,"; \
		echo "      empty volume).  On a genuinely fresh machine run:"; \
		echo "        make redis-create"; \
		exit 1; \
	fi
	@# ── 2. App services ──
	@# Production split: API (gunicorn workers), bot+scheduler, and the
	@# ARQ queue worker each have their own systemd unit.  Start them
	@# in API → bot → queue order so workers can enqueue from the
	@# moment they boot.  Each unit is optional — only the installed
	@# ones are touched, so single-process dev setups (where the legacy
	@# `4truck-bot` unit runs everything) still work.
	@started_any=0; t_all=$$(date +%s); \
	for svc in $(APP_SERVICES_START); do \
		if systemctl is-enabled $$svc >/dev/null 2>&1; then \
			t0=$$(date +%s); \
			sudo systemctl start $$svc; \
			echo "   ✅ $$svc started (systemd)  [$$(( $$(date +%s) - t0 ))s]"; \
			started_any=1; \
		fi; \
	done; \
	if [ $$started_any -eq 1 ]; then \
		echo "   ⏱  app services launched in $$(( $$(date +%s) - t_all ))s"; \
	fi; \
	if [ $$started_any -eq 0 ]; then \
		if ss -tlnp 2>/dev/null | grep -q ':8000 '; then \
			echo "   🧹 Port 8000 in use — clearing (race after stop)..."; \
			fuser -k 8000/tcp 2>/dev/null; sleep 2; \
			if ss -tlnp 2>/dev/null | grep -q ':8000 '; then \
				echo "   ❌ Port 8000 still held (root process?) — run: sudo fuser -k 8000/tcp"; \
				exit 1; \
			fi; \
			nohup python3 run.py >> $(LOG_FILE) 2>&1 & echo $$! > $(PID_FILE); \
			echo "   ✅ Bot + API started (PID $$(cat $(PID_FILE))) — logs: $(LOG_FILE)"; \
		elif [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
			echo "   ⚠️  Bot already running (PID $$(cat $(PID_FILE)))"; \
		else \
			nohup python3 run.py >> $(LOG_FILE) 2>&1 & echo $$! > $(PID_FILE); \
			echo "   ✅ Bot + API started (PID $$(cat $(PID_FILE))) — logs: $(LOG_FILE)"; \
		fi; \
	fi
	@# ── 3. Health check ──
	@# 180s, not 5.  "Listening at" in api.log means the arbiter bound
	@# the socket, NOT that anything can answer — a worker only serves
	@# once it logs "Application startup complete", which has been
	@# observed 65-87s later on a contended box (the import is ~12s of
	@# CPU; the rest was waiting for a scheduler slot).  The old
	@# 5-attempt window expired long before the first worker was ready
	@# and printed a warning that meant nothing, every single time.
	@# Progress is printed as it goes: a silent "Checking health..." is
	@# indistinguishable from a hang, and the endpoint reports db/redis
	@# separately, which is what tells you WHICH part is slow.
	@echo "   🔍 Checking health — http://127.0.0.1:8000/api/health (workers need ~60-90s)"
	@ok=0; \
	for i in $$(seq 1 180); do \
		body=$$(curl -sf --max-time 3 http://127.0.0.1:8000/api/health 2>/dev/null); \
		if [ -n "$$body" ]; then \
			echo "      → answered after $${i}s: $$body"; \
			echo "   ✅ All services healthy"; \
			ok=1; \
			break; \
		fi; \
		if [ $$(( i % 3 )) -eq 0 ]; then \
			echo "      → no response yet ($${i}s elapsed) — workers still importing"; \
		fi; \
		sleep 1; \
	done; \
	if [ $$ok -eq 0 ]; then \
		echo "   ⚠️  No health response after 180s — check api.log"; \
	fi
	@# ── 4. Nginx — 4truck.us config only ──────────────────────────────────────
	@$(MAKE) --no-print-directory nginx-sync-if-needed

## Sync the 4truck.us nginx config when it differs from the checked-in
## copy.  Safe to run while the app is serving — nginx reloads without
## dropping connections, and other sites on this box are untouched.
## Only acts when sudo credentials are already cached (no password
## prompt).  Run `sudo -v` first, or `make nginx-install` separately, to
## apply config changes.
nginx-sync-if-needed:
	@if sudo -n true 2>/dev/null; then \
		sudo rm -f /etc/nginx/sites-enabled/semi-telematics-bot /etc/nginx/sites-available/semi-telematics-bot 2>/dev/null; true; \
		if [ ! -f /etc/nginx/sites-available/$(NGINX_CONF) ] || \
				! diff -q $(NGINX_SRC) /etc/nginx/sites-available/$(NGINX_CONF) >/dev/null 2>&1; then \
			echo "   🌐 Nginx config changed — updating 4truck.us..."; \
			sudo cp $(NGINX_SRC) /etc/nginx/sites-available/$(NGINX_CONF); \
			sudo ln -sf /etc/nginx/sites-available/$(NGINX_CONF) /etc/nginx/sites-enabled/$(NGINX_CONF); \
			if sudo -n nginx -t >/dev/null 2>&1; then \
				sudo systemctl reload nginx; \
				echo "   ✅ Nginx reloaded — 4truck.us config active"; \
			else \
				echo "   ❌ Nginx config invalid — run: sudo nginx -t"; \
			fi; \
		else \
			sudo ln -sf /etc/nginx/sites-available/$(NGINX_CONF) /etc/nginx/sites-enabled/$(NGINX_CONF) 2>/dev/null; true; \
			echo "   🌐 Nginx config unchanged"; \
		fi; \
	else \
		echo "   ℹ️  Nginx: skipped (no sudo session — run 'sudo -v' then 'make nginx-install' to update)"; \
	fi

## Stop all services: queue + bot + API + Redis
stop:
	@echo "🛑 Stopping 4truck services..."
	@# State the cost before paying it.  Stopping is not symmetric with
	@# starting: gunicorn binds :8000 within a second but a worker cannot
	@# answer until it has imported the app, measured at 65-87s.  Someone
	@# reaching for `make stop` to deploy a code change is buying a
	@# minute-plus outage they could have avoided entirely.
	@echo "   ⚠️  This takes the API OFFLINE. After 'make start' it is"
	@echo "      ~60-90s before it serves again (workers must import)."
	@echo "      To deploy code with no outage, use: make restart"
	@# ── 1. App services ──
	@# Reverse-order from start: queue first so the ARQ worker drains
	@# in-flight jobs cleanly; bot next so the scheduler tearsdown
	@# without a half-running cron; API last so the request layer
	@# stays available until the bot+queue are quiet.
	@stopped_any=0; t_all=$$(date +%s); \
	for svc in $(APP_SERVICES_STOP); do \
		if systemctl is-enabled $$svc >/dev/null 2>&1; then \
			t0=$$(date +%s); \
			sudo systemctl stop $$svc; \
			echo "   🛑 $$svc stopped (systemd)  [$$(( $$(date +%s) - t0 ))s drain]"; \
			stopped_any=1; \
		fi; \
	done; \
	if [ $$stopped_any -eq 1 ]; then \
		echo "   ⏱  app services down in $$(( $$(date +%s) - t_all ))s"; \
	fi; \
	if [ $$stopped_any -eq 0 ]; then \
		if [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
			kill $$(cat $(PID_FILE)) && rm -f $(PID_FILE); \
			echo "   🛑 Bot + API stopped (PID file)"; \
		else \
			echo "   ⚠️  No app services found running"; \
			rm -f $(PID_FILE); \
		fi; \
		: ; \
		: "── Port clear — nohup fallback path ONLY ──"; \
		: "fuser -k sends SIGKILL. That is acceptable for a stray nohup"; \
		: "process with no drain logic, but NOT on the systemd path:"; \
		: "there, systemctl stop has already drained gunicorn, and this"; \
		: "would only ever land on a worker still finishing a slow"; \
		: "request (the API timeout is 90s — cold scorecards do take that"; \
		: "long).  So it stays inside the fallback branch."; \
		if ss -tlnp 2>/dev/null | grep -q ':8000 '; then \
			echo "   🧹 Clearing port 8000..."; \
			fuser -k 8000/tcp 2>/dev/null; \
			sleep 1; \
			if ss -tlnp 2>/dev/null | grep -q ':8000 '; then \
				echo "   ⚠️  Port 8000 still held (root process?) — run: sudo fuser -k 8000/tcp"; \
			else \
				echo "   ✅ Port 8000 cleared"; \
			fi; \
		fi; \
	fi
	@# Kill any orphan run.py belonging to THIS project directory (catches edge cases)
	@pgrep -f "$(CURDIR)/run\.py" 2>/dev/null | xargs -r kill 2>/dev/null; true
	@# ── 2. Redis — left running, on purpose ──────────────────────────
	@# Redis holds state, not code: the JWT denylist, the ARQ queue, the
	@# APScheduler lock, staged acks.  Stopping it to deploy a code
	@# change gains nothing and risks that state.  Same treatment as
	@# nginx below.  To take it down deliberately: make redis-stop
	@if docker ps --format '{{.Names}}' | grep -qx $(REDIS_CONTAINER); then \
		echo "   ℹ️  Redis: still running (state — not stopped by design)"; \
	else \
		echo "   ℹ️  Redis: not running — 'make start' will start it"; \
	fi
	@echo "   ✅ App services stopped"
	@# ── Nginx stays running ─────────────────────────────────────────────────
	@# nginx is NOT stopped here — it is a shared service that also serves
	@# 2bot, analyticbot, and any other site on this machine.
	@# Only the 4truck app process and Redis are stopped above.
	@if systemctl is-active nginx >/dev/null 2>&1; then \
		echo "   ℹ️  Nginx: still running (shared — also serves 2bot and other sites)"; \
	else \
		echo "   ⚠️  Nginx: not running"; \
	fi

## Rolling restart — the everyday deploy.
##
## Everything expensive happens FIRST, while the old services are still
## serving traffic: dependency install, the three frontend builds, the
## nginx diff.  Then the new code is proven importable in a throwaway
## process, and only then are the units rolled one at a time with a
## health gate between each (scripts/rolling_restart.sh).
##
## That import pre-flight is not belt-and-braces, it is load-bearing:
## gunicorn's SIGHUP reload retires the healthy workers before the new
## ones finish importing, so code that fails to import takes the port
## down rather than degrading gracefully.  See the long comment on
## ExecReload in 4truck-api.service.
##
## Measured against `make stop && make clean && make start`:
##   API downtime   11s  →  0s   (SIGHUP reload, no dropped connections)
##   bot downtime   56s  → ~15s
##   frontend rebuild no longer happens while the site is down (that was
##   worth another 30-90s of 502 whenever a dashboard file changed)
##
## Trade-offs, both deliberate:
##   * For a few seconds the queue+bot run new code while the API still
##     runs old.  Additive schema changes are fine; for a destructive
##     migration, a dependency upgrade, or "something is weird", use the
##     cold path: make stop && make clean && make start
##   * nginx serves interfaces/*/dist straight off disk, so a rebuilt
##     frontend goes live before the API reloads.  A deploy that adds a
##     new endpoint can 404 for anyone loading the page in that window.
##     Rolling the API last is still the safer choice: a frontend skew
##     costs one failed request and a refresh, a backend that will not
##     boot costs everyone.
restart: sudo-preflight prep-banner deps-install-if-needed dashboard-build-if-needed miniapp-build-if-needed system-dashboard-build-if-needed
	@$(MAKE) --no-print-directory nginx-sync-if-needed
	@bash scripts/rolling_restart.sh

## Header for the preparation phase, so its lines have a parent.
##
## The dependency check and the three build checks are peers, but they
## printed at three different indents with mixed markers — a bare `·`
## next to 📦 and 🔨 — and with no heading above them they read as loose
## fragments before the run rather than as one phase.  The banner also
## states the fact that matters here: none of this touches the running
## services, so a three-minute build is not downtime.
prep-banner:
	@echo "🔧 Preparing — services keep running throughout this phase"

## Ask for sudo ONCE, before anything else runs.
##
## First in the prerequisite list on purpose.  Previously sudo was not
## requested until the first `systemctl restart`, so the password prompt
## landed in the middle of the service roll — interleaved with progress
## output, and it stopped the deploy dead while it waited, which also
## inflated the timing of whichever service happened to be restarting
## ("up in 18s" when 15 of those seconds were someone typing).
##
## Asking up front also fixes a silent gap: `nginx-sync-if-needed` only
## acts when sudo is already cached, so on the old ordering it ALWAYS
## skipped with "no sudo session" and nginx config changes never got
## applied by `make restart` at all.
sudo-preflight:
	@if sudo -n true 2>/dev/null; then \
		echo "🔑 sudo: already authorised for this terminal"; \
	else \
		echo "🔑 This deploy needs sudo (systemctl + nginx)"; \
		if sudo -v; then \
			touch .sudo-owned; \
			echo "   ✅ sudo accepted — released again when the deploy finishes"; \
		else \
			echo "   ❌ sudo not granted — stopping before anything was changed."; \
			exit 1; \
		fi; \
	fi

## Rolling restart preceded by a bytecode clean.  Use after a pull that
## moved or deleted Python files.
##
## `clean` runs BEFORE anything is stopped, and that is safe: Python
## holds imported modules in memory, so deleting .pyc under a live
## process changes nothing for it, and anything imported later simply
## recompiles from the .py.  It adds no version-skew risk that editing
## the source did not already create.
restart-clean:
	@$(MAKE) --no-print-directory clean
	@$(MAKE) --no-print-directory restart

## Show what a rolling restart would do, without changing anything.
restart-dry:
	@bash scripts/rolling_restart.sh --dry-run

## Show status of all services
status:
	@echo "📋 4truck service status:"
	@# ── App services ──
	@any_systemd=0; \
	for svc in $(APP_SERVICES_START); do \
		if systemctl is-enabled $$svc >/dev/null 2>&1; then \
			any_systemd=1; \
			if systemctl is-active $$svc >/dev/null 2>&1; then \
				echo "   ✅ $$svc: running (systemd)"; \
			else \
				echo "   ❌ $$svc: stopped (systemd)"; \
			fi; \
		fi; \
	done; \
	if [ $$any_systemd -eq 0 ]; then \
		if [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
			echo "   ✅ Bot + API: running (PID $$(cat $(PID_FILE)))"; \
		else \
			echo "   ❌ App: stopped (no systemd units installed)"; \
		fi; \
	fi
	@# ── Redis ──
	@if docker ps --format '{{.Names}}' | grep -qx $(REDIS_CONTAINER); then \
		echo "   ✅ Redis: running on port 8002 ($(REDIS_CONTAINER))"; \
	else \
		echo "   ❌ Redis: stopped"; \
	fi
	@# ── Health ──
	@if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then \
		echo "   ✅ Health: $$(curl -s http://127.0.0.1:8000/api/health)"; \
	else \
		echo "   ⚠️  Health endpoint not responding"; \
	fi
	@# ── Nginx ──
	@if systemctl is-active nginx >/dev/null 2>&1; then \
		echo "   ✅ Nginx: running"; \
		if [ -L /etc/nginx/sites-enabled/$(NGINX_CONF) ]; then \
			echo "      4truck.us config: enabled"; \
		else \
			echo "      ⚠️  4truck.us config NOT enabled — run: make nginx-install"; \
		fi; \
	else \
		echo "   ❌ Nginx: stopped"; \
	fi

## Tail logs
logs:
	@if systemctl is-enabled $(SERVICE) >/dev/null 2>&1; then \
		sudo journalctl -u $(SERVICE) -f; \
	else \
		tail -f $(LOG_FILE); \
	fi

## Remove Python caches, test caches, PID files and other generated junk
## Does NOT touch the database (data/) or source code.
##
## Frontend build caches are deliberately NOT cleared here — see
## clean-frontend.  They used to be, which meant every `make clean`
## threw away Vite's dependency pre-bundling even on runs where no
## frontend rebuild happened, making the NEXT real build pay a cold
## start for nothing.
clean:
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null; true
	@find . \( -name "*.pyc" -o -name "*.pyo" \) -delete 2>/dev/null; true
	@rm -f .bot.pid .api.pid .pid
	@rm -f "=0.5.7"
	@echo "✅ Python cache cleared (DB, source and frontend caches untouched)"

## Clear the three Vite dependency caches.  Use when a frontend build
## misbehaves in a way that smells like a stale cache; the next build
## will be slower because Vite re-optimises deps from scratch.
clean-frontend:
	@rm -rf interfaces/miniapp/node_modules/.vite 2>/dev/null; true
	@rm -rf interfaces/dashboard/node_modules/.vite 2>/dev/null; true
	@rm -rf interfaces/system_dashboard/node_modules/.vite 2>/dev/null; true
	@echo "✅  Frontend build caches cleared (next build will be slower)"

## Everything clean + clean-frontend do.  This is what `make clean` used
## to be, kept under an explicit name.
clean-all: clean clean-frontend

## Build all frontend assets (dashboard + miniapp + system console)
build: dashboard-build miniapp-build system-dashboard-build browser-extension-build

## Build the dashboard React app (always rebuilds)
dashboard-build:
	@echo "   🔨 Building dashboard (~1-3 min)..."
	@cd interfaces/dashboard && npm run build
	@echo "   ✅ Dashboard built → interfaces/dashboard/dist/"

## Build the operator-only system console.  Installs deps on first run.
browser-extension-build:
	@echo "   🔨 Building browser extension..."
	@if [ ! -d interfaces/browser_extension/node_modules ]; then \
		cd interfaces/browser_extension && npm install --silent; \
	fi
	@cd interfaces/browser_extension && npm run build
	@echo "   ✅ Extension built → interfaces/browser_extension/dist/ (served by /extension/download)"

system-dashboard-build:
	@echo "   🔨 Building system console..."
	@if [ ! -d interfaces/system_dashboard/node_modules ]; then \
		echo "   📦 Installing system-dashboard deps (first run)..."; \
		cd interfaces/system_dashboard && npm install --silent; \
	fi
	@cd interfaces/system_dashboard && npm run build
	@echo "   ✅ System console built → interfaces/system_dashboard/dist/"

# Everything whose change should invalidate a built bundle.
#
# The original list was `src/ + index.html + vite.config.*`, which meant
# a dependency bump, a Tailwind token, a tsconfig path change or anything
# in public/ silently deployed a STALE dist: the check reported "up to
# date" and `make restart` shipped the previous bundle.  Emits only paths
# that exist, so `find` never errors on a frontend that lacks one.
BUILD_WATCH_SET = sh -c 'd=$$0; s="$$d/src $$d/index.html"; \
	for p in "$$d"/public "$$d"/vite.config.* "$$d"/package.json \
	         "$$d"/package-lock.json "$$d"/tailwind.config.* \
	         "$$d"/postcss.config.* "$$d"/tsconfig*.json; do \
		[ -e "$$p" ] && s="$$s $$p"; \
	done; echo "$$s"'

## Build the miniapp only when sources are newer than the last build output.
miniapp-build-if-needed:
	@DIST=interfaces/miniapp/dist/index.html; \
	SRC=$$($(BUILD_WATCH_SET) interfaces/miniapp); \
	if [ ! -f "$$DIST" ]; then \
		echo "   📦 Miniapp dist missing — building"; \
		$(MAKE) miniapp-build; \
	elif find $$SRC -newer "$$DIST" -print -quit 2>/dev/null | grep -q .; then \
		echo "   📦 Miniapp sources changed — rebuilding"; \
		$(MAKE) miniapp-build; \
	else \
		echo "   📦 Miniapp unchanged — no rebuild needed"; \
	fi

## Build the dashboard only when sources are newer than the last build output.
## Called automatically by `make start` so fresh code is always deployed.
dashboard-build-if-needed:
	@DIST=interfaces/dashboard/dist/index.html; \
	SRC=$$($(BUILD_WATCH_SET) interfaces/dashboard); \
	if [ ! -f "$$DIST" ]; then \
		echo "   📦 Dashboard dist missing — building"; \
		$(MAKE) dashboard-build; \
	elif find $$SRC -newer "$$DIST" -print -quit 2>/dev/null | grep -q .; then \
		echo "   📦 Dashboard sources changed — rebuilding"; \
		$(MAKE) dashboard-build; \
	else \
		echo "   📦 Dashboard unchanged — no rebuild needed"; \
	fi

## Build the operator system console only when its sources changed.
## Called automatically by `make start` so the system.4truck.us bundle
## stays in lockstep with dash./miniapp.  First run installs deps (the
## system-dashboard-build target handles the npm install itself).
system-dashboard-build-if-needed:
	@DIST=interfaces/system_dashboard/dist/index.html; \
	SRC=$$($(BUILD_WATCH_SET) interfaces/system_dashboard); \
	if [ ! -f "$$DIST" ]; then \
		echo "   📦 System console dist missing — building"; \
		$(MAKE) system-dashboard-build; \
	elif find $$SRC -newer "$$DIST" -print -quit 2>/dev/null | grep -q .; then \
		echo "   📦 System console sources changed — rebuilding"; \
		$(MAKE) system-dashboard-build; \
	else \
		echo "   📦 System console unchanged — no rebuild needed"; \
	fi

# ── testing targets ──────────────────────────────────
#
# None of these name a path.  pytest.ini's `testpaths` is the ONE
# definition of the suite, because tests are migrating into the package
# that owns them (features/<x>/tests/, capabilities/<x>/tests/) — and a
# hardcoded `tests/` silently collects less and less as they go, while
# still exiting 0.

## Run tests
test:
	python3 -m pytest

## Run tests with coverage report
test-cov:
	python3 -m pytest --cov=bot --cov-report=term-missing

## Run tests in parallel (uses all available CPU cores)
test-fast:
	python3 -m pytest -n auto

## Watch mode — re-runs tests on file changes
test-watch:
	ptw -- -v --tb=short

## How many tests does the suite collect?  CI pins a floor on this.
test-census:
	@python3 -m pytest --collect-only -q | tail -1

# ── PTI manual triggers (development / staging) ──────
#
# Each target runs ONE PTI job once against every active account.
# Useful for: deploy-time smoke ("did the new templates seed?"),
# debugging stuck rows ("force-spawn now"), and pre-prod rehearsal of
# the notification path.  The local-hour gates inside each job still
# fire — if you need to bypass them, set ``PTI_FORCE_LOCAL_HOUR=1``
# in the environment before invoking.

## Spawn weekly PTI inspections now (gated on local 06:00 by default)
pti-spawn:
	python3 -c "import asyncio; from features.pti.jobs import job_pti_spawn_weekly; asyncio.run(job_pti_spawn_weekly())"

## Remind drivers whose PTI is due in <24h
pti-remind:
	python3 -c "import asyncio; from features.pti.jobs import job_pti_remind_due_soon; asyncio.run(job_pti_remind_due_soon())"

## Escalate overdue PTIs to admin (gated on local 09:00)
pti-escalate:
	python3 -c "import asyncio; from features.pti.jobs import job_pti_escalate_overdue; asyncio.run(job_pti_escalate_overdue())"

## Send the daily fleet PTI digest (gated on local 09:00)
pti-digest:
	python3 -c "import asyncio; from features.pti.jobs import job_pti_fleet_digest; asyncio.run(job_pti_fleet_digest())"

# ── Docker targets ───────────────────────────────────

## Build Docker image
docker-build:
	docker compose build

## Start all services (bot + redis)
docker-up:
	docker compose up -d

## Stop all services
docker-down:
	docker compose down

## View Docker logs (follow)
docker-logs:
	docker compose logs -f

## Rebuild and restart
docker-restart:
	docker compose down
	docker compose build
	docker compose up -d

# ── Nginx targets ────────────────────────────────────

NGINX_CONF = 4truck
NGINX_SRC  = nginx/4truck.conf

## Install/update nginx config (safe — only adds 4truck.us, won't touch other sites)
nginx-install:
	@echo "📋 Installing nginx config for 4truck.us..."
	@# Remove old semi-telematics-bot conf if it exists (leftover from rename)
	@sudo rm -f /etc/nginx/sites-enabled/semi-telematics-bot /etc/nginx/sites-available/semi-telematics-bot 2>/dev/null; true
	@# Snapshot the existing live config before overwriting so the
	@# subdomain-rollout runbook's rollback step has a `.bak` to copy
	@# from.  Timestamped so successive installs don't clobber each
	@# other; the most-recent one is also symlinked to ``.bak``.
	@if sudo test -f /etc/nginx/sites-available/$(NGINX_CONF); then \
		BAK="/etc/nginx/sites-available/$(NGINX_CONF).$$(date +%Y%m%d-%H%M%S).bak"; \
		sudo cp /etc/nginx/sites-available/$(NGINX_CONF) "$$BAK"; \
		sudo ln -sf "$$BAK" /etc/nginx/sites-available/$(NGINX_CONF).bak; \
		echo "   🗂  Backed up previous config → $$BAK"; \
	fi
	@sudo cp $(NGINX_SRC) /etc/nginx/sites-available/$(NGINX_CONF)
	@sudo ln -sf /etc/nginx/sites-available/$(NGINX_CONF) /etc/nginx/sites-enabled/$(NGINX_CONF)
	@echo "🔍 Testing nginx config..."
	@sudo nginx -t
	@sudo systemctl reload nginx
	@echo "✅ Nginx config installed and reloaded"
	@echo "   Other sites (2bot.org, analyticbot.org) are untouched"
	@echo "   ↩  To roll back: sudo cp /etc/nginx/sites-available/$(NGINX_CONF).bak /etc/nginx/sites-available/$(NGINX_CONF) && sudo systemctl reload nginx"

## Test nginx config without applying
nginx-test:
	@sudo cp $(NGINX_SRC) /etc/nginx/sites-available/$(NGINX_CONF)
	@sudo nginx -t

## Show which nginx sites are enabled
nginx-status:
	@echo "📋 Enabled nginx sites:"
	@ls -la /etc/nginx/sites-enabled/ 2>/dev/null || echo "   (none)"
	@echo ""
	@echo "📋 Listening ports:"
	@ss -tlnp 2>/dev/null | grep -E ":(80|443|8000|8001|8002|8080)" || echo "   (none listening)"

# ── Port overview ────────────────────────────────────

## Host CPU / memory / disk right now, and which commands are using them.
##
## Read-only by design: it names heavy processes but never signals them.
## The big consumers here are development agents, and a make target has
## no business deciding those should stop.
##
## Run it when a deploy feels slow.  The app import is ~12s of CPU, so a
## restart that takes minutes is almost always contention rather than
## anything in the code — this is how you tell the difference in one
## second instead of guessing.
metrics:
	@python3 scripts/host_snapshot.py

## Show port assignments for this project
ports:
	@echo "📋 4truck — Port Layout"
	@echo "   8000  FastAPI API + static files (miniapp, dashboard)"
	@echo "   8001  Telegram webhook listener"
	@echo "   8002  Redis cache (localhost only)"
	@echo ""
	@echo "📋 Currently listening:"
	@ss -tlnp 2>/dev/null | grep -E ":(8000|8001|8002) " || echo "   (none — services not running)"

# ── Redis standalone (when not using docker-compose) ─

# The Redis this project actually runs on.  The name predates the
# 4truck rename and is NOT cosmetic: this container owns the
# ``semi-telematics-redis`` volume holding the JWT denylist, the ARQ
# job queue, the APScheduler global lock, staged acks and the capacity
# counters.
#
# Do NOT "modernise" this to 4truck-redis.  That name maps to a
# different, EMPTY volume — starting it would silently bring the app up
# on a blank Redis (revoked sessions valid again, queued jobs gone).
# For years this Makefile pointed at 4truck-redis and every start
# printed a green checkmark while the docker run failed on the port
# bind; the mismatch is what kept the damage theoretical.
REDIS_CONTAINER = semi-telematics-redis
REDIS_VOLUME    = semi-telematics-redis

## Start the existing Redis container (never creates one — see redis-create)
redis-start:
	@if docker ps --format '{{.Names}}' | grep -qx $(REDIS_CONTAINER); then \
		echo "✅ Redis already running on port 8002"; \
	elif docker ps -a --format '{{.Names}}' | grep -qx $(REDIS_CONTAINER); then \
		docker start $(REDIS_CONTAINER) >/dev/null && \
		echo "✅ Redis started on port 8002 (volume $(REDIS_VOLUME) intact)"; \
	else \
		echo "❌ Container '$(REDIS_CONTAINER)' does not exist — run: make redis-create"; \
		exit 1; \
	fi

## Create the Redis container from scratch.  FRESH MACHINES ONLY.
##
## Deliberately not automatic and deliberately not part of `make start`:
## on a box that already has data, creating a container means attaching
## a new empty volume, which silently resets the JWT denylist, the ARQ
## queue and the APScheduler lock.  Refuses to run if the container is
## already there.
redis-create:
	@if docker ps -a --format '{{.Names}}' | grep -qx $(REDIS_CONTAINER); then \
		echo "❌ Container '$(REDIS_CONTAINER)' already exists — not touching it."; \
		echo "   To start it:  make redis-start"; \
		exit 1; \
	fi
	@if docker volume ls --format '{{.Name}}' | grep -qx $(REDIS_VOLUME); then \
		echo "ℹ️  Reusing existing volume '$(REDIS_VOLUME)' (data preserved)."; \
	fi
	@docker run -d \
		--name $(REDIS_CONTAINER) \
		--restart unless-stopped \
		-p 127.0.0.1:8002:8002 \
		-v $(REDIS_VOLUME):/data \
		redis:7-alpine \
		redis-server --port 8002 >/dev/null
	@echo "✅ Redis container created and started on port 8002"

## Stop Redis container
redis-stop:
	@docker stop $(REDIS_CONTAINER) 2>/dev/null && echo "🛑 Redis stopped" || echo "⚠️  Redis not running"

## Redis CLI on port 8002
redis-cli:
	@redis-cli -p 8002

# ── Split-service targets (Phase C) ──────────────────

SERVICE_API = 4truck-api

.PHONY: start-api stop-api start-bot stop-bot \
        install-api install-split docker-split-up docker-split-down

## Install the API-only systemd unit (runs alongside the bot unit)
install-api:
	@echo "📋 Installing API service unit..."
	@sudo cp 4truck-api.service /etc/systemd/system/$(SERVICE_API).service
	@sudo systemctl daemon-reload
	@sudo systemctl enable $(SERVICE_API)
	@echo "✅ $(SERVICE_API) installed. Start with: make start-api"

## Install both split-service units (bot+scheduler + API as separate processes)
install-split: install-api
	@sudo cp 4truck-bot.service /etc/systemd/system/$(SERVICE).service
	@sudo systemctl daemon-reload
	@echo "✅ Both units installed. Run: make start-api && make start"
	@echo "   NOTE: update $(SERVICE).service to set ENABLE_API=0 before starting both."

## Start only the API service (systemd or direct)
start-api:
	@if systemctl is-enabled $(SERVICE_API) >/dev/null 2>&1; then \
		sudo systemctl start $(SERVICE_API); \
		echo "✅ API service started (systemd)"; \
	else \
		ENABLE_API=1 ENABLE_BOT=0 ENABLE_SCHEDULER=0 \
		nohup python3 run.py >> api.log 2>&1 & echo $$! > .api.pid; \
		echo "✅ API started (PID $$(cat .api.pid)) — logs: api.log"; \
	fi

## Stop only the API service
stop-api:
	@if systemctl is-enabled $(SERVICE_API) >/dev/null 2>&1; then \
		sudo systemctl stop $(SERVICE_API); \
		echo "🛑 API service stopped (systemd)"; \
	elif [ -f .api.pid ] && kill -0 $$(cat .api.pid) 2>/dev/null; then \
		kill $$(cat .api.pid) && rm -f .api.pid; \
		echo "🛑 API process stopped"; \
	else \
		echo "⚠️  API service not running"; \
	fi

## Start only the bot+scheduler (no API, systemd or direct)
start-bot:
	@if systemctl is-enabled $(SERVICE) >/dev/null 2>&1; then \
		sudo systemctl start $(SERVICE); \
		echo "✅ Bot+scheduler started (systemd)"; \
	else \
		ENABLE_API=0 ENABLE_BOT=1 ENABLE_SCHEDULER=1 \
		nohup python3 run.py >> bot.log 2>&1 & echo $$! > .bot.pid; \
		echo "✅ Bot+scheduler started (PID $$(cat .bot.pid)) — logs: bot.log"; \
	fi

## Stop only the bot+scheduler
stop-bot:
	@if systemctl is-enabled $(SERVICE) >/dev/null 2>&1; then \
		sudo systemctl stop $(SERVICE); \
		echo "🛑 Bot service stopped (systemd)"; \
	elif [ -f .bot.pid ] && kill -0 $$(cat .bot.pid) 2>/dev/null; then \
		kill $$(cat .bot.pid) && rm -f .bot.pid; \
		echo "🛑 Bot process stopped"; \
	else \
		echo "⚠️  Bot service not running"; \
	fi

## Start only the ARQ queue worker
start-queue:
	@if systemctl is-enabled $(SERVICE_QUEUE) >/dev/null 2>&1; then \
		sudo systemctl start $(SERVICE_QUEUE); \
		echo "✅ Queue worker started (systemd)"; \
	else \
		echo "⚠️  $(SERVICE_QUEUE) is not installed as a systemd unit"; \
	fi

## Stop only the ARQ queue worker
stop-queue:
	@if systemctl is-enabled $(SERVICE_QUEUE) >/dev/null 2>&1; then \
		sudo systemctl stop $(SERVICE_QUEUE); \
		echo "🛑 Queue worker stopped (systemd)"; \
	else \
		echo "⚠️  $(SERVICE_QUEUE) is not installed as a systemd unit"; \
	fi

## Restart only the API workers (fast — no scheduler drain)
restart-api:
	@if systemctl is-enabled $(SERVICE_API) >/dev/null 2>&1; then \
		sudo systemctl restart $(SERVICE_API); \
		echo "🔄 $(SERVICE_API) restarted (systemd)"; \
	else \
		$(MAKE) stop-api; $(MAKE) start-api; \
	fi

## Restart only the bot + scheduler (drains in-flight jobs gracefully)
restart-bot:
	@if systemctl is-enabled $(SERVICE) >/dev/null 2>&1; then \
		sudo systemctl restart $(SERVICE); \
		echo "🔄 $(SERVICE) restarted (systemd)"; \
	else \
		$(MAKE) stop-bot; $(MAKE) start-bot; \
	fi

## Restart only the ARQ queue worker (drains in-flight jobs gracefully)
restart-queue:
	@if systemctl is-enabled $(SERVICE_QUEUE) >/dev/null 2>&1; then \
		sudo systemctl restart $(SERVICE_QUEUE); \
		echo "🔄 $(SERVICE_QUEUE) restarted (systemd)"; \
	else \
		echo "⚠️  $(SERVICE_QUEUE) is not installed as a systemd unit"; \
	fi

## Start split services via docker-compose.services.yml (api + bot + redis)
docker-split-up:
	docker compose -f docker-compose.services.yml up -d

## Stop split services
docker-split-down:
	docker compose -f docker-compose.services.yml down

# ── Mini App (Telegram Mini App — React + Vite) ──────────────────

.PHONY: miniapp-install miniapp-build miniapp-dev

## Install Mini App npm dependencies
miniapp-install:
	@echo "📦 Installing Mini App dependencies..."
	@cd interfaces/miniapp && npm install
	@echo "✅ Mini App dependencies installed"

## Build the Mini App for production (output → interfaces/miniapp/dist/)
miniapp-build:
	@echo "   🔨 Building Mini App..."
	@cd interfaces/miniapp && npm run build
	@echo "   ✅ Mini App built → interfaces/miniapp/dist/"

## Start Mini App dev server (port 8003, API proxied to localhost:8000)
miniapp-dev:
	@echo "🚀 Starting Mini App dev server on port 8003..."
	@cd interfaces/miniapp && npm run dev

