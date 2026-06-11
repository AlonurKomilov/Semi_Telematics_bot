"""Working-hours schedules API — the work-hours feature's router.

router.py is interface-layer code co-located with its domain
(docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.
Paths keep the historical ``/admin`` prefix so URLs are unchanged.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from interfaces.api.deps import require_permission, get_tenant_db, resolve_user_id

router = APIRouter(prefix="/admin", tags=["admin"])

# ── Work Hours ─────────────────────────────────────────────

class ScheduleCreate(BaseModel):
    label: str = Field(..., min_length=1)
    start_hour: int = Field(..., ge=0, le=23)
    end_hour: int = Field(..., ge=0, le=23)
    target_role: str = "all"

    @model_validator(mode="after")
    def end_after_start(self) -> "ScheduleCreate":
        if self.end_hour <= self.start_hour:
            raise ValueError("end_hour must be greater than start_hour")
        return self


class ScheduleUpdate(BaseModel):
    label: Optional[str] = None
    start_hour: Optional[int] = Field(None, ge=0, le=23)
    end_hour: Optional[int] = Field(None, ge=0, le=23)
    target_role: Optional[str] = None


@router.get("/work-hours")
async def list_schedules(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """List work schedules."""
    schedules = await tenant_db.get_work_hours(user["account_id"])
    return {"schedules": schedules, "count": len(schedules)}


@router.post("/work-hours")
async def create_schedule(
    body: ScheduleCreate,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """Create a work schedule."""
    sched = await tenant_db.create_work_hour(
        account_id=user["account_id"],
        label=body.label,
        start_hour=body.start_hour,
        end_hour=body.end_hour,
        created_by=await resolve_user_id(user),
        target_role=body.target_role,
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "schedule_create",
        target_type="schedule", target_id=str(sched.get("id", "")),
        details=body.label,
    )
    return sched


@router.put("/work-hours/{schedule_id}")
async def update_schedule(
    schedule_id: int,
    body: ScheduleUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """Update a work schedule."""
    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not kwargs:
        raise HTTPException(status_code=422, detail="No fields to update")
    # Validate end_hour > start_hour when both are provided in the update
    if "start_hour" in kwargs and "end_hour" in kwargs:
        if kwargs["end_hour"] <= kwargs["start_hour"]:
            raise HTTPException(status_code=422, detail="end_hour must be greater than start_hour")
    ok = await tenant_db.update_work_hour(schedule_id, account_id=user["account_id"], **kwargs)
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"ok": True}


@router.delete("/work-hours/{schedule_id}")
async def delete_schedule(
    schedule_id: int,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """Delete a work schedule."""
    sched = await tenant_db.get_work_hour(schedule_id, account_id=user["account_id"])
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await tenant_db.delete_work_hour(schedule_id, account_id=user["account_id"])
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "schedule_delete",
        target_type="schedule", target_id=str(schedule_id),
    )
    return {"ok": True}


