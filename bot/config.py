"""Bot-wide configuration, globals, and client cache."""

import os
import logging
import time

from database import Database
from samsara_client import MultiCompanyClient, build_multi_company_client

# ── Environment ──────────────────────────────────────────────────

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SAMSARA_BASE_URL = os.getenv("SAMSARA_BASE_URL", "https://api.samsara.com")
ALERT_INTERVAL = int(os.getenv("ALERT_CHECK_INTERVAL_MINUTES", "30"))
FUEL_THRESHOLD = int(os.getenv("FUEL_LOW_THRESHOLD_PERCENT", "20"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/bot.db")
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "")

# Alert escalation: minutes before re-sending unacknowledged alert
ESCALATION_TIMEOUT_MINUTES = int(os.getenv("ESCALATION_TIMEOUT_MINUTES", "30"))

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

# ── Logging ──────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bot")

# ── Database ─────────────────────────────────────────────────────

db = Database(DATABASE_PATH)

# ── In-memory caches ─────────────────────────────────────────────

_client_cache: dict[int, MultiCompanyClient] = {}
_known_faults: dict[str, set[str]] = {}       # "acct:ORG:vid" → set(codes)
_active_messages: dict[tuple[int, int], list[int]] = {}   # (chat_id, user_id) → [msg_ids]
bot_username: str = ""                         # set in post_init via getMe

# Rate limiter: (user_id, command) → last_use_timestamp
_rate_limits: dict[tuple[int, str], float] = {}


def check_rate_limit(user_id: int, command: str) -> bool:
    """Return True if the command is allowed (not rate-limited).

    Returns False if the user should be throttled.
    """
    key = (user_id, command)
    now = time.time()
    last = _rate_limits.get(key, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return False
    _rate_limits[key] = now
    return True


# ── Client cache helpers ─────────────────────────────────────────

async def get_client(account_id: int) -> MultiCompanyClient:
    """Get or build a MultiCompanyClient for an account."""
    if account_id in _client_cache:
        return _client_cache[account_id]
    companies = await db.get_account_companies(account_id)
    client = build_multi_company_client(companies, SAMSARA_BASE_URL)
    _client_cache[account_id] = client
    return client


async def invalidate_client(account_id: int):
    """Drop cached client — call after adding/removing companies."""
    old = _client_cache.pop(account_id, None)
    if old:
        await old.close()


async def get_user_company_codes(account_id: int) -> list[str]:
    """Get sorted company codes for an account."""
    companies = await db.get_account_companies(account_id)
    return [o.code for o in companies]
