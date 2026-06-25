"""Roll-up engine — runs one cascade stage across every active account.

The generic half of a downsampling cascade: take a registered stage and fan
its per-account run function across all active accounts, reusing the
warehouse ingestor's bounded-concurrency, error-isolated iterator (the same
one the Retention hub uses).  The feature-specific aggregation lives in the
stage's ``run``; the engine only decides FOR WHOM it runs.

Runs for every active account (no capability gate): a roll-up on a stream
with no rows is a harmless no-op, and the tiers are provider-agnostic — they
downsample whatever already sits in the warehouse regardless of which
provider filled it.
"""

from __future__ import annotations

import logging

from .registry import RollupStage

logger = logging.getLogger(__name__)


async def run_stage(stage: RollupStage) -> None:
    from .._common import for_each_active_account

    await for_each_active_account(stage.run)
