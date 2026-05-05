"""Maintenance service — Single Source of Truth for overdue task detection.

Business logic only: DB queries + status mutations.
Notification delivery stays in the bot interface layer
(via the NotificationSender port or direct bot send).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from constants import METERS_PER_MILE
from infra.services import get_client
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
    newly_overdue: list[dict] = []
    for task in overdue_tasks:
        await tenant_db.update_maintenance_status(
            task["id"], "overdue", account_id=account_id,
        )
        newly_overdue.append(task)
    return newly_overdue


async def mark_overdue_tasks_by_mileage(
    account_id: int,
    tenant_db,
) -> list[dict]:
    """Mark pending mileage-based tasks as 'overdue' when current odometer >= due_miles.

    Also updates last_odometer on each task for progress tracking.
    Returns list of tasks newly marked overdue so the caller can notify.
    """
    tasks = await tenant_db.get_pending_tasks_by_miles()
    if not tasks:
        return []

    # Group by company_code to minimise Samsara API calls
    by_company: dict[str, list[dict]] = {}
    for task in tasks:
        co = task.get("company_code", "")
        if co:
            by_company.setdefault(co, []).append(task)

    newly_overdue: list[dict] = []

    for company_code, company_tasks in by_company.items():
        try:
            client = await get_client(account_id)
        except Exception:
            logger.debug("Could not get Samsara client for account %d", account_id)
            continue

        fleet = await client.get_fleet_overview(company=company_code)
        name_to_id = {v["name"]: v["id"] for v in fleet}

        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=12)
        try:
            raw = await client._get_paginated_history(
                "obdOdometerMeters", start, end=end,
            )
        except Exception as e:
            logger.debug("Odometer fetch failed for %s: %s", company_code, e)
            continue

        for task in company_tasks:
            vid = name_to_id.get(task["vehicle_name"], "")
            if not vid or vid not in raw:
                continue
            points = raw[vid].get("obdOdometerMeters", [])
            if not points:
                continue

            current_miles = round(points[-1].get("value", 0) / METERS_PER_MILE, 1)
            due_miles = task["due_miles"]

            await tenant_db.update_maintenance_task(
                task["id"], account_id=account_id,
                last_odometer=current_miles,
            )

            if current_miles >= due_miles:
                await tenant_db.update_maintenance_status(
                    task["id"], "overdue", account_id=account_id,
                )
                task["_current_miles"] = current_miles  # attach for notification text
                newly_overdue.append(task)

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
