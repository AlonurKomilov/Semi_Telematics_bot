"""How long the security ledger is kept.

Ninety days.  Long enough that a slow probe — one signup a day for a
month, then the sweep — is still one story when someone reads it, and
that a monitored account's timeline outlives the decision to monitor it.
Short enough that the denial ledger, which every scanner on the internet
contributes to, does not become the biggest table on the box.

Declared like every other dataset's window so the nightly retention job
finds it (capabilities/data_lifecycle/retention/__init__.py lists this
module), and so the operator console's Retention page shows it beside
the rest.
"""

from __future__ import annotations

from capabilities.data_lifecycle.retention.registry import (
    RetentionNeed, RetentionTarget, register_need, register_target,
)

TARGET_KEY = "security.requests"
KEEP_DAYS = 90


async def _prune(db, _account_id, keep_days: int) -> int:
    return await db.prune_security_requests(keep_days)


register_target(RetentionTarget(
    key=TARGET_KEY,
    label="Security request ledger",
    scope="platform",
    prune=_prune,
))
register_need(RetentionNeed(
    feature="security",
    target=TARGET_KEY,
    keep_days=KEEP_DAYS,
    reason="monitored-account timelines and the refusal ledger the detector reads",
))
