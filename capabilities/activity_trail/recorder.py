"""Pure helpers that build ``activity_events`` payloads.

No I/O here — everything is unit-testable data shaping.  The SQL lives
in ``adapters/storage/activity_trail.py``; the write contract (people
only, same transaction, bulk = group) lives in that mixin's docstring.
"""

import uuid
from typing import Any, Iterable, Optional

# Fields no diff should ever include: bookkeeping columns that change
# on every write and would drown the real edit.
_ALWAYS_SKIP = frozenset({"updated_at", "created_at", "id", "account_id"})


def diff_rows(
    old: dict[str, Any],
    new: dict[str, Any],
    fields: Optional[Iterable[str]] = None,
    *,
    skip: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    """Field-level ``{field: {"from": x, "to": y}}`` between two rows.

    ``fields`` narrows the comparison (pass the endpoint's editable
    allow-list); otherwise every key present in either row is compared.
    Equal values are excluded — an empty result means "nothing actually
    changed" and the caller may skip recording entirely.
    """
    skipset = _ALWAYS_SKIP | set(skip)
    keys = set(fields) if fields is not None else (set(old) | set(new))
    out: dict[str, dict[str, Any]] = {}
    for k in keys:
        if k in skipset:
            continue
        a, b = old.get(k), new.get(k)
        if _same(a, b):
            continue
        out[k] = {"from": a, "to": b}
    return out


def delete_changes(
    row: dict[str, Any],
    fields: Optional[Iterable[str]] = None,
) -> dict[str, dict[str, Any]]:
    """The DELETE body: every meaningful field as ``{from: v, to: None}``.

    This is the trail's recovery record — restoring a deleted row is
    replaying these ``from`` values.  Nulls/empties are kept out (they
    restore to nothing anyway); bookkeeping columns are skipped.
    """
    keys = set(fields) if fields is not None else set(row)
    out: dict[str, dict[str, Any]] = {}
    for k in keys:
        if k in _ALWAYS_SKIP:
            continue
        v = row.get(k)
        if v is None or v == "":
            continue
        out[k] = {"from": v, "to": None}
    return out


def new_group_id() -> str:
    """One id per bulk ACTION — every affected entity's event carries it."""
    return uuid.uuid4().hex


async def record_simple(
    db: Any,
    account_id: int,
    actor_user_id: Optional[int],
    action: str,
    entity_type: str,
    entity_id: Any,
    *,
    changes: Optional[dict] = None,
    context: Optional[dict] = None,
    note: str = "",
    group_id: Optional[str] = None,
) -> None:
    """One-call adoption for router-level writers.

    For mutations that flow through shared self-committing storage
    methods (update_user and friends), the event appends right after
    the mutation instead of inside it — the same documented deviation
    as team management; full same-transaction threading arrives when
    that storage modernizes.  Everything else the contract demands
    still holds: people-only attribution, values over prose, real
    action names.
    """
    await db.append_activity_events(account_id, [{
        "entity_type": entity_type, "entity_id": entity_id,
        "action": action, "changes": changes or {},
        "actor_user_id": actor_user_id,
        "context": context or {}, "note": note, "group_id": group_id,
    }])


def _same(a: Any, b: Any) -> bool:
    """Value equality that doesn't invent edits: 35000 == 35000.0, but
    '' vs None IS a change (clearing a field is an action)."""
    if a is None and b is None:
        return True
    if isinstance(a, bool) != isinstance(b, bool):
        return False        # True vs 1: a type change is a change
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return float(a) == float(b)
    return a == b
