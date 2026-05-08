"""Coaching scheduled jobs — nightly evaluation pass.

Runs every night and, for each account with ``coaching_enabled = 1``,
calls :func:`capabilities.coaching.service.run_evaluation` to persist
proposed assignments based on the last 7 days of scorecards / events.
"""

from __future__ import annotations

import logging

from infra.platform import get_platform_db

from . import service as svc
from .service import CoachingDisabledError

logger = logging.getLogger(__name__)


async def run_nightly_coaching_job(_app=None) -> None:
    """Iterate active, coaching-enabled accounts and run evaluation."""
    pdb = get_platform_db()
    accounts = await pdb.list_accounts(active_only=True)

    total_created = 0
    skipped = 0
    failed = 0
    for acc in accounts:
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
