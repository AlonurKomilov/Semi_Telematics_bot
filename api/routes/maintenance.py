"""Maintenance API endpoints — CRUD for maintenance tasks."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from api.deps import get_current_user, require_permission, get_tenant_db
from permissions import can

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


class TaskCreate(BaseModel):
    vehicle_name: str = Field(..., min_length=1)
    company_code: str = ""
    task_type: str = Field(..., min_length=1)
    description: str = ""
    due_date: Optional[str] = None
    due_miles: Optional[float] = None
    recur_interval_days: Optional[int] = None
    recur_interval_miles: Optional[float] = None


class TaskUpdate(BaseModel):
    task_type: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[str] = None
    due_miles: Optional[float] = None
    status: Optional[str] = Field(None, pattern=r"^(pending|in_progress|completed|cancelled)$")
    recur_interval_days: Optional[int] = None
    recur_interval_miles: Optional[float] = None


def _check_maint_perm(user: dict) -> bool:
    """Check if user has any maintenance permission."""
    return can(user["role"], "can_maintenance_all") or can(user["role"], "can_maintenance_own")


@router.get("/tasks")
async def list_tasks(
    status: Optional[str] = Query(None),
    vehicle: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """List maintenance tasks for the account."""
    if not _check_maint_perm(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    tasks = await tenant_db.get_maintenance_tasks(
        user["account_id"],
        status=status,
        vehicle_name=vehicle,
    )
    return {"tasks": tasks, "count": len(tasks)}


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: int,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Get a single maintenance task."""
    if not _check_maint_perm(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    task = await tenant_db.get_maintenance_task(task_id, account_id=user["account_id"])
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/tasks")
async def create_task(
    body: TaskCreate,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Create a new maintenance task."""
    task_id = await tenant_db.add_maintenance_task(
        account_id=user["account_id"],
        company_code=body.company_code,
        vehicle_name=body.vehicle_name,
        task_type=body.task_type,
        description=body.description,
        due_date=body.due_date,
        due_miles=body.due_miles,
        recur_interval_days=body.recur_interval_days,
        recur_interval_miles=body.recur_interval_miles,
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "maintenance_create",
        target_type="maintenance", target_id=str(task_id),
        details=f"{body.task_type}: {body.vehicle_name}",
    )
    return {"id": task_id, "status": "created"}


@router.put("/tasks/{task_id}")
async def update_task(
    task_id: int,
    body: TaskUpdate,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Update a maintenance task."""
    task = await tenant_db.get_maintenance_task(task_id, account_id=user["account_id"])
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not kwargs:
        raise HTTPException(status_code=422, detail="No fields to update")

    # If status change, use the dedicated method
    if "status" in kwargs and len(kwargs) == 1:
        ok = await tenant_db.update_maintenance_status(task_id, kwargs["status"], account_id=user["account_id"])
    else:
        ok = await tenant_db.update_maintenance_task(task_id, account_id=user["account_id"], **kwargs)

    if ok:
        await tenant_db.add_audit_log(
            user["account_id"], int(user["sub"]),
            "maintenance_update",
            target_type="maintenance", target_id=str(task_id),
            details=str(list(kwargs.keys())),
        )
    return {"ok": ok}


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: int,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Delete a maintenance task."""
    task = await tenant_db.get_maintenance_task(task_id, account_id=user["account_id"])
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    await tenant_db.delete_maintenance_task(task_id, account_id=user["account_id"])
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "maintenance_delete",
        target_type="maintenance", target_id=str(task_id),
    )
    return {"ok": True}
