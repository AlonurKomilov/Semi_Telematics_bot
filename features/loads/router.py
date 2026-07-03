"""Loads HTTP surface — the "All Loads" screen + manual entry.

router.py is interface-layer code co-located with its domain
(docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.

Visibility: ``can_loads_all`` sees the account's loads; ``can_loads_own``
(drivers) sees only loads linked to the caller's user id.  Writes need the
delegatable ``can_manage_loads``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from capabilities.permissions.roles import Role, get_user_permissions
from features.loads import service
from features.loads.service import load_to_dict
from infra.platform import get_tenant_db as _get_tenant_db
from interfaces.api.deps import require_permission, require_permission_any

router = APIRouter(prefix="/loads", tags=["loads"])

_view_loads = require_permission_any("can_loads_all", "can_loads_own")
_manage_loads = require_permission("can_manage_loads")


async def _scope_driver_id(user: dict) -> int | None:
    """None = account-wide; a user id = own-scope (driver) caller."""
    perms = await get_user_permissions(
        Role(user["role"]), user["account_id"],
        is_manager=bool(user.get("is_manager")),
        is_primary_owner=bool(user.get("is_primary_owner")),
    )
    if getattr(perms, "can_loads_all", False):
        return None
    return int(user["id"])


class LoadCreate(BaseModel):
    load_number: str = Field("", max_length=64)
    status: str = Field("upcoming")
    payment_status: str = Field("", max_length=16)
    customer: str = Field("", max_length=200)
    company_code: str = Field("", max_length=64)
    pickup_location: str = Field("", max_length=200)
    pickup_date: str = Field("", max_length=32)
    delivery_location: str = Field("", max_length=200)
    delivery_date: str = Field("", max_length=32)
    driver_user_id: int | None = None
    driver_name: str = Field("", max_length=120)
    dispatcher_user_id: int | None = None
    dispatcher_name: str = Field("", max_length=120)
    vehicle_unit: str = Field("", max_length=64)
    trailer_unit: str = Field("", max_length=64)
    total_rate: float | None = Field(None, ge=0)
    loaded_miles: float | None = Field(None, ge=0)
    empty_miles: float | None = Field(None, ge=0)
    driver_pay: float | None = Field(None, ge=0)
    other_costs: float | None = Field(None, ge=0)
    notes: str = Field("", max_length=1000)


class LoadUpdate(BaseModel):
    load_number: str | None = Field(None, max_length=64)
    status: str | None = None
    payment_status: str | None = Field(None, max_length=16)
    customer: str | None = Field(None, max_length=200)
    company_code: str | None = Field(None, max_length=64)
    pickup_location: str | None = Field(None, max_length=200)
    pickup_date: str | None = Field(None, max_length=32)
    delivery_location: str | None = Field(None, max_length=200)
    delivery_date: str | None = Field(None, max_length=32)
    driver_user_id: int | None = None
    driver_name: str | None = Field(None, max_length=120)
    dispatcher_user_id: int | None = None
    dispatcher_name: str | None = Field(None, max_length=120)
    vehicle_unit: str | None = Field(None, max_length=64)
    trailer_unit: str | None = Field(None, max_length=64)
    total_rate: float | None = Field(None, ge=0)
    loaded_miles: float | None = Field(None, ge=0)
    empty_miles: float | None = Field(None, ge=0)
    driver_pay: float | None = Field(None, ge=0)
    other_costs: float | None = Field(None, ge=0)
    notes: str | None = Field(None, max_length=1000)


@router.get("/")
async def list_loads(
    status: str | None = None,
    since: str | None = None,
    until: str | None = None,
    user: dict = Depends(_view_loads),
):
    """Loads visible to the caller + the status tab counts."""
    account_id = int(user["account_id"])
    scope = await _scope_driver_id(user)
    loads = await service.get_loads(
        account_id, scope_driver_user_id=scope,
        status=status, since=since, until=until,
    )
    counts = await service.get_load_counts(
        account_id, scope_driver_user_id=scope,
    )
    return {"loads": loads, "counts": counts}


@router.post("/")
async def create_load(
    body: LoadCreate,
    user: dict = Depends(_manage_loads),
):
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    try:
        load_id = await tenant.add_load(account_id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    l = await tenant.get_load(account_id, load_id)
    return load_to_dict(l)


@router.put("/{load_id}")
async def update_load(
    load_id: int,
    body: LoadUpdate,
    user: dict = Depends(_manage_loads),
):
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        changed = await tenant.update_load(account_id, load_id, **fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not changed:
        raise HTTPException(404, "load not found or nothing to update")
    l = await tenant.get_load(account_id, load_id)
    return load_to_dict(l)


@router.delete("/{load_id}")
async def delete_load(
    load_id: int,
    user: dict = Depends(_manage_loads),
):
    """Soft delete — the row leaves the operational tabs; history (KPI,
    reports) keeps counting delivered work."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    if not await tenant.deactivate_load(account_id, load_id):
        raise HTTPException(404, "load not found")
    return {"deleted": True, "id": load_id}


@router.get("/{load_id}")
async def get_load(
    load_id: int,
    user: dict = Depends(_view_loads),
):
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    l = await tenant.get_load(account_id, load_id)
    if l is None:
        raise HTTPException(404, "load not found")
    scope = await _scope_driver_id(user)
    if scope is not None and l.driver_user_id != scope:
        raise HTTPException(404, "load not found")   # own-scope: don't leak
    return load_to_dict(l)
