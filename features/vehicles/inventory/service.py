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
