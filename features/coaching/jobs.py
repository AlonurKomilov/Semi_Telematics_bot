"""Coaching scheduled jobs — nightly evaluation pass.

Runs every night and, for each account with ``coaching_enabled = 1``,
calls :func:`features.coaching.service.run_evaluation` to persist
proposed assignments based on the last 7 days of scorecards / events.
"""

from __future__ import annotations

import logging

from infra.platform import get_platform_db

from . import service as svc
from .service import CoachingDisabledError

logger = logging.getLogger(__name__)


async def run_nightly_coaching_job(_app=None) -> None:
    """Iterate active, coaching-enabled accounts and run evaluation.

    Scheduler ticks hourly; the per-account body fires when it's
    currently 03:30-window (hour == 3) in the account's effective
    timezone.  Eastern fleet runs at 03:00 ET (08:00 UTC), Pacific
    fleet at 03:00 PT (11:00 UTC).
    """
    from capabilities.localization.tz import is_local_hour as _is_local_hour
    _TARGET_HOUR = 3
    pdb = get_platform_db()
    accounts = await pdb.list_accounts(active_only=True)

    total_created = 0
    skipped = 0
    failed = 0
    for acc in accounts:
        if not await _is_local_hour(acc.id, _TARGET_HOUR):
            continue
        if not getattr(acc, "coaching_enabled", False):
            skipped += 1
            continue
        try:
            ids = await svc.run_evaluation(acc.id, user_id=0, days=7)
            total_created += len(ids)
        except CoachingDisabledError:
            skipped += 1
        except Exception:
            logger.exception("nightly coaching evaluation failed for account %s", acc.id)
            failed += 1

    logger.info(
        "Nightly coaching job done — created=%d skipped=%d failed=%d",
        total_created, skipped, failed,
    )
