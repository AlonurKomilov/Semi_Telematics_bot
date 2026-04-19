"""Shared configuration — non-bot-specific environment variables.

Bot-specific settings (TELEGRAM_TOKEN, WEBHOOK_*, WEBAPP_URL) stay in
bot/config.py.  Everything here is needed by the platform layer regardless
of whether a Telegram bot is running.
"""

import os

# ── Database ─────────────────────────────────────────────────────

DATABASE_PATH = os.getenv("DATABASE_PATH", "data/bot.db")

# Feature flag: set MULTI_TENANT_DB=1 to use per-tenant SQLite databases
MULTI_TENANT = bool(os.getenv("MULTI_TENANT_DB"))

# ── Samsara ──────────────────────────────────────────────────────

SAMSARA_BASE_URL = os.getenv("SAMSARA_BASE_URL", "https://api.samsara.com")
SAMSARA_DASHBOARD_URL = os.getenv(
    "SAMSARA_DASHBOARD_URL",
    SAMSARA_BASE_URL.replace("://api.", "://cloud.").rstrip("/"),
)

# ── Alert intervals & thresholds ─────────────────────────────────

ALERT_INTERVAL = int(os.getenv("ALERT_CHECK_INTERVAL_MINUTES", "30"))
FUEL_THRESHOLD = int(os.getenv("FUEL_LOW_THRESHOLD_PERCENT", "20"))
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "")

ESCALATION_TIMEOUT_MINUTES = int(os.getenv("ESCALATION_TIMEOUT_MINUTES", "30"))
ESCALATION_MAX_HOURS = int(os.getenv("ESCALATION_MAX_HOURS", "8"))
HEALTH_ALERT_COOLDOWN_HOURS = int(os.getenv("HEALTH_ALERT_COOLDOWN_HOURS", "4"))
FUEL_HYSTERESIS_PERCENT = int(os.getenv("FUEL_HYSTERESIS_PERCENT", "5"))
FAULT_ALERT_COOLDOWN_HOURS = int(os.getenv("FAULT_ALERT_COOLDOWN_HOURS", "2"))

RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "10"))

# ── Vertex AI ────────────────────────────────────────────────────

GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
VERTEX_AI_MODEL = os.getenv("VERTEX_AI_MODEL", "gemini-2.5-flash")

# ── Redis ────────────────────────────────────────────────────────

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:8002/0")
