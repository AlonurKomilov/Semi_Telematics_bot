"""Map config — which basemap this account is drawn on.

ONE config endpoint per feature at ``/<feature>/config``, in a file
named ``config.py`` (the convention features/vehicles/config.py sets
out).  The READ sits on the config flag with the write: its only
consumer is the control that edits it, and a read on the weaker
permission would make the write gate decorative.

The map itself asks ``GET /map/engine`` on ``can_view_location`` — that
answer carries the billable key and is what DRAWS; this one carries
none and is what DECIDES.  Billing will write the same setting through
``map_engine.set_engine`` when there is a plan to write it from; this
endpoint is how the owner does it until then.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from features.location import map_engine
from infra.platform import get_tenant_db as _get_tenant_db
from interfaces.api.deps import require_permission

router = APIRouter(prefix="/map", tags=["map"])
_config = require_permission("can_manage_config_all")


class EngineUpdate(BaseModel):
    engine: str


def _without_key(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k != "key"}


@router.get("/config")
async def get_map_config(user: dict = Depends(_config)):
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    return _without_key(await map_engine.for_account(account_id, tenant))


@router.put("/config")
async def put_map_config(body: EngineUpdate, user: dict = Depends(_config)):
    """Set the engine.  An unknown name is refused rather than stored:
    the resolver would read it back as the free engine, and the control
    would then show a choice that silently meant something else."""
    if map_engine.normalise(body.engine) != body.engine.strip().lower():
        raise HTTPException(
            422, f"unknown map engine {body.engine!r}; one of {list(map_engine.ENGINES)}")
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    return _without_key(await map_engine.set_engine(account_id, tenant, body.engine))
