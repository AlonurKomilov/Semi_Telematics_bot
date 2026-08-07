"""Maintenance service — Single Source of Truth for overdue task detection.

Business logic only: DB queries + status mutations.
Notification delivery stays in the bot interface layer
(via the NotificationSender port or direct bot send).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from capabilities.permissions.roles import can
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
    return can(r, "can_maintenance_all") or can(r, "can_maintenance_vehicle")


def has_work_orders_access(role: str) -> bool:
    """Return True if *role* grants any work-order permission.

    Work orders are gated independently of maintenance via the
    ``can_work_orders_*`` flags — defaults mirror maintenance, but an
    account can grant/revoke them separately in the Role Permissions matrix.
    """
    try:
        r = Role(role) if not isinstance(role, Role) else role
    except ValueError:
        return False
    return can(r, "can_work_orders_all") or can(r, "can_work_orders_vehicle")


# ── Due-soon classifier (shared between API stats + scheduler) ─────
#
# Same thresholds the dashboard's chip filter uses in
# ``interfaces/dashboard/src/pages/maintenance/Tasks.tsx``.  Keeping
# the Python and TS classifiers literally identical means the
# "Maintenance due 9" badge in the shell header matches the "Due
# Soon 9" chip on the Maintenance page — operators won't see two
# different counts for the same concept.

DUE_SOON_DAYS = 7
DUE_SOON_MILES = 5_000
DUE_SOON_HOURS = 100

# A big service needs more warning than a small one.
#
# A flat 5,000 miles is ~10 days of driving: fine for an oil change you
# can book anywhere, thin for a 200,000-mile transmission service that
# needs parts ordered, a bay booked and the truck off the road.  The
# window therefore scales with the INTERVAL and never falls below the
# flat floor:
#
#     30,000 mi oil change   -> max(5,000,  1,500) =  5,000   unchanged
#    100,000 mi service      -> max(5,000,  5,000) =  5,000   unchanged
#    200,000 mi transmission -> max(5,000, 10,000) = 10,000   ~3 weeks
#
# Only LONG intervals move, so nothing that was due-soon yesterday stops
# being due-soon today.
#
# MIRRORED in interfaces/dashboard/src/features/maintenance/
# useMaintenanceTasks.ts — two implementations of one rule, pinned in
# sync by tests/test_maintenance_due_soon_parity.py.
DUE_SOON_INTERVAL_FRACTION = 0.05


def due_soon_miles_for(interval: Optional[float]) -> float:
    """The mileage window for a task on this recur interval."""
    return max(DUE_SOON_MILES, (interval or 0) * DUE_SOON_INTERVAL_FRACTION)


def due_soon_hours_for(interval: Optional[float]) -> float:
    """The engine-hours window for a task on this recur interval."""
    return max(DUE_SOON_HOURS, (interval or 0) * DUE_SOON_INTERVAL_FRACTION)


def classify_task_urgency(task: dict) -> Optional[str]:
    """Return ``"overdue"``, ``"due_soon"``, or ``None``.

    Mirrors ``dueSoonClassify`` in [Tasks.tsx].  An ``overdue`` result
    on ANY axis (date / mileage / engine hours) wins — a task that's
    overdue on mileage but not yet on date is still overdue.

    Tasks that are closed (status completed / cancelled) return
    ``None`` regardless of dates, so the cleanup pass in the caller
    doesn't have to special-case them.
    """
    status = (task.get("status") or "").lower()
    if status in ("completed", "cancelled"):
        return None
    if status == "overdue":
        return "overdue"

    # Date axis.
    due_date = task.get("due_date")
    if due_date:
        try:
            from datetime import date as _date, datetime as _dt
            today = _dt.now().date()
            if isinstance(due_date, str):
                d = _dt.fromisoformat(due_date[:10]).date()
            elif isinstance(due_date, _date):
                d = due_date
            else:
                d = None
            if d is not None:
                days = (d - today).days
                if days < 0:
                    return "overdue"
                if days <= DUE_SOON_DAYS:
                    return "due_soon"
        except (TypeError, ValueError):
            pass

    # Mileage axis.
    due_miles = task.get("due_miles")
    last_odo = task.get("last_odometer")
    if due_miles is not None and last_odo is not None:
        try:
            remaining_mi = float(due_miles) - float(last_odo)
            if remaining_mi < 0:
                return "overdue"
            if remaining_mi <= due_soon_miles_for(
                task.get("recur_interval_miles")
            ):
                return "due_soon"
        except (TypeError, ValueError):
            pass

    # Engine-hours axis.
    due_hours = task.get("due_engine_hours")
    last_hours = task.get("last_engine_hours")
    if due_hours is not None and last_hours is not None:
        try:
            remaining_h = float(due_hours) - float(last_hours)
            if remaining_h < 0:
                return "overdue"
            if remaining_h <= due_soon_hours_for(
                task.get("recur_interval_engine_hours")
            ):
                return "due_soon"
        except (TypeError, ValueError):
            pass

    return None


logger = logging.getLogger(__name__)


async def apply_live_readings(tenant_db, account_id: int, tasks: list[dict]) -> None:
    """Merge LIVE odometer / engine-hours from ``vehicle_state`` into task rows.

    Mutates ``tasks`` in place, refreshing ``last_odometer`` /
    ``last_engine_hours`` so that :func:`classify_task_urgency` judges
    against the truck's CURRENT reading — even for tasks the 6-h
    scheduler hasn't touched yet (newly created tasks, or tasks where
    the alerted_at filter strands last_odometer at the value it had
    when the first alert fired).

    Shared by every reader that derives urgency: the dashboard list
    endpoint, the maintenance AI tools, and the AI context snapshot.
    Before this was shared, the AI classified against the stale stored
    odometer and answered "0 overdue" while the dashboard (which had
    the live merge inline) showed the same truck past due.

    Key by ``vehicle_id`` when the task has one (always unique in
    vehicle_state — it's the table's PRIMARY KEY).  Fall back to
    ``(company_code, vehicle_name)`` for legacy tasks that pre-date
    vehicle_id capture.  Keying by ``vehicle_name`` alone was the old
    bug: two companies under the same account can both have a "103",
    and the dict-write last-row-wins would silently merge them and
    show the wrong company's odometer.

    Falls through silently on any warehouse error; each row just keeps
    whatever last_odometer / last_engine_hours was stored.
    """
    if not tasks:
        return
    try:
        # Bulk lookup pulls every state row for the account once; the
        # python-side index then routes each task to its OWN vehicle.
        # One DB query, three index lookups per task.
        state_rows = await tenant_db.get_vehicle_state(account_id)
        by_id: dict[str, dict] = {}
        by_company_name: dict[tuple[str, str], dict] = {}
        # Name-only fallback table — populated ONLY for vehicle names
        # that appear exactly once across the whole account.  Lets us
        # enrich legacy tasks that were created without
        # company_code / vehicle_id without ever colliding into a
        # cross-company match (when a name is ambiguous, the entry
        # is removed and the task stays blank — better empty than
        # wrong).
        name_counts: dict[str, int] = {}
        by_name_unique: dict[str, dict] = {}
        # A state row answers to its REGISTRY unit number as well as to
        # the provider's display name.  Providers edit display names —
        # one truck became "229 Idris Ahmed" — and a task filed under
        # the clean unit number could no longer reach its own vehicle:
        # that is how a truck sat 2,752 miles past service-due while the
        # task showed "pending".  The registry name is the one WE own,
        # so it indexes the same row under both spellings.
        canonical: dict[Any, str] = {}
        try:
            for rv in await tenant_db.list_vehicles(account_id):
                canonical[rv.id] = (rv.unit_number or "").strip()
        except Exception:
            canonical = {}
        for row in state_rows:
            vid = row.get("vehicle_id") or ""
            if vid:
                by_id[vid] = row
            cc = (row.get("company_code") or "").strip()
            names = {(row.get("vehicle_name") or "").strip()}
            canon = canonical.get(row.get("registry_id"))
            if canon:
                names.add(canon)
            names.discard("")
            for nm in names:
                if cc:
                    by_company_name[(cc, nm)] = row
                else:
                    # Legacy rows without company_code can still be
                    # reached via name-only — single-tenant collision
                    # risk accepted only when company_code is empty in
                    # BOTH the task and the state row.
                    by_company_name[("", nm)] = row
                name_counts[nm] = name_counts.get(nm, 0) + 1
                if name_counts[nm] == 1:
                    by_name_unique[nm] = row
                else:
                    by_name_unique.pop(nm, None)
        for t in tasks:
            live = None
            # ``trust_live`` flips on when the lookup is unambiguous
            # (vehicle_id match or company-scoped name match) — at
            # that point the live reading is authoritative for THAT
            # exact vehicle and can be used even if it's lower than
            # the stored value.  This is the recovery path for
            # tasks whose ``last_odometer`` was corrupted by the
            # pre-fix scheduler's name-only cross-company merge.
            trust_live = False
            tid = (t.get("vehicle_id") or "").strip()
            if tid and tid in by_id:
                live = by_id[tid]
                trust_live = True
            else:
                cc = (t.get("company_code") or "").strip()
                nm = (t.get("vehicle_name") or "").strip()
                if nm:
                    live = by_company_name.get((cc, nm))
                    if live is not None:
                        # Scoped by company → authoritative.
                        trust_live = bool(cc)
                    # Final fallback: name-only when unambiguous.
                    # Catches tasks that were created with neither
                    # vehicle_id nor company_code (the SPN
                    # auto-creator + legacy dashboard form did
                    # this).  Skipped when the name resolves to
                    # multiple companies — we'd rather leave the
                    # company chip blank than guess wrong.
                    if live is None:
                        live = by_name_unique.get(nm)
                        trust_live = live is not None
            if not live:
                continue
            # Backfill the task row's identity fields from the
            # matched vehicle_state row so the Company column +
            # future telemetry lookups have something to work with.
            # ``or ""`` guards against None on the state row.
            if not (t.get("company_code") or "").strip():
                t["company_code"] = live.get("company_code") or ""
            if not (t.get("vehicle_id") or "").strip():
                t["vehicle_id"] = live.get("vehicle_id") or ""
            # When ``trust_live`` is set, take the live value
            # unconditionally — it's the current odometer for the
            # uniquely identified vehicle.  Otherwise keep the
            # historical "never go backwards" guard so a transient
            # warehouse blip (returns 0 or stale low value) can't
            # undo a real reading.
            live_odo = live.get("odometer_mi")
            if isinstance(live_odo, (int, float)):
                stored_odo = t.get("last_odometer")
                if (
                    trust_live
                    or stored_odo is None
                    or float(live_odo) > float(stored_odo)
                ):
                    t["last_odometer"] = float(live_odo)
            live_hrs = live.get("engine_hours")
            if isinstance(live_hrs, (int, float)):
                stored_hrs = t.get("last_engine_hours")
                if (
                    trust_live
                    or stored_hrs is None
                    or float(live_hrs) > float(stored_hrs)
                ):
                    t["last_engine_hours"] = float(live_hrs)
    except Exception:
        # Warehouse outage (or a context without vehicle_state, e.g.
        # unit-test fakes) shouldn't break the caller's read.
        pass


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


def _index_state_rows_for_routing(state_rows: list[dict]) -> tuple[
    dict[str, dict],
    dict[tuple[str, str], dict],
    dict[str, dict],
]:
    """Build the three indices that route a task to its OWN vehicle.

    Returns ``(by_id, by_company_name, by_name_unique)`` — same shape
    as the route-time enrichment block in
    ``interfaces/api/routes/maintenance.py``.  Kept here so the
    scheduler and the route share one mental model.

    The name-only fallback contains an entry ONLY for names that
    appear exactly once across the account; ambiguous names get
    removed so the scheduler can't accidentally cross-merge "103" on
    G1 with "103" on OSY (the historical bug that corrupted
    ``last_odometer`` across both tasks every 6 hours).
    """
    by_id: dict[str, dict] = {}
    by_company_name: dict[tuple[str, str], dict] = {}
    name_counts: dict[str, int] = {}
    by_name_unique: dict[str, dict] = {}
    for row in state_rows:
        vid = (row.get("vehicle_id") or "").strip()
        cc = (row.get("company_code") or "").strip()
        nm = (row.get("vehicle_name") or "").strip()
        if vid:
            by_id[vid] = row
        if nm:
            # Always index by (company, name) — empty company tagged
            # explicitly so legacy single-company state rows still
            # match the legacy company-less task rows.
            by_company_name[(cc, nm)] = row
            name_counts[nm] = name_counts.get(nm, 0) + 1
            if name_counts[nm] == 1:
                by_name_unique[nm] = row
            else:
                by_name_unique.pop(nm, None)
    return by_id, by_company_name, by_name_unique


def _route_task_to_state_row(
    task: dict,
    by_id: dict[str, dict],
    by_company_name: dict[tuple[str, str], dict],
    by_name_unique: dict[str, dict],
) -> Optional[dict]:
    """Resolve a task to its single ``vehicle_state`` row.

    Priority — same as the route-time enrichment:
      1. ``vehicle_id`` — unique PRIMARY KEY of vehicle_state.
      2. ``(company_code, vehicle_name)`` — scoped fallback.
      3. ``vehicle_name`` — only when unambiguous across the account.

    Returns ``None`` when the task can't be resolved to exactly one
    state row — better empty than wrong.
    """
    vid = (task.get("vehicle_id") or "").strip()
    if vid and vid in by_id:
        return by_id[vid]
    nm = (task.get("vehicle_name") or "").strip()
    if not nm:
        return None
    cc = (task.get("company_code") or "").strip()
    live = by_company_name.get((cc, nm))
    if live is not None:
        return live
    return by_name_unique.get(nm)


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
    # Samsara fan-out, no rate-limit risk.  Routed through the shared
    # ``_route_task_to_state_row`` helper so two trucks with the same
    # name in different companies (e.g. "103" in G1 + "103" in OSY)
    # don't get cross-merged into a single odometer value the way the
    # pre-fix ``odometer_by_vehicle_name[name] = ...`` lookup did.
    state_rows = await tenant_db.get_vehicle_state(account_id)
    if not state_rows:
        logger.debug(
            "mark_overdue_tasks_by_mileage acct=%d — warehouse has no odometer yet",
            account_id,
        )
        return []
    by_id, by_company_name, by_name_unique = _index_state_rows_for_routing(
        state_rows,
    )

    newly_overdue: list[dict] = []
    odometer_updates: list[tuple[int, float]] = []
    overdue_ids: list[int] = []
    for task in tasks:
        live = _route_task_to_state_row(
            task, by_id, by_company_name, by_name_unique,
        )
        if live is None:
            continue
        miles = live.get("odometer_mi")
        if not isinstance(miles, (int, float)):
            continue
        current_miles = float(miles)
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

    # Shared routing — see ``mark_overdue_tasks_by_mileage`` for the
    # rationale.  Lookup priority is vehicle_id → (company, name) →
    # name-when-unambiguous so cross-company name collisions can't
    # corrupt ``last_engine_hours`` across both tasks every 6 hours.
    state_rows = await tenant_db.get_vehicle_state(account_id)
    if not state_rows:
        logger.debug(
            "mark_overdue_tasks_by_engine_hours acct=%d — warehouse has no engine_hours yet",
            account_id,
        )
        return []
    by_id, by_company_name, by_name_unique = _index_state_rows_for_routing(
        state_rows,
    )

    newly_overdue: list[dict] = []
    hours_updates: list[tuple[int, float]] = []
    overdue_ids: list[int] = []
    for task in tasks:
        live = _route_task_to_state_row(
            task, by_id, by_company_name, by_name_unique,
        )
        if live is None:
            continue
        hrs = live.get("engine_hours")
        if not isinstance(hrs, (int, float)):
            continue
        current = float(hrs)
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
    actor_user_id: Optional[int] = None,
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

    return await tenant_db.spawn_recurring_followup(
        task_id, account_id=account_id, actor_user_id=actor_user_id,
    )


# ── Auto-maintenance from critical fault codes ────────────────────────────────

# J1939 SPN → maintenance task-type mapping (SSOT).
# Moved here from capabilities/alerting/ai_maintenance.py so maintenance
# domain logic is not scattered inside the alerting layer.
# SPN → service-task canonical key.  These predate the service-task
# vocabulary, when everything not oil/brakes had to be "custom" — which
# now resolves to Custom/Other and lands fault-driven spend in the
# report's junk bucket.  Mapped to the real tasks so the spend-by-system
# rollup fills itself from telematics with zero human input.
_SPN_MAINTENANCE_MAP: dict[int, str] = {
    110: "coolant",      # Coolant temp        → Cooling System
    111: "coolant",      # Coolant level       → Cooling System
    100: "oil",          # Oil pressure        → Engine
    101: "oil",          # Oil level           → Engine
    91: "brakes",        # Brake pressure      → Brakes
    97: "fuel_filter",   # Water in fuel       → Fuel System
    # Overspeed is a DRIVER event, not a repair — deliberately kept in
    # Custom/Other (owner decision 2026-07-27) so it never pollutes
    # maintenance spend.
    190: "custom",
    4331: "def_refill",  # DEF quality         → Exhaust & Aftertreatment
    3031: "def_refill",  # DEF level           → Exhaust & Aftertreatment
    5246: "def_refill",  # DEF tank            → Exhaust & Aftertreatment
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
