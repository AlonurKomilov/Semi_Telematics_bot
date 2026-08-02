"""The generic per-record History endpoint.

``GET /activity/{entity_type}/{entity_id}`` serves any registered
entity's trail — the same HistoryList card everywhere, driven entirely
by the feature declarations in the registry: unknown types 404, and
the gate is the OWNING feature's permission (any-of), so history can
never leak past a feature gate.  Because passing that gate means the
viewer may see the feature's data, values are returned unmasked here;
the account-wide /admin/activity feed (a different, broader audience)
stays the masking surface.

Features with scoped visibility subtleties (maintenance's per-company
task access) keep their own richer endpoints; this one is the shared
manager-grade lens.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from interfaces.api.deps import get_current_user, get_tenant_db, resolve_user_id
from capabilities.activity_trail.registry import (
    ensure_declarations_loaded, entity_descriptor,
)
from capabilities.activity_trail.restore import (
    RestoreConflict, restorable, restore_from_event,
)
from capabilities.permissions.roles import Role, get_user_permissions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/activity", tags=["activity"])


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
