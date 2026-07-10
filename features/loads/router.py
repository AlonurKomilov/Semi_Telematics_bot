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
from interfaces.api.deps import (
    get_user_company_codes, require_permission, require_permission_any,
)

router = APIRouter(prefix="/loads", tags=["loads"])

_view_loads = require_permission_any("can_loads_all", "can_loads_own")
_manage_loads = require_permission("can_manage_loads")

# Rows returned to the list screen per request.  The response carries a
# ``truncated`` flag when the account has more — never a silent cap.
LIST_CAP = 500


async def _user_perms(user: dict):
    return await get_user_permissions(
        Role(user["role"]), user["account_id"],
        is_manager=bool(user.get("is_manager")),
        is_primary_owner=bool(user.get("is_primary_owner")),
    )


async def _scope_driver_id(user: dict) -> int | None:
    """None = account-wide; a user id = own-scope (driver) caller."""
    perms = await _user_perms(user)
    if getattr(perms, "can_loads_all", False):
        return None
    return int(user["id"])


async def _is_manage_all(user: dict) -> bool:
    return bool(getattr(await _user_perms(user), "can_loads_manage_all", False))


async def _can_write_load(user: dict, load) -> bool:
    """A ``can_manage_loads`` holder may write a load either because they
    manage ALL loads (owner/admin/dispatch-manager) or because they OWN it
    (dispatcher_user_id == self).  An unassigned load (no dispatcher) is
    manager-only — a base dispatcher claims a load by being assigned to it,
    never by editing an orphan."""
    if await _is_manage_all(user):
        return True
    return (
        load.dispatcher_user_id is not None
        and load.dispatcher_user_id == int(user["id"])
    )


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
    allowed = await get_user_company_codes(user)
    loads = await service.get_loads(
        account_id, scope_driver_user_id=scope,
        status=status, since=since, until=until,
        company_codes=allowed or None,
        limit=LIST_CAP + 1,
    )
    truncated = len(loads) > LIST_CAP
    if truncated:
        loads = loads[:LIST_CAP]
    counts = await service.get_load_counts(
        account_id, scope_driver_user_id=scope,
        company_codes=allowed or None,
    )
    return {"loads": loads, "counts": counts, "truncated": truncated}


@router.post("/")
async def create_load(
    body: LoadCreate,
    user: dict = Depends(_manage_loads),
):
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    fields = body.model_dump()
    perms = await _user_perms(user)
    if not getattr(perms, "can_loads_manage_all", False):
        # Own-scope creator: the new load is stamped to THEM, so they can
        # keep managing it and can't hand it off to another dispatcher.
        uid = int(user["id"])
        fields["dispatcher_user_id"] = uid
        me = await tenant.get_user(uid)
        fields["dispatcher_name"] = (me.display_name if me else "") or ""
    try:
        load_id = await tenant.add_load(account_id, **fields)
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
    existing = await tenant.get_load(account_id, load_id)
    if existing is None:
        raise HTTPException(404, "load not found")
    if not await _can_write_load(user, existing):
        # Another dispatcher's (or an unassigned) load — 404, don't leak.
        raise HTTPException(404, "load not found")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    # An own-scope dispatcher can't reassign a load away from themselves.
    if not await _is_manage_all(user):
        fields.pop("dispatcher_user_id", None)
        fields.pop("dispatcher_name", None)
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
    """Soft delete — the load leaves every read surface (tabs, KPI,
    reports); the row is kept only for audit/undelete.  Deleting a
    delivered load therefore also removes it from past KPI windows —
    deletion is for mistakes and duplicates, not archiving."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    existing = await tenant.get_load(account_id, load_id)
    if existing is None:
        raise HTTPException(404, "load not found")
    if not await _can_write_load(user, existing):
        raise HTTPException(404, "load not found")   # not yours — don't leak
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
    allowed = await get_user_company_codes(user)
    if allowed and l.company_code and l.company_code not in allowed:
        raise HTTPException(404, "load not found")   # company-scope: don't leak
    return load_to_dict(l)


# ── Line items (extra pay & costs: TONU / layover / tolls / …) ────────


class LineItemCreate(BaseModel):
    kind: str = Field(..., max_length=16)
    amount: float = Field(..., gt=0)
    bucket: str | None = Field(None, max_length=16)
    load_id: int | None = None
    driver_user_id: int | None = None
    dispatcher_user_id: int | None = None
    item_date: str = Field("", max_length=32)
    notes: str = Field("", max_length=400)


@router.get("/{load_id}/line-items")
async def list_line_items(
    load_id: int,
    user: dict = Depends(_view_loads),
):
    """Extra pay & costs on one load — same visibility rules as the load."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    l = await tenant.get_load(account_id, load_id)
    if l is None:
        raise HTTPException(404, "load not found")
    scope = await _scope_driver_id(user)
    if scope is not None and l.driver_user_id != scope:
        raise HTTPException(404, "load not found")
    allowed = await get_user_company_codes(user)
    if allowed and l.company_code and l.company_code not in allowed:
        raise HTTPException(404, "load not found")   # company-scope: don't leak
    items = await tenant.list_load_line_items(account_id, load_id=load_id)
    return {"items": [service.line_item_to_dict(i) for i in items]}


@router.post("/line-items")
async def create_line_item(
    body: LineItemCreate,
    user: dict = Depends(_manage_loads),
):
    """Add an extra pay/cost item — on a load, or (layover) on a
    driver + date with the responsible dispatcher attributed."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    dispatcher_user_id = body.dispatcher_user_id
    manage_all = await _is_manage_all(user)
    if body.load_id is not None:
        l = await tenant.get_load(account_id, body.load_id)
        if l is None:
            raise HTTPException(404, "load not found")
        allowed = await get_user_company_codes(user)
        if allowed and l.company_code and l.company_code not in allowed:
            raise HTTPException(404, "load not found")   # company-scope
        # Own-scope: only the load's own dispatcher may add costs to it.
        if not manage_all and not await _can_write_load(user, l):
            raise HTTPException(404, "load not found")
    else:
        # Off-load layover — a base dispatcher can only charge it to
        # THEMSELVES as the responsible dispatcher, never to a peer.
        if not manage_all:
            dispatcher_user_id = int(user["id"])
    try:
        item_id = await tenant.add_load_line_item(
            account_id,
            kind=body.kind,
            amount=body.amount,
            bucket=body.bucket,
            load_id=body.load_id,
            driver_user_id=body.driver_user_id,
            dispatcher_user_id=dispatcher_user_id,
            item_date=body.item_date,
            notes=body.notes,
            created_by=int(user["sub"]),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": item_id}


@router.delete("/line-items/{item_id}")
async def delete_line_item(
    item_id: int,
    user: dict = Depends(_manage_loads),
):
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    item = await tenant.get_load_line_item(account_id, item_id)
    if item is None:
        raise HTTPException(404, "item not found")
    manage_all = await _is_manage_all(user)
    if item.load_id is not None:
        l = await tenant.get_load(account_id, item.load_id)
        if l is not None:
            allowed = await get_user_company_codes(user)
            if allowed and l.company_code and l.company_code not in allowed:
                raise HTTPException(404, "item not found")   # company-scope
            if not manage_all and not await _can_write_load(user, l):
                raise HTTPException(404, "item not found")   # own-scope
    elif not manage_all and item.dispatcher_user_id != int(user["id"]):
        # Off-load layover owned by another dispatcher.
        raise HTTPException(404, "item not found")
    if not await tenant.delete_load_line_item(account_id, item_id):
        raise HTTPException(404, "item not found")
    return {"deleted": True, "id": item_id}
