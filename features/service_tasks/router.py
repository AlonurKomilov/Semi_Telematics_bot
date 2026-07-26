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


class ServiceTaskUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    expected_labor_hours: float | None = Field(None, ge=0, le=1000)
    parent_id: int | None = None
    status: str | None = Field(None, pattern="^(active|archived)$")


@router.get("")
async def list_service_tasks(
    include_archived: bool = False,
    user: dict = Depends(require_permission_any(
        "can_service_tasks", "can_maintenance_all", "can_work_orders_all",
    )),
    tenant_db=Depends(get_tenant_db),
):
    """The account's task list — the Service Tasks page grid AND the
    task pickers on the maintenance / work-order forms."""
    rows = await tenant_db.list_service_tasks(
        user["account_id"], include_archived=include_archived,
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
    ok = await tenant_db.update_service_task(
        task_id, user["account_id"], **body.model_dump(exclude_none=True),
    )
    if not ok:
        raise HTTPException(
            status_code=422,
            detail="Nothing to update, the name is taken, or the parent "
                   "task is invalid (only one level of nesting).",
        )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]), "service_task_update",
        target_type="service_task", target_id=str(task_id),
    )
    return await tenant_db.get_service_task(task_id, user["account_id"])


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
