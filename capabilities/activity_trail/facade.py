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


# The delete-shaped actions whose event body carries the whole row —
# the same list the per-record wall uses (activity_trail/router.py).
_BODY_BEARING_ACTIONS = ("delete", "merge_away", "deactivate")


async def filter_company_scoped(
    db: Any, account_id: int, events: list[dict], allowed: list[str],
) -> list[dict]:
    """Team Management's data scope, applied to an aggregate page.

    A company-scoped event carries no company of its own: create and
    update diffs record only what CHANGED, and the company almost never
    does.  The owning ROW is the source of truth — which the per-record
    wall reads one row at a time.  Here that would be N+1, so this
    resolves per TABLE instead: there are only ever two owning tables,
    so the whole wall costs at most two queries for a page, and none at
    all for an unrestricted viewer (the common case, and always the
    owner).

    Deleted records are absent from their table, so they fall back to
    their own delete body, which does carry ``company_code`` — exactly
    the fallback ``history_in_company_scope`` uses.

    Unresolvable means WITHHELD: blank/unknown fails closed for a
    restricted caller, matching ``_company_allows``.  One asymmetry is
    deliberate: a create/update event of a DELETED own-company record
    stays withheld here, because only its delete event self-resolves.
    The per-record endpoint still shows it (it scans all of an entity's
    events and resolves once); closing that in the feed is the N+1 this
    design exists to avoid.
    """
    if not allowed:
        return events                      # unrestricted: nothing to do
    from interfaces.api.deps import filter_by_allowed_companies
    from .registry import ensure_declarations_loaded, entity_descriptor
    from .restore import row_from_event
    ensure_declarations_loaded()

    def _scoped(ev):
        d = entity_descriptor(ev["entity_type"])
        return d if (d and d.company_scoped) else None

    # One id-set per owning table.
    need: dict[str, set[int]] = {}
    for ev in events:
        d = _scoped(ev)
        if not (d and d.restore_table):
            continue
        try:
            need.setdefault(d.restore_table, set()).add(int(ev["entity_id"]))
        except (TypeError, ValueError):
            continue                       # non-numeric id: unresolvable
    resolved: dict[str, dict[int, str]] = {}
    for table, ids in need.items():
        resolved[table] = await db.get_rows_company_codes(
            account_id, table, sorted(ids),
        )

    out: list[dict] = []
    for ev in events:
        d = _scoped(ev)
        if d is None:
            out.append(ev)                 # not company-scoped
            continue
        company = None
        if d.restore_table:
            try:
                company = resolved.get(d.restore_table, {}).get(int(ev["entity_id"]))
            except (TypeError, ValueError):
                company = None
        if company is None and ev.get("action") in _BODY_BEARING_ACTIONS:
            company = row_from_event(ev).get("company_code")
        # ONE matching rule, shared with the per-record wall.
        if filter_by_allowed_companies(
            [{"company_code": company or ""}], allowed, key="company_code",
        ):
            out.append(ev)
    return out


async def account_activity(
    db: Any,
    account_id: int,
    *,
    limit: int = 100,
    before_ts: Optional[str] = None,
    entity_type: Optional[str] = None,
    viewer_can_see: dict[str, bool],
    allowed_companies: list[str],
    collapse: bool = True,
) -> list[dict]:
    """The account-wide lens: all four arms, merged newest-first.

    Each arm is fetched up to ``limit`` so the merged page is always
    complete down to its cut-off timestamp; keyset pagination continues
    with ``before_ts`` = the last row's ``created_at``.

    ``viewer_can_see`` is the GATE, not a hint, and it is REQUIRED —
    build it with ``registry.viewer_entity_flags(user)``.  An entity the
    viewer may not open on its own page is DROPPED here, whole: this
    lens once passed the same map to field-masking instead, which meant
    an entity that declared no ``sensitive_fields`` (23 of 26 of them)
    printed its values in full to anyone holding can_manage_users.
    Dropping the row is what the registry always promised — "the view
    gate is the OWNING feature's permission, so the trail can never
    become a side door around a feature gate" — and unlike masking it
    also covers ``context``, ``note``, ``entity_id`` and ``action``,
    which no field allowlist can reach.

    Unregistered entity types are absent from the map and therefore
    dropped (``.get(et, False)``) — deliberately, so a writer that
    emits an unknown or empty entity_type fails closed.

    The keyword is positional-order-free and has NO default on purpose:
    a caller that forgets it gets a TypeError, never a silent leak.
    """
    fetch = limit
    flags = viewer_can_see
    # Filter each arm BEFORE the merge so ``limit`` counts rows the
    # viewer will actually receive, and so arms they cannot see are
    # never fetched at all.
    trail = [
        ev for ev in (
            normalize_trail(e) for e in await db.list_activity_events(
                account_id, before_ts=before_ts, limit=fetch,
                entity_type=entity_type,
            )
        )
        if flags.get(ev["entity_type"], False)
    ]
    # …then the company wall, on the arm that carries scoped entities.
    trail = await filter_company_scoped(db, account_id, trail, allowed_companies)
    if (entity_type is None or entity_type == "load") and flags.get("load", False):
        loads = [normalize_load(e) for e in await db.list_trail_legacy_loads(
            account_id, before_ts=before_ts, limit=fetch)]
    else:
        loads = []
    if ((entity_type is None or entity_type == "inventory_item")
            and flags.get("inventory_item", False)):
        inv = [normalize_inventory(e) for e in
               await db.list_trail_legacy_inventory(
                   account_id, before_ts=before_ts, limit=fetch)]
    else:
        inv = []
    merged = merge_arms(trail, loads, inv, limit=limit)
    # Field masking stays as defense in depth behind the filter: every
    # row here already passed the gate, so this is a no-op unless a
    # future bug lets one through.
    for ev in merged:
        ev["changes"] = mask_changes(
            ev["entity_type"], ev["changes"],
            viewer_can_see=flags.get(ev["entity_type"], False),
        )
    return collapse_groups(merged) if collapse else merged
