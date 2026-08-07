"""Operator Telegram messages via the SYSTEM bot's API identity.

Raw HTTPS on purpose: the bot's API identity works from ANY process
that holds the token (the backup script proved the pattern), so
machinery like the ingest can announce operator-grade events without
importing the telegram stack.  Best-effort — a notify failure must
never poison the caller.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


async def send_operator_message(text: str) -> None:
    token = os.getenv("TELEGRAM_SYSTEM_BOT_TOKEN", "")
    owners = [o.strip() for o in
              os.getenv("SYSTEM_OWNER_IDS", "").split(",") if o.strip()]
    if not token or not owners:
        return
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            for chat_id in owners:
                await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    data={"chat_id": chat_id, "text": text},
                )
    except Exception as e:
        logger.warning("operator notify failed: %s", e)
