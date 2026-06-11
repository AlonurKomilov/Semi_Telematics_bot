"""Maintenance service — Single Source of Truth for overdue task detection.

Business logic only: DB queries + status mutations.
Notification delivery stays in the bot interface layer
(via the NotificationSender port or direct bot send).
"""

from __future__ import annotations

import logging
from typing import Optional

from capabilities.iam.permissions import can
from adapters.storage import Role

# ── Task type registry (SSOT used by both bot and API) ────────────────────────

TASK_TYPES: dict[str, str] = {
    "oil": "🛢 Oil Change",
    "tires": "🛞 Tire Service",
    "brakes": "🔴 Brake Inspection",
    "inspection": "📋 General Inspection",
    "transmission": "⚙️ Transmission",
    "electrical": "⚡ Electrical",
    "dot_inspection": "🏛 DOT Inspection",
    "dpf_regen": "♨️ DPF Regen",
    "def_refill": "💧 DEF Refill",
    "custom": "✏️ Custom",
}


def has_maintenance_access(role: str) -> bool:
    """Return True if *role* grants any maintenance permission."""
    try:
        r = Role(role) if not isinstance(role, Role) else role
    except ValueError:
        return False
    return can(r, "can_maintenance_all") or can(r, "can_maintenance_own")

logger = logging.getLogger(__name__)


async def fetch_current_telemetry_for_vehicle(
    tenant_db,
    account_id: int,
    vehicle_name: str,
) -> tuple[Optional[float], Optional[float]]:
    """Look up the current odometer and engine-hours reading for a vehicle.

    Used by ``add_maintenance_task`` callers (API route, bot wizard,
    fault auto-create) to backfill ``last_odometer`` / ``last_engine_hours``
    at task-creation time so the dashboard's progress bar shows up
    immediately instead of waiting up to 6 hours for the next mileage
    scheduler tick.

    Returns ``(odometer_mi, engine_hours)`` from the ``vehicle_state``
    warehouse row that matches ``vehicle_name`` exactly.  Both fields
    are ``None`` when the truck isn't in the warehouse (no Samsara
    telemetry, name mismatch, or device doesn't report odometer).

    Best-effort: any read failure returns ``(None, None)`` rather than
    propagating — task creation MUST NOT depend on the warehouse being
    available.  The 6-h scheduler will eventually backfill if telemetry
    arrives later.
    """
    if not vehicle_name:
        return (None, None)
    try:
        rows = await tenant_db.get_vehicle_state(
            account_id, vehicle_nums=[vehicle_name],
        )
    except Exception as e:
        logger.debug(
            "fetch_current_telemetry_for_vehicle(acct=%d, name=%r) failed: %s",
            account_id, vehicle_name, e,
        )
        return (None, None)
    if not rows:
        return (None, None)
    row = rows[0]
    odo = row.get("odometer_mi")
    hrs = row.get("engine_hours")
    return (
        float(odo) if isinstance(odo, (int, float)) else None,
        float(hrs) if isinstance(hrs, (int, float)) else None,
    )


async def mark_overdue_tasks_by_date(account_id: int, tenant_db) -> list[dict]:
    """Mark all pending tasks whose due_date has passed as 'overdue'.

    Returns the list of tasks that were just marked overdue so the caller
    (bot scheduler) can send notifications for each.

    Throttling: ``get_pending_tasks_by_date`` already filters to
    ``alerted_at IS NULL``, and we stamp ``alerted_at`` here in the same
    transaction.  Net effect — each task notifies exactly once per
    crossing.  Recurring follow-up tasks spawn with ``alerted_at = NULL``
    so they alert fresh on their own crossing.
    """
    overdue_tasks = await tenant_db.get_pending_tasks_by_date(account_id)
    if not overdue_tasks:
        return []
    overdue_ids = [t["id"] for t in overdue_tasks]
    # Two bulk operations: flip status + stamp alerted_at. Both touch the
    # same rows so we could fold into one UPDATE, but keeping them
    # separate lets ``mark_tasks_alerted_bulk`` be reused from elsewhere
    # (e.g. one-off "send a reminder for this task" code paths).
    await tenant_db.update_maintenance_status_bulk(
        account_id, overdue_ids, "overdue",
    )
    await tenant_db.mark_tasks_alerted_bulk(account_id, overdue_ids)
    return list(overdue_tasks)


async def mark_overdue_tasks_by_mileage(
    account_id: int,
    tenant_db,
) -> list[dict]:
    """Mark pending mileage-based tasks as 'overdue' when current odometer >= due_miles.

    Reads current odometer directly from the ``vehicle_state`` warehouse
    table (single source of truth) — bypasses the WAREHOUSE_READS_ENABLED
    cutover flag because this signal lives only in the warehouse.
    ``ingest_vehicle_state`` (every 60s) keeps it fresh; this scheduled
    check runs every 6h so freshness is plenty.

    Also updates ``last_odometer`` on each task for progress tracking.
    Returns the list of tasks newly marked overdue so the caller can
    push a notification.
    """
    tasks = await tenant_db.get_pending_tasks_by_miles(account_id)
    if not tasks:
        return []

    # Single warehouse read for the whole account — no per-company
    # Samsara fan-out, no rate-limit risk.
    state_rows = await tenant_db.get_vehicle_state(account_id)
    odometer_by_vehicle_name: dict[str, float] = {}
    for row in state_rows:
        name = row.get("vehicle_name") or ""
        miles = row.get("odometer_mi")
        if name and isinstance(miles, (int, float)):
            odometer_by_vehicle_name[name] = float(miles)

    if not odometer_by_vehicle_name:
        logger.debug(
            "mark_overdue_tasks_by_mileage acct=%d — warehouse has no odometer yet",
            account_id,
        )
        return []

    newly_overdue: list[dict] = []
    odometer_updates: list[tuple[int, float]] = []
    overdue_ids: list[int] = []
    for task in tasks:
        current_miles = odometer_by_vehicle_name.get(task["vehicle_name"])
        if current_miles is None:
            continue
        due_miles = task["due_miles"]

        odometer_updates.append((int(task["id"]), round(current_miles, 1)))

        if current_miles >= due_miles:
            overdue_ids.append(int(task["id"]))
            task["_current_miles"] = round(current_miles, 1)
            newly_overdue.append(task)

    # Three bulk operations replace 3 N per-row UPDATE-then-commit cycles.
    # Marking ``alerted_at`` here (alongside the status flip) ensures the
    # 6-h scheduler doesn't re-notify the same crossing forever; see the
    # mark_overdue_tasks_by_date docstring for the throttling rationale.
    if odometer_updates:
        await tenant_db.update_maintenance_last_odometer_bulk(
            account_id, odometer_updates,
        )
    if overdue_ids:
        await tenant_db.update_maintenance_status_bulk(
            account_id, overdue_ids, "overdue",
        )
        await tenant_db.mark_tasks_alerted_bulk(account_id, overdue_ids)

    return newly_overdue


# ── Engine-hours overdue scheduler ───────────────────────────────────────────


async def mark_overdue_tasks_by_engine_hours(
    account_id: int,
    tenant_db,
) -> list[dict]:
    """Engine-hours twin of ``mark_overdue_tasks_by_mileage``.

    Reads each vehicle's current engine_hours from the ``vehicle_state``
    warehouse table (same path the mileage scheduler uses), then flips
    any pending engine-hours task whose threshold has been crossed to
    'overdue'.  Updates ``last_engine_hours`` for progress tracking on
    all matching tasks (not just the newly-overdue ones).

    Why a separate scheduler (not folded into the mileage one): keeps
    the early-out "no tasks in this dimension" branch cheap and isolates
    failures — if engine_hours readings are stale for some accounts, we
    don't poison the mileage path's accuracy.
    """
    tasks = await tenant_db.get_pending_tasks_by_engine_hours(account_id)
    if not tasks:
        return []

    state_rows = await tenant_db.get_vehicle_state(account_id)
    hours_by_vehicle: dict[str, float] = {}
    for row in state_rows:
        name = row.get("vehicle_name") or ""
        hrs = row.get("engine_hours")
        if name and isinstance(hrs, (int, float)):
            hours_by_vehicle[name] = float(hrs)

    if not hours_by_vehicle:
        logger.debug(
            "mark_overdue_tasks_by_engine_hours acct=%d — warehouse has no engine_hours yet",
            account_id,
        )
        return []

    newly_overdue: list[dict] = []
    hours_updates: list[tuple[int, float]] = []
    overdue_ids: list[int] = []
    for task in tasks:
        current = hours_by_vehicle.get(task["vehicle_name"])
        if current is None:
            continue
        due = task["due_engine_hours"]

        hours_updates.append((int(task["id"]), round(current, 1)))

        if current >= due:
            overdue_ids.append(int(task["id"]))
            task["_current_engine_hours"] = round(current, 1)
            newly_overdue.append(task)

    if hours_updates:
        await tenant_db.update_maintenance_last_engine_hours_bulk(
            account_id, hours_updates,
        )
    if overdue_ids:
        await tenant_db.update_maintenance_status_bulk(
            account_id, overdue_ids, "overdue",
        )
        await tenant_db.mark_tasks_alerted_bulk(account_id, overdue_ids)

    return newly_overdue


# ── Pre-overdue warning detector ─────────────────────────────────────────────


async def detect_upcoming_warnings(
    account_id: int,
    tenant_db,
    days_ahead: int = 7,
    miles_ahead: float = 500.0,
    engine_hours_ahead: float = 50.0,
) -> list[dict]:
    """Find tasks approaching their threshold but not yet overdue.

    Returns the tasks (without mutating them); the caller is expected
    to fan out a "due in 7 days / 500 mi" notification and then call
    ``mark_tasks_warned_bulk`` with the IDs so the warning doesn't fire
    every scheduler tick.

    Default thresholds chosen to match common shop-scheduling lead times:
      * 7 days  — covers a normal scheduling-and-appointment window
      * 500 mi  — about 6-8 hours of highway driving (one shift)
      * 50 hrs  — about a week of normal duty-cycle engine time

    These are configurable so an account with very tight or very loose
    intervals can override at the scheduler-job level without touching
    business logic here.
    """
    tasks = await tenant_db.get_pending_tasks_for_warning(
        account_id,
        days_ahead=days_ahead,
        miles_ahead=miles_ahead,
        engine_hours_ahead=engine_hours_ahead,
    )
    return list(tasks)


# ── Recurring task auto-spawn ────────────────────────────────────────────────


# Task types whose recurrence cadence is regulated by federal/state
# compliance rather than wear.  When a completed task has one of these
# types AND no explicit ``recur_interval_*`` set, we default the next
# instance to the regulatory interval so the operator can't forget to
# schedule it.  Today only annual DOT inspections (49 CFR § 396.17);
# extend this map for other compliance windows (e.g. IRP/IFTA filings,
# state safety stickers) as they're added.
COMPLIANCE_DEFAULT_INTERVAL_DAYS: dict[str, int] = {
    "dot_inspection": 365,
}


async def spawn_recurring_if_completed(
    task_id: int, account_id: int, new_status: str, tenant_db,
) -> Optional[int]:
    """Auto-create the next instance of a recurring task on completion.

    Called from any status-mutation entry point (API route, bot wizard,
    AI tool) immediately after the status flip succeeds.  Returns the
    newly-spawned task ID or ``None`` if no follow-up was needed
    (status != 'completed', or the parent task has no recurrence
    interval set AND no compliance default applies).

    Why this lives in the service layer (not the adapter):
    the adapter's ``update_maintenance_status`` is called from bulk paths
    too (scheduler marking dozens of tasks 'overdue' at once) where
    spawning a child per row would be wrong.  Putting the spawn here
    means only the *user-driven* completion path triggers it.

    Accepts both ``"completed"`` (API + dashboard surface) and ``"done"``
    (bot surface) as terminal states — see the adapter docstring for the
    historical schism.

    Compliance auto-renewal: if the parent's ``task_type`` is in
    ``COMPLIANCE_DEFAULT_INTERVAL_DAYS`` and the user didn't set their
    own ``recur_interval_days``, we patch the parent with the regulatory
    default before calling the adapter's spawn.  This means a one-off
    "dot_inspection" task ALSO spawns a 365-day follow-up — the user
    isn't required to remember the compliance cadence.
    """
    if new_status not in ("completed", "done"):
        return None

    # Compliance auto-renewal — only kicks in when the user didn't
    # already specify a recurrence, so a manually-set interval (e.g.
    # 180 days for a stricter internal policy) wins over the default.
    parent = await tenant_db.get_maintenance_task(task_id, account_id=account_id)
    if parent:
        task_type = parent.get("task_type") or ""
        if (task_type in COMPLIANCE_DEFAULT_INTERVAL_DAYS
                and not parent.get("recur_interval_days")
                and not parent.get("recur_interval_miles")
                and not parent.get("recur_interval_engine_hours")):
            await tenant_db.update_maintenance_task(
                task_id, account_id=account_id,
                recur_interval_days=COMPLIANCE_DEFAULT_INTERVAL_DAYS[task_type],
            )
            logger.info(
                "Compliance auto-renewal: patched task %d (%s) with %d-day default interval",
                task_id, task_type, COMPLIANCE_DEFAULT_INTERVAL_DAYS[task_type],
            )

    return await tenant_db.spawn_recurring_followup(task_id, account_id=account_id)


# ── Auto-maintenance from critical fault codes ────────────────────────────────

# J1939 SPN → maintenance task-type mapping (SSOT).
# Moved here from capabilities/alerting/ai_maintenance.py so maintenance
# domain logic is not scattered inside the alerting layer.
_SPN_MAINTENANCE_MAP: dict[int, str] = {
    110: "custom",   # Coolant temp
    111: "custom",   # Coolant level
    100: "oil",      # Oil pressure
    101: "oil",      # Oil level
    91: "brakes",    # Brake pressure
    97: "custom",    # Water in fuel
    190: "custom",   # Engine overspeed
    4331: "custom",  # DEF quality
    3031: "custom",  # DEF level
    5246: "custom",  # DEF tank
}

_SPN_DESCRIPTIONS: dict[int, str] = {
    110: "Coolant temperature issue",
    111: "Coolant level issue",
    100: "Engine oil pressure issue",
    101: "Engine oil level issue",
    91: "Brake system pressure issue",
    97: "Water-in-fuel detected",
    190: "Engine overspeed event",
    4331: "DEF quality issue",
    3031: "DEF level low",
    5246: "DEF tank issue",
}


async def auto_create_maintenance_from_faults(
    account_id: int, vehicle_name: str, dtcs: list[dict],
) -> None:
    """Auto-create maintenance tasks from critical fault codes.

    Only creates a task if one doesn't already exist (pending/overdue)
    for the same vehicle and task type.
    """
    from infra.services import get_tenant_db  # local import avoids circular deps

    try:
        tenant = await get_tenant_db(account_id)
        existing = await tenant.get_maintenance_tasks(account_id, vehicle_name=vehicle_name)
        existing_types = {
            (t["vehicle_name"], t["task_type"])
            for t in existing
            if t["status"] in ("pending", "overdue")
        }

        for dtc in dtcs:
            spn = dtc.get("spnId")
            if spn not in _SPN_MAINTENANCE_MAP:
                continue

            task_type = _SPN_MAINTENANCE_MAP[spn]
            if (vehicle_name, task_type) in existing_types:
                continue  # already has a pending task

            desc = _SPN_DESCRIPTIONS.get(spn, f"Auto-created from SPN {spn}")
            fmi_desc = dtc.get("fmiDescription", "")
            if fmi_desc:
                desc += f" ({fmi_desc})"

            await tenant.add_maintenance_task(
                account_id=account_id,
                company_code="",
                vehicle_name=vehicle_name,
                task_type=task_type,
                description=f"🤖 Auto-created: {desc}",
                created_by=0,  # system-generated
            )
            existing_types.add((vehicle_name, task_type))
            logger.info("Auto-maintenance: %s → %s (SPN %s)", vehicle_name, task_type, spn)
    except Exception as e:
        logger.error("Auto-maintenance creation failed: %s", e)
