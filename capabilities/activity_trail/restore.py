"""Restore-from-trail — the trail's write side.

A delete event's ``changes`` carry the entire row as
``{field: {"from": value, "to": null}}`` — the recovery record the
2026-07-30 incident taught us to keep.  This engine replays those
from-values back into the owning table, driven entirely by the
feature's declaration (restore_table + restore_permissions +
restore_mode in its ``activity.py``): no declaration, no restore.

APPEND-ONLY, by owner requirement: restoring NEVER rewrites history.
The delete event stays forever; the restore appends its own event
(action ``restore``, values reversed, ``context.restored_from_event``
pointing at the delete it undid) in the SAME transaction as the
re-insert — so the record's story reads created → deleted → restored,
on one id, and a wrong restore is itself visible and deletable.
"""

import logging
from typing import Any, Optional

from adapters.storage.activity_trail import RestoreConflict  # noqa: F401 (re-export)
from .registry import EntityDescriptor, entity_descriptor

logger = logging.getLogger(__name__)

# Actions whose events carry a restorable body.
RESTORABLE_ACTIONS = frozenset({"delete", "merge_away", "deactivate"})


def restorable(d: Optional[EntityDescriptor], event: dict[str, Any]) -> bool:
    """Can THIS event be offered for restore at all (permission aside)?"""
    if d is None or not d.restore_permissions or not d.restore_table:
        return False
    if event.get("action") not in RESTORABLE_ACTIONS:
        return False
    if d.restore_mode == "insert" and not event.get("changes"):
        return False        # legacy imports carry no body — nothing to replay
    return True


def row_from_event(event: dict[str, Any]) -> dict[str, Any]:
    """The insertable row: every field's from-value."""
    return {
        f: pair.get("from")
        for f, pair in (event.get("changes") or {}).items()
        if isinstance(pair, dict)
    }


async def restore_from_event(
    db: Any,
    account_id: int,
    event: dict[str, Any],
    actor_user_id: int,
) -> int:
    """Re-insert (or reactivate) the entity and append the ``restore``
    event — one transaction, so the trail can never claim a restore
    that didn't land, nor miss one that did.

    Raises ``RestoreConflict`` when the id is occupied and
    ``ValueError`` for undeclared/unrestorable events.
    """
    d = entity_descriptor(event["entity_type"])
    if not restorable(d, event):
        raise ValueError("this event is not restorable")
    entity_id = int(event["entity_id"])
    row = row_from_event(event)
    async with db.transaction():
        if d.restore_mode == "reactivate":
            await db.reactivate_trail_row(account_id, d.restore_table, entity_id)
            changes = {"status": {"from": "inactive", "to": "active"}}
        else:
            await db.restore_trail_row(
                account_id, d.restore_table, entity_id, row,
            )
            changes = {f: {"from": None, "to": v}
                       for f, v in row.items() if v is not None}
        await db.append_activity_events(account_id, [{
            "entity_type": event["entity_type"],
            "entity_id": entity_id,
            "action": "restore",
            "changes": changes,
            "actor_user_id": actor_user_id,
            "context": {"restored_from_event": event["id"]},
        }])
    return entity_id
