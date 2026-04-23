# 4truck — convenience targets
# ─────────────────────────────────────────

SERVICE  = 4truck-bot
PID_FILE = .bot.pid
LOG_FILE = bot.log

.PHONY: start stop restart status logs install clean \
       test test-cov test-fast test-watch \
       backup backup-list backup-restore \
       docker-build docker-up docker-down docker-logs docker-restart \
       nginx-install nginx-test nginx-status ports \
       redis-start redis-stop redis-cli

# ── systemd-aware targets (preferred) ────────────────

## Install as systemd service (auto-start on boot + auto-restart on crash)
install:
	@bash install-service.sh

## Start all services: Redis + bot/API (systemd or nohup fallback)
start:
	@echo "🚀 Starting 4truck services..."
	@# ── 1. Redis ──
	@if docker ps --format '{{.Names}}' | grep -q $(REDIS_CONTAINER); then \
		echo "   ✅ Redis already running on port 8002"; \
	else \
		docker start $(REDIS_CONTAINER) 2>/dev/null || \
		docker run -d \
			--name $(REDIS_CONTAINER) \
			--restart unless-stopped \
			-p 127.0.0.1:8002:8002 \
			-v 4truck-redis:/data \
			redis:7-alpine \
			redis-server --port 8002 >/dev/null; \
		echo "   ✅ Redis started on port 8002"; \
	fi
	@# ── 2. Bot + API ──
	@if systemctl is-enabled $(SERVICE) >/dev/null 2>&1; then \
		sudo systemctl start $(SERVICE); \
		echo "   ✅ Bot + API started via systemd"; \
	elif [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		echo "   ⚠️  Bot already running (PID $$(cat $(PID_FILE)))"; \
	else \
		nohup python3 run.py >> $(LOG_FILE) 2>&1 & echo $$! > $(PID_FILE); \
		echo "   ✅ Bot + API started (PID $$(cat $(PID_FILE))) — logs: $(LOG_FILE)"; \
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
	@# Reload is safe for multi-site: only THIS project's conf file changes.
	@# Other sites (2bot, analyticbot, etc.) are untouched — their conf files
	@# are separate and are NOT modified by this target.
	@# Remove old semi-telematics-bot conf if still lingering from before rename
	@sudo rm -f /etc/nginx/sites-enabled/semi-telematics-bot /etc/nginx/sites-available/semi-telematics-bot 2>/dev/null; true
	@if [ ! -f /etc/nginx/sites-available/$(NGINX_CONF) ] || \
			! diff -q $(NGINX_SRC) /etc/nginx/sites-available/$(NGINX_CONF) >/dev/null 2>&1; then \
		echo "   🔄 Nginx config changed — updating 4truck.us..."; \
		sudo cp $(NGINX_SRC) /etc/nginx/sites-available/$(NGINX_CONF); \
		sudo ln -sf /etc/nginx/sites-available/$(NGINX_CONF) /etc/nginx/sites-enabled/$(NGINX_CONF); \
		if sudo nginx -t >/dev/null 2>&1; then \
			sudo systemctl reload nginx; \
			echo "   ✅ Nginx reloaded — 4truck.us config active (other sites unaffected)"; \
		else \
			echo "   ❌ Nginx config invalid — not reloaded (run: sudo nginx -t)"; \
		fi; \
	else \
		sudo ln -sf /etc/nginx/sites-available/$(NGINX_CONF) /etc/nginx/sites-enabled/$(NGINX_CONF) 2>/dev/null; \
		echo "   ✅ Nginx config already up to date (no reload needed)"; \
	fi

## Stop all services: bot/API + Redis
stop:
	@echo "🛑 Stopping 4truck services..."
	@# ── 1. Bot + API ──
	@if systemctl is-enabled $(SERVICE) >/dev/null 2>&1; then \
		sudo systemctl stop $(SERVICE); \
		echo "   🛑 Bot + API stopped (systemd)"; \
	elif [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		kill $$(cat $(PID_FILE)) && rm -f $(PID_FILE); \
		echo "   🛑 Bot + API stopped"; \
	else \
		echo "   ⚠️  Bot is not running"; \
		rm -f $(PID_FILE); \
	fi
	@# Kill any orphan run.py processes for this project
	@ps aux | grep "[p]ython.*$(CURDIR)/run.py" | awk '{print $$2}' | while read pid; do \
		echo "   🧹 Killing orphan bot process $$pid"; \
		kill $$pid 2>/dev/null || kill -9 $$pid 2>/dev/null; \
	done; true
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
	@# ── Bot + API ──
	@if systemctl is-enabled $(SERVICE) >/dev/null 2>&1; then \
		if systemctl is-active $(SERVICE) >/dev/null 2>&1; then \
			echo "   ✅ Bot + API: running (systemd)"; \
		else \
			echo "   ❌ Bot + API: stopped (systemd)"; \
		fi; \
	elif [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		echo "   ✅ Bot + API: running (PID $$(cat $(PID_FILE)))"; \
	else \
		echo "   ❌ Bot + API: stopped"; \
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

## Build the dashboard React app
dashboard-build:
	@echo "🔨 Building dashboard..."
	@cd interfaces/dashboard && npm run build
	@echo "✅  Dashboard built → interfaces/dashboard/dist/"

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

# ── backup targets ───────────────────────────────────

BACKUP_DIR   = backups
BACKUP_TS   := $(shell date +%Y%m%d_%H%M%S)
BACKUP_NAME  = $(SERVICE)_$(BACKUP_TS)

## Create a pre-work backup: source code + consistent SQLite DB snapshot
## Archives land in ./backups/  (git-ignored, never deployed)
backup:
	@echo "📦 Creating backup: $(BACKUP_NAME)"
	@mkdir -p $(BACKUP_DIR)/$(BACKUP_NAME)/data
	@# ── 1. Consistent SQLite backup (handles WAL safely) ──
	@if [ -f data/bot.db ]; then \
		sqlite3 data/bot.db ".backup '$(BACKUP_DIR)/$(BACKUP_NAME)/data/bot.db'"; \
		echo "   ✅ Database snapshot saved"; \
	else \
		echo "   ⚠️  No database found at data/bot.db — skipping DB"; \
	fi
	@# ── 2. Source code archive (exclude junk) ──
	@tar cf - \
		--exclude='__pycache__'   \
		--exclude='.pytest_cache' \
		--exclude='.mypy_cache'   \
		--exclude='.git'          \
		--exclude='*.pyc'         \
		--exclude='*.pyo'         \
		--exclude='.venv'         \
		--exclude='venv'          \
		--exclude='env'           \
		--exclude='*.log'         \
		--exclude='.bot.pid'      \
		--exclude='.pid'          \
		--exclude='data'          \
		--exclude='backups'       \
		--exclude='.env'          \
		--exclude='*.db'          \
		--exclude='*.db-shm'     \
		--exclude='*.db-wal'     \
		. | tar xf - -C $(BACKUP_DIR)/$(BACKUP_NAME)/
	@# ── 3. Pack everything into a single .tar.gz ──
	@cd $(BACKUP_DIR) && tar czf $(BACKUP_NAME).tar.gz $(BACKUP_NAME)/ \
		&& rm -rf $(BACKUP_NAME)/
	@echo "✅ Backup ready: $(BACKUP_DIR)/$(BACKUP_NAME).tar.gz"
	@echo "   Size: $$(du -h $(BACKUP_DIR)/$(BACKUP_NAME).tar.gz | cut -f1)"

## List existing backups
backup-list:
	@if [ -d $(BACKUP_DIR) ] && ls $(BACKUP_DIR)/*.tar.gz >/dev/null 2>&1; then \
		echo "📋 Available backups:"; \
		ls -lh $(BACKUP_DIR)/*.tar.gz | awk '{print "   " $$NF " (" $$5 ")"}'; \
	else \
		echo "⚠️  No backups found"; \
	fi

## Restore from latest backup (prompts for confirmation)
backup-restore:
	@LATEST=$$(ls -t $(BACKUP_DIR)/*.tar.gz 2>/dev/null | head -1); \
	if [ -z "$$LATEST" ]; then \
		echo "⚠️  No backups found in $(BACKUP_DIR)/"; \
		exit 1; \
	fi; \
	echo "⚠️  This will restore from: $$LATEST"; \
	echo "   Current data/bot.db will be overwritten."; \
	read -p "   Continue? [y/N] " confirm; \
	if [ "$$confirm" = "y" ] || [ "$$confirm" = "Y" ]; then \
		TMPDIR=$$(mktemp -d); \
		tar xzf "$$LATEST" -C "$$TMPDIR"; \
		INNER=$$(ls "$$TMPDIR"); \
		if [ -f "$$TMPDIR/$$INNER/data/bot.db" ]; then \
			cp "$$TMPDIR/$$INNER/data/bot.db" data/bot.db; \
			echo "   ✅ Database restored"; \
		fi; \
		rm -rf "$$TMPDIR"; \
		echo "✅ Restore complete from $$LATEST"; \
	else \
		echo "❌ Restore cancelled"; \
	fi

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
	@sudo cp $(NGINX_SRC) /etc/nginx/sites-available/$(NGINX_CONF)
	@sudo ln -sf /etc/nginx/sites-available/$(NGINX_CONF) /etc/nginx/sites-enabled/$(NGINX_CONF)
	@echo "🔍 Testing nginx config..."
	@sudo nginx -t
	@sudo systemctl reload nginx
	@echo "✅ Nginx config installed and reloaded"
	@echo "   Other sites (2bot.org, analyticbot.org) are untouched"

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

