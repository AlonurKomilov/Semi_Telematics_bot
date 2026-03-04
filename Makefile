# Semi Telematics Bot — convenience targets
# ─────────────────────────────────────────

PID_FILE = .bot.pid
LOG_FILE = bot.log

.PHONY: start stop restart status logs

## Start bot in background
start:
	@if [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		echo "⚠️  Bot already running (PID $$(cat $(PID_FILE)))"; \
	else \
		nohup python3 run.py >> $(LOG_FILE) 2>&1 & echo $$! > $(PID_FILE); \
		echo "✅ Bot started (PID $$(cat $(PID_FILE))) — logs: $(LOG_FILE)"; \
	fi

## Stop bot
stop:
	@if [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		kill $$(cat $(PID_FILE)) && rm -f $(PID_FILE); \
		echo "🛑 Bot stopped"; \
	else \
		echo "⚠️  Bot is not running"; \
		rm -f $(PID_FILE); \
	fi

## Restart bot
restart: stop start

## Show bot status
status:
	@if [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		echo "✅ Bot running (PID $$(cat $(PID_FILE)))"; \
	else \
		echo "⚠️  Bot is not running"; \
	fi

## Tail logs
logs:
	@tail -f $(LOG_FILE)
