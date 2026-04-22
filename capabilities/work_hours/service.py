"""Schedules capability — business logic for work schedule management (SSOT).

Thin pass-through layer that lets API routes call schedule operations
without importing adapter storage classes directly.
"""

from __future__ import annotations


async def list_schedules(tenant_db, account_id: int) -> list[dict]:
    """Return all work schedules for an account."""
    return await tenant_db.get_work_hours(account_id)


async def get_schedule(tenant_db, schedule_id: int, account_id: int) -> dict | None:
    """Return a single schedule, or None if not found / wrong account."""
    return await tenant_db.get_work_hour(schedule_id, account_id=account_id)


async def create_schedule(
    tenant_db,
    account_id: int,
    label: str,
    start_hour: int,
    end_hour: int,
    target_role: str,
    created_by: int,
) -> dict:
    """Create a new work schedule and return the created row."""
    return await tenant_db.create_work_hour(
        account_id=account_id,
        label=label,
        start_hour=start_hour,
        end_hour=end_hour,
        target_role=target_role,
        created_by=created_by,
    )


async def update_schedule(
    tenant_db,
    schedule_id: int,
    account_id: int,
    **fields,
) -> bool:
    """Update allowed schedule fields. Returns True when a row was changed."""
    return await tenant_db.update_work_hour(
        schedule_id, account_id=account_id, **fields
    )


async def delete_schedule(tenant_db, schedule_id: int, account_id: int) -> None:
    """Delete a work schedule (scoped to account)."""
    await tenant_db.delete_work_hour(schedule_id, account_id=account_id)
