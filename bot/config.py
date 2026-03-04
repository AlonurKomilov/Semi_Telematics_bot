"""Bot-wide configuration, globals, and client cache."""

import os
import logging

from database import Database
from samsara_client import MultiOrgClient, build_multi_org_client

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

_client_cache: dict[int, MultiOrgClient] = {}
_known_faults: dict[str, set[str]] = {}       # "acct:ORG:vid" → set(codes)
_active_messages: dict[int, list[int]] = {}   # chat_id → [msg_ids]
bot_username: str = ""                         # set in post_init via getMe


# ── Client cache helpers ─────────────────────────────────────────

async def get_client(account_id: int) -> MultiOrgClient:
    """Get or build a MultiOrgClient for an account."""
    if account_id in _client_cache:
        return _client_cache[account_id]
    orgs = await db.get_account_orgs(account_id)
    client = build_multi_org_client(orgs, SAMSARA_BASE_URL)
    _client_cache[account_id] = client
    return client


async def invalidate_client(account_id: int):
    """Drop cached client — call after adding/removing orgs."""
    old = _client_cache.pop(account_id, None)
    if old:
        await old.close()


async def get_user_org_codes(account_id: int) -> list[str]:
    """Get sorted org codes for an account."""
    orgs = await db.get_account_orgs(account_id)
    return [o.code for o in orgs]
