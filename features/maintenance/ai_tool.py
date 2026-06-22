"""Maintenance AI tools — per-vehicle tasks and account-wide summary.

``get_maintenance_summary`` is account-wide, so it filters its tasks to the
caller's Vehicle-Access scope (via the shared helper) before aggregating —
a company-restricted manager gets a summary of *their own* vehicles.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope


@register_tool({
    "name": "get_vehicle_maintenance",
    "description": (
        "Get pending and overdue maintenance tasks for a specific vehicle. "
        "Includes task type (oil change, tires, brakes, DOT inspection, "
        "DPF regen, DEF refill, etc.), due date, due mileage, and status."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {
                "type": "string",
                "description": "The vehicle name or number",
            },
        },
        "required": ["vehicle_name"],
    },
})
async def get_vehicle_maintenance(tool_args: dict, samsara_client,
                                  account_id: int | None = None, db=None) -> dict:
    vehicle = tool_args.get("vehicle_name", "")
    if not db or account_id is None:
        return {"error": "Maintenance data not available in this context"}
    tasks = await db.get_maintenance_tasks(account_id, vehicle_name=vehicle)
    active = [t for t in tasks if t.get("status") in ("pending", "overdue")]
    return {
        "vehicle": vehicle,
        "total_tasks": len(active),
        "tasks": [
            {
                "type": t.get("task_type", "custom"),
                "description": t.get("description", ""),
                "status": t.get("status", ""),
                "due_date": t.get("due_date"),
                "due_miles": t.get("due_miles"),
                "created_at": t.get("created_at", ""),
            }
            for t in active[:15]
        ],
    }


@register_tool({
    "name": "get_maintenance_summary",
    "description": (
        "Get account-wide maintenance summary: total pending tasks, "
        "overdue tasks, breakdown by task type, and the most urgent "
        "overdue items."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
async def get_maintenance_summary(tool_args: dict, samsara_client,
                                  account_id: int | None = None, db=None) -> dict:
    if not db or account_id is None:
        return {"error": "Maintenance data not available in this context"}
    tasks = await db.get_maintenance_tasks(account_id)
    # Scope to the caller's vehicles before aggregating.
    tasks = filter_to_scope(tasks, tool_args)
    pending = [t for t in tasks if t.get("status") == "pending"]
    overdue = [t for t in tasks if t.get("status") == "overdue"]
    # By type
    maint_by_type: dict[str, int] = {}
    for t in pending + overdue:
        tt = t.get("task_type", "custom")
        maint_by_type[tt] = maint_by_type.get(tt, 0) + 1
    return {
        "total_pending": len(pending),
        "total_overdue": len(overdue),
        "tasks_by_type": maint_by_type,
        "overdue_tasks": [
            {
                "vehicle": t.get("vehicle_name", "?"),
                "type": t.get("task_type", "custom"),
                "description": t.get("description", ""),
                "due_date": t.get("due_date"),
                "due_miles": t.get("due_miles"),
            }
            for t in overdue[:10]
        ],
        # Per-vehicle pending rows too (not just overdue) so the assistant can
        # name specific trucks and the dashboard can render clickable chips for
        # "show me pending tasks" — the common case when nothing is overdue yet.
        "pending_tasks": [
            {
                "vehicle": t.get("vehicle_name", "?"),
                "type": t.get("task_type", "custom"),
                "description": t.get("description", ""),
                "due_date": t.get("due_date"),
                "due_miles": t.get("due_miles"),
            }
            for t in pending[:10]
        ],
    }
