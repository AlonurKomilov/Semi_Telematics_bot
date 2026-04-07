"""Bot-wide configuration — environment settings and re-exported runtime state.

Pure read-only configuration (env vars, logging) lives here.
Mutable runtime state (db, caches, client helpers) lives in bot.state
and is re-exported below for backward compatibility.
"""

import os
import logging

# ── Environment ──────────────────────────────────────────────────

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SAMSARA_BASE_URL = os.getenv("SAMSARA_BASE_URL", "https://api.samsara.com")
# Dashboard base — derived from API URL (api.→cloud.) unless overridden
SAMSARA_DASHBOARD_URL = os.getenv(
    "SAMSARA_DASHBOARD_URL",
    SAMSARA_BASE_URL.replace("://api.", "://cloud.").rstrip("/"),
)
ALERT_INTERVAL = int(os.getenv("ALERT_CHECK_INTERVAL_MINUTES", "30"))
FUEL_THRESHOLD = int(os.getenv("FUEL_LOW_THRESHOLD_PERCENT", "20"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/bot.db")
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "")

# Alert escalation: minutes before re-sending unacknowledged alert
ESCALATION_TIMEOUT_MINUTES = int(os.getenv("ESCALATION_TIMEOUT_MINUTES", "30"))

# Alert escalation: max hours before auto-expiring unacknowledged alerts
ESCALATION_MAX_HOURS = int(os.getenv("ESCALATION_MAX_HOURS", "8"))

# Health alert cooldown: don't re-alert same vehicle within this many hours
# even if the alert set changes (e.g. engine on/off cycling)
HEALTH_ALERT_COOLDOWN_HOURS = int(os.getenv("HEALTH_ALERT_COOLDOWN_HOURS", "4"))

# Fuel hysteresis: clear low-fuel flag only when fuel rises this many %
# above the threshold (prevents oscillation spam around the threshold)
FUEL_HYSTERESIS_PERCENT = int(os.getenv("FUEL_HYSTERESIS_PERCENT", "5"))

# Fault alert cooldown: don't re-alert same vehicle within this many hours
# even if fault codes clear and reappear (e.g. intermittent faults)
FAULT_ALERT_COOLDOWN_HOURS = int(os.getenv("FAULT_ALERT_COOLDOWN_HOURS", "2"))

# Rate limiting: minimum seconds between same command from same user
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "10"))

# Vertex AI settings
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
VERTEX_AI_MODEL = os.getenv("VERTEX_AI_MODEL", "gemini-2.5-flash")

# Webhook settings (optional — if WEBHOOK_URL is empty, bot uses polling)
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
USE_WEBHOOK = bool(WEBHOOK_URL)

# Web App URL (for Telegram Mini App buttons)
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

# ── Logging ──────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bot")

# ── Mutable runtime state (canonical home: bot.state) ────────────
# Re-exported here for backward compatibility so existing
# ``from bot.config import db, get_client, …`` keeps working.

bot_username: str = ""                         # set in post_init via getMe

from bot.state import (  # noqa: E402
    db,
    _client_cache,
    _known_faults,
    _active_messages,
    _rate_limits,
    check_rate_limit,
    get_client,
    invalidate_client,
    get_user_company_codes,
)
