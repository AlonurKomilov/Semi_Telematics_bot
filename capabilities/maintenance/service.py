"""Maintenance service — Single Source of Truth for overdue task detection.

Business logic only: DB queries + status mutations.
Notification delivery stays in the bot interface layer
(via the NotificationSender port or direct bot send).
"""

from __future__ import annotations

import logging

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


async def mark_overdue_tasks_by_date(account_id: int, tenant_db) -> list[dict]:
    """Mark all pending tasks whose due_date has passed as 'overdue'.

    Returns the list of tasks that were just marked overdue so the caller
    (bot scheduler) can send notifications for each.
    """
    overdue_tasks = await tenant_db.get_pending_tasks_by_date(account_id)
    if not overdue_tasks:
        return []
    # One bulk UPDATE replaces N × per-task UPDATE-then-commit cycles.
    await tenant_db.update_maintenance_status_bulk(
        account_id, [t["id"] for t in overdue_tasks], "overdue",
    )
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
    tasks = await tenant_db.get_pending_tasks_by_miles()
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

    # Two bulk operations replace 2 N per-row UPDATE-then-commit cycles.
    if odometer_updates:
        await tenant_db.update_maintenance_last_odometer_bulk(
            account_id, odometer_updates,
        )
    if overdue_ids:
        await tenant_db.update_maintenance_status_bulk(
            account_id, overdue_ids, "overdue",
        )

    return newly_overdue


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
