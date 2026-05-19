"""Maintenance API endpoints — CRUD for maintenance tasks."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from typing import Optional

from interfaces.api.deps import get_current_user, require_permission, get_tenant_db, get_platform_db, get_user_vehicle_nums, paginate
from capabilities.iam.permissions import can
from capabilities.maintenance.service import has_maintenance_access, spawn_recurring_if_completed


async def _build_user_name_map(account_id: int, platform_db) -> dict[int, str]:
    """telegram_id → display_name for one account.

    Resolved once per request and reused for every row's
    ``attested_by_name`` enrichment.  Returns an empty map if the
    platform DB call fails (best-effort — falls back to raw ids in the
    UI, no hard error).
    """
    try:
        users = await platform_db.list_account_users(account_id)
        return {int(u.telegram_id): (u.display_name or str(u.telegram_id)) for u in users}
    except Exception:
        return {}


def _enrich_task(task: dict, name_map: dict[int, str]) -> dict:
    """Add ``attested_by_name`` to a task row when ``attested_by`` resolves.

    Pure dict mutation + return — works whether the input came from
    ``get_maintenance_tasks`` (list view) or ``get_maintenance_task``
    (single).  Leaves the field absent (not empty) if no attester yet,
    so the UI can distinguish "task is open" from "completed without
    attestation".
    """
    tid = task.get("attested_by")
    if tid:
        name = name_map.get(int(tid))
        if name:
            task["attested_by_name"] = name
    return task

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


class TaskCreate(BaseModel):
    """Payload for creating a maintenance task.

    Validation rules:

    * ``description`` must be at least 3 chars — guards against
      placeholder text like "PM" / "ASAP" / "f" that provides no
      context for whoever services the truck.  Empty descriptions
      still flow through internal auto-creation paths (e.g. SPN fault
      → auto-created task), so the adapter doesn't enforce; only this
      user-facing entry point does.
    * At least one of ``due_date`` / ``due_miles`` / ``due_engine_hours``
      must be set — otherwise the task stays "pending" forever and the
      overdue schedulers have nothing to compare against.  Recurring
      fields don't satisfy this on their own; the *first* instance
      still needs an initial trigger.
    """
    vehicle_name: str = Field(..., min_length=1)
    company_code: str = ""
    task_type: str = Field(..., min_length=1)
    description: str = Field(..., min_length=3, max_length=500)
    due_date: Optional[str] = None
    due_miles: Optional[float] = Field(None, ge=0)
    # Engine-hours threshold parallel to due_miles.  Optional; satisfies
    # the at-least-one-trigger requirement below the same way.
    due_engine_hours: Optional[float] = Field(None, ge=0)
    recur_interval_days: Optional[int] = Field(None, ge=1)
    recur_interval_miles: Optional[float] = Field(None, ge=1)
    recur_interval_engine_hours: Optional[float] = Field(None, ge=1)
    # Small enum.  Default 'medium' so callers that don't care about
    # priority (bot wizard, SPN auto-create) don't need to specify.
    priority: str = Field("medium", pattern=r"^(low|medium|high|critical)$")
    # Link to a Work Order row.  Settable on create for the rare case
    # where a user logs a task that was already closed by an existing
    # shop visit; usually NULL and populated later when the task is
    # marked completed via the Work Orders module.
    work_order_id: Optional[int] = None

    @model_validator(mode="after")
    def _require_trigger(self):
        if (not self.due_date
                and self.due_miles is None
                and self.due_engine_hours is None):
            raise ValueError(
                "Set a due date, due miles, or due engine hours — "
                "otherwise this task will never become overdue and "
                "won't notify anyone."
            )
        return self


class TaskUpdate(BaseModel):
    task_type: Optional[str] = None
    description: Optional[str] = Field(None, min_length=3, max_length=500)
    due_date: Optional[str] = None
    due_miles: Optional[float] = Field(None, ge=0)
    due_engine_hours: Optional[float] = Field(None, ge=0)
    status: Optional[str] = Field(None, pattern=r"^(pending|in_progress|completed|cancelled)$")
    recur_interval_days: Optional[int] = Field(None, ge=1)
    recur_interval_miles: Optional[float] = Field(None, ge=1)
    recur_interval_engine_hours: Optional[float] = Field(None, ge=1)
    priority: Optional[str] = Field(None, pattern=r"^(low|medium|high|critical)$")
    work_order_id: Optional[int] = None


@router.get("/tasks")
async def list_tasks(
    status: Optional[str] = Query(None),
    vehicle: Optional[str] = Query(None),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """List maintenance tasks for the account."""
    if not has_maintenance_access(user["role"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    tasks = await tenant_db.get_maintenance_tasks(
        user["account_id"],
        status=status,
        vehicle_name=vehicle,
    )
    # Filter to assigned trucks for _own permission.  Pre-compile a
    # single alternation regex from the driver's truck-num set so the
    # inner ``in`` loop collapses to one scan per task — was O(tasks ×
    # needles); now O(tasks).  Substring semantics preserved so
    # "Truck-107A" still matches needle "107".
    if not can(user["role"], "can_maintenance_all"):
        trucks = await get_user_vehicle_nums(user)
        if trucks:
            import re
            pattern = re.compile("|".join(re.escape(t.lower()) for t in trucks if t))
            tasks = [
                t for t in tasks
                if pattern.search((t.get("vehicle_name") or "").lower())
            ]

    paged = paginate(tasks, page, page_size)
    # Resolve telegram_id → display_name once and enrich each row so the
    # dashboard can render "Attested by John Doe" without a second round
    # trip per attester.  Lookup is account-scoped so we never leak names
    # across accounts.
    name_map = await _build_user_name_map(user["account_id"], platform_db)
    items = [_enrich_task(t, name_map) for t in paged["items"]]

    # Enrich with LIVE telemetry from vehicle_state so the dashboard's
    # progress bar reflects current odometer / engine-hours, even for
    # tasks the 6-h scheduler hasn't touched yet (newly created tasks,
    # or tasks where the alerted_at filter strands last_odometer at the
    # value it had when the first alert fired — see Bug B notes).
    #
    # One bulk warehouse read per request, keyed by unique vehicle_name.
    # Falls through silently on any warehouse error; the row just keeps
    # whatever last_odometer / last_engine_hours was stored.
    unique_names = {t.get("vehicle_name") for t in items if t.get("vehicle_name")}
    if unique_names:
        try:
            state_rows = await tenant_db.get_vehicle_state(
                user["account_id"], vehicle_nums=list(unique_names),
            )
            live_by_name: dict[str, dict] = {}
            for row in state_rows:
                nm = row.get("vehicle_name") or ""
                if nm:
                    live_by_name[nm] = row
            for t in items:
                live = live_by_name.get(t.get("vehicle_name") or "")
                if not live:
                    continue
                # Use the live value when the stored one is NULL, or
                # when the live reading is newer (higher odometer /
                # higher engine-hours).  Never go backwards — a glitchy
                # warehouse read shouldn't undo a real reading.
                live_odo = live.get("odometer_mi")
                if isinstance(live_odo, (int, float)):
                    stored_odo = t.get("last_odometer")
                    if stored_odo is None or float(live_odo) > float(stored_odo):
                        t["last_odometer"] = float(live_odo)
                live_hrs = live.get("engine_hours")
                if isinstance(live_hrs, (int, float)):
                    stored_hrs = t.get("last_engine_hours")
                    if stored_hrs is None or float(live_hrs) > float(stored_hrs):
                        t["last_engine_hours"] = float(live_hrs)
        except Exception:
            # Warehouse outage shouldn't break the list view.
            pass

    return {"tasks": items, "count": paged["total"],
            "page": paged["page"], "page_size": paged["page_size"],
            "total_pages": paged["total_pages"]}


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: int,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
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
    name_map = await _build_user_name_map(user["account_id"], platform_db)
    enriched = _enrich_task(task, name_map)

    # Mirror the list-view live-telemetry merge so the modal and the
    # list show the same odometer / engine-hours readings.  Live wins
    # only when newer than the stored value.
    vehicle_name = enriched.get("vehicle_name")
    if vehicle_name:
        try:
            state_rows = await tenant_db.get_vehicle_state(
                user["account_id"], vehicle_nums=[vehicle_name],
            )
            if state_rows:
                live = state_rows[0]
                live_odo = live.get("odometer_mi")
                if isinstance(live_odo, (int, float)):
                    stored_odo = enriched.get("last_odometer")
                    if stored_odo is None or float(live_odo) > float(stored_odo):
                        enriched["last_odometer"] = float(live_odo)
                live_hrs = live.get("engine_hours")
                if isinstance(live_hrs, (int, float)):
                    stored_hrs = enriched.get("last_engine_hours")
                    if stored_hrs is None or float(live_hrs) > float(stored_hrs):
                        enriched["last_engine_hours"] = float(live_hrs)
        except Exception:
            pass

    return enriched


@router.post("/tasks")
async def create_task(
    body: TaskCreate,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Create a new maintenance task."""
    # Backfill last_odometer + last_engine_hours from the warehouse so the
    # dashboard's progress bar appears immediately for trucks that report
    # telemetry — previously the bar showed "no telemetry" until the next
    # 6-h scheduler tick.  Best-effort: no telemetry → both None, the
    # scheduler will fill in later.
    from capabilities.maintenance.service import (
        fetch_current_telemetry_for_vehicle,
    )
    last_odo, last_hrs = await fetch_current_telemetry_for_vehicle(
        tenant_db, user["account_id"], body.vehicle_name,
    )

    task_id = await tenant_db.add_maintenance_task(
        account_id=user["account_id"],
        company_code=body.company_code,
        vehicle_name=body.vehicle_name,
        task_type=body.task_type,
        description=body.description,
        due_date=body.due_date,
        due_miles=body.due_miles,
        due_engine_hours=body.due_engine_hours,
        priority=body.priority,
        recur_interval_days=body.recur_interval_days,
        recur_interval_miles=body.recur_interval_miles,
        recur_interval_engine_hours=body.recur_interval_engine_hours,
        work_order_id=body.work_order_id,
        last_odometer=last_odo,
        last_engine_hours=last_hrs,
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

    spawned_id: Optional[int] = None
    if ok:
        await tenant_db.add_audit_log(
            user["account_id"], int(user["sub"]),
            "maintenance_update",
            target_type="maintenance", target_id=str(task_id),
            details=str(list(kwargs.keys())),
        )
        # Recurring-task auto-spawn: if the user just flipped status to
        # 'completed' AND the parent has recur_interval_days / _miles set
        # (or is a compliance task with a default interval like DOT), the
        # service helper creates the next instance.  Returns ``None`` for
        # one-shot tasks so the response shape is identical when no
        # spawn happened.  Also stamp the dashboard user as the
        # attester — same audit trail the bot maintains.
        if kwargs.get("status") == "completed":
            try:
                await tenant_db.record_task_attestation(
                    task_id, account_id=user["account_id"],
                    attested_by=int(user["sub"]),
                )
            except Exception:
                # Don't block the completion on an attestation write —
                # the status flip already succeeded above.
                pass
            spawned_id = await spawn_recurring_if_completed(
                task_id, user["account_id"], "completed", tenant_db,
            )
            if spawned_id:
                await tenant_db.add_audit_log(
                    user["account_id"], int(user["sub"]),
                    "maintenance_recurring_spawn",
                    target_type="maintenance", target_id=str(spawned_id),
                    details=f"from parent {task_id}",
                )

    return {"ok": ok, "spawned_id": spawned_id}


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


class BulkStatusUpdate(BaseModel):
    """Payload for bulk-updating status on N tasks.

    Capped at 200 ids per request — matches the dashboard list page
    size so a "select all visible" never exceeds the limit.  Status
    pattern matches ``TaskUpdate`` so the same set of terminal/non-
    terminal values is accepted.
    """
    task_ids: list[int] = Field(..., min_length=1, max_length=200)
    status: str = Field(..., pattern=r"^(pending|in_progress|completed|cancelled)$")


class BulkDelete(BaseModel):
    task_ids: list[int] = Field(..., min_length=1, max_length=200)


@router.post("/tasks/bulk/status")
async def bulk_update_status(
    body: BulkStatusUpdate,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Bulk-update status on N maintenance tasks.

    Designed for the dashboard's "mark N selected as completed" flow
    after a shop visit closes multiple tasks (oil + tires + filter all
    in one trip → one bulk update).  Attestation + recurring spawn are
    triggered per task when transitioning to 'completed' so the DOT
    audit trail is preserved at the row level — not lost because the
    user used the bulk path.
    """
    touched = await tenant_db.update_maintenance_status_bulk(
        user["account_id"], body.task_ids, body.status,
    )
    spawned: list[int] = []
    if body.status == "completed":
        # Per-task attestation + spawn.  The bulk-status helper handles
        # the UPDATEs in chunks; here we walk the ids in Python so each
        # one gets a proper attestation row + recurring follow-up.
        for tid in body.task_ids:
            try:
                await tenant_db.record_task_attestation(
                    tid, account_id=user["account_id"],
                    attested_by=int(user["sub"]),
                )
            except Exception:
                pass
            child = await spawn_recurring_if_completed(
                tid, user["account_id"], "completed", tenant_db,
            )
            if child:
                spawned.append(child)
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "maintenance_bulk_status",
        target_type="maintenance",
        target_id=",".join(str(i) for i in body.task_ids[:10]),  # first 10 for display
        details=f"status={body.status} count={touched}",
    )
    return {"updated": touched, "spawned_ids": spawned}


@router.post("/tasks/bulk/delete")
async def bulk_delete(
    body: BulkDelete,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Bulk-delete N maintenance tasks (account-scoped, idempotent)."""
    deleted = await tenant_db.delete_maintenance_tasks_bulk(
        user["account_id"], body.task_ids,
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "maintenance_bulk_delete",
        target_type="maintenance",
        target_id=",".join(str(i) for i in body.task_ids[:10]),
        details=f"count={deleted}",
    )
    return {"deleted": deleted}


@router.get("/odometer/{vehicle_name}")
async def get_vehicle_odometer(
    vehicle_name: str,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Return current odometer + engine-hours reading for a vehicle.

    Reads ``vehicle_state`` (odometer_mi, engine_hours) directly from
    the warehouse — single source of truth, refreshed every 60s by
    ``ingest_vehicle_state``.  Bypasses the WAREHOUSE_READS_ENABLED
    cutover flag because both readings are *only* in the warehouse.

    Returns ``odometer_miles=None`` / ``engine_hours=None`` when the
    vehicle doesn't report the corresponding OBD signal (no CAN bus
    gateway, plan limitation, or warehouse cold-start before the first
    ingest).

    Path kept as ``/odometer/...`` for backward compatibility — callers
    that only need odometer ignore the new fields.
    """
    empty = {
        "vehicle_name": vehicle_name,
        "odometer_miles": None,
        "time": None,
        "engine_hours": None,
        "engine_hours_time": None,
    }
    if not has_maintenance_access(user["role"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    # DRIVER (can_maintenance_own, no can_maintenance_all): enforce truck ownership
    # so a driver cannot enumerate readings for the entire fleet by
    # guessing truck names.
    if not can(user["role"], "can_maintenance_all"):
        allowed_trucks = await get_user_vehicle_nums(user)
        if allowed_trucks is not None:
            name_lower = vehicle_name.strip().lower()
            if not any(t.strip().lower() == name_lower for t in allowed_trucks):
                return empty
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
        return empty
    return {
        "vehicle_name": match.get("vehicle_name", vehicle_name),
        "odometer_miles": match.get("odometer_mi"),
        "time": match.get("odometer_time"),
        "engine_hours": match.get("engine_hours"),
        "engine_hours_time": match.get("engine_hours_time"),
        "company": match.get("company_code", ""),
    }


@router.get("/history/{vehicle_name}")
async def get_service_history(
    vehicle_name: str,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Per-vehicle service history — every completed/cancelled task in
    chronological order, with summary stats.

    Pulls from the same ``maintenance_tasks`` table the live UI reads;
    no new schema needed.  Cost aggregation will become richer once the
    Work Orders module ships and tasks link to ``work_order_id`` — at
    that point this endpoint will additionally pull labor/parts totals
    from the joined work_orders rows.

    Response shape::

        {
          "vehicle_name": "221",
          "tasks": [{...completed task rows in completed_at DESC...}],
          "summary": {
            "total_completed": 47,
            "by_type": {"oil": 12, "tires": 8, ...},
            "last_service_at": "2026-04-30T14:22:00Z",
            "first_service_at": "2024-08-12T09:00:00Z"
          }
        }
    """
    if not has_maintenance_access(user["role"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    # Drivers see only their own truck — matches the list-route policy.
    if not can(user["role"], "can_maintenance_all"):
        trucks = await get_user_vehicle_nums(user)
        if trucks:
            needles = {t.lower() for t in trucks}
            if not any(n in vehicle_name.lower() for n in needles):
                raise HTTPException(status_code=404, detail="Vehicle not found")

    # Pull every task for this vehicle (the adapter returns all rows
    # ordered by status then created_at; we re-sort here for the timeline).
    all_tasks = await tenant_db.get_maintenance_tasks(
        user["account_id"], vehicle_name=vehicle_name,
    )
    closed = [t for t in all_tasks if t.get("status") in ("completed", "done", "cancelled")]
    # Newest first so the timeline reads top→bottom past-to-present.
    closed.sort(
        key=lambda t: (t.get("completed_at") or t.get("created_at") or ""),
        reverse=True,
    )

    by_type: dict[str, int] = {}
    for t in closed:
        ttype = t.get("task_type") or "custom"
        by_type[ttype] = by_type.get(ttype, 0) + 1

    # Enrich each completed task with the attester's display name so the
    # history modal can render "Attested by John Doe" without exposing
    # raw telegram_ids in the UI.
    name_map = await _build_user_name_map(user["account_id"], platform_db)
    enriched = [_enrich_task(t, name_map) for t in closed]

    return {
        "vehicle_name": vehicle_name,
        "tasks": enriched,
        "summary": {
            "total_completed": sum(1 for t in closed if t.get("status") in ("completed", "done")),
            "total_cancelled": sum(1 for t in closed if t.get("status") == "cancelled"),
            "by_type": by_type,
            "last_service_at":  closed[0].get("completed_at") or closed[0].get("created_at") if closed else None,
            "first_service_at": closed[-1].get("completed_at") or closed[-1].get("created_at") if closed else None,
        },
    }


@router.get("/tasks.csv")
async def export_tasks_csv(
    status: Optional[str] = Query(None),
    vehicle: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """CSV export of maintenance tasks for the operator's DOT audit
    binder, fleet review, or spreadsheet analysis.

    Columns mirror the dashboard table so a tech who's been looking at
    the UI gets the same shape on disk.  Respects the same permission
    scoping as the list route (drivers see only their truck).
    """
    import csv
    import io
    from fastapi.responses import StreamingResponse

    if not has_maintenance_access(user["role"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    tasks = await tenant_db.get_maintenance_tasks(
        user["account_id"], status=status, vehicle_name=vehicle,
    )
    if not can(user["role"], "can_maintenance_all"):
        trucks = await get_user_vehicle_nums(user)
        if trucks:
            needles = {t.lower() for t in trucks}
            tasks = [t for t in tasks
                     if any(n in (t.get("vehicle_name") or "").lower() for n in needles)]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "vehicle_name", "task_type", "priority", "status",
        "description", "due_date", "due_miles", "due_engine_hours",
        "last_odometer", "last_engine_hours",
        "created_at", "completed_at", "company_code",
    ])
    for t in tasks:
        writer.writerow([
            t.get("id", ""), t.get("vehicle_name", ""),
            t.get("task_type", ""), t.get("priority", ""),
            t.get("status", ""), t.get("description", ""),
            t.get("due_date", "") or "", t.get("due_miles", "") or "",
            t.get("due_engine_hours", "") or "",
            t.get("last_odometer", "") or "",
            t.get("last_engine_hours", "") or "",
            t.get("created_at", ""), t.get("completed_at", "") or "",
            t.get("company_code", ""),
        ])
    buf.seek(0)
    # Filename includes the date so saved files don't overwrite each
    # other in the user's downloads folder.
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="maintenance-tasks-{today}.csv"',
            # Prevent the SPA middleware from stamping no-store and the
            # browser from inferring HTML — explicit content-type wins.
            "Cache-Control": "no-store",
        },
    )


@router.get("/dot-binder")
async def export_dot_binder(
    days: int = Query(365, ge=1, le=3650),
    vehicle: Optional[str] = Query(None, description="Single-vehicle binder when set"),
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Generate a DOT compliance binder PDF for the account.

    Bundles every vehicle's open + completed maintenance, work orders,
    attestation trail, and DOT inspection records into one printable
    document.  Permission scoped to ``can_maintenance_all`` only — the
    binder is sensitive aggregate data that managers, not drivers, are
    authorized to disclose.

    Heavy compute on large fleets — the assembly walks every vehicle
    and joins per-task, per-WO data.  We run it in a thread executor
    so the event loop stays free for other requests, the same pattern
    the risk-summary route uses.
    """
    import asyncio
    from datetime import datetime, timezone
    from fastapi.responses import StreamingResponse
    from capabilities.reporting.dot_binder import build_dot_binder
    from capabilities.reporting.dot_binder_pdf import render_dot_binder_pdf

    # Resolve account display name.  ``list_accounts`` is the cheapest
    # lookup we have; fall back to ``Account #N`` if the platform DB
    # call fails so the binder still renders.
    account_name = f"Account #{user['account_id']}"
    try:
        accounts = await platform_db.list_accounts()
        for acc in accounts:
            if acc.id == user["account_id"]:
                account_name = acc.name or account_name
                break
    except Exception:
        pass

    binder = await build_dot_binder(
        account_id=user["account_id"],
        account_name=account_name,
        tenant_db=tenant_db,
        platform_db=platform_db,
        generated_by_id=int(user["sub"]),
        days=days,
        vehicle_name_filter=vehicle,
    )

    # Render in a thread — reportlab's SimpleDocTemplate.build is
    # synchronous CPU work (~1-2 s for a 50-truck fleet).  Hand it off
    # so the event loop stays responsive to concurrent requests.
    loop = asyncio.get_event_loop()
    pdf_bytes = await loop.run_in_executor(None, render_dot_binder_pdf, binder)

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "dot_binder_export",
        target_type="account", target_id=str(user["account_id"]),
        details=f"days={days} vehicle={vehicle or 'all'} bytes={len(pdf_bytes)}",
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    suffix = f"-{vehicle}" if vehicle else ""
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="dot-binder-{today}{suffix}.pdf"',
            "Cache-Control": "no-store",
        },
    )
