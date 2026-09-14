"""Market-intel nightly job (Phase D) — rebuilds the anonymized price
rollups from sharing accounts: per-shop cells (vendor profiles) AND
part-centric geographic cells (Catalog estimates), one pass.

Launch gate: the ``market_intel_enabled()`` method on the shared
``Database`` — the system-console switch, with the MARKET_INTEL_ENABLED
env var honored as an emergency override.  While off the job no-ops
and every read endpoint answers "disabled"; flip happens on
system.4truck.us after the owner's legal review.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


async def job_market_rollups(_app=None) -> None:
    """Nightly full rebuild (04:40 UTC).  Cheap while adoption is
    small — one pass over sharing accounts' 12-month window."""
    import infra.platform as platform
    db = getattr(platform, "_db", None)
    if db is None:
        return
    if not await db.market_intel_enabled():
        return
    since = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    try:
        # Duration is the scale gauge: the full rebuild is deliberately
        # simple (sliding window + rank statistics + retroactive edits
        # all self-heal), and THIS number tells us years in advance
        # when to reach for percentile_cont / dirty-cell marking.
        import time as _time
        t0 = _time.monotonic()
        n = await db.compute_market_rollups(since)
        elapsed = _time.monotonic() - t0
        logger.info(
            "market_rollups: %d vendor cells published in %.1fs%s",
            n, elapsed,
            " — SLOW, consider SQL-side percentiles + dirty-cell marking"
            if elapsed > 300 else "",
        )
    except Exception as e:
        logger.error("market_rollups failed: %s", e, exc_info=True)
