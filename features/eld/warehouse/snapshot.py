"""Copy each driver's live duty reading into the history tier.

The live table is a mirror that the feed overwrites every five minutes;
without this, the previous reading is simply gone, and "who was driving
at 14:00 yesterday" — an audit's first question — had no answer while
every vehicle feed in the warehouse could answer its own.

WHAT IS COPIED, AND WHAT IS NOT.  The row as the feed WROTE it: the
provider's own driver id, the link to our member if an admin made one,
the duty status, the four countdown clocks (nullable — an ELD that does
not report them writes NULL, and history must not turn that into zero),
and the provider's own ``source_ts``.  Not the roster's display name
and not the joined truck: those are how the reader dresses a row for
today's screen, and history keeps what was observed, not what was shown.

THE SLOT IS FIVE MINUTES because the ingest is.  The vehicle tier
samples every minute to match a one-minute feed; here a one-minute
sample would write four identical rows per reading and call it
history.  ``captured_at`` is the slot label; ``source_ts`` is when the
device actually saw the world move — the two are kept apart on purpose
(Contract 2).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from capabilities.data_lifecycle.timegrid import floor_to_slot
from infra.services import get_tenant_db

logger = logging.getLogger(__name__)

#: Matches the ELD ingest cadence (features/eld/lifecycle.py).
SLOT_SECONDS = 5 * 60


async def snapshot_driver_hos(account_id: int) -> int:
    """One slot of duty-status history for every driver the account's
    ELDs currently report.  Returns rows written."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    live = await tenant.list_driver_hos_live_raw(account_id)
    if not live:
        return 0
    slot = floor_to_slot(datetime.now(timezone.utc), SLOT_SECONDS)
    rows = [{**r, "captured_at": slot} for r in live]
    n = await tenant.upsert_driver_hos_minutes(account_id, rows)
    if n:
        logger.debug("hos snapshot acct=%d slot=%s rows=%d", account_id, slot, n)
    return n
