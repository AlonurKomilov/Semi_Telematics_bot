"""Activity Trail — the unified who-did-what capability.

Every feature that mutates records adopts this trail: the storage side
is ``ActivityTrailMixin`` (adapters/storage/activity_trail.py, table
``activity_events``); THIS package holds the pure contract helpers —
building field diffs, delete bodies, and bulk groups — plus the
sensitive-field registry the reader masks by.

Adoption recipe (see the maintenance feature for the worked example):

    from capabilities.activity_trail import diff_rows, delete_changes, new_group_id

    async with db.transaction():                    # ONE transaction
        old = await db.get_thing(account_id, thing_id)
        await db.update_thing(...)
        await db.append_activity_events(account_id, [{
            "entity_type": "thing", "entity_id": thing_id,
            "action": "update", "actor_user_id": actor,
            "changes": diff_rows(old, new, fields=EDITABLE_FIELDS),
        }])

The contract lives in the mixin's docstring: people only, same
transaction, deletes carry the body, bulk = N events + group_id.
"""

from .recorder import delete_changes, diff_rows, new_group_id
from .sensitive import SENSITIVE_FIELDS, mask_changes

__all__ = [
    "diff_rows",
    "delete_changes",
    "new_group_id",
    "SENSITIVE_FIELDS",
    "mask_changes",
]
