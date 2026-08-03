"""The generic per-record History endpoint.

``GET /activity/{entity_type}/{entity_id}`` serves any registered
entity's trail — the same HistoryList card everywhere, driven entirely
by the feature declarations in the registry: unknown types 404, and
the gate is the OWNING feature's permission (any-of), so history can
never leak past a feature gate.  Because passing that gate means the
viewer may see the feature's data, values are returned unmasked here;
the account-wide /admin/activity feed (a different, broader audience)
stays the masking surface.

Company-scoped features (maintenance, work orders) are served here
too, and their entities declare ``company_scoped`` so this endpoint
applies the SAME per-user company wall their own by-id routes apply —
an earlier version of this docstring claimed they kept to their own
endpoints while the registry served them here anyway, which left the
wall off the history lens.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from interfaces.api.deps import (
    filter_by_allowed_companies, get_current_user, get_tenant_db,
    get_user_company_codes, resolve_user_id,
)
from capabilities.activity_trail.registry import (
    EntityDescriptor, ensure_declarations_loaded, entity_descriptor,
)
from capabilities.activity_trail.restore import (
    RestoreConflict, restorable, restore_from_event, row_from_event,
)
from capabilities.permissions.roles import Role, get_user_permissions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/activity", tags=["activity"])


async def _company_allows(user: dict, company: str) -> bool:
    """One company-matching rule, shared by the read and write paths.

    Semantics match ``_require_company_visible_task`` exactly, blank
    company included: an unrestricted caller (empty assignment, and
    always the owner) passes everything; a restricted one passes only
    its own codes, and a blank/unknown company fails closed.
    """
    allowed = await get_user_company_codes(user)
    if not allowed:
        return True
    return bool(filter_by_allowed_companies(
        [{"company_code": company or ""}], allowed, key="company_code",
    ))


async def _in_company_scope(
    d: EntityDescriptor, event: dict, user: dict,
) -> bool:
    """WRITE path (restore): the deleted row's ``company_code`` rides in
    the event body — the recovery record carries it — so no table read
    is needed to decide whether the caller may resurrect this record."""
    if not d.company_scoped:
        return True
    return await _company_allows(user, row_from_event(event).get("company_code"))


async def _history_in_company_scope(
    d: EntityDescriptor, entity_id: str, events: list[dict],
    user: dict, tenant_db, account_id: int,
) -> bool:
    """READ path (history): resolve the record's company from the OWNING
    ROW, because create/update events record only what changed and the
    company almost never does — the body alone would wave everything
    through.  A record that no longer exists falls back to its delete
    event, which does carry the body; if neither answers, a restricted
    caller is refused (fail closed).
    """
    if not d.company_scoped:
        return True
    allowed = await get_user_company_codes(user)
    if not allowed:
        return True
    try:
        row_id = int(entity_id)
    except (TypeError, ValueError):
        return False
    company = await tenant_db.get_row_company_code(
        account_id, d.restore_table, row_id,
    )
    if company is None:
        # Deleted record — its delete event holds the row body.
        for e in events:
            if e.get("action") in ("delete", "merge_away", "deactivate"):
                body_company = row_from_event(e).get("company_code")
                if body_company is not None:
                    company = body_company
                    break
    return await _company_allows(user, company or "")


@router.get("/{entity_type}/{entity_id}")
async def entity_history(
    entity_type: str,
    entity_id: str,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    ensure_declarations_loaded()
    d = entity_descriptor(entity_type)
    if d is None:
        raise HTTPException(status_code=404, detail="Unknown entity type")
    perms = await get_user_permissions(
        Role(user["role"]), user["account_id"],
        is_manager=bool(user.get("is_manager")),
        is_primary_owner=bool(user.get("is_primary_owner")),
    )
    if not any(getattr(perms, p, False) for p in d.view_permissions):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    events = await tenant_db.list_activity_events(
        user["account_id"],
        entity_type=entity_type, entity_id=str(entity_id),
    )
    # The company wall.  The feature permission is not the whole gate
    # for company-scoped entities: their own by-id routes 404 a
    # restricted caller, and a history lens that didn't would re-open
    # the id-guessing path those guards exist to close.
    if not await _history_in_company_scope(
        d, str(entity_id), events, user, tenant_db, user["account_id"],
    ):
        raise HTTPException(status_code=404, detail="Not found")
    # Restore affordance: server decides, UI just renders the flag.
    can_restore = any(getattr(perms, p, False) for p in d.restore_permissions)
    for e in events:
        e["restorable"] = bool(can_restore and restorable(d, e))
    # Names resolve server-side, same as every other trail reader.
    names: dict[int, str] = {}
    for e in events:
        uid = e["actor_user_id"]
        if uid is None or uid in names:
            continue
        u = await tenant_db.get_user(int(uid))
        names[int(uid)] = (
            (u.display_name if u else "") or ""
        ).strip() or f"#{uid}"
    for e in events:
        e["actor_name"] = (
            names.get(e["actor_user_id"], "") if e["actor_user_id"] else ""
        )
    return {"events": events, "entity_label": d.label}


@router.post("/restore/{event_id}")
async def restore_entity(
    event_id: int,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Bring a deleted record back from its own delete event.

    APPEND-ONLY: the delete event is never touched — the restore lands
    as a new ``restore`` event in the same transaction as the
    re-insert, so the record's history reads created → deleted →
    restored on one id.  Gated by the OWNING feature's restore
    (manage-grade) permission; entities without a declared restorer
    404 — fail-closed.
    """
    ensure_declarations_loaded()
    event = await tenant_db.get_activity_event(user["account_id"], event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    d = entity_descriptor(event["entity_type"])
    if not restorable(d, event):
        raise HTTPException(status_code=404, detail="This event is not restorable")
    perms = await get_user_permissions(
        Role(user["role"]), user["account_id"],
        is_manager=bool(user.get("is_manager")),
        is_primary_owner=bool(user.get("is_primary_owner")),
    )
    if not any(getattr(perms, p, False) for p in d.restore_permissions):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if not await _in_company_scope(d, event, user):
        # 404, not 403 — the sibling routes hide existence from callers
        # outside the company, and so must this one.
        raise HTTPException(status_code=404, detail="Event not found")
    try:
        restored_id = await restore_from_event(
            tenant_db, user["account_id"], event,
            actor_user_id=await resolve_user_id(user),
        )
    except RestoreConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"restored_id": restored_id, "entity_type": event["entity_type"]}


@router.post("/restore-group/{group_id}")
async def restore_group(
    group_id: str,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Undo a whole bulk action — every record it deleted, back.

    Per-record transactions, deliberately: if two of 33 tasks were
    recreated by hand since, the other 31 still come back and the
    response says so.  Silence about the two would be the truncation
    sin in a new costume.

    Single deletes ride this same path as a group of one, so the UI has
    exactly one undo call.
    """
    ensure_declarations_loaded()
    events = await tenant_db.list_activity_events(
        user["account_id"], group_id=group_id, limit=500,
    )
    if not events:
        raise HTTPException(status_code=404, detail="Nothing to restore")
    actor = await resolve_user_id(user)
    perms = await get_user_permissions(
        Role(user["role"]), user["account_id"],
        is_manager=bool(user.get("is_manager")),
        is_primary_owner=bool(user.get("is_primary_owner")),
    )
    restored: list[int] = []
    conflicts: list[dict] = []
    skipped = 0
    for ev in events:
        d = entity_descriptor(ev["entity_type"])
        if not restorable(d, ev):
            skipped += 1
            continue
        if not any(getattr(perms, p, False) for p in d.restore_permissions):
            raise HTTPException(
                status_code=403, detail="Insufficient permissions",
            )
        if not await _in_company_scope(d, ev, user):
            # Counted as skipped, not denied: the count stays honest
            # without telling the caller what sits outside its scope.
            skipped += 1
            continue
        try:
            restored.append(
                await restore_from_event(
                    tenant_db, user["account_id"], ev, actor_user_id=actor,
                ),
            )
        except (RestoreConflict, ValueError) as e:
            conflicts.append({"entity_id": ev["entity_id"], "reason": str(e)})
    if not restored and not conflicts:
        raise HTTPException(
            status_code=422, detail="This action can't be undone",
        )
    return {
        "restored": len(restored), "restored_ids": restored,
        "conflicts": conflicts, "skipped": skipped,
    }
