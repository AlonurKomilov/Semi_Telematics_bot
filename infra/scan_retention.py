"""Retention contract for the platform-scope ``scan_log`` table.

The scanner (``infra/file_safety.scan_or_reject``) writes one row per
upload scan attempt — clean, infected, OR daemon-unavailable.  This
module wires that table into the cross-cutting Retention hub so the
nightly ``data_retention`` job prunes it on the operator-declared
90-day window without the scanner itself needing to know about cron.

Lives under ``infra/`` because the scanner is infrastructure (not a
feature), and the retention registry already accepts cross-feature
contributions.  Imported at app startup so ``register_target`` /
``register_need`` fire before the engine resolves.
"""

from __future__ import annotations

from capabilities.lifecycle.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)


# 90-day window matches the audit-retention defaults the operator
# console uses for sensitive logs.  Long enough for forensics on a
# malware-upload incident, short enough that an inactive table doesn't
# grow unbounded.  Tunable via the registry — change here, no migration
# needed.
_SCAN_LOG_DAYS = 90


async def _prune_scan_log(db, _account_id_unused, days: int) -> int:
    """Delete scan-log rows older than ``days``.  Returns rows deleted.

    Platform-scoped (``account_id`` is ignored — scan_log is global).
    Uses a single DELETE so the row count comes back from the cursor;
    no per-account fan-out needed.
    """
    cur = await db._db.execute(
        f"DELETE FROM scan_log WHERE created_at < NOW() - INTERVAL '{int(days)} days'"
    )
    # asyncpg-style cursor exposes ``rowcount`` after a DML execute.
    return int(getattr(cur, "rowcount", 0) or 0)


register_target(RetentionTarget(
    "platform.scan_log",
    "File scan history",
    "platform",
    _prune_scan_log,
))
register_need(RetentionNeed(
    "security", "platform.scan_log", _SCAN_LOG_DAYS,
    "per-upload AV scan audit trail (compliance + incident response)",
))
