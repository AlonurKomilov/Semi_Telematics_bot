"""Operator-curated standard service tasks (system.4truck.us).

The standard task list used to be a Python tuple — adding one
fleet-wide meant a code change, a migration and a deploy.  This is the
same control the operator already has over the parts and vendor
directories, applied to the vocabulary Maintenance and Work Orders
share.

System-owner only (``require_system_owner``); customers never reach
this.  Their own list lives at ``/service-tasks`` and is where they add
tasks specific to their fleet.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from interfaces.api.deps import get_tenant_db, require_system_owner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system/service-task-library", tags=["system"])


class EntryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    expected_labor_hours: float = Field(0, ge=0, le=1000)
    vehicle_type: str = Field("", pattern="^(truck|trailer|)$")
    system_key: str = Field("", max_length=40)


class EntryUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    expected_labor_hours: float | None = Field(None, ge=0, le=1000)
    vehicle_type: str | None = Field(None, pattern="^(truck|trailer|)$")
    system_key: str | None = Field(None, max_length=40)
    status: str | None = Field(None, pattern="^(active|archived)$")


@router.get("")
async def list_entries(
    _user: dict = Depends(require_system_owner),
    db=Depends(get_tenant_db),
):
    """The library, with how many accounts hold each entry — the sanity
    check before renaming or archiving one."""
    return {"entries": await db.list_service_task_library()}


@router.post("")
async def create_entry(
    body: EntryCreate,
    _user: dict = Depends(require_system_owner),
    db=Depends(get_tenant_db),
):
    """Add a standard task and hand it to EVERY existing account.

    Without the fan-out a new standard task would only ever reach
    accounts created afterwards, which is the bug this endpoint exists
    to avoid.
    """
    entry = await db.create_service_task_library_entry(
        body.name,
        description=body.description,
        expected_labor_hours=body.expected_labor_hours,
        vehicle_type=body.vehicle_type,
        system_key=body.system_key,
    )
    if not entry:
        raise HTTPException(
            status_code=409,
            detail="A standard task with that name (or its key) already exists.",
        )
    return entry


@router.get("/candidates")
async def candidates(
    min_accounts: int = 2,
    _user: dict = Depends(require_system_owner),
    db=Depends(get_tenant_db),
):
    """Custom task names used by 2+ accounts — the promote signal.

    Promoting is just ``POST /system/service-task-library`` with the
    (cleaned-up) name: the fan-out then ADOPTS every account's matching
    custom in place — same row, history intact, identity upgraded —
    and seeds the task into accounts that never had it.
    """
    return {"candidates": await db.service_task_candidates(
        min_accounts=max(1, min(min_accounts, 50)),
    )}


@router.put("/{entry_id}")
async def update_entry(
    entry_id: int,
    body: EntryUpdate,
    _user: dict = Depends(require_system_owner),
    db=Depends(get_tenant_db),
):
    """Edit an entry.

    A label / estimate change PUSHES DOWN to every account — the point
    of a shared key is that the task means the same thing everywhere.
    Archiving does not: it only stops the task being seeded into NEW
    accounts, because existing ones may have years of history under it.
    The ``canonical_key`` itself is immutable; changing it would orphan
    every account's copy.
    """
    ok = await db.update_service_task_library_entry(
        entry_id, **body.model_dump(exclude_none=True),
    )
    if not ok:
        raise HTTPException(
            status_code=404, detail="Entry not found, or nothing to update.",
        )
    return await db.get_service_task_library_entry(entry_id)


@router.post("/{entry_id}/resync")
async def resync_entry(
    entry_id: int,
    _user: dict = Depends(require_system_owner),
    db=Depends(get_tenant_db),
):
    """Re-run the fan-out for one entry.

    Repair hatch: an account created while an earlier fan-out failed
    (or restored from a backup) can be missing a standard task.  This
    is idempotent — accounts that already hold it just get their label
    refreshed, and an account that archived its copy stays archived.
    """
    entry = await db.get_service_task_library_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    created = await db.fan_out_service_task_library_entry(entry)
    return {"resynced": True, "accounts_added": created}
