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

from capabilities.ai.tools.registry import (
    register_tool, register_action_executor, tool_propose, tool_error,
)
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
    overdue_rows = [_task_row(t, "overdue") for t in overdue[:10]]
    due_soon_rows = [_task_row(t, "due_soon") for t in due_soon[:10]]
    pending_rows = [_task_row(t, "pending") for t in pending[:10]]
    return {
        "total_overdue": len(overdue),
        "total_due_soon": len(due_soon),
        "total_pending": len(pending),
        "tasks_by_type": maint_by_type,
        "overdue_tasks": overdue_rows,
        "due_soon_tasks": due_soon_rows,
        # Per-vehicle pending rows too so the assistant can name specific
        # trucks (and the dashboard can render clickable chips) for "show me
        # pending tasks" — the common case when nothing is overdue yet.
        "pending_tasks": pending_rows,
        # Copilot artifact — a glanceable table of open tasks the chat
        # renders natively (the maintenance feature owns this, in its own
        # ai_tool.py).  Display-only; omitted when nothing is open.
        "artifacts": _maintenance_table_artifact(
            overdue_rows + due_soon_rows + pending_rows,
        ),
    }


def _maintenance_table_artifact(rows: list[dict]) -> list[dict]:
    """A `table` artifact of open maintenance tasks, or [] when none."""
    if not rows:
        return []
    return [{
        "type": "table",
        "title": "Open maintenance",
        "columns": [
            {"key": "vehicle", "label": "Vehicle"},
            {"key": "type", "label": "Type"},
            {"key": "status", "label": "Status"},
            {"key": "miles_remaining", "label": "Miles left"},
        ],
        "rows": [{
            "vehicle": r.get("vehicle", "?"),
            "type": r.get("type", ""),
            "status": r.get("status", ""),
            "miles_remaining": r.get("miles_remaining"),
        } for r in rows[:20]],
    }]


# ── Write action: create a maintenance task (copilot "hands") ──────
#
# PROPOSE (the registered tool, run during a chat turn): validate the
# args, mutate NOTHING, return a proposal the user approves.
# EXECUTE (the registered executor, run only from the approve endpoint
# after re-authorization): actually create the task.

_TASK_TYPES = (
    "oil", "tires", "brakes", "inspection", "dot_inspection", "dpf_regen",
    "def_refill", "transmission", "coolant", "battery", "custom",
)


@register_tool({
    "name": "create_maintenance_task",
    "description": (
        "Propose creating a maintenance task for a vehicle (e.g. schedule "
        "an oil change for Truck 228 due next month). This does NOT create "
        "it directly — it asks the user to approve first. Use when the user "
        "asks to add / schedule / create a maintenance task."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {"type": "string", "description": "Vehicle name/number (e.g. '228')."},
            "task_type": {"type": "string", "description": "One of: oil, tires, brakes, inspection, dot_inspection, dpf_regen, def_refill, transmission, coolant, battery, custom."},
            "description": {"type": "string", "description": "Short description of the work."},
            "due_date": {"type": "string", "description": "Optional due date YYYY-MM-DD."},
            "due_miles": {"type": "number", "description": "Optional absolute odometer (miles) at which it's due."},
        },
        "required": ["vehicle_name", "task_type"],
    },
    # Marks this a write action — proposes, never mutates in the loop.
    "writes": True,
    "risk": "low",
    # Scope shape (enforced by tests/test_ai_write_tool_scope.py): a required
    # vehicle_name, so the gate rejects a scoped caller naming a vehicle
    # outside their access (⇒ must be in VEHICLE_SPECIFIC_TOOLS).
    "scope": "vehicle_param",
})
async def create_maintenance_task(tool_args, samsara_client,
                                  account_id=None, db=None):
    vehicle = str(tool_args.get("vehicle_name", "")).strip()
    task_type = str(tool_args.get("task_type", "")).strip().lower()
    if not vehicle:
        return tool_error("A vehicle is required to create a maintenance task.")
    if task_type not in _TASK_TYPES:
        task_type = "custom"
    desc = str(tool_args.get("description", "")).strip()
    due_date = str(tool_args.get("due_date", "")).strip() or None
    due_miles = tool_args.get("due_miles")
    try:
        due_miles = float(due_miles) if due_miles is not None else None
    except (TypeError, ValueError):
        due_miles = None

    when = []
    if due_date:
        when.append(f"due {due_date}")
    if due_miles is not None:
        when.append(f"due at {int(due_miles):,} mi")
    when_str = (" (" + ", ".join(when) + ")") if when else ""
    summary = (
        f"Create a {task_type} maintenance task for Truck {vehicle}"
        f"{f' — {desc}' if desc else ''}{when_str}."
    )
    return tool_propose(
        "create_maintenance_task", summary,
        {"vehicle_name": vehicle, "task_type": task_type,
         "description": desc, "due_date": due_date, "due_miles": due_miles},
        risk="low",
        consequence="Adds a task to the schedule — you can edit or delete it anytime.",
    )


@register_action_executor("create_maintenance_task")
async def _execute_create_maintenance_task(payload, account_id, user_context, db):
    """Actually create the task — runs only post-approval.  Re-resolves
    everything from ``payload`` inside ``account_id`` (never trusts a
    client body)."""
    vehicle = str(payload.get("vehicle_name", "")).strip()
    task_type = str(payload.get("task_type", "custom")).strip().lower()
    if task_type not in _TASK_TYPES:
        task_type = "custom"
    task_id = await db.add_maintenance_task(
        account_id,
        company_code="",
        vehicle_name=vehicle,
        task_type=task_type,
        description=str(payload.get("description", "")).strip(),
        due_date=payload.get("due_date") or None,
        due_miles=payload.get("due_miles"),
        created_by=int((user_context or {}).get("user_id") or 0),
    )
    return {
        "created": True,
        "target_type": "maintenance_task",
        "target_id": str(task_id),
        "vehicle_name": vehicle,
        "task_type": task_type,
        "message": f"Created a {task_type} task for Truck {vehicle}.",
    }
