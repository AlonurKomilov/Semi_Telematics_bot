"""Market-intel nightly job (Phase D) — rebuilds the anonymized
price rollups from sharing accounts.

Dark-launch flag: ``MARKET_INTEL_ENABLED``.  While unset the job is a
no-op and the read/toggle endpoints answer "disabled" — engineering
ships fully built, the switch flips after the legal review (plan doc
open question #3).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

FLAG = "MARKET_INTEL_ENABLED"


def market_intel_enabled() -> bool:
    return os.getenv(FLAG, "0").strip() in ("1", "true", "TRUE", "yes")


async def job_market_rollups(_app=None) -> None:
    """Nightly full rebuild.  Cheap while adoption is small (full
    rebuild = one pass over sharing accounts' 12-month window)."""
    if not market_intel_enabled():
        return
    import infra.platform as platform
    db = getattr(platform, "_db", None)
    if db is None:
        return
    since = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    try:
        n = await db.compute_market_rollups(since)
        logger.info("market_rollups: %d cells published", n)
    except Exception as e:
        logger.error("market_rollups failed: %s", e, exc_info=True)
