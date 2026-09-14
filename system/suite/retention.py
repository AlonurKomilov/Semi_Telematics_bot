"""How long the board remembers.

Ninety days, the same window the security ledger keeps, and for a
related reason: the bracket a failure carries is only as good as the
runs behind it, and a test that goes red in March and is read in May
needs March still to be there.

Registered by ``system.bootstrap`` rather than named in the retention
hub's contributor list — nothing below the system layer may know this
module exists.
"""

from __future__ import annotations

from capabilities.data_lifecycle.retention.registry import (
    RetentionNeed, RetentionTarget, register_need, register_target,
)

TARGET_KEY = "suite.runs"
KEEP_DAYS = 90


async def _prune(db, _account_id, keep_days: int) -> int:
    return await db.prune_suite_runs(keep_days)


register_target(RetentionTarget(
    key=TARGET_KEY,
    label="Test board runs",
    scope="platform",
    prune=_prune,
))

register_need(RetentionNeed(
    feature="suite",
    target=TARGET_KEY,
    keep_days=KEEP_DAYS,
    reason="a failure's bracket is only as good as the runs behind it",
))
