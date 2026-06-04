"""TTL enforcer for ``alert_acknowledgments``.

What this does
──────────────
Closes ack rows whose ``expires_at`` has passed.  ``expires_at`` is
stamped at row creation (see ``AlertsMixin.create_alert_ack``):

    critical → NULL    (never expires — never touched here)
    warning  → +14 days
    info     →  +7 days

Runs nightly at 03:15 UTC via ``interfaces/bot/scheduler.py``.

Why TTL is a first-class column, not a periodic sweep
─────────────────────────────────────────────────────
Previous designs swept "old active rows" with a `--days N` parameter
on a CLI script — operationally a band-aid that pretended those rows
shouldn't exist.  Making the expiry a column changes the mental model:
each alert is **born** with a known life span tied to its severity.
The nightly job is the enforcer of a contract the row signed at
creation, not an external sweep.  Dashboard queries can also filter
on ``expires_at`` so they never show ack-rows that have already
aged out.

Safety rails
────────────
* Critical alerts have ``expires_at IS NULL`` — they're invisible to
  this job by construction.  Only human-ack or system-resolve can
  close a critical row.
* ``maintenance`` alerts are also excluded — their own due-date
  lifecycle owns ack state for them.
* Writes an ``audit_log`` entry per batch with the touched IDs (capped
  at 500 for storage) so an accidental close can be reverted by
  setting the listed rows back to ``status='active'``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# Alert types whose ack rows are NEVER closed by the TTL enforcer,
# even when their ``expires_at`` has passed.  ``maintenance`` rows
# have their own due-date / completion lifecycle that owns ack state
# for them.  ``events`` already get NULL ``expires_at`` from
# ``create_alert_ack`` (they're critical-equivalent), so this is a
# defence-in-depth filter on top.
SKIP_ALERT_TYPES: frozenset[str] = frozenset({"events", "maintenance"})


async def stale_close_for_account(
    tenant_db: Any,
    account_id: int,
    *,
    apply_changes: bool,
) -> dict:
    """Close ack rows whose ``expires_at`` has passed.

    Caller is responsible for the ``tenant_db.with_account()``
    context; this function only runs the SQL.

    ``apply_changes=False`` returns the candidate counts without
    modifying anything — useful for an audit-only dry run.

    Returns a stats dict suitable for logging or audit storage:

        {
          "account_id":           1,
          "candidate_total":      247,
          "candidates_by_type":   {"fault": 120, "fuel": 80, ...},
          "skipped_types":        ["events", "maintenance"],
          "applied":              True,
          "closed_ack_rows":      247,
          "closed_history_rows":  41,
        }

    Native Postgres SQL with ``$N`` placeholders — passes through the
    adapter's SQLite translator unchanged.
    """
    # ``expires_at`` stored as ISO-8601 text — lexicographic
    # comparison against ``now`` is the same as chronological
    # comparison for ISO format.  No timestamp cast needed.
    now_iso = datetime.now(timezone.utc).isoformat()

    # Step 1 — count candidates (always run, even on dry-run).
    # Explicit ``::text`` cast on the cutoff: asyncpg can't infer the
    # parameter type when ``expires_at IS NOT NULL AND expires_at < $2``
    # because the IS-NOT-NULL doesn't constrain $2's type.
    cur = await tenant_db._db.execute(
        "SELECT alert_type, COUNT(*) AS n "
        "FROM alert_acknowledgments "
        "WHERE account_id = $1 "
        "  AND status = 'active' "
        "  AND expires_at IS NOT NULL "
        "  AND expires_at < $2::text "
        "GROUP BY alert_type",
        (account_id, now_iso),
    )
    rows = await cur.fetchall()
    counts: dict[str, int] = {
        r["alert_type"]: r["n"] for r in rows
        if r["alert_type"] not in SKIP_ALERT_TYPES
    }
    total = sum(counts.values())

    stats: dict = {
        "account_id":          account_id,
        "candidate_total":     total,
        "candidates_by_type":  counts,
        "skipped_types":       sorted(SKIP_ALERT_TYPES),
        "applied":             False,
        "closed_ack_rows":     0,
        "closed_history_rows": 0,
    }

    if not apply_changes or total == 0:
        stats["applied"] = apply_changes
        return stats

    # Step 2 — capture the IDs we're about to touch (for the audit
    # log rollback target list).  Same ``::text`` cast as Step 1.
    cur = await tenant_db._db.execute(
        "SELECT id FROM alert_acknowledgments "
        "WHERE account_id = $1 "
        "  AND status = 'active' "
        "  AND expires_at IS NOT NULL "
        "  AND expires_at < $2::text "
        "  AND alert_type NOT IN ('events', 'maintenance')",
        (account_id, now_iso),
    )
    touched_ids = [r["id"] for r in await cur.fetchall()]

    # Step 3 — close the expired rows in 200-row chunks to keep
    # the row lock window small (matches the bulk-ack endpoint).
    chunk = 200
    for i in range(0, len(touched_ids), chunk):
        ids = touched_ids[i:i + chunk]
        # Placeholders start at $2 — $1 binds ``now_iso`` for the SET clause.
        placeholders = ",".join(f"${j + 2}" for j in range(len(ids)))
        await tenant_db._db.execute(
            f"UPDATE alert_acknowledgments "
            f"SET status = 'acknowledged', "
            f"    acknowledged_at = $1, "
            f"    acknowledged_by = 0 "
            f"WHERE id IN ({placeholders})",
            [now_iso] + ids,
        )

    # Step 4 — close expired ``alert_history`` rows under the SAME
    # TTL ladder.  ``alert_history`` doesn't have its own
    # ``expires_at`` column — we derive expiry from ``severity`` +
    # ``last_seen`` so the two tables stay in sync without doubling
    # the storage cost or needing a foreign key.
    #
    # Dashboard reads from ``alert_history`` so this is what actually
    # makes the "1895 pending" count drop after a night of TTL sweeps.
    from datetime import timedelta
    now_dt = datetime.fromisoformat(now_iso)
    warning_cutoff = (now_dt - timedelta(days=14)).isoformat()
    info_cutoff    = (now_dt - timedelta(days=7)).isoformat()

    cur = await tenant_db._db.execute(
        "UPDATE alert_history "
        "SET status = 'cleared', last_seen = $1 "
        "WHERE account_id = $2 "
        "  AND status = 'active' "
        "  AND alert_type NOT IN ('events', 'maintenance') "
        "  AND severity != 'critical' "
        "  AND ("
        "      (severity = 'warning' AND last_seen < $3::text) "
        "   OR (severity = 'info'    AND last_seen < $4::text) "
        "  ) "
        "RETURNING id",
        (now_iso, account_id, warning_cutoff, info_cutoff),
    )
    history_ids = [r["id"] for r in await cur.fetchall()]

    # Step 5 — audit_log entry for the batch.
    await tenant_db.add_audit_log(
        account_id=account_id,
        user_id=None,
        action="alerts_ttl_close",
        target_type="alert_acknowledgments",
        target_id=",".join(str(i) for i in touched_ids[:500]),
        details=json.dumps({
            "ack_closed_count":      len(touched_ids),
            "history_cleared_count": len(history_ids),
            "by_type":               counts,
            "skipped_types":         sorted(SKIP_ALERT_TYPES),
        }),
    )

    stats["applied"] = True
    stats["closed_ack_rows"] = len(touched_ids)
    stats["closed_history_rows"] = len(history_ids)
    return stats


async def nightly_stale_close(_app=None) -> None:
    """Scheduled-job entry point — runs once per night at 03:15 UTC.

    Walks every active account and closes ack rows whose ``expires_at``
    has passed.  Per-account work is wrapped in ``run_account_job(
    tenant_db=...)`` which sets the ``app.account_id`` GUC for the
    duration so RLS policies apply.  Per-account failures are
    isolated — one bad account doesn't stop the others.
    """
    from infra.services import get_platform_db, get_tenant_db
    from infra.isolation import run_account_job

    try:
        accounts = await get_platform_db().list_accounts(active_only=True)
    except Exception:
        logger.exception("nightly_stale_close: list_accounts failed")
        return

    if not accounts:
        return

    grand_total_ack = 0
    grand_total_history = 0
    grand_failures = 0

    for acc in accounts:
        async def _do(aid=acc.id):
            nonlocal grand_total_ack, grand_total_history
            tenant = await get_tenant_db(aid)
            stats = await stale_close_for_account(
                tenant, aid, apply_changes=True,
            )
            grand_total_ack += stats["closed_ack_rows"]
            grand_total_history += stats["closed_history_rows"]
            if stats["closed_ack_rows"]:
                logger.info(
                    "nightly_ttl_close acct=%d closed=%d history=%d by_type=%s",
                    aid, stats["closed_ack_rows"],
                    stats["closed_history_rows"],
                    stats["candidates_by_type"],
                )

        tenant_db = await get_tenant_db(acc.id)
        ok = await run_account_job(
            _do(), account_id=acc.id,
            job_name="nightly_ttl_close",
            tenant_db=tenant_db,
        )
        if not ok:
            grand_failures += 1

    if grand_total_ack or grand_failures:
        logger.info(
            "nightly_ttl_close summary: accounts=%d closed_ack=%d "
            "closed_history=%d failures=%d",
            len(accounts), grand_total_ack, grand_total_history,
            grand_failures,
        )
