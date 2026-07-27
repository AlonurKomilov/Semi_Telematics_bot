"""Service Tasks API — the shared task vocabulary.

Graduated out of Maintenance for the same reason Parts graduated out
of Work Orders: a thing two features both consume cannot live inside
one of them.  It did, and the vocabulary drifted into three
disagreeing copies (dashboard dropdown, maintenance AI tool, work-order
matcher) with no owner.

Access model: ``can_service_tasks`` owns every WRITE (create / edit /
archive / delete).  The LIST read is deliberately wider —
``can_maintenance_all`` / ``can_work_orders_all`` also pass — because
it feeds the task pickers on both forms; gating the read strictly
would break task entry for a dispatcher who can schedule maintenance
but doesn't administer the task list.

Standard tasks (``canonical_key`` set) are archive-only and
name-locked: their key is the cross-account identity that makes
fleet-wide comparison possible, so a rename in one account would
silently fork the vocabulary again.
"""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from interfaces.api.deps import (
    get_tenant_db, require_permission, require_permission_any, resolve_user_id,
)

router = APIRouter(prefix="/service-tasks", tags=["service-tasks"])


class ServiceTaskCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    expected_labor_hours: float = Field(0, ge=0, le=1000)
    parent_id: int | None = None
    vehicle_type: str = Field("", pattern="^(truck|trailer|)$")
    system_key: str = Field("", max_length=40)


class ServiceTaskUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    expected_labor_hours: float | None = Field(None, ge=0, le=1000)
    # 0 means "no parent" (detach a subtask).  A null can't say that:
    # unsent fields are dropped, so null would be indistinguishable
    # from "leave it alone".
    parent_id: int | None = Field(None, ge=0)
    vehicle_type: str | None = Field(None, pattern="^(truck|trailer|)$")
    system_key: str | None = Field(None, max_length=40)
    status: str | None = Field(None, pattern="^(active|archived)$")


@router.get("")
async def list_service_tasks(
    include_archived: bool = False,
    vehicle_type: str = "",
    user: dict = Depends(require_permission_any(
        "can_service_tasks", "can_maintenance_all", "can_work_orders_all",
    )),
    tenant_db=Depends(get_tenant_db),
):
    """The account's task list — the Service Tasks page grid AND the
    task pickers on the maintenance / work-order forms."""
    rows = await tenant_db.list_service_tasks(
        user["account_id"], include_archived=include_archived,
        vehicle_type=vehicle_type,
    )
    return {"service_tasks": rows, "count": len(rows)}


@router.post("")
async def create_service_task(
    body: ServiceTaskCreate,
    user: dict = Depends(require_permission("can_service_tasks")),
    tenant_db=Depends(get_tenant_db),
):
    """Add a task.  Names are unique per account (Fleetio's rule — two
    spellings of one task silently split every report), so a collision
    is a 409 rather than a second row."""
    task = await tenant_db.create_service_task(
        user["account_id"], body.name,
        description=body.description,
        expected_labor_hours=body.expected_labor_hours,
        parent_id=body.parent_id,
        vehicle_type=body.vehicle_type,
        system_key=body.system_key,
        created_by=await resolve_user_id(user),
    )
    if not task:
        raise HTTPException(
            status_code=409,
            detail="A service task with that name already exists "
                   "(or the parent task is invalid).",
        )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]), "service_task_create",
        target_type="service_task", target_id=str(task["id"]),
        details=task["name"],
    )
    return task


@router.put("/{task_id}")
async def update_service_task(
    task_id: int,
    body: ServiceTaskUpdate,
    user: dict = Depends(require_permission("can_service_tasks")),
    tenant_db=Depends(get_tenant_db),
):
    """Edit a task.  A STANDARD task's name is immutable (its
    canonical key is shared across accounts); description, labor
    estimate and archive state stay editable."""
    existing = await tenant_db.get_service_task(task_id, user["account_id"])
    if not existing:
        raise HTTPException(status_code=404, detail="Service task not found")
    if body.name is not None and existing.get("canonical_key"):
        raise HTTPException(
            status_code=422,
            detail="Standard service tasks can't be renamed — archive it "
                   "and add your own if you need a different name.",
        )
    # Pre-validate so each failure gets its OWN message.  The storage
    # layer still refuses these (it's the real guard); this exists so
    # the user is told which thing was wrong instead of a menu of
    # three possibilities.
    if body.name is not None:
        clash = await tenant_db.find_service_task_by_name(
            user["account_id"], body.name,
        )
        if clash and int(clash["id"]) != task_id:
            raise HTTPException(
                status_code=409,
                detail=f"“{clash['name']}” already exists. Merge the two "
                       f"tasks instead of keeping both spellings.",
            )
    if body.parent_id is not None and body.parent_id:
        if int(body.parent_id) == task_id:
            raise HTTPException(
                status_code=422, detail="A task can't be its own parent.",
            )
        parent = await tenant_db.get_service_task(
            int(body.parent_id), user["account_id"],
        )
        if not parent:
            raise HTTPException(status_code=404, detail="Parent task not found")
        if parent.get("parent_id"):
            raise HTTPException(
                status_code=422,
                detail="That task is already a subtask — service tasks nest "
                       "only one level deep.",
            )

    ok = await tenant_db.update_service_task(
        task_id, user["account_id"], **body.model_dump(exclude_none=True),
    )
    if not ok:
        raise HTTPException(
            status_code=422, detail="Nothing to update.",
        )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]), "service_task_update",
        target_type="service_task", target_id=str(task_id),
    )
    return await tenant_db.get_service_task(task_id, user["account_id"])


@router.get("/systems")
async def list_systems(
    user: dict = Depends(require_permission_any(
        "can_service_tasks", "can_maintenance_all", "can_work_orders_all",
    )),
):
    """The system vocabulary — the reporting axis above a task.

    Served rather than hardcoded in the dashboard on purpose: a second
    copy in the frontend is exactly how the old task vocabulary drifted
    into three disagreeing lists.  One source, fetched.

    Declared BEFORE ``/{task_id}`` so the literal path wins.
    """
    from adapters.storage.service_tasks import SERVICE_TASK_SYSTEMS
    return {"systems": list(SERVICE_TASK_SYSTEMS)}


class MergeBody(BaseModel):
    loser_id: int
    winner_id: int


@router.post("/merge")
async def merge_service_tasks(
    body: MergeBody,
    user: dict = Depends(require_permission("can_service_tasks")),
    tenant_db=Depends(get_tenant_db),
):
    """Fold a duplicate task into the canonical one.

    "Brake Job", "brake job" and "Brakes" typed at different times
    split every report three ways; merging repoints all history onto
    one task so the numbers add up again.  Destructive and
    irreversible — the loser row is gone afterwards — so only the
    account's OWN tasks can be merged away (a standard task's key is
    shared vocabulary; archive it instead).
    """
    ok, reason = await tenant_db.merge_service_tasks(
        user["account_id"], body.loser_id, body.winner_id,
    )
    if not ok:
        raise HTTPException(status_code=422, detail=reason)
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]), "service_task_merge",
        target_type="service_task", target_id=str(body.winner_id),
        details=f"merged #{body.loser_id} into #{body.winner_id}",
    )
    return {"merged": True}


@router.get("/defaults")
async def service_task_defaults(
    task: str,
    user: dict = Depends(require_permission_any(
        "can_service_tasks", "can_maintenance_all", "can_work_orders_all",
    )),
    tenant_db=Depends(get_tenant_db),
):
    """What picking ``task`` should pre-fill — its labor estimate and
    usual parts.  Read-only by contract: an unknown task returns empty
    rather than creating anything (the writers' fail-open resolver is
    for WRITES; a lookup must never write).

    Declared BEFORE ``/{task_id}`` so the literal path wins over the
    int converter.
    """
    found = await tenant_db.service_task_defaults(user["account_id"], task)
    if not found:
        return {"found": False, "expected_labor_hours": 0, "parts": []}
    return {"found": True, **found}


class TaskPartLink(BaseModel):
    part_id: int
    quantity: float = Field(1, gt=0, le=10000)


@router.get("/{task_id}/parts")
async def list_task_parts(
    task_id: int,
    user: dict = Depends(require_permission_any(
        "can_service_tasks", "can_maintenance_all", "can_work_orders_all",
    )),
    tenant_db=Depends(get_tenant_db),
):
    """The parts this task normally needs."""
    if not await tenant_db.get_service_task(task_id, user["account_id"]):
        raise HTTPException(status_code=404, detail="Service task not found")
    return {"parts": await tenant_db.list_service_task_parts(
        task_id, user["account_id"],
    )}


@router.post("/{task_id}/parts")
async def link_task_part(
    task_id: int,
    body: TaskPartLink,
    user: dict = Depends(require_permission("can_service_tasks")),
    tenant_db=Depends(get_tenant_db),
):
    """Link a catalog part to this task so work orders pre-fill it."""
    link = await tenant_db.add_service_task_part(
        user["account_id"], task_id, body.part_id, quantity=body.quantity,
    )
    if not link:
        raise HTTPException(
            status_code=409,
            detail="That part is already linked, or the task/part wasn't found.",
        )
    return link


@router.delete("/{task_id}/parts/{link_id}")
async def unlink_task_part(
    task_id: int,
    link_id: int,
    user: dict = Depends(require_permission("can_service_tasks")),
    tenant_db=Depends(get_tenant_db),
):
    """Unlink a part.  Work orders already created keep their lines —
    this only changes what future ones pre-fill."""
    if not await tenant_db.remove_service_task_part(user["account_id"], link_id):
        raise HTTPException(status_code=404, detail="Link not found")
    return {"deleted": True}


@router.delete("/{task_id}")
async def delete_service_task(
    task_id: int,
    user: dict = Depends(require_permission("can_service_tasks")),
    tenant_db=Depends(get_tenant_db),
):
    """Delete a CUSTOM task that nothing references.  Standard tasks
    and anything with history are archived instead, so historical rows
    never lose their label."""
    existing = await tenant_db.get_service_task(task_id, user["account_id"])
    if not existing:
        raise HTTPException(status_code=404, detail="Service task not found")
    if existing.get("canonical_key"):
        raise HTTPException(
            status_code=422,
            detail="Standard service tasks can only be archived.",
        )
    used = await tenant_db.service_task_usage(task_id, user["account_id"])
    if used:
        raise HTTPException(
            status_code=409,
            detail=f"This task is used by {used} record"
                   f"{'s' if used > 1 else ''} — archive it instead so "
                   f"those records keep their label.",
        )
    ok = await tenant_db.delete_service_task(task_id, user["account_id"])
    if not ok:
        raise HTTPException(status_code=422, detail="Could not delete that task")
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]), "service_task_delete",
        target_type="service_task", target_id=str(task_id),
        details=existing.get("name", ""),
    )
    return {"deleted": True}
