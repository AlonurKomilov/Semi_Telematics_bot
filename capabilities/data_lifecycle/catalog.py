"""DB self-description — the registry, stamped onto the tables.

Every table a registered dataset writes carries a ``COMMENT`` —
visible in psql ``\\dt+`` and in the tree of every DB GUI — generated
from the ingest registry at boot, so it cannot drift from the code
that owns it.  The ``warehouse`` schema groups the family physically
(migration 183); the comments say what a name alone still can't:
which dataset feeds it, who owns it, what grain it holds.

Table names here are UNQUALIFIED on purpose: the pool search_path
(public,warehouse) resolves them on either side of the schema-move
window, so this job is safe to run before and after it.

Comments are metadata only: no reader, writer, or query plan depends
on them.  Re-running the sync is idempotent (COMMENT overwrites).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SSOT = "SSOT capabilities/data_lifecycle/docs/warehouse.md"

# Grain/kind notes for tables whose dataset key alone doesn't say what
# tier they hold.  Keyed by physical table name.
_TABLE_NOTES = {
    "vehicle_state_live": "grain live — one current sample row per vehicle",
    "vehicle_state_minute": "grain minute — sample history, labels on the minute grid",
}

# Build outputs and machinery: written by the rollup/ledger paths, not
# by any ingest dataset, so the registry loop can't describe them.
_EXTRA_COMMENTS = {
    **{
        f"vehicle_state_{g}":
            f"warehouse: vehicle.timeline aggregate (owner: vehicles) · "
            f"grain {g} · built by rollup stages, never ingested · {_SSOT}"
        for g in ("hour", "day", "week")
    },
    "ingest_runs":
        f"warehouse machinery: day-grain ACQUIRE ledger (dataset_key × account × day) · {_SSOT}",
    "device_event_log":
        f"warehouse log: identity events — vin_change / gateway_swap / "
        f"odo_rebase, appended by the ingest's identity watch · {_SSOT}",
    "ingest_orphans":
        f"warehouse machinery: identities the registry could not resolve at ingest · {_SSOT}",
}


def _comment_for(dataset, table: str) -> str:
    base = f"warehouse: {dataset.key} (owner: {dataset.owner})"
    note = _TABLE_NOTES.get(table)
    if note:
        base += f" · {note}"
    return f"{base} · {_SSOT}"


async def sync_table_comments(db) -> int:
    """Stamp every registered warehouse table with its family comment.

    Fail-soft per table: a table missing on a partially-migrated
    install skips with a debug line rather than aborting the sweep.
    """
    from capabilities.data_lifecycle.ingest import all_datasets, discover

    discover()
    comments: dict[str, str] = {}
    for dataset in all_datasets():
        for table in dataset.tables:
            comments[table] = _comment_for(dataset, table)
    comments.update(_EXTRA_COMMENTS)

    synced = 0
    for table, text in sorted(comments.items()):
        literal = text.replace("'", "''")
        try:
            # Table names and text both come from code-declared
            # registries — nothing user-supplied reaches this DDL.
            await db._db.execute(
                f"COMMENT ON TABLE {table} IS '{literal}'"  # noqa: S608
            )
            synced += 1
        except Exception as e:
            logger.debug("catalog comment skipped for %s (%s)", table, e)
    await db._db.commit()
    return synced


async def job_sync_table_comments() -> None:
    """One-shot boot job: the scheduler fires this once per process."""
    from infra.platform import get_platform_db

    try:
        db = get_platform_db()
    except Exception:
        return
    if db is None:
        return
    synced = await sync_table_comments(db)
    logger.info("warehouse catalog: %d table comments synced", synced)
