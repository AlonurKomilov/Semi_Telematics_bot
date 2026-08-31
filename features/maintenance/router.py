"""Maintenance API endpoints — CRUD for maintenance tasks."""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may;
# service/alert/ai_tool/signal modules never do.


import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional

from interfaces.api.deps import get_current_user, require_permission, get_tenant_db, get_platform_db, get_user_vehicle_nums, paginate, resolve_user_id, get_user_company_codes, filter_by_allowed_companies
from capabilities.activity_trail import new_group_id
from capabilities.permissions.roles import can
from capabilities.permissions.vehicle_scope import VehicleScope, build_vehicle_scope
from features.maintenance.service import apply_live_readings, has_maintenance_access, spawn_recurring_if_completed

# Who-did-what now rides capabilities/activity_trail (recorded inside
# the storage mutations, same transaction, field-level old→new — the
# thin add_audit_log calls that used to live in these endpoints are
# gone).  The old audit_log rows remain readable on the /audit page.

logger = logging.getLogger(__name__)

# Attachment constraints — match the Work Orders limits exactly so the
# user can't sneak past one upload cap by using the other route.  Same
# allow-list for content types (PDF + common image MIMEs).
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
_ALLOWED_ATTACHMENT_TYPES = {
    "application/pdf",
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "image/heic", "image/heif",
}


def _coerce_due_date(v: Optional[str]) -> Optional[str]:
    """Validate that ``due_date`` is YYYY-MM-DD or an ISO 8601 datetime.

    Returns the original string unchanged so existing callers (bot,
    dashboard) keep their wire format.  An empty string is normalised
    to ``None`` because Pydantic treats ``""`` as set; storing it
    would corrupt the scheduler's ``due_date < ?`` comparison.

    Rejecting garbage strings here keeps the scheduler honest — a
    bogus ``"banana"`` previously stored fine and silently never fired
    overdue alerts.
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        if len(s) == 10:
            datetime.strptime(s, "%Y-%m-%d")
        else:
            # Accept both "...Z" (Telegram/bot output) and "+00:00"
            # forms; fromisoformat handles the second natively.
            datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise ValueError(
            "due_date must be YYYY-MM-DD or ISO 8601 datetime"
        )
    return s


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

    _validate_due_date = field_validator("due_date")(
        lambda cls, v: _coerce_due_date(v),
    )

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
    # Cost is stored as integer cents to avoid floating-point drift on
    # aggregate totals; the UI submits dollars and converts on the way
    # in.  ``vendor_name`` is free-text — shops aren't first-class
    # entities yet.
    cost_cents: Optional[int] = Field(None, ge=0)
    vendor_name: Optional[str] = Field(None, max_length=120)

    _validate_due_date = field_validator("due_date")(
        lambda cls, v: _coerce_due_date(v),
    )


async def _maintenance_vehicle_scope(user: dict, tenant_db):
    """The vehicle wall for a ``_own`` maintenance user, or None.

    ``None`` means unrestricted — the caller holds ``can_maintenance_all``.
    Anything else is authoritative, INCLUDING an empty scope, which denies
    every row.  That safe-deny is deliberate and is the opposite of
    ``deps.py``'s "no assignments = unrestricted": maintenance has always
    denied here, and widening it would be a silent grant.

    Membership is decided by identity (registry id -> provider id ->
    exact name) in ``capabilities/permissions/vehicle_scope``.  In THIS
    module only the last rung is reachable: ``maintenance_tasks`` has no
    registry_id and never writes its vehicle_id column, so a task row
    carries no id to match on.  That is fine while every task stores the
    bare unit number (it does today, 156/156); the fix for a fleet that
    decorates names is to enrich the rows from the registry, never to
    restore the substring.  SEVEN call
    sites in this file each re-decided it with a substring test, so an
    assignment of "230" also matched vehicle "2303" and "100" matched
    trailer "AK1001" — on a visibility wall that is a disclosure, and on
    the attachment routes it let a driver read and write another truck's
    files.  ``interfaces/api/deps.py`` was corrected for exactly this;
    these copies were left behind.
    """
    if can(user["role"], "can_maintenance_all"):
        return None
    trucks = await get_user_vehicle_nums(user)
    if not trucks:
        return VehicleScope()
    return await build_vehicle_scope(tenant_db, user["account_id"], trucks)


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
    # Company scoping — a restricted user sees only their companies' tasks.
    # Legacy tasks with no company_code are conservatively hidden from a
    # restricted user (safe-deny); unrestricted users (empty allowed) see all.
    tasks = filter_by_allowed_companies(tasks, await get_user_company_codes(user), key="company_code")
    # Filter to assigned trucks for _own permission — membership decided
    # by IDENTITY (registry id → provider id → exact name), never by
    # substring.
    #
    # This wall used an alternation regex with substring semantics, so an
    # assignment of "230" also matched vehicle "2303" and "100" matched
    # trailer "AK1001".  On a visibility wall an over-match is a
    # disclosure, not a cosmetic bug.  ``interfaces/api/deps.py`` was
    # corrected for exactly this and this second copy was left behind —
    # so it now shares the one engine instead of re-deciding membership.
    #
    # Safe-deny: a user with can_maintenance_vehicle but NO assigned
    # trucks gets an empty list, never the unfiltered account dataset.
    # That is the OPPOSITE of deps.py's "no assignments = unrestricted"
    # and is preserved deliberately — maintenance has always denied here,
    # and widening it would be a silent grant.
    scope = await _maintenance_vehicle_scope(user, tenant_db)
    if scope is not None:
        tasks = [t for t in tasks if scope.allows_row(t, name_key="vehicle_name")]

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
    # The merge itself lives in the service (shared with the AI tools +
    # snapshot so every urgency reader judges the same readings).
    await apply_live_readings(tenant_db, user["account_id"], items)

    # Mileage-projected due dates for the calendar view.  A task that
    # tracks only by ``due_miles`` has nothing to render on a date
    # grid, so the calendar would silently skip it.  Computing
    # ``projected_due_date = today + (mi_to_go / avg_daily_miles)``
    # lets the dashboard place those tasks at their expected service
    # day with a clear "projected" visual marker.
    #
    # Bounds:
    #   * Skip when no telemetry-driven velocity exists for the truck.
    #   * Skip when the projection lands more than 365 days out — that
    #     far ahead the truck's recent average is noise; the calendar
    #     would over-promise.
    #   * Skip closed / cancelled tasks (already in history).
    if items:
        try:
            # Velocity comes from the daily tier of ``vehicle_telemetry``
            # (730-day retention) so the
            # requested 30-day window is actually honoured.  Median of
            # drive-day miles is robust to weekend / repair / spare-
            # truck idle distributions that would drag a mean
            # unrealistically low.
            velocity_map = await tenant_db.compute_vehicle_velocity_daily(
                user["account_id"], window_days=30,
            )
            # Snapshot + hourly fallbacks are computed LAZILY: only
            # when at least one task references a vehicle missing from
            # ``velocity_map``.  This handles the "mixed account" case
            # where most trucks have 30+ days of daily roll-ups but a
            # recently-onboarded truck has none — the older trucks
            # use the median path, the new truck falls back to the
            # 7-day snapshot helper instead of getting no projection
            # at all (the pre-fix behaviour).
            snapshot_velocities: Optional[dict[str, float]] = None

            async def _fetch_snapshot_fallback() -> dict[str, float]:
                # NB: bounded by 7-day snapshot retention regardless
                # of the ``days=30`` request — this is a degraded-mode
                # fallback for vehicles missing from the daily table.
                snap = await tenant_db.compute_vehicle_velocity_window(
                    user["account_id"], days=30, min_days_required=3,
                )
                if not snap:
                    snap = await tenant_db.get_vehicle_avg_daily_miles(
                        user["account_id"], days=30,
                    )
                return snap or {}

            from datetime import datetime as _dt, timedelta as _td, timezone as _tz
            today = _dt.now(_tz.utc)
            for t in items:
                if t.get("status") in ("completed", "cancelled"):
                    continue
                if t.get("due_date"):
                    # Date already pinned by the operator — projection
                    # would override their intent.
                    continue
                due_mi = t.get("due_miles")
                last_mi = t.get("last_odometer")
                if due_mi is None or last_mi is None:
                    continue
                mi_to_go = float(due_mi) - float(last_mi)
                if mi_to_go <= 0:
                    # Already overdue by mileage — let DueDateChip /
                    # the bucket logic flag it, no projection needed.
                    continue
                vid = str(t.get("vehicle_id") or "")
                if not vid:
                    continue

                avg = 0.0
                window_days = 30
                drive_days = 0
                days_observed = 0
                source = ""
                stats = velocity_map.get(vid)
                if stats:
                    avg = float(stats.get("velocity") or 0)
                    window_days = int(stats.get("window_days") or 30)
                    drive_days = int(stats.get("drive_days") or 0)
                    days_observed = int(stats.get("days_observed") or 0)
                    source = "daily_metrics"
                else:
                    # Lazy fetch — only the first task that needs a
                    # fallback triggers the queries; later tasks reuse
                    # the cached result.
                    if snapshot_velocities is None:
                        snapshot_velocities = await _fetch_snapshot_fallback()
                    if vid in snapshot_velocities:
                        avg = float(snapshot_velocities.get(vid) or 0)
                        source = "snapshot_fallback"

                if avg <= 0:
                    continue
                days_out = mi_to_go / avg
                if days_out > 365:
                    continue
                projected = today + _td(days=days_out)
                t["projected_due_date"] = projected.date().isoformat()
                t["velocity_avg_daily_miles"] = round(float(avg), 1)
                # Provenance fields surface in the calendar tooltip so
                # operators can sanity-check the projection.  Older
                # dashboards that don't know about them ignore them.
                t["velocity_window_days"] = window_days
                t["velocity_drive_days"] = drive_days
                t["velocity_days_observed"] = days_observed
                t["velocity_source"] = source
        except Exception:
            # Warehouse outage on the velocity query shouldn't break
            # the list — projections just don't appear this request.
            # Log the actual cause so a hidden schema drift / KeyError
            # is grep-able from production instead of silently dropping
            # every projection forever.
            logger.warning(
                "maintenance list: velocity projection block failed acct=%d",
                user["account_id"],
                exc_info=True,
            )

    return {"tasks": items, "count": paged["total"],
            "page": paged["page"], "page_size": paged["page_size"],
            "total_pages": paged["total_pages"]}


async def _require_company_visible_task(task: dict, user: dict) -> None:
    """404 if the task's company is outside the caller's allowed companies.

    Used by every by-id maintenance route so a company-restricted user
    can't view/modify another company's task by guessing the id — the
    company-filtered list already hides it.
    """
    allowed = await get_user_company_codes(user)
    if allowed and not filter_by_allowed_companies([task], allowed, key="company_code"):
        raise HTTPException(status_code=404, detail="Task not found")


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
    await _require_company_visible_task(task, user)
    # Check truck ownership for _own permission.  Safe-deny: a user with
    # can_maintenance_vehicle but no assigned trucks must NOT see anything.
    scope = await _maintenance_vehicle_scope(user, tenant_db)
    if scope is not None and not scope.allows_row(task, name_key="vehicle_name"):
        raise HTTPException(status_code=404, detail="Task not found")
    name_map = await _build_user_name_map(user["account_id"], platform_db)
    enriched = _enrich_task(task, name_map)

    # Mirror the list-view live-telemetry merge so the modal and the
    # list show the same odometer / engine-hours readings.  Live wins
    # only when newer than the stored value.
    #
    # Lookup priority:
    #   1. ``vehicle_id`` — the unique PRIMARY KEY of vehicle_state.
    #      Always correct when the task has one.
    #   2. ``(company_code, vehicle_name)`` — fallback for legacy tasks
    #      that pre-date vehicle_id capture.  Scopes by company so
    #      "truck 103" in COMPANY_A doesn't pick up live data from
    #      "truck 103" in COMPANY_B (the old bug).
    vehicle_id = (enriched.get("vehicle_id") or "").strip()
    vehicle_name = (enriched.get("vehicle_name") or "").strip()
    company_code = (enriched.get("company_code") or "").strip()
    if vehicle_id or vehicle_name:
        try:
            kwargs: dict = {}
            if vehicle_id:
                kwargs["vehicle_id"] = vehicle_id
            else:
                kwargs["vehicle_nums"] = [vehicle_name]
                if company_code:
                    kwargs["company"] = company_code
            state_rows = await tenant_db.get_vehicle_state(
                user["account_id"], **kwargs,
            )
            # When the task has no company_code and the name resolves to
            # multiple vehicle_state rows, leave both telemetry and the
            # company chip blank rather than pick arbitrarily.  This is
            # the same "unambiguous-only" guard the list view uses.
            if len(state_rows) == 1:
                live = state_rows[0]
                # Backfill identity fields so the modal's company chip
                # has something to render even though the task row was
                # stored without it.
                if not company_code:
                    enriched["company_code"] = live.get("company_code") or ""
                if not vehicle_id:
                    enriched["vehicle_id"] = live.get("vehicle_id") or ""
                # Single-row lookup is scoped by vehicle_id or
                # (company, name) — authoritative for THIS exact
                # vehicle.  Trust the live value unconditionally so a
                # task whose stored ``last_odometer`` got corrupted by
                # the pre-fix scheduler's cross-company merge can
                # recover on the next read.  See the list-view
                # ``trust_live`` block for the matching logic.
                live_odo = live.get("odometer_mi")
                if isinstance(live_odo, (int, float)):
                    enriched["last_odometer"] = float(live_odo)
                live_hrs = live.get("engine_hours")
                if isinstance(live_hrs, (int, float)):
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
    from features.maintenance.service import (
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
        actor_user_id=await resolve_user_id(user),
    )
    return {"id": task_id, "status": "created"}


class BulkTaskCreate(BaseModel):
    """Create the same task template across N vehicles.

    Each vehicle gets its own ``maintenance_tasks`` row + audit log
    entry, so post-creation edits / completions / spawn trees stay
    fully independent.  Reuses ``TaskCreate``'s validation rules for
    the shared template; the only difference is ``vehicle_names`` (a
    list) replaces ``vehicle_name`` (a string).
    """
    vehicle_names: list[str] = Field(..., min_length=1, max_length=100)
    company_code: str = ""
    task_type: str = Field(..., min_length=1)
    description: str = Field(..., min_length=3, max_length=500)
    due_date: Optional[str] = None
    due_miles: Optional[float] = Field(None, ge=0)
    due_engine_hours: Optional[float] = Field(None, ge=0)
    recur_interval_days: Optional[int] = Field(None, ge=1)
    recur_interval_miles: Optional[float] = Field(None, ge=1)
    recur_interval_engine_hours: Optional[float] = Field(None, ge=1)
    priority: str = Field("medium", pattern=r"^(low|medium|high|critical)$")
    # Re-checked AT CREATE TIME, not trusted from the preflight: a task
    # created between the preflight and the submit is still skipped, so
    # the answer the user confirmed cannot rot in the gap.
    skip_duplicates: bool = False

    _validate_due_date = field_validator("due_date")(
        lambda cls, v: _coerce_due_date(v),
    )

    @model_validator(mode="after")
    def _require_trigger(self):
        if (not self.due_date
                and self.due_miles is None
                and self.due_engine_hours is None):
            raise ValueError(
                "Set a due date, due miles, or due engine hours — "
                "otherwise the task will never become overdue."
            )
        return self


class BulkPreflight(BaseModel):
    vehicle_names: list[str] = Field(..., min_length=1, max_length=100)
    task_type: str = Field(..., min_length=1)


@router.post("/tasks/bulk/preflight")
async def bulk_create_preflight(
    body: BulkPreflight,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Which of these vehicles ALREADY have an open task of this type?

    The form asks before creating so the confirmation can say "7 of
    these already have an open Oil change" with the server's numbers —
    the client's own task list is capped at one page and would
    silently under-count on a large account.  Read-only; the create
    call re-checks, so this answer going stale costs nothing.
    """
    open_set = await tenant_db.get_open_task_vehicles(
        user["account_id"], body.task_type)
    wanted = [v.strip() for v in body.vehicle_names if (v or "").strip()]
    return {"duplicates": [v for v in wanted if v in open_set]}


@router.post("/tasks/bulk/create")
async def bulk_create_tasks(
    body: BulkTaskCreate,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Create the same task template across N vehicles in one request.

    Designed for the "onboarded 10 trucks, all need the same oil
    schedule" workflow.  Each fan-out gets its own warehouse-telemetry
    backfill, audit log entry, and recurrence chain — failures on one
    vehicle don't roll back the others (best-effort per-row).
    """
    from features.maintenance.service import (
        fetch_current_telemetry_for_vehicle,
    )
    actor = await resolve_user_id(user)
    group = new_group_id()      # one bulk action, N events, one group
    created: list[dict] = []
    failed: list[dict] = []
    skipped: list[str] = []
    open_set: set[str] = (
        await tenant_db.get_open_task_vehicles(user["account_id"], body.task_type)
        if body.skip_duplicates else set()
    )
    for vname in body.vehicle_names:
        vname = (vname or "").strip()
        if not vname:
            continue
        if vname in open_set:
            # The rule the fault auto-creator has applied to itself
            # since service.py grew it: an open task of the same type
            # means this vehicle is already covered, not under-served.
            skipped.append(vname)
            continue
        try:
            last_odo, last_hrs = await fetch_current_telemetry_for_vehicle(
                tenant_db, user["account_id"], vname,
            )
            task_id = await tenant_db.add_maintenance_task(
                account_id=user["account_id"],
                company_code=body.company_code,
                vehicle_name=vname,
                task_type=body.task_type,
                description=body.description,
                due_date=body.due_date,
                due_miles=body.due_miles,
                due_engine_hours=body.due_engine_hours,
                priority=body.priority,
                recur_interval_days=body.recur_interval_days,
                recur_interval_miles=body.recur_interval_miles,
                recur_interval_engine_hours=body.recur_interval_engine_hours,
                last_odometer=last_odo,
                last_engine_hours=last_hrs,
                actor_user_id=actor,
                trail_group_id=group,
            )
            created.append({"id": task_id, "vehicle_name": vname})
        except Exception as e:
            failed.append({"vehicle_name": vname, "error": str(e)})

    return {"created": created, "failed": failed, "skipped": skipped}


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
    await _require_company_visible_task(task, user)

    # Clearable fields can be set to ``null`` explicitly to wipe the
    # column.  Everything else only flows through when a real value is
    # provided, so accidentally PUT'ing ``priority: null`` doesn't
    # blank a valid enum column.
    _CLEARABLE = {
        "due_date", "due_miles", "due_engine_hours",
        "recur_interval_days", "recur_interval_miles",
        "recur_interval_engine_hours",
        "work_order_id",
        "cost_cents", "vendor_name",
    }
    dump = body.model_dump(exclude_unset=True)
    kwargs = {
        k: v for k, v in dump.items()
        if v is not None or k in _CLEARABLE
    }
    if not kwargs:
        raise HTTPException(status_code=422, detail="No fields to update")

    actor = await resolve_user_id(user)
    # If status change, use the dedicated method
    if "status" in kwargs and len(kwargs) == 1:
        ok = await tenant_db.update_maintenance_status(
            task_id, kwargs["status"], account_id=user["account_id"],
            actor_user_id=actor,
        )
    else:
        ok = await tenant_db.update_maintenance_task(
            task_id, account_id=user["account_id"], actor_user_id=actor,
            **kwargs,
        )

    spawned_id: Optional[int] = None
    if ok:
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
                actor_user_id=actor,
            )

    return {"ok": ok, "spawned_id": spawned_id}


class SnoozePayload(BaseModel):
    """Snooze a task until an ISO timestamp, or clear the snooze.

    ``until`` accepts ISO 8601 datetime (``2026-06-01T12:00:00Z``) or
    ``null`` to clear an active snooze.  We don't accept a relative
    "for 48 hours" form here — the dashboard does the arithmetic
    client-side so the server interprets one canonical format.
    """
    until: Optional[str] = None

    _validate_until = field_validator("until")(
        lambda cls, v: _coerce_due_date(v),
    )


@router.post("/tasks/{task_id}/snooze")
async def snooze_task(
    task_id: int,
    body: SnoozePayload,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Snooze an overdue/pending task until ``body.until`` (or clear).

    The schedulers (date / mileage / engine-hours) consult
    ``snoozed_until`` and skip the row while it points to the future.
    ``alerted_at`` is cleared in the same write so the next alert fires
    fresh once the snooze expires.
    """
    task = await tenant_db.get_maintenance_task(task_id, account_id=user["account_id"])
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await _require_company_visible_task(task, user)
    ok = await tenant_db.snooze_task(
        task_id, account_id=user["account_id"], until_iso=body.until,
        actor_user_id=await resolve_user_id(user),
    )
    return {"ok": ok, "snoozed_until": body.until}


@router.get("/tasks/{task_id}/history")
async def get_task_history(
    task_id: int,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """The task's activity trail — who did what, field-level old→new.

    Visible to anyone who can see the task itself (the trail's values
    are the task's own fields, so no extra permission tier).  Events
    for DELETED tasks are still returned — that is the point: the
    History dialog answers "where did it go" after the row is gone.
    """
    # The gate is the REGISTRY's, not a local role check.  This route
    # used to ask has_maintenance_access(), which passes on
    # can_maintenance_vehicle — driver grade — while the entity declares
    # can_maintenance_all and the generic history endpoint enforces
    # exactly that.  Two doors onto one trail must not have two locks.
    from capabilities.activity_trail.registry import (
        ensure_declarations_loaded, entity_descriptor,
    )
    from capabilities.activity_trail.router import history_in_company_scope
    from capabilities.permissions.roles import Role, get_user_permissions
    ensure_declarations_loaded()
    d = entity_descriptor("maintenance_task")
    perms = await get_user_permissions(
        Role(user["role"]), user["account_id"],
        is_manager=bool(user.get("is_manager")),
        is_primary_owner=bool(user.get("is_primary_owner")),
    )
    if not (d and any(getattr(perms, p, False) for p in d.view_permissions)):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    task = await tenant_db.get_maintenance_task(task_id, account_id=user["account_id"])
    events = await tenant_db.list_activity_events(
        user["account_id"],
        entity_type="maintenance_task", entity_id=str(task_id),
    )
    # The company wall runs UNCONDITIONALLY.  It used to hang off
    # ``if task:`` — so a DELETED task skipped it entirely, and a delete
    # event carries the whole row by contract (recorder.delete_changes).
    # That handed any restricted caller the full body of any deleted
    # task in any company, by guessing an id: precisely the hole the
    # generic endpoint's wall was written to close.
    if not await history_in_company_scope(
        d, str(task_id), events, user, tenant_db, user["account_id"],
    ):
        raise HTTPException(status_code=404, detail="Task not found")
    if not task and not events:
        raise HTTPException(status_code=404, detail="Task not found")
    names: dict[int, str] = {}
    for e in events:
        uid = e["actor_user_id"]
        if uid is None or uid in names:
            continue
        u = await tenant_db.get_user(int(uid))
        names[int(uid)] = ((u.display_name if u else "") or "").strip() or f"#{uid}"
    for e in events:
        e["actor_name"] = names.get(e["actor_user_id"], "") if e["actor_user_id"] else ""
    return {"events": events}


@router.post("/tasks/{task_id}/attachment")
async def upload_task_attachment(
    task_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Attach a single receipt or photo to a maintenance task.

    Differs from the Work Orders attachment route in that maintenance
    tasks hold ONE attachment (the latest upload replaces the previous
    one) — heavyweight multi-file timelines belong on the Work Order.
    This route is for the quick "snap a photo of the roadside DEF
    receipt" workflow.

    Permission: ``can_maintenance_all`` OR ``can_maintenance_vehicle`` on
    a task whose vehicle the driver is assigned to.  Drivers must NOT
    be able to attach evidence to other trucks' tasks.
    """
    from adapters.storage.object_storage import get_object_storage_for_account
    from capabilities.object_storage.tracking import track_for_sync_if_hybrid
    from capabilities.object_storage.paths import resolve_company_folder
    from features.work_orders.paths import safe_attachment_name
    if not has_maintenance_access(user["role"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    task = await tenant_db.get_maintenance_task(task_id, account_id=user["account_id"])
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await _require_company_visible_task(task, user)
    # Drivers locked to their assigned trucks.  Safe-deny on empty
    # assignment list, matching the list/get routes.
    scope = await _maintenance_vehicle_scope(user, tenant_db)
    if scope is not None and not scope.allows_row(task, name_key="vehicle_name"):
        raise HTTPException(status_code=404, detail="Task not found")

    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_ATTACHMENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {content_type or 'unknown'}",
        )

    raw = await file.read()
    if len(raw) > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {_MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB limit.",
        )

    safe_name = safe_attachment_name(file.filename or "attachment")
    company_folder = await resolve_company_folder(
        tenant_db, user["account_id"], task.get("company_code", ""),
    )
    # Folder layout mirrors work-order convention: account / company /
    # maintenance / task-id.  Keeps maintenance evidence siblings of
    # the work-order tree so admins know where to look.
    folder = f"{company_folder}/maintenance/{task_id}"
    store = await get_object_storage_for_account(user["account_id"], tenant_db)
    file_path = store.put(folder, safe_name, raw)

    await tenant_db.set_task_attachment(
        task_id, account_id=user["account_id"],
        attachment_path=file_path,
        attachment_name=safe_name,
        attachment_content_type=content_type,
        actor_user_id=await resolve_user_id(user),
    )
    # Cloud sync on a hybrid account.  The task id IS the entity: one
    # attachment per task (set_task_attachment overwrites), so the
    # repointer's single-column UPDATE is exact.
    await track_for_sync_if_hybrid(
        store, folder, safe_name, file_path,
        entity_type="maintenance_attachment", entity_id=int(task_id),
        file_size=len(raw),
    )
    return {
        "ok": True,
        "file_name": safe_name,
        "size": len(raw),
        "content_type": content_type,
    }


@router.get("/tasks/{task_id}/attachment")
async def download_task_attachment(
    task_id: int,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Stream the task's attached file back to the client.

    Read-through ``ObjectStorage.get`` so the route works for any
    backend.  ``inline`` Content-Disposition so images preview in the
    browser; PDFs render too.
    """
    from adapters.storage.object_storage import get_object_storage_for_account
    from capabilities.object_storage.paths import resolve_company_folder
    from fastapi.responses import StreamingResponse
    if not has_maintenance_access(user["role"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    task = await tenant_db.get_maintenance_task(task_id, account_id=user["account_id"])
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await _require_company_visible_task(task, user)
    scope = await _maintenance_vehicle_scope(user, tenant_db)
    if scope is not None and not scope.allows_row(task, name_key="vehicle_name"):
        raise HTTPException(status_code=404, detail="Task not found")

    name = task.get("attachment_name")
    ctype = task.get("attachment_content_type") or "application/octet-stream"
    if not name:
        raise HTTPException(status_code=404, detail="No attachment")

    company_folder = await resolve_company_folder(
        tenant_db, user["account_id"], task.get("company_code", ""),
    )
    folder = f"{company_folder}/maintenance/{task_id}"
    store = await get_object_storage_for_account(user["account_id"], tenant_db)
    data = store.get(folder, name)
    if data is None:
        raise HTTPException(status_code=404, detail="File not found in storage")

    return StreamingResponse(
        iter([data]),
        media_type=ctype,
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )


@router.delete("/tasks/{task_id}/attachment")
async def delete_task_attachment(
    task_id: int,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Remove the task's attached file (managers only).

    Object-store delete is best-effort; the metadata clear always wins
    so the UI stops linking to a possibly-orphaned file.  Drivers can
    re-upload but can't delete — once an attestation artifact is
    captured, only a manager removes it (audit-trail intent).
    """
    from adapters.storage.object_storage import get_object_storage_for_account
    from capabilities.object_storage.paths import resolve_company_folder
    task = await tenant_db.get_maintenance_task(task_id, account_id=user["account_id"])
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await _require_company_visible_task(task, user)
    name = task.get("attachment_name")
    if name:
        try:
            company_folder = await resolve_company_folder(
                tenant_db, user["account_id"], task.get("company_code", ""),
            )
            folder = f"{company_folder}/maintenance/{task_id}"
            store = await get_object_storage_for_account(user["account_id"], tenant_db)
            try:
                store.delete(folder, name)
            except Exception:
                # Best-effort — the metadata wipe below makes the UI
                # consistent even if the bytes leak.
                pass
        except Exception:
            pass
    await tenant_db.set_task_attachment(
        task_id, account_id=user["account_id"],
        attachment_path=None, attachment_name=None, attachment_content_type=None,
        actor_user_id=await resolve_user_id(user),
    )
    return {"ok": True}


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
    await _require_company_visible_task(task, user)

    # A "group" of one: single and bulk deletes share ONE undo path.
    group_id = new_group_id()
    await tenant_db.delete_maintenance_task(
        task_id, account_id=user["account_id"],
        actor_user_id=await resolve_user_id(user),
        trail_group_id=group_id,
    )
    return {"ok": True, "group_id": group_id}


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

    Idempotency: attestation + recurring spawn only fire for tasks that
    were NOT already in the target status.  Otherwise a second bulk
    click on the same selection would re-attest and spawn duplicate
    follow-up tasks for every recurring item.
    """
    # Snapshot pre-update status so we can tell which rows are actually
    # transitioning vs. already in the target state.  One query per
    # task is fine — body.task_ids is capped at 200.
    newly_transitioned: list[int] = []
    if body.status == "completed":
        for tid in body.task_ids:
            prev = await tenant_db.get_maintenance_task(
                tid, account_id=user["account_id"],
            )
            if prev and prev.get("status") != "completed":
                newly_transitioned.append(tid)

    actor = await resolve_user_id(user)
    group = new_group_id()
    touched = await tenant_db.update_maintenance_status_bulk(
        user["account_id"], body.task_ids, body.status,
        actor_user_id=actor, trail_group_id=group,
    )
    spawned: list[int] = []
    if body.status == "completed":
        # Per-task attestation + spawn — only for ids that genuinely
        # transitioned this request.  Re-clicking the same selection is
        # a no-op for the audit trail and the recurring follow-up chain.
        for tid in newly_transitioned:
            try:
                await tenant_db.record_task_attestation(
                    tid, account_id=user["account_id"],
                    attested_by=int(user["sub"]),
                )
            except Exception:
                pass
            child = await spawn_recurring_if_completed(
                tid, user["account_id"], "completed", tenant_db,
                actor_user_id=actor,
            )
            if child:
                spawned.append(child)
    return {"updated": touched, "spawned_ids": spawned}


@router.post("/tasks/bulk/delete")
async def bulk_delete(
    body: BulkDelete,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Bulk-delete N maintenance tasks (account-scoped, idempotent).

    The trail records one delete event PER task — full field values,
    all sharing one group — so a wrong bulk delete is reconstructable
    from the audit page alone.  (The 2026-07-30 incident's fix: the old
    thin log truncated the id list to 10 of 33 and kept no values.)
    """
    group_id = new_group_id()
    deleted = await tenant_db.delete_maintenance_tasks_bulk(
        user["account_id"], body.task_ids,
        actor_user_id=await resolve_user_id(user),
        trail_group_id=group_id,
    )
    # ``group_id`` rides the response so the caller can offer Undo —
    # which is just a restore of this group (capabilities/activity_trail).
    return {"deleted": deleted, "group_id": group_id}


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
    # DRIVER (can_maintenance_vehicle, no can_maintenance_all): enforce truck ownership
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
    # Safe-deny: an unassigned driver gets a 404, never another truck's
    # history just because their assignment list is empty.
    # Walled on the NAME rung alone, and that is all this module has:
    # ``maintenance_tasks`` carries no registry_id and its vehicle_id
    # column is never written, so a task row cannot reach rungs 1-2 no
    # matter how the check is arranged.  Every task in this account
    # stores the bare unit number, so exact equality is the right rule —
    # but a fleet that decorated names ("Truck-107A") would need the
    # rows enriched from the registry first, NOT the substring back.
    scope = await _maintenance_vehicle_scope(user, tenant_db)
    if scope is not None and not scope.allows(name=vehicle_name):
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

    # Tier-2 B2: completed shop invoices join the vehicle's history.
    # Manager-gated like the Work Orders pages themselves — drivers
    # keep the tasks-only view (WO rows carry invoice money).
    work_orders: list[dict] = []
    if can(user["role"], "can_work_orders_all"):
        wos = await tenant_db.list_work_orders(
            user["account_id"], vehicle_name=vehicle_name,
        )
        work_orders = [
            {
                "id": w["id"],
                "service_date": w.get("service_date"),
                "vendor_name": w.get("vendor_name") or "",
                "invoice_number": w.get("invoice_number") or "",
                "repair_priority": w.get("repair_priority") or "",
                "payment_status": w.get("payment_status") or "",
                "labor_cost": w.get("labor_cost") or 0,
                "parts_cost": w.get("parts_cost") or 0,
                "total_cost": w.get("total_cost") or 0,
            }
            for w in wos
            if w.get("status") not in ("open", "void") and w.get("service_date")
        ]

    return {
        "vehicle_name": vehicle_name,
        "tasks": enriched,
        "work_orders": work_orders,
        "summary": {
            "total_completed": sum(1 for t in closed if t.get("status") in ("completed", "done")),
            "total_cancelled": sum(1 for t in closed if t.get("status") == "cancelled"),
            "by_type": by_type,
            "last_service_at":  closed[0].get("completed_at") or closed[0].get("created_at") if closed else None,
            "first_service_at": closed[-1].get("completed_at") or closed[-1].get("created_at") if closed else None,
        },
    }


# ── Templates ───────────────────────────────────────────────────────────────


class TemplateBody(BaseModel):
    """Re-usable task template.

    Stores defaults that the dashboard can one-click apply to any
    vehicle.  ``due_in_*`` are relative offsets — when the template is
    applied, the dashboard converts to absolute targets the same way
    the manual add form does.  No ``vehicle_name`` field — templates
    are vehicle-agnostic.
    """
    name: str = Field(..., min_length=1, max_length=120)
    task_type: str = Field("custom", min_length=1)
    description: str = Field("", max_length=500)
    priority: str = Field("medium", pattern=r"^(low|medium|high|critical)$")
    due_in_days: Optional[int] = Field(None, ge=1)
    due_in_miles: Optional[float] = Field(None, ge=1)
    due_in_hours: Optional[float] = Field(None, ge=1)
    recur_interval_days: Optional[int] = Field(None, ge=1)
    recur_interval_miles: Optional[float] = Field(None, ge=1)
    recur_interval_engine_hours: Optional[float] = Field(None, ge=1)


@router.get("/templates")
async def list_templates(
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """List all templates for the operator's account.

    Read permission matches the rest of the maintenance module —
    anyone with ``can_maintenance_vehicle`` can SEE the templates; only
    ``can_maintenance_all`` can mutate them (write routes below).
    """
    if not has_maintenance_access(user["role"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    items = await tenant_db.list_maintenance_templates(user["account_id"])
    return {"templates": items}


@router.post("/templates")
async def create_template(
    body: TemplateBody,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    try:
        tid = await tenant_db.add_maintenance_template(
            account_id=user["account_id"],
            name=body.name,
            task_type=body.task_type,
            description=body.description,
            priority=body.priority,
            due_in_days=body.due_in_days,
            due_in_miles=body.due_in_miles,
            due_in_hours=body.due_in_hours,
            recur_interval_days=body.recur_interval_days,
            recur_interval_miles=body.recur_interval_miles,
            recur_interval_engine_hours=body.recur_interval_engine_hours,
            created_by=await resolve_user_id(user),
            actor_user_id=await resolve_user_id(user),
        )
    except Exception as e:
        # The UNIQUE(account_id, name) constraint surfaces here when
        # a user tries to re-use an existing template name.
        if "UNIQUE" in str(e):
            raise HTTPException(
                status_code=409,
                detail="A template with that name already exists.",
            )
        raise
    return {"id": tid, "status": "created"}


@router.put("/templates/{template_id}")
async def update_template(
    template_id: int,
    body: TemplateBody,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    existing = await tenant_db.get_maintenance_template(template_id, user["account_id"])
    if not existing:
        raise HTTPException(status_code=404, detail="Template not found")
    ok = await tenant_db.update_maintenance_template(
        template_id, account_id=user["account_id"],
        actor_user_id=await resolve_user_id(user),
        **body.model_dump(),
    )
    return {"ok": ok}


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: int,
    user: dict = Depends(require_permission("can_maintenance_all")),
    tenant_db=Depends(get_tenant_db),
):
    ok = await tenant_db.delete_maintenance_template(
        template_id, user["account_id"],
        actor_user_id=await resolve_user_id(user),
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"ok": True}


# The /maintenance/task-types shims (GET/POST/DELETE) lived here for
# one release after the service-tasks cutover, serving dashboards
# bundled before it.  Their last caller (Tasks.tsx) now reads
# /service-tasks directly, so they're gone; /service-tasks is the one
# surface for the task vocabulary.


@router.get("/tasks.csv")
async def export_tasks_csv(
    status: Optional[str] = Query(None),
    vehicle: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """CSV export of maintenance tasks for the operator's DOT audit
    binder, fleet review, or spreadsheet analysis.

    Columns mirror the dashboard table so a tech who's been looking at
    the UI gets the same shape on disk.  Respects the same permission
    scoping as the list route (drivers see only their truck).

    Audit columns (``attested_by_name``, ``attested_at``, ``recur_*``,
    ``spawned_from_id``, ``work_order_id``) are included so the file is
    a self-contained DOT artifact — an auditor reading the CSV can see
    who signed off on each task without needing the dashboard open.
    """
    import csv
    import io
    from fastapi.responses import StreamingResponse

    if not has_maintenance_access(user["role"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    tasks = await tenant_db.get_maintenance_tasks(
        user["account_id"], status=status, vehicle_name=vehicle,
    )
    # Safe-deny: an unassigned driver gets an empty CSV, never the
    # account-wide list.
    scope = await _maintenance_vehicle_scope(user, tenant_db)
    if scope is not None:
        tasks = [t for t in tasks if scope.allows_row(t, name_key="vehicle_name")]

    # Resolve telegram_id → display name once so the CSV shows readable
    # attester names instead of raw user IDs.
    name_map = await _build_user_name_map(user["account_id"], platform_db)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "vehicle_name", "task_type", "priority", "status",
        "description", "due_date", "due_miles", "due_engine_hours",
        "last_odometer", "last_engine_hours",
        "created_at", "completed_at", "company_code",
        "attested_by", "attested_by_name", "attested_at",
        "recur_interval_days", "recur_interval_miles",
        "recur_interval_engine_hours",
        "spawned_from_id", "work_order_id",
        "cost_dollars", "vendor_name",
    ])
    for t in tasks:
        attested_by = t.get("attested_by") or ""
        attested_name = (
            name_map.get(int(attested_by)) if attested_by else ""
        ) or ""
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
            attested_by, attested_name,
            t.get("attested_at", "") or "",
            t.get("recur_interval_days", "") or "",
            t.get("recur_interval_miles", "") or "",
            t.get("recur_interval_engine_hours", "") or "",
            t.get("spawned_from_id", "") or "",
            t.get("work_order_id", "") or "",
            (
                f"{t.get('cost_cents', 0) / 100:.2f}"
                if isinstance(t.get("cost_cents"), int) else ""
            ),
            t.get("vendor_name", "") or "",
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

    from capabilities.activity_trail import record_simple
    await record_simple(
        tenant_db, user["account_id"], await resolve_user_id(user),
        "dot_binder_export", "account", user["account_id"],
        note=f"days={days} vehicle={vehicle or 'all'} bytes={len(pdf_bytes)}",
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


@router.get("/due-locations")
async def maintenance_due_locations(
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Per-vehicle aggregate of pending / overdue maintenance tasks for
    the Fleet persona's Live Map overlay (``MaintenanceMarkersLayer``).

    Returns one row per vehicle that has at least one pending or
    overdue task — empty array when the fleet is fully caught up.  The
    frontend joins this against the current vehicle positions
    (already loaded for the base map) to drop wrench icons on trucks
    that need shop time, so the overlay doesn't need lat/lon plumbed
    server-side.

    ``can_maintenance_vehicle`` callers (drivers / per-truck scoping) see
    only their assigned vehicles' tasks.
    """
    if not has_maintenance_access(user["role"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Pull pending + overdue separately so the response can show the
    # counts side by side (UI may render "2 overdue · 1 pending" in
    # a tooltip).  Both lists are tenant-scoped; no cross-account leak.
    pending_tasks = await tenant_db.get_maintenance_tasks(
        user["account_id"], status="pending",
    )
    overdue_tasks = await tenant_db.get_overdue_tasks(user["account_id"])

    # _own scope: the same identity wall /tasks uses.
    scope = await _maintenance_vehicle_scope(user, tenant_db)
    if scope is not None:
        def _allowed(t: dict) -> bool:
            return scope.allows_row(t, name_key="vehicle_name")
        pending_tasks = [t for t in pending_tasks if _allowed(t)]
        overdue_tasks = [t for t in overdue_tasks if _allowed(t)]

    # Aggregate per (vehicle_id or vehicle_name).  Vehicle_id is the
    # canonical join key (matches the base map's marker id) but some
    # legacy tasks carry only vehicle_name — fall back so we never
    # drop a real task from the overlay.
    agg: dict[str, dict] = {}
    def _bump(t: dict, key: str) -> None:
        vid = t.get("vehicle_id") or ""
        vname = t.get("vehicle_name") or ""
        if not vid and not vname:
            return
        # Prefer vehicle_id as the key when present (stable across renames).
        bucket = vid or vname
        row = agg.setdefault(bucket, {
            "vehicle_id":   vid,
            "vehicle_name": vname,
            "pending_count":  0,
            "overdue_count":  0,
        })
        row[key] = row[key] + 1
        # Backfill the OTHER identity field if we learned it on a later row.
        if vid and not row["vehicle_id"]:
            row["vehicle_id"] = vid
        if vname and not row["vehicle_name"]:
            row["vehicle_name"] = vname

    for t in pending_tasks:
        _bump(t, "pending_count")
    for t in overdue_tasks:
        _bump(t, "overdue_count")

    items = list(agg.values())
    # Sort overdue-first so the busiest trucks come first when the UI
    # iterates the list for marker placement.
    items.sort(
        key=lambda r: (r["overdue_count"], r["pending_count"]),
        reverse=True,
    )
    return {"items": items, "count": len(items)}
