"""Maintenance API endpoints — CRUD for maintenance tasks."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from interfaces.api.deps import get_current_user, require_permission, get_tenant_db, get_user_vehicle_nums, paginate
from capabilities.iam.permissions import can
from capabilities.maintenance.service import has_maintenance_access
from infra.services import get_client

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


@router.get("/tasks")
async def list_tasks(
    status: Optional[str] = Query(None),
    vehicle: Optional[str] = Query(None),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """List maintenance tasks for the account."""
    if not has_maintenance_access(user["role"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    tasks = await tenant_db.get_maintenance_tasks(
        user["account_id"],
        status=status,
        vehicle_name=vehicle,
    )
    # Filter to assigned trucks for _own permission
    if not can(user["role"], "can_maintenance_all"):
        trucks = await get_user_vehicle_nums(user)
        if trucks:
            needles = {t.lower() for t in trucks}
            tasks = [t for t in tasks if any(n in (t.get("vehicle_name") or "").lower() for n in needles)]

    paged = paginate(tasks, page, page_size)
    return {"tasks": paged["items"], "count": paged["total"],
            "page": paged["page"], "page_size": paged["page_size"],
            "total_pages": paged["total_pages"]}


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: int,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Get a single maintenance task."""
    if not has_maintenance_access(user["role"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    task = await tenant_db.get_maintenance_task(task_id, account_id=user["account_id"])
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    # Check truck ownership for _own permission
    if not can(user["role"], "can_maintenance_all"):
        trucks = await get_user_vehicle_nums(user)
        if trucks:
            needles = {t.lower() for t in trucks}
            if not any(n in (task.get("vehicle_name") or "").lower() for n in needles):
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


@router.get("/odometer/{vehicle_name}")
async def get_vehicle_odometer(
    vehicle_name: str,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Return current odometer reading for a specific vehicle.

    Reads ``vehicle_state.odometer_mi`` directly from the warehouse
    table (single source of truth, refreshed every 60s by
    ``ingest_vehicle_state``).  Bypasses the WAREHOUSE_READS_ENABLED
    cutover flag because odometer is *only* in the warehouse.
    Returns ``odometer_miles=None`` when the vehicle doesn't report
    OBD odometer (no CAN bus gateway, plan limitation, or warehouse
    cold-start before the first ingest).
    """
    if not has_maintenance_access(user["role"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    # DRIVER (can_maintenance_own, no can_maintenance_all): enforce truck ownership
    # so a driver cannot enumerate odometer readings for the entire fleet by
    # guessing truck names.
    if not can(user["role"], "can_maintenance_all"):
        allowed_trucks = await get_user_vehicle_nums(user)
        if allowed_trucks is not None:
            name_lower = vehicle_name.strip().lower()
            if not any(t.strip().lower() == name_lower for t in allowed_trucks):
                return {"vehicle_name": vehicle_name, "odometer_miles": None, "time": None}
    # Direct warehouse-table lookup (case-insensitive match).
    rows = await tenant_db.get_vehicle_state(
        user["account_id"], vehicle_nums=[vehicle_name],
    )
    name_lower = vehicle_name.strip().lower()
    match = next(
        (r for r in rows if (r.get("vehicle_name") or "").lower() == name_lower),
        None,
    )
    if not match:
        return {"vehicle_name": vehicle_name, "odometer_miles": None, "time": None}
    return {
        "vehicle_name": match.get("vehicle_name", vehicle_name),
        "odometer_miles": match.get("odometer_mi"),
        "time": match.get("odometer_time"),
        "company": match.get("company_code", ""),
    }
