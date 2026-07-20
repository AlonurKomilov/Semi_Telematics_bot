"""Scheduled digest flushes — the consumers of the batched-cadence queue.

Thin scheduler adapters: resolve the db handle, call
:func:`capabilities.notifications.service.flush_digests`, log the count.
All grouping/rendering logic lives in the service so it is testable
without a scheduler.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def _flush(cadence: str) -> None:
    import infra.platform as platform

    from capabilities.notifications.service import flush_digests

    db = getattr(platform, "_db", None)
    if db is None:
        return
    sent = await flush_digests(db, cadence)
    if sent:
        logger.info("notification digest flush (%s): %d sent", cadence, sent)


async def job_flush_hourly_digests(_app=None) -> None:
    await _flush("hourly")


async def job_flush_daily_digests(_app=None) -> None:
    """Fires at one fixed UTC hour.  Per-account local-morning delivery is
    a refinement for when Email ships — the queue is already keyed by
    account, so it needs a filter here, not a schema change."""
    await _flush("daily")
