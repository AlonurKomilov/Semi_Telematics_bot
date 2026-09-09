"""Inventory domain reads — the service contract for non-router consumers
(future AI tools, reports, PTI auto-verification hook).

Routers may talk to the mixin directly for writes; anything OUTSIDE this
component reads through here so the data contract has one front door.
"""

from __future__ import annotations

from infra.services import get_tenant_db

from adapters.storage.vehicle_inventory import (  # re-exported contract
    ATTENTION_STATUSES,
    INVENTORY_CATEGORIES,
    INVENTORY_STATUSES,
)

__all__ = [
    "ATTENTION_STATUSES",
    "INVENTORY_CATEGORIES",
    "INVENTORY_STATUSES",
    "get_vehicle_inventory",
    "get_attention_map",
    "item_if_visible",
    "driver_on_truck",
    "verify_item",
    "set_item_status",
]


async def get_vehicle_inventory(account_id: int, vehicle_id: int) -> list[dict]:
    """All active items in one vehicle."""
    tenant = await get_tenant_db(account_id)
    return await tenant.list_vehicle_inventory(account_id, vehicle_id)


async def get_attention_map(account_id: int) -> dict[int, dict]:
    """``vehicle_id → {total, attention}`` across the account — powers the
    fleet-list badge and any future alert/report consumer."""
    tenant = await get_tenant_db(account_id)
    return await tenant.inventory_attention_by_vehicle(account_id)


# ── writes ───────────────────────────────────────────────────────
#
# The dashboard router and the browser extension both reach these.  They
# live here rather than in either router because the two must never
# diverge: the extension gained the verify-and-flag verbs on 2026-09-09,
# and a second copy of the company wall or the driver snapshot is a
# second chance to forget one.


async def item_if_visible(
    account_id: int, item_id: int, allowed_companies,
) -> dict | None:
    """One item, or None when it does not exist OR belongs to a company
    this caller may not see.

    Both answer None on purpose: a 403 on a foreign item would confirm
    the id belongs to somebody, which is what the wall exists to hide.
    The caller turns None into its own 404.
    """
    tenant = await get_tenant_db(account_id)
    item = await tenant.get_inventory_item(account_id, item_id)
    if item is None:
        return None
    vehicle = await tenant.get_vehicle(account_id, int(item["vehicle_id"]))
    if (
        vehicle is not None
        and allowed_companies
        and vehicle.company_code
        and vehicle.company_code not in allowed_companies
    ):
        return None
    return item


async def driver_on_truck(account_id: int, vehicle_id: int) -> int | None:
    """Who has the truck right now — stamped onto the event, because the
    trail answers "who was accountable", not just "who typed"."""
    tenant = await get_tenant_db(account_id)
    vehicle = await tenant.get_vehicle(account_id, vehicle_id)
    if vehicle is None:
        return None
    return await tenant.get_assigned_driver_for_truck(
        account_id, vehicle.unit_number,
    )


async def verify_item(account_id: int, item: dict, *, actor_user_id: int | None) -> bool:
    """"I looked, it is here."  The safest write there is: it adds a
    check to the trail and destroys nothing."""
    tenant = await get_tenant_db(account_id)
    return await tenant.verify_inventory_item(
        account_id, int(item["id"]),
        actor_user_id=actor_user_id,
        driver_user_id=await driver_on_truck(account_id, int(item["vehicle_id"])),
    )


async def set_item_status(
    account_id: int, item: dict, status: str, *,
    note: str = "", actor_user_id: int | None = None,
) -> bool:
    """Move an item along its lifecycle, with the reason attached."""
    if status not in INVENTORY_STATUSES:
        raise ValueError(f"status must be one of {INVENTORY_STATUSES}")
    if status == item.get("status"):
        return False
    tenant = await get_tenant_db(account_id)
    return await tenant.change_inventory_status(
        account_id, int(item["id"]), status, note=note,
        actor_user_id=actor_user_id,
        driver_user_id=await driver_on_truck(account_id, int(item["vehicle_id"])),
    )
