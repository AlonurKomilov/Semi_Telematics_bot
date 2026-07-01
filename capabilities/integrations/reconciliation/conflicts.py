"""Generic cross-source conflict store (the ``data_conflicts`` table).

Functions take the tenant ``db`` so nothing new registers on the storage
mixin (keeps the extraction off ``adapters/storage/__init__.py``).  ``resolve``
dispatches to the entity's registered applier, so it works for any domain.
"""

from __future__ import annotations

import logging
from typing import Any

from .registry import get_entity

logger = logging.getLogger(__name__)


async def record_conflict(
    db: Any, account_id: int, entity_type: str, entity_id: int, c: dict,
) -> None:
    """Upsert one OPEN conflict for ``(entity, field)``; refreshes on re-detect."""
    now = db._now()
    await db._db.execute(
        """INSERT INTO data_conflicts
           (account_id, entity_type, entity_id, field,
            current_value, current_source, incoming_value, incoming_source,
            status, detected_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
           ON CONFLICT(account_id, entity_type, entity_id, field)
           DO UPDATE SET
               current_value   = excluded.current_value,
               current_source  = excluded.current_source,
               incoming_value  = excluded.incoming_value,
               incoming_source = excluded.incoming_source,
               status          = 'open',
               resolved_by     = NULL,
               resolved_value  = NULL,
               updated_at      = excluded.updated_at""",
        (
            account_id, entity_type, entity_id, str(c.get("field") or ""),
            str(c.get("current_value") if c.get("current_value") is not None else ""),
            str(c.get("current_source") or ""),
            str(c.get("incoming_value") if c.get("incoming_value") is not None else ""),
            str(c.get("incoming_source") or ""),
            now, now,
        ),
    )


async def clear_conflict(
    db: Any, account_id: int, entity_type: str, entity_id: int, field: str,
) -> None:
    """Drop any OPEN conflict for ``(entity, field)`` — sources agree again."""
    await db._db.execute(
        "DELETE FROM data_conflicts WHERE account_id = ? AND entity_type = ? "
        "AND entity_id = ? AND field = ? AND status = 'open'",
        (account_id, entity_type, entity_id, field),
    )


async def list_open(
    db: Any, account_id: int, entity_type: "str | None" = None,
) -> list[dict]:
    """Open conflicts for the account, newest first."""
    where = ["account_id = ?", "status = 'open'"]
    args: list[Any] = [account_id]
    if entity_type:
        where.append("entity_type = ?")
        args.append(entity_type)
    cols = [
        "id", "entity_type", "entity_id", "field", "current_value",
        "current_source", "incoming_value", "incoming_source", "detected_at",
    ]
    cur = await db._db.execute(
        f"SELECT {', '.join(cols)} FROM data_conflicts "
        f"WHERE {' AND '.join(where)} ORDER BY detected_at DESC, id DESC",
        tuple(args),
    )
    return [dict(zip(cols, row)) for row in await cur.fetchall()]


async def count_open(db: Any, account_id: int) -> int:
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM data_conflicts "
        "WHERE account_id = ? AND status = 'open'",
        (account_id,),
    )
    rows = await cur.fetchall()
    return int(rows[0][0]) if rows and rows[0] else 0


async def resolve(
    db: Any, account_id: int, conflict_id: int, *, chosen_value: Any, resolved_by: int,
) -> "dict | None":
    """Resolve a conflict: dispatch to the entity's applier (which writes +
    PINS the chosen value), then mark the conflict resolved (audit kept).
    Returns ``{entity_type, entity_id, field}`` or None if not an open
    conflict of a registered entity."""
    cur = await db._db.execute(
        "SELECT entity_type, entity_id, field FROM data_conflicts "
        "WHERE id = ? AND account_id = ? AND status = 'open'",
        (conflict_id, account_id),
    )
    row = await cur.fetchone()
    if not row:
        return None
    entity_type, entity_id, fld = str(row[0]), int(row[1]), str(row[2])
    ent = get_entity(entity_type)
    if ent is None:
        return None
    await ent.apply_resolution(db, account_id, entity_id, fld, chosen_value)
    await db._db.execute(
        "UPDATE data_conflicts SET status = 'resolved', resolved_by = ?, "
        "resolved_value = ?, updated_at = ? WHERE id = ? AND account_id = ?",
        (resolved_by, str(chosen_value), db._now(), conflict_id, account_id),
    )
    await db._db.commit()
    return {"entity_type": entity_type, "entity_id": entity_id, "field": fld}


async def sync_batch(
    db: Any, account_id: int, entity_type: str, ops: list,
) -> None:
    """Record/clear conflicts for a batch AFTER the entity writes commit —
    best-effort + isolated, so the auxiliary store can never fail a sync.
    ``ops`` = list of ``(entity_id, conflicts, cleared)``."""
    if not ops:
        return
    try:
        for entity_id, conflicts, cleared in ops:
            for c in conflicts:
                await record_conflict(db, account_id, entity_type, entity_id, c)
            for fld in cleared:
                await clear_conflict(db, account_id, entity_type, entity_id, fld)
        await db._db.commit()
    except Exception as e:
        logger.debug("conflict sync skipped acct=%d: %s", account_id, e)
        try:
            await db._db.rollback()
        except Exception:
            pass
