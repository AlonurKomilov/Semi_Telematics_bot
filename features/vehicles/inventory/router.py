"""Onboard Inventory API — a component of the Vehicle feature.

router.py is interface-layer code co-located with its feature — only
router.py may import interfaces.api.deps.

Routes (mounted under the same /vehicles prefix as the parent feature):

    GET    /vehicles/{vehicle_name}/inventory        items + summary
    POST   /vehicles/{vehicle_name}/inventory        add item
    GET    /vehicles/inventory/alerts                fleet badge counts
    PATCH  /vehicles/inventory/{item_id}             edit fields / status
    POST   /vehicles/inventory/{item_id}/verify      stamp verified-by/at
    POST   /vehicles/inventory/{item_id}/transfer    move to another truck
    POST   /vehicles/inventory/{item_id}/remove      soft-remove (trail kept)
    GET    /vehicles/inventory/{item_id}/events      accountability trail

Gates: VIEW rides normal vehicle access; WRITE rides can_manage_vehicles
(the registry-admin permission — inventory is part of "manage vehicles").
Every write snapshots the driver assigned to the truck AT THAT MOMENT
onto the event row — the accountability answer is captured when the
event happens, not reconstructed later.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adapters.storage.vehicle_inventory import (
    INVENTORY_CATEGORIES,
    normalize_inventory_category,
    INVENTORY_STATUSES,
)
from interfaces.api.deps import (
    get_platform_db,
    get_tenant_db,
    get_user_company_codes,
    require_permission,
    require_permission_any,
    resolve_user_id,
)

router = APIRouter(prefix="/vehicles", tags=["vehicle-inventory"])

_VIEW = require_permission_any("can_vehicle_all", "can_vehicle_vehicle")
_MANAGE = require_permission("can_manage_vehicles")


# ── helpers ──────────────────────────────────────────────────────

async def _resolve_vehicle(
    tenant, user: dict, vehicle_name: str, company: str | None = None,
) -> dict:
    """Registry row for a unit number, company-scope enforced (a user
    restricted to company A never sees company B's truck — 404, not 403,
    so existence isn't leaked)."""
    account_id = int(user["account_id"])
    vehicle = await tenant.get_vehicle_by_unit(
        account_id, vehicle_name, company_code=company,
    )
    if vehicle is None:
        raise HTTPException(404, "Vehicle not found in the registry")
    allowed = await get_user_company_codes(user)
    if allowed and vehicle["company_code"] and vehicle["company_code"] not in allowed:
        raise HTTPException(404, "Vehicle not found in the registry")
    return vehicle


async def _item_or_404(tenant, user: dict, item_id: int) -> dict:
    account_id = int(user["account_id"])
    item = await tenant.get_inventory_item(account_id, item_id)
    if item is None:
        raise HTTPException(404, "Inventory item not found")
    vehicle = await tenant.get_vehicle(account_id, int(item["vehicle_id"]))
    allowed = await get_user_company_codes(user)
    if vehicle is not None and allowed and vehicle.company_code and vehicle.company_code not in allowed:
        raise HTTPException(404, "Inventory item not found")
    return item


async def _driver_snapshot(tenant, account_id: int, vehicle_id: int) -> int | None:
    """Driver assigned to the item's truck right now — stamped onto the
    event for accountability."""
    vehicle = await tenant.get_vehicle(account_id, vehicle_id)
    if vehicle is None:
        return None
    return await tenant.get_assigned_driver_for_truck(
        account_id, vehicle.unit_number,
    )


def _summary(items: list[dict]) -> dict:
    from adapters.storage.vehicle_inventory import ATTENTION_STATUSES
    attention = [i for i in items if i["status"] in ATTENTION_STATUSES]
    return {"total": len(items), "attention": len(attention)}


# ── vehicle-scoped routes ────────────────────────────────────────

@router.get("/{vehicle_name}/inventory")
async def vehicle_inventory(
    vehicle_name: str,
    company: str | None = None,
    user: dict = Depends(_VIEW),
    tenant=Depends(get_tenant_db),
):
    vehicle = await _resolve_vehicle(tenant, user, vehicle_name, company)
    items = await tenant.list_vehicle_inventory(
        int(user["account_id"]), int(vehicle["id"]),
    )
    return {
        "vehicle_id": vehicle["id"],
        "items": items,
        "summary": _summary(items),
        "categories": await tenant.list_inventory_categories(int(user["account_id"])),
        "statuses": list(INVENTORY_STATUSES),
    }


class AddItemBody(BaseModel):
    category: str
    label: str = Field(..., min_length=1, max_length=120)
    identifier: str = Field("", max_length=120)
    notes: str = Field("", max_length=1000)
    company: str | None = None


@router.post("/{vehicle_name}/inventory")
async def add_item(
    vehicle_name: str,
    body: AddItemBody,
    user: dict = Depends(_MANAGE),
    tenant=Depends(get_tenant_db),
):
    # Category is an OPEN vocabulary — custom values are welcomed and
    # normalized ("Safety Equipment" -> safety_equipment); STATUS stays
    # the fixed lifecycle enum.
    category = normalize_inventory_category(body.category)
    account_id = int(user["account_id"])
    vehicle = await _resolve_vehicle(tenant, user, vehicle_name, body.company)
    driver_id = await _driver_snapshot(tenant, account_id, int(vehicle["id"]))
    item_id = await tenant.add_inventory_item(
        account_id, int(vehicle["id"]),
        category=category, label=body.label,
        identifier=body.identifier, notes=body.notes,
        actor_user_id=await resolve_user_id(user), driver_user_id=driver_id,
    )
    return {"ok": True, "item_id": item_id}


# ── fleet badge ──────────────────────────────────────────────────

@router.get("/inventory/alerts")
async def inventory_alerts(
    user: dict = Depends(_VIEW),
    tenant=Depends(get_tenant_db),
):
    """``vehicle_id → {total, attention}`` for the fleet-list badge —
    the list shows a cue ONLY where something needs attention."""
    return {
        "by_vehicle": await tenant.inventory_attention_by_vehicle(
            int(user["account_id"]),
        ),
    }


@router.get("/inventory/all")
async def inventory_all(
    user: dict = Depends(_VIEW),
    tenant=Depends(get_tenant_db),
):
    """Every active item across the fleet (joined with truck unit +
    company) — the account-wide Inventory page.  Company-restricted users
    see only their companies' trucks."""
    rows = await tenant.list_account_inventory(int(user["account_id"]))
    allowed = await get_user_company_codes(user)
    if allowed:
        rows = [r for r in rows if not r["company_code"] or r["company_code"] in allowed]
    return {
        "items": rows,
        "categories": await tenant.list_inventory_categories(int(user["account_id"])),
        "statuses": list(INVENTORY_STATUSES),
    }


# ── item routes ──────────────────────────────────────────────────

class PatchItemBody(BaseModel):
    label: str | None = Field(None, max_length=120)
    identifier: str | None = Field(None, max_length=120)
    notes: str | None = Field(None, max_length=1000)
    category: str | None = None
    status: str | None = None
    note: str = Field("", max_length=500)  # reason attached to a status change


@router.patch("/inventory/{item_id}")
async def patch_item(
    item_id: int,
    body: PatchItemBody,
    user: dict = Depends(_MANAGE),
    tenant=Depends(get_tenant_db),
):
    if body.category is not None:
        body.category = normalize_inventory_category(body.category)
    if body.status is not None and body.status not in INVENTORY_STATUSES:
        raise HTTPException(400, f"status must be one of {INVENTORY_STATUSES}")
    account_id = int(user["account_id"])
    item = await _item_or_404(tenant, user, item_id)
    driver_id = await _driver_snapshot(tenant, account_id, int(item["vehicle_id"]))
    actor = await resolve_user_id(user)

    changed = False
    if any(v is not None for v in (body.label, body.identifier, body.notes, body.category)):
        changed |= await tenant.update_inventory_item(
            account_id, item_id,
            label=body.label, identifier=body.identifier,
            notes=body.notes, category=body.category,
            actor_user_id=actor, driver_user_id=driver_id,
        )
    if body.status is not None and body.status != item["status"]:
        changed |= await tenant.change_inventory_status(
            account_id, item_id, body.status, note=body.note,
            actor_user_id=actor, driver_user_id=driver_id,
        )
    return {"ok": True, "changed": changed}


@router.post("/inventory/{item_id}/verify")
async def verify_item(
    item_id: int,
    user: dict = Depends(_MANAGE),
    tenant=Depends(get_tenant_db),
):
    account_id = int(user["account_id"])
    item = await _item_or_404(tenant, user, item_id)
    driver_id = await _driver_snapshot(tenant, account_id, int(item["vehicle_id"]))
    ok = await tenant.verify_inventory_item(
        account_id, item_id,
        actor_user_id=await resolve_user_id(user), driver_user_id=driver_id,
    )
    return {"ok": ok}


class TransferBody(BaseModel):
    to_vehicle_name: str = Field(..., min_length=1)
    company: str | None = None
    note: str = Field("", max_length=500)


@router.post("/inventory/{item_id}/transfer")
async def transfer_item(
    item_id: int,
    body: TransferBody,
    user: dict = Depends(_MANAGE),
    tenant=Depends(get_tenant_db),
):
    account_id = int(user["account_id"])
    item = await _item_or_404(tenant, user, item_id)
    target = await _resolve_vehicle(tenant, user, body.to_vehicle_name, body.company)
    if int(target["id"]) == int(item["vehicle_id"]):
        raise HTTPException(400, "Item is already on that vehicle")
    # Snapshot the driver of the truck the item is LEAVING — that's who
    # is accountable for handing it over.
    driver_id = await _driver_snapshot(tenant, account_id, int(item["vehicle_id"]))
    ok = await tenant.transfer_inventory_item(
        account_id, item_id, int(target["id"]), note=body.note,
        actor_user_id=await resolve_user_id(user), driver_user_id=driver_id,
    )
    return {"ok": ok, "to_vehicle_id": target["id"]}


class RemoveBody(BaseModel):
    note: str = Field("", max_length=500)


@router.post("/inventory/{item_id}/remove")
async def remove_item(
    item_id: int,
    body: RemoveBody,
    user: dict = Depends(_MANAGE),
    tenant=Depends(get_tenant_db),
):
    account_id = int(user["account_id"])
    item = await _item_or_404(tenant, user, item_id)
    driver_id = await _driver_snapshot(tenant, account_id, int(item["vehicle_id"]))
    ok = await tenant.remove_inventory_item(
        account_id, item_id, note=body.note,
        actor_user_id=await resolve_user_id(user), driver_user_id=driver_id,
    )
    return {"ok": ok}


@router.get("/inventory/{item_id}/events")
async def item_events(
    item_id: int,
    user: dict = Depends(_VIEW),
    tenant=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """The accountability trail, with actor/driver names resolved for
    display (ids stay in the payload for exactness)."""
    account_id = int(user["account_id"])
    await _item_or_404(tenant, user, item_id)
    events = await tenant.list_inventory_events(account_id, item_id)

    # Resolve names once per distinct user id (small N).
    ids = {e[k] for e in events for k in ("actor_user_id", "driver_user_id") if e.get(k)}
    names: dict[int, str] = {}
    for uid in ids:
        u = await platform_db.get_user(int(uid))
        if u is not None:
            # ``display_name`` is the User model's name field (there is no
            # full_name/username); fall back to email, then a stable ref —
            # never a bare "user 3", which reads as a bug to an operator.
            names[int(uid)] = (
                (u.display_name or "").strip()
                or (u.email or "").strip()
                or f"#{uid}"
            )
    for e in events:
        e["actor_name"] = names.get(e.get("actor_user_id") or 0, "")
        e["driver_name"] = names.get(e.get("driver_user_id") or 0, "")
    return {"events": events}
