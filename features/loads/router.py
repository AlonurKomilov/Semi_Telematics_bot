"""Loads HTTP surface — the "All Loads" screen + manual entry.

router.py is interface-layer code co-located with its domain
(docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may.

Visibility: ``can_view_loads`` opens the page; WIDTH is the role's —
``person_width``: a driver sees the loads they drive, everyone else
(drivers) sees only loads linked to the caller's user id.  Writes need the
delegatable ``can_manage_loads``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


from features.loads import service
from features.loads.service import load_to_dict
from infra.platform import get_tenant_db as _get_tenant_db
from interfaces.api.deps import (
    get_user_company_codes, require_permission, require_permission_any,
    resolve_user_id,
)

router = APIRouter(prefix="/loads", tags=["loads"])

_view_loads = require_permission("can_view_loads")
_manage_loads = require_permission("can_manage_loads")

# Rows returned to the list screen per request.  The response carries a
# ``truncated`` flag when the account has more — never a silent cap.
#
# 500 was low enough that a 1,575-load account could not reach its own
# history: the list screen's only narrowing control was the status tab,
# so Delivered (1,380) sat permanently over the cap and could never be
# totalled or pivoted.  The screen now sends a pickup-date window, and
# rendering is bounded by pagination (250 rows on screen) rather than by
# the fetch — so the cap's real cost is payload, and this is the size at
# which payload starts to matter rather than an arbitrary floor.
LIST_CAP = 5000


async def _scope_driver_id(user: dict) -> int | None:
    """None = account-wide; a user id = a self-width caller (a driver).

    Width is the ROLE's (``person_width``), not a grant's: the old
    "holds the view verb = account-wide" proxy would have opened every
    load to a driver the day the own flag folded into that verb.
    """
    from capabilities.permissions.scope import person_width
    if person_width(user["role"], "loads") == "all":
        return None
    return await resolve_user_id(user)


# Any ``can_manage_loads`` holder manages ANY load (owner decision
# 2026-07-29): the dispatcher own-scope write wall came down in the same
# release the per-load accountability trail went up.  Colleagues cover
# each other's loads freely; the trail — actor + field-level old→new on
# every human write (load_events) — is what answers "who changed what".
# The retired can_loads_manage_all flag was removed with the wall.


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
        # The list screen narrows HISTORY, never live work — a load still
        # in transit has no end date, so a pickup window would drop it and
        # read as a deletion.  Aggregation callers pass no window at all.
        keep_open=True,
        limit=LIST_CAP + 1,
    )
    truncated = len(loads) > LIST_CAP
    if truncated:
        loads = loads[:LIST_CAP]
    counts = await service.get_load_counts(
        account_id, scope_driver_user_id=scope,
        company_codes=allowed or None,
        # Same window as the rows above: a badge that counts a different
        # set than the grid holds is the bug this pairing prevents.
        since=since, until=until, keep_open=True,
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
    if not fields.get("dispatcher_user_id"):
        # Default attribution, not a wall: an unassigned new load lands on
        # its creator (KPI + the trail's dispatcher snapshot stay honest),
        # but any explicit assignment in the body is honored as-is.
        uid = await resolve_user_id(user)
        fields["dispatcher_user_id"] = uid
        me = await tenant.get_user(uid)
        fields["dispatcher_name"] = (me.display_name if me else "") or ""
    try:
        load_id = await tenant.add_load(
            account_id, actor_user_id=await resolve_user_id(user), **fields)
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
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        changed = await tenant.update_load(
            account_id, load_id, actor_user_id=await resolve_user_id(user), **fields)
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
    if not await tenant.deactivate_load(
            account_id, load_id, actor_user_id=await resolve_user_id(user)):
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


@router.get("/{load_id}/history")
async def get_load_history(
    load_id: int,
    user: dict = Depends(_view_loads),
):
    """The load's accountability trail — same visibility rules as the
    load itself.  Display names are resolved server-side (the Inventory
    pattern); ids stay in the payload for exactness."""
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
    events = await tenant.list_load_events(account_id, load_id)
    names: dict[int, str] = {}
    ids = {e["actor_user_id"] for e in events} | {e["dispatcher_user_id"] for e in events}
    for uid in ids:
        if uid is None:
            continue
        u = await tenant.get_user(int(uid))
        names[int(uid)] = ((u.display_name if u else "") or "").strip() or f"#{uid}"
    for e in events:
        e["actor_name"] = names.get(e["actor_user_id"], "") if e["actor_user_id"] else ""
        e["dispatcher_name"] = names.get(e["dispatcher_user_id"], "") if e["dispatcher_user_id"] else ""
    return {"events": events}


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
    if body.load_id is not None:
        l = await tenant.get_load(account_id, body.load_id)
        if l is None:
            raise HTTPException(404, "load not found")
        allowed = await get_user_company_codes(user)
        if allowed and l.company_code and l.company_code not in allowed:
            raise HTTPException(404, "load not found")   # company-scope
    elif dispatcher_user_id is None:
        # Off-load layover with nobody named: default the responsible
        # dispatcher to the creator (attribution, not a wall).
        dispatcher_user_id = await resolve_user_id(user)
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
            actor_user_id=await resolve_user_id(user),
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
    if item.load_id is not None:
        l = await tenant.get_load(account_id, item.load_id)
        if l is not None:
            allowed = await get_user_company_codes(user)
            if allowed and l.company_code and l.company_code not in allowed:
                raise HTTPException(404, "item not found")   # company-scope
    if not await tenant.delete_load_line_item(
            account_id, item_id, actor_user_id=await resolve_user_id(user)):
        raise HTTPException(404, "item not found")
    return {"deleted": True, "id": item_id}
