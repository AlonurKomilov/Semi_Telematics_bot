"""Maintenance AI tools — per-vehicle tasks and account-wide summary.

``get_maintenance_summary`` is account-wide, so it filters its tasks to the
caller's Vehicle-Access scope (via the shared helper) before aggregating —
a company-restricted manager gets a summary of *their own* vehicles.

Urgency is DERIVED, not read from the stored ``status`` column: a task whose
truck rolled past its due mileage is overdue even while the DB row still says
``pending``.  We classify with :func:`classify_task_urgency` — the same
three-axis (date / mileage / engine-hours) rule the dashboard's Tasks page
uses — so the AI's counts always match the Overdue / Due Soon chips the user
is looking at.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope

# Stored statuses that mean "closed" — excluded from every open-task bucket.
_CLOSED = ("completed", "cancelled")


def _mileage_fields(t: dict) -> dict:
    """Mileage context for a maintenance task.

    ``due_miles`` is the ABSOLUTE odometer reading at which the task is due
    (e.g. 326,620), not a distance remaining — returning it bare made the AI
    say "oil change due in 326,620 miles".  Expose the current odometer and the
    remaining distance so the answer reads "due at 326,620 mi (~X to go)".
    """
    due = t.get("due_miles")
    odo = t.get("last_odometer")
    remaining = None
    try:
        if due is not None and odo is not None:
            remaining = round(float(due) - float(odo))
    except (TypeError, ValueError):
        remaining = None
    return {
        "due_at_miles": due,             # absolute odometer threshold
        "current_odometer": odo,
        "miles_remaining": remaining,    # None = unknown; negative = overdue by N
    }


def _bucket_open_tasks(tasks: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Split open tasks into (overdue, due_soon, pending) by DERIVED urgency.

    Uses the dashboard's own classifier so the AI and the Maintenance page
    always agree — e.g. a stored-``pending`` task 25 mi past its due odometer
    lands in ``overdue`` here exactly as it does in the page's chips.
    """
    from features.maintenance.service import classify_task_urgency

    overdue: list[dict] = []
    due_soon: list[dict] = []
    pending: list[dict] = []
    for t in tasks:
        if (t.get("status") or "").lower() in _CLOSED:
            continue
        urgency = classify_task_urgency(t)
        if urgency == "overdue":
            overdue.append(t)
        elif urgency == "due_soon":
            due_soon.append(t)
        else:
            pending.append(t)
    return overdue, due_soon, pending


def _derived_priority(t: dict, urgency: str) -> str:
    """Priority the AI should report — mirrors the DB.

    The scheduled overdue-marker jobs persist ``priority='critical'``
    whenever a task flips to overdue (adapters/storage/maintenance.py
    ``update_maintenance_status_bulk``).  We derive the SAME value here
    so the AI's answer matches both the dashboard chip and the stored
    row once the next 6h tick catches the DB up — an overdue task is
    Critical regardless of the priority it was created with.
    """
    stored = (t.get("priority") or "medium").lower()
    if urgency == "overdue" and stored != "critical":
        return "critical"
    return stored


def _task_row(t: dict, urgency: str, *, include_vehicle: bool = True) -> dict:
    row = {
        "type": t.get("task_type", "custom"),
        "description": t.get("description", ""),
        "status": urgency,   # derived urgency, not the stale stored label
        "priority": _derived_priority(t, urgency),
        "due_date": t.get("due_date"),
        **_mileage_fields(t),
    }
    if include_vehicle:
        row["vehicle"] = t.get("vehicle_name", "?")
    return row


@register_tool({
    "name": "get_vehicle_maintenance",
    "description": (
        "Get open maintenance tasks for a specific vehicle, classified as "
        "overdue / due soon / pending by due date, mileage, and engine "
        "hours. Includes task type (oil change, tires, brakes, DOT "
        "inspection, DPF regen, DEF refill, etc.), due date, and mileage "
        "context."
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
    # Merge LIVE odometer / engine-hours before classifying — the stored
    # readings can be stranded stale (alerted_at filter), and judging
    # against them made the AI answer "0 overdue" while the dashboard
    # (which does the same merge) showed the truck past due.
    from features.maintenance.service import apply_live_readings
    await apply_live_readings(db, account_id, tasks)
    overdue, due_soon, pending = _bucket_open_tasks(tasks)
    ordered = (
        [(t, "overdue") for t in overdue]
        + [(t, "due_soon") for t in due_soon]
        + [(t, "pending") for t in pending]
    )
    return {
        "vehicle": vehicle,
        "total_tasks": len(ordered),
        "total_overdue": len(overdue),
        "total_due_soon": len(due_soon),
        "tasks": [
            _task_row(t, u, include_vehicle=False) for t, u in ordered[:15]
        ],
    }


@register_tool({
    "name": "get_maintenance_summary",
    "description": (
        "Get account-wide maintenance summary: overdue, due-soon, and "
        "pending task counts (classified by due date, mileage, and engine "
        "hours — matching the Maintenance page), breakdown by task type, "
        "and the most urgent items per vehicle."
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
    # Live-readings merge before classifying — same rationale as
    # get_vehicle_maintenance above.
    from features.maintenance.service import apply_live_readings
    await apply_live_readings(db, account_id, tasks)
    overdue, due_soon, pending = _bucket_open_tasks(tasks)
    # By type — over every open task, whatever its urgency.
    maint_by_type: dict[str, int] = {}
    for t in overdue + due_soon + pending:
        tt = t.get("task_type", "custom")
        maint_by_type[tt] = maint_by_type.get(tt, 0) + 1
    return {
        "total_overdue": len(overdue),
        "total_due_soon": len(due_soon),
        "total_pending": len(pending),
        "tasks_by_type": maint_by_type,
        "overdue_tasks": [_task_row(t, "overdue") for t in overdue[:10]],
        "due_soon_tasks": [_task_row(t, "due_soon") for t in due_soon[:10]],
        # Per-vehicle pending rows too so the assistant can name specific
        # trucks (and the dashboard can render clickable chips) for "show me
        # pending tasks" — the common case when nothing is overdue yet.
        "pending_tasks": [_task_row(t, "pending") for t in pending[:10]],
    }
