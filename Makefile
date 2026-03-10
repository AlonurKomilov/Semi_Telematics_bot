# Semi Telematics Bot — convenience targets
# ─────────────────────────────────────────

SERVICE  = semi-telematics-bot
PID_FILE = .bot.pid
LOG_FILE = bot.log

.PHONY: start stop restart status logs install

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

## Stop bot
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
