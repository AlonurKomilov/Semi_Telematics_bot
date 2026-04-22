"""Work Hours API — CRUD for work hour presets.

Endpoints:
  GET    /work-hours/           — list all work hour presets for the account
  POST   /work-hours/           — create a new preset
  GET    /work-hours/{id}       — fetch a single preset
  PUT    /work-hours/{id}       — update a preset
  DELETE /work-hours/{id}       — delete a preset
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from interfaces.api.deps import require_permission, get_tenant_db
from capabilities.work_hours.service import (
    list_schedules,
    get_schedule,
    create_schedule,
    update_schedule,
    delete_schedule,
)

router = APIRouter(prefix="/work-hours", tags=["work-hours"])

_VALID_ROLES = {"all", "owner", "admin", "fleet", "safety", "dispatcher", "driver"}


class ScheduleCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    start_hour: int = Field(..., ge=0, le=23)
    end_hour: int = Field(..., ge=1, le=24)
    target_role: str = Field("all", pattern=r"^(all|owner|admin|fleet|safety|dispatcher|driver)$")


class ScheduleUpdate(BaseModel):
    label: Optional[str] = Field(None, min_length=1, max_length=100)
    start_hour: Optional[int] = Field(None, ge=0, le=23)
    end_hour: Optional[int] = Field(None, ge=1, le=24)
    target_role: Optional[str] = Field(None, pattern=r"^(all|owner|admin|fleet|safety|dispatcher|driver)$")


@router.get("/")
async def get_schedules(
    user: dict = Depends(require_permission("can_manage_users")),
    tenant_db=Depends(get_tenant_db),
):
    """List all work schedules for the account."""
    schedules = await list_schedules(tenant_db, user["account_id"])
    return {"schedules": schedules, "count": len(schedules)}


@router.post("/", status_code=201)
async def add_schedule(
    body: ScheduleCreate,
    user: dict = Depends(require_permission("can_manage_users")),
    tenant_db=Depends(get_tenant_db),
):
    """Create a new work schedule."""
    if body.end_hour <= body.start_hour:
        raise HTTPException(status_code=422, detail="end_hour must be greater than start_hour")
    schedule = await create_schedule(
        tenant_db,
        account_id=user["account_id"],
        label=body.label,
        start_hour=body.start_hour,
        end_hour=body.end_hour,
        target_role=body.target_role,
        created_by=int(user["sub"]),
    )
    return schedule


@router.get("/{schedule_id}")
async def get_schedule_detail(
    schedule_id: int,
    user: dict = Depends(require_permission("can_manage_users")),
    tenant_db=Depends(get_tenant_db),
):
    """Get a single work schedule."""
    schedule = await get_schedule(tenant_db, schedule_id, user["account_id"])
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


@router.put("/{schedule_id}")
async def edit_schedule(
    schedule_id: int,
    body: ScheduleUpdate,
    user: dict = Depends(require_permission("can_manage_users")),
    tenant_db=Depends(get_tenant_db),
):
    """Update a work schedule."""
    # Verify it exists in this account first
    existing = await get_schedule(tenant_db, schedule_id, user["account_id"])
    if not existing:
        raise HTTPException(status_code=404, detail="Schedule not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    start = updates.get("start_hour", existing["start_hour"])
    end = updates.get("end_hour", existing["end_hour"])
    if end <= start:
        raise HTTPException(status_code=422, detail="end_hour must be greater than start_hour")

    changed = await update_schedule(tenant_db, schedule_id, user["account_id"], **updates)
    if not changed:
        raise HTTPException(status_code=409, detail="No changes applied")
    return {"status": "updated", "id": schedule_id}


@router.delete("/{schedule_id}", status_code=204)
async def remove_schedule(
    schedule_id: int,
    user: dict = Depends(require_permission("can_manage_users")),
    tenant_db=Depends(get_tenant_db),
):
    """Delete a work schedule."""
    existing = await get_schedule(tenant_db, schedule_id, user["account_id"])
    if not existing:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await delete_schedule(tenant_db, schedule_id, user["account_id"])
