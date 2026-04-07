# Semi Telematics Bot — convenience targets
# ─────────────────────────────────────────

SERVICE  = semi-telematics-bot
PID_FILE = .bot.pid
LOG_FILE = bot.log

.PHONY: start stop restart status logs install clean

# ── systemd-aware targets (preferred) ────────────────

## Install as systemd service (auto-start on boot + auto-restart on crash)
install:
	@bash install-service.sh

## Start bot via systemd (falls back to nohup if service not installed)
start:
	@if systemctl is-enabled $(SERVICE) >/dev/null 2>&1; then \
		sudo systemctl start $(SERVICE); \
		echo "✅ Bot started via systemd"; \
	elif [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		echo "⚠️  Bot already running (PID $$(cat $(PID_FILE)))"; \
	else \
		nohup python3 run.py >> $(LOG_FILE) 2>&1 & echo $$! > $(PID_FILE); \
		echo "✅ Bot started (PID $$(cat $(PID_FILE))) — logs: $(LOG_FILE)"; \
	fi

## Stop bot (also kills any orphan run.py processes for this project)
stop:
	@if systemctl is-enabled $(SERVICE) >/dev/null 2>&1; then \
		sudo systemctl stop $(SERVICE); \
		echo "🛑 Bot stopped (systemd)"; \
	elif [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		kill $$(cat $(PID_FILE)) && rm -f $(PID_FILE); \
		echo "🛑 Bot stopped"; \
	else \
		echo "⚠️  Bot is not running"; \
		rm -f $(PID_FILE); \
	fi
	@# Kill any orphan run.py processes for this project
	@ps aux | grep "[p]ython.*$(CURDIR)/run.py" | awk '{print $$2}' | while read pid; do \
		echo "🧹 Killing orphan bot process $$pid"; \
		kill $$pid 2>/dev/null || kill -9 $$pid 2>/dev/null; \
	done; true

## Restart bot
restart:
	@if systemctl is-enabled $(SERVICE) >/dev/null 2>&1; then \
		sudo systemctl restart $(SERVICE); \
		echo "🔄 Bot restarted (systemd)"; \
	else \
		$(MAKE) stop; \
		$(MAKE) start; \
	fi

## Show bot status
status:
	@if systemctl is-enabled $(SERVICE) >/dev/null 2>&1; then \
		sudo systemctl status $(SERVICE) --no-pager; \
	elif [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		echo "✅ Bot running (PID $$(cat $(PID_FILE)))"; \
	else \
		echo "⚠️  Bot is not running"; \
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
	@rm -f .bot.pid .pid
	@rm -f "=0.5.7"
	@echo "✅  Cache cleared (DB and source untouched)"

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
