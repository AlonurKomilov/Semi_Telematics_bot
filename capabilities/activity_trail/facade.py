"""The unified READ over every who-did-what source.

Exactly three arms, by ruling (never a fourth):
  * ``activity_events``            — the universal trail (all adopters +
    the frozen log's human history, imported by migration 178)
  * ``load_events``                — shipped rich trail, stays put
  * ``vehicle_inventory_events``   — shipped rich trail, stays put

``audit_log`` itself is no longer read here: since migration 178 it is
a machine-only log (alert lifecycle, webhooks) whose rows age out.

Every arm normalizes to one wire shape::

    {source, id, entity_type, entity_id, action,
     changes: {field: {"from": x, "to": y}},
     actor_user_id, actor_space ('platform'|'telegram'),
     group_id, context, note, created_at}

``actor_space`` exists because the frozen log stored TELEGRAM ids while
the trails store platform ``users.id`` — the API layer resolves names
with the right map per space.

Pure data shaping (the SQL lives on the mixin) — unit-testable without
a database.
"""

from typing import Any, Optional

from .sensitive import mask_changes


def normalize_trail(e: dict) -> dict:
    return {
        "source": "trail", "id": f"trail:{e['id']}",
        "entity_type": e["entity_type"], "entity_id": e["entity_id"],
        "action": e["action"], "changes": e.get("changes") or {},
        "actor_user_id": e.get("actor_user_id"), "actor_space": "platform",
        "group_id": e.get("group_id"), "context": e.get("context") or {},
        "note": e.get("note") or "", "created_at": e["created_at"],
    }


def normalize_load(e: dict) -> dict:
    # load_events stores diffs as {field: [old, new]} — normalize pairs.
    changes = {
        k: {"from": v[0], "to": v[1]}
        for k, v in (e.get("changes") or {}).items()
        if isinstance(v, (list, tuple)) and len(v) == 2
    }
    return {
        "source": "loads", "id": f"loads:{e['id']}",
        "entity_type": "load", "entity_id": str(e["load_id"]),
        "action": e["event_type"], "changes": changes,
        "actor_user_id": e.get("actor_user_id"), "actor_space": "platform",
        "group_id": None,
        "context": ({"dispatcher_user_id": e["dispatcher_user_id"]}
                    if e.get("dispatcher_user_id") else {}),
        "note": e.get("note") or "", "created_at": e["created_at"],
    }


def normalize_inventory(e: dict) -> dict:
    changes: dict[str, dict] = {}
    if e.get("from_status") is not None or e.get("to_status") is not None:
        changes["status"] = {"from": e.get("from_status"), "to": e.get("to_status")}
    if e.get("from_vehicle_id") is not None or e.get("to_vehicle_id") is not None:
        changes["vehicle_id"] = {
            "from": e.get("from_vehicle_id"), "to": e.get("to_vehicle_id"),
        }
    return {
        "source": "inventory", "id": f"inventory:{e['id']}",
        "entity_type": "inventory_item", "entity_id": str(e["item_id"]),
        "action": e["event_type"], "changes": changes,
        "actor_user_id": e.get("actor_user_id"), "actor_space": "platform",
        "group_id": None,
        "context": ({"driver_user_id": e["driver_user_id"]}
                    if e.get("driver_user_id") else {}),
        "note": e.get("note") or "", "created_at": e["created_at"],
    }


def merge_arms(
    *arms: list[dict], limit: int = 100,
) -> list[dict]:
    """Newest-first merge of already-normalized arm results."""
    merged = [ev for arm in arms for ev in arm]
    merged.sort(key=lambda e: e["created_at"] or "", reverse=True)
    return merged[:limit]


def collapse_groups(events: list[dict]) -> list[dict]:
    """One entry per bulk action: events sharing a ``group_id`` fold
    into a group row carrying the count and a sample; singles pass
    through.  Order (newest first by each entry's newest member) is
    preserved."""
    out: list[dict] = []
    seen_groups: dict[str, dict] = {}
    for ev in events:
        gid = ev.get("group_id")
        if not gid:
            out.append(ev)
            continue
        g = seen_groups.get(gid)
        if g is None:
            g = {
                "source": ev["source"], "id": f"group:{gid}",
                "group_id": gid, "is_group": True,
                "entity_type": ev["entity_type"],
                "action": ev["action"], "count": 0,
                "actor_user_id": ev["actor_user_id"],
                "actor_space": ev["actor_space"],
                "sample_entity_ids": [],
                "created_at": ev["created_at"],
                "changes": {}, "context": {}, "note": "",
            }
            seen_groups[gid] = g
            out.append(g)
        g["count"] += 1
        if len(g["sample_entity_ids"]) < 5:
            g["sample_entity_ids"].append(ev["entity_id"])
    return out


async def account_activity(
    db: Any,
    account_id: int,
    *,
    limit: int = 100,
    before_ts: Optional[str] = None,
    entity_type: Optional[str] = None,
    viewer_can_see: Optional[dict[str, bool]] = None,
    collapse: bool = True,
) -> list[dict]:
    """The account-wide lens: all four arms, merged newest-first.

    Each arm is fetched up to ``limit`` so the merged page is always
    complete down to its cut-off timestamp; keyset pagination continues
    with ``before_ts`` = the last row's ``created_at``.
    """
    fetch = limit
    trail = [normalize_trail(e) for e in await db.list_activity_events(
        account_id, before_ts=before_ts, limit=fetch,
        entity_type=entity_type,
    )]
    if entity_type is None or entity_type == "load":
        loads = [normalize_load(e) for e in await db.list_trail_legacy_loads(
            account_id, before_ts=before_ts, limit=fetch)]
    else:
        loads = []
    if entity_type is None or entity_type == "inventory_item":
        inv = [normalize_inventory(e) for e in
               await db.list_trail_legacy_inventory(
                   account_id, before_ts=before_ts, limit=fetch)]
    else:
        inv = []
    merged = merge_arms(trail, loads, inv, limit=limit)
    flags = viewer_can_see or {}
    for ev in merged:
        ev["changes"] = mask_changes(
            ev["entity_type"], ev["changes"],
            viewer_can_see=flags.get(ev["entity_type"], False),
        )
    return collapse_groups(merged) if collapse else merged
