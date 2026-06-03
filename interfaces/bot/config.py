"""Bot-wide configuration — environment settings and re-exported runtime state.

Pure read-only configuration (env vars, logging) lives here.
Mutable runtime state (db, caches, client helpers) lives in bot.state
and is re-exported below for backward compatibility.
"""

import os
import logging

# ── Environment ──────────────────────────────────────────────────

# Customer-facing front-door bot — runs the long-running PTB daemon
# (interfaces/bot/app.py) that handles /start /register /join and
# validates Telegram Login Widget signatures for accounts without a
# per-account bot.  Prefers TELEGRAM_LOGIN_BOT_TOKEN; falls back to the
# system token (new name first, legacy name last) during the rollout
# window so existing deploys don't break before the operator sets the
# dedicated login-bot env var.
TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_LOGIN_BOT_TOKEN")
    or os.getenv("TELEGRAM_SYSTEM_BOT_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN", "")
)
if not os.getenv("TELEGRAM_LOGIN_BOT_TOKEN"):
    import logging as _log
    _log.getLogger(__name__).warning(
        "TELEGRAM_LOGIN_BOT_TOKEN is unset — the customer-facing bot daemon "
        "is falling back to the system bot token.  Set TELEGRAM_LOGIN_BOT_TOKEN "
        "to separate the customer login bot from the system/operator bot."
    )
SAMSARA_BASE_URL = os.getenv("SAMSARA_BASE_URL", "https://api.samsara.com")
# Dashboard base — derived from API URL (api.→cloud.) unless overridden
SAMSARA_DASHBOARD_URL = os.getenv(
    "SAMSARA_DASHBOARD_URL",
    SAMSARA_BASE_URL.replace("://api.", "://cloud.").rstrip("/"),
)
ALERT_INTERVAL = int(os.getenv("ALERT_CHECK_INTERVAL_MINUTES", "30"))
FUEL_THRESHOLD = int(os.getenv("FUEL_LOW_THRESHOLD_PERCENT", "20"))
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "")

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
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8001"))
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

# Mutable runtime state lives in ``interfaces.bot.state`` —
# ``from interfaces.bot.state import db, get_client, get_platform_db,
# get_tenant_db, invalidate_client, …``.  This module only owns
# read-only env-var configuration.

bot_username: str = ""                         # set in post_init via getMe

# Redis key prefix + TTL for the Telegram-link flow.  Lives here so both
# the API route that creates the token and the bot handler that resolves
# it agree on the bucket name without one importing the other.
TELEGRAM_LINK_PREFIX = "tg_link:"
TELEGRAM_LINK_TTL = 300  # seconds — short window: link or retry

