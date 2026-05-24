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

.PHONY: start stop restart restart-api restart-bot restart-queue status logs install clean \
       start-queue stop-queue \
       test test-cov test-fast test-watch \
       docker-build docker-up docker-down docker-logs docker-restart \
       nginx-install nginx-test nginx-status ports \
       redis-start redis-stop redis-cli \
       build dashboard-build dashboard-build-if-needed miniapp-build miniapp-build-if-needed

# ── systemd-aware targets (preferred) ────────────────

## Install as systemd service (auto-start on boot + auto-restart on crash)
install:
	@bash install-service.sh

## Start all services: Redis + bot/API (systemd or nohup fallback)
start: dashboard-build-if-needed miniapp-build-if-needed
	@echo "🚀 Starting 4truck services..."
	@# ── 0. Clear stale Python bytecode so live source is always used ──
	@find . -path ./.git -prune -o -name '*.pyc' -delete 2>/dev/null; true
	@# ── 1. Redis ──
	@if docker ps --format '{{.Names}}' | grep -q $(REDIS_CONTAINER); then \
		echo "   ✅ Redis already running on port 8002"; \
	elif docker ps -a --format '{{.Names}}' | grep -q $(REDIS_CONTAINER); then \
		if docker start $(REDIS_CONTAINER) >/dev/null 2>&1; then \
			echo "   ✅ Redis started on port 8002 (existing container)"; \
		else \
			echo "   🔄 Port 8002 race — waiting 3s for Docker to release..."; \
			sleep 3; \
			if docker start $(REDIS_CONTAINER) >/dev/null 2>&1; then \
				echo "   ✅ Redis started on port 8002 (existing container — retry ok)"; \
			else \
				echo "   🔄 Recreating Redis container (port still held)..."; \
				docker rm $(REDIS_CONTAINER) >/dev/null 2>&1 || true; \
				sleep 1; \
				docker run -d \
					--name $(REDIS_CONTAINER) \
					--restart unless-stopped \
					-p 127.0.0.1:8002:8002 \
					-v 4truck-redis:/data \
					redis:7-alpine \
					redis-server --port 8002 >/dev/null; \
				echo "   ✅ Redis started on port 8002 (container recreated)"; \
			fi; \
		fi; \
	else \
		docker run -d \
			--name $(REDIS_CONTAINER) \
			--restart unless-stopped \
			-p 127.0.0.1:8002:8002 \
			-v 4truck-redis:/data \
			redis:7-alpine \
			redis-server --port 8002 >/dev/null; \
		echo "   ✅ Redis started on port 8002 (new container)"; \
	fi
	@# ── 2. App services ──
	@# Production split: API (gunicorn workers), bot+scheduler, and the
	@# ARQ queue worker each have their own systemd unit.  Start them
	@# in API → bot → queue order so workers can enqueue from the
	@# moment they boot.  Each unit is optional — only the installed
	@# ones are touched, so single-process dev setups (where the legacy
	@# `4truck-bot` unit runs everything) still work.
	@started_any=0; \
	for svc in $(APP_SERVICES_START); do \
		if systemctl is-enabled $$svc >/dev/null 2>&1; then \
			sudo systemctl start $$svc; \
			echo "   ✅ $$svc started (systemd)"; \
			started_any=1; \
		fi; \
	done; \
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
	@echo "   🔍 Checking health..."
	@for i in 1 2 3 4 5; do \
		if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then \
			echo "   ✅ All services healthy"; \
			break; \
		fi; \
		if [ $$i -eq 5 ]; then \
			echo "   ⚠️  Health check not responding yet (services may still be starting)"; \
		fi; \
		sleep 1; \
	done
	@# ── 4. Nginx — 4truck.us config only ──────────────────────────────────────
	@# Only updates nginx when sudo credentials are already cached (no password prompt).
	@# Run `sudo -v` first, or `make nginx-install` separately, to apply config changes.
	@if sudo -n true 2>/dev/null; then \
		sudo rm -f /etc/nginx/sites-enabled/semi-telematics-bot /etc/nginx/sites-available/semi-telematics-bot 2>/dev/null; true; \
		if [ ! -f /etc/nginx/sites-available/$(NGINX_CONF) ] || \
				! diff -q $(NGINX_SRC) /etc/nginx/sites-available/$(NGINX_CONF) >/dev/null 2>&1; then \
			echo "   🔄 Nginx config changed — updating 4truck.us..."; \
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
			echo "   ✅ Nginx config already up to date"; \
		fi; \
	else \
		echo "   ℹ️  Nginx: skipped (no sudo session — run 'sudo -v' then 'make nginx-install' to update)"; \
	fi

## Stop all services: queue + bot + API + Redis
stop:
	@echo "🛑 Stopping 4truck services..."
	@# ── 1. App services ──
	@# Reverse-order from start: queue first so the ARQ worker drains
	@# in-flight jobs cleanly; bot next so the scheduler tearsdown
	@# without a half-running cron; API last so the request layer
	@# stays available until the bot+queue are quiet.
	@stopped_any=0; \
	for svc in $(APP_SERVICES_STOP); do \
		if systemctl is-enabled $$svc >/dev/null 2>&1; then \
			sudo systemctl stop $$svc; \
			echo "   🛑 $$svc stopped (systemd)"; \
			stopped_any=1; \
		fi; \
	done; \
	if [ $$stopped_any -eq 0 ]; then \
		if [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
			kill $$(cat $(PID_FILE)) && rm -f $(PID_FILE); \
			echo "   🛑 Bot + API stopped (PID file)"; \
		else \
			echo "   ⚠️  No app services found running"; \
			rm -f $(PID_FILE); \
		fi; \
	fi
	@# ── Nuclear port clear: release port 8000 regardless of which process holds it ──────────────────
	@# fuser -k sends SIGKILL directly to whatever owns the port — no PID file or name matching needed.
	@# This works for user-owned processes without sudo. If port is still held after (root process),
	@# the message below will instruct the operator.
	@if ss -tlnp 2>/dev/null | grep -q ':8000 '; then \
		echo "   🧹 Clearing port 8000..."; \
		fuser -k 8000/tcp 2>/dev/null; \
		sleep 1; \
		if ss -tlnp 2>/dev/null | grep -q ':8000 '; then \
			echo "   ⚠️  Port 8000 still held (root process?) — run: sudo fuser -k 8000/tcp"; \
		else \
			echo "   ✅ Port 8000 cleared"; \
		fi; \
	fi
	@# Kill any orphan run.py belonging to THIS project directory (catches edge cases)
	@pgrep -f "$(CURDIR)/run\.py" 2>/dev/null | xargs -r kill 2>/dev/null; true
	@# ── 2. Redis ──
	@if docker ps --format '{{.Names}}' | grep -q $(REDIS_CONTAINER); then \
		docker stop $(REDIS_CONTAINER) >/dev/null; \
		echo "   🛑 Redis stopped"; \
	else \
		echo "   ⚠️  Redis not running"; \
	fi
	@echo "   ✅ All services stopped"
	@# ── Nginx stays running ─────────────────────────────────────────────────
	@# nginx is NOT stopped here — it is a shared service that also serves
	@# 2bot, analyticbot, and any other site on this machine.
	@# Only the 4truck app process and Redis are stopped above.
	@if systemctl is-active nginx >/dev/null 2>&1; then \
		echo "   ℹ️  Nginx: still running (shared — also serves 2bot and other sites)"; \
	else \
		echo "   ⚠️  Nginx: not running"; \
	fi

## Restart all services
restart:
	@$(MAKE) stop
	@$(MAKE) start

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
	@if docker ps --format '{{.Names}}' | grep -q $(REDIS_CONTAINER); then \
		echo "   ✅ Redis: running on port 8002"; \
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
clean:
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null; true
	@find . \( -name "*.pyc" -o -name "*.pyo" \) -delete 2>/dev/null; true
	@rm -f .bot.pid .api.pid .pid
	@rm -f "=0.5.7"
	@# Clear miniapp Vite cache
	@rm -rf interfaces/miniapp/node_modules/.vite 2>/dev/null; true
	@# Clear dashboard Vite cache
	@rm -rf interfaces/dashboard/node_modules/.vite 2>/dev/null; true
	@echo "✅  Cache cleared (DB and source untouched)"

## Build all frontend assets (dashboard + miniapp)
build: dashboard-build miniapp-build

## Build the dashboard React app (always rebuilds)
dashboard-build:
	@echo "🔨 Building dashboard..."
	@cd interfaces/dashboard && npm run build
	@echo "✅  Dashboard built → interfaces/dashboard/dist/"

## Build the miniapp only when sources are newer than the last build output.
miniapp-build-if-needed:
	@DIST=interfaces/miniapp/dist/index.html; \
	if [ ! -f "$$DIST" ]; then \
		echo "📦 Miniapp dist missing — building..."; \
		$(MAKE) miniapp-build; \
	elif find interfaces/miniapp/src interfaces/miniapp/index.html interfaces/miniapp/vite.config.* \
		-newer "$$DIST" -print -quit 2>/dev/null | grep -q .; then \
		echo "📦 Miniapp sources changed — rebuilding..."; \
		$(MAKE) miniapp-build; \
	else \
		echo "   ✅ Miniapp dist up to date (no rebuild needed)"; \
	fi

## Build the dashboard only when sources are newer than the last build output.
## Called automatically by `make start` so fresh code is always deployed.
dashboard-build-if-needed:
	@DIST=interfaces/dashboard/dist/index.html; \
	if [ ! -f "$$DIST" ]; then \
		echo "📦 Dashboard dist missing — building..."; \
		$(MAKE) dashboard-build; \
	elif find interfaces/dashboard/src interfaces/dashboard/index.html interfaces/dashboard/vite.config.* \
		-newer "$$DIST" -print -quit 2>/dev/null | grep -q .; then \
		echo "📦 Dashboard sources changed — rebuilding..."; \
		$(MAKE) dashboard-build; \
	else \
		echo "   ✅ Dashboard dist up to date (no rebuild needed)"; \
	fi

# ── testing targets ──────────────────────────────────

## Run tests
test:
	python3 -m pytest tests/

## Run tests with coverage report
test-cov:
	python3 -m pytest tests/ --cov=bot --cov-report=term-missing

## Run tests in parallel (uses all available CPU cores)
test-fast:
	python3 -m pytest tests/ -n auto

## Watch mode — re-runs tests on file changes
test-watch:
	ptw -- tests/ -v --tb=short

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

REDIS_CONTAINER = 4truck-redis

## Start Redis on port 8002 (Docker container, standalone)
redis-start:
	@if docker ps --format '{{.Names}}' | grep -q $(REDIS_CONTAINER); then \
		echo "✅ Redis already running on port 8002"; \
	else \
		docker start $(REDIS_CONTAINER) 2>/dev/null || \
		docker run -d \
			--name $(REDIS_CONTAINER) \
			--restart unless-stopped \
			-p 127.0.0.1:8002:8002 \
			-v 4truck-redis:/data \
			redis:7-alpine \
			redis-server --port 8002; \
		echo "✅ Redis started on port 8002"; \
	fi

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
	@echo "🔨 Building Mini App..."
	@cd interfaces/miniapp && npm run build
	@echo "✅ Mini App built → interfaces/miniapp/dist/"

## Start Mini App dev server (port 8003, API proxied to localhost:8000)
miniapp-dev:
	@echo "🚀 Starting Mini App dev server on port 8003..."
	@cd interfaces/miniapp && npm run dev

