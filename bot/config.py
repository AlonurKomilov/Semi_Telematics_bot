"""Bot-wide configuration, globals, and client cache."""

import os
import logging

from database import Database
from samsara_client import MultiCompanyClient, build_multi_company_client

# ── Environment ──────────────────────────────────────────────────

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SAMSARA_BASE_URL = os.getenv("SAMSARA_BASE_URL", "https://api.samsara.com")
ALERT_INTERVAL = int(os.getenv("ALERT_CHECK_INTERVAL_MINUTES", "30"))
FUEL_THRESHOLD = int(os.getenv("FUEL_LOW_THRESHOLD_PERCENT", "20"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/bot.db")
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "")

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
