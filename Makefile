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
