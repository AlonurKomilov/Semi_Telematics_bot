"""Operator-curated assembly library (system.4truck.us).

Level 2 of System → Assembly → Part.  Same principle as the service-
task library (owner decision): shared-across-accounts vocabulary is
edited in the operator console, not shipped as code.

Two immutability rules (advisor, 2026-07-27):
  * ``key`` never changes — parts store it as a plain string.
  * ``system_key`` never changes — re-parenting an assembly would
    rewrite every historical rollup that used it.  A wrong parent is
    fixed by archive + recreate.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from interfaces.api.deps import get_tenant_db, require_system_owner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system/service-assemblies", tags=["system"])


class AssemblyCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    system_key: str = Field(..., min_length=1, max_length=40)


class AssemblyUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=120)
    status: str | None = Field(None, pattern="^(active|archived)$")


@router.get("")
async def list_assemblies(
    _user: dict = Depends(require_system_owner),
    db=Depends(get_tenant_db),
):
    """The library, with how many parts hold each key — the sanity
    check before archiving one."""
    return {"assemblies": await db.list_service_assemblies()}


@router.post("")
async def create_assembly(
    body: AssemblyCreate,
    _user: dict = Depends(require_system_owner),
    db=Depends(get_tenant_db),
):
    """Add an assembly under a system.  The system parent is chosen
    HERE and never again — re-parenting rewrites history."""
    from adapters.storage.service_tasks import SYSTEM_KEYS
    if body.system_key not in SYSTEM_KEYS:
        raise HTTPException(status_code=422, detail="Unknown system.")
    entry = await db.create_service_assembly(body.label, body.system_key)
    if not entry:
        raise HTTPException(
            status_code=409,
            detail="An assembly with that name (or its key) already exists.",
        )
    return entry


@router.put("/{assembly_id}")
async def update_assembly(
    assembly_id: int,
    body: AssemblyUpdate,
    _user: dict = Depends(require_system_owner),
    db=Depends(get_tenant_db),
):
    """Label / archive only.  Archiving stops NEW assignments; parts
    already holding the key keep it and its label keeps resolving."""
    ok = await db.update_service_assembly(
        assembly_id, **body.model_dump(exclude_none=True),
    )
    if not ok:
        raise HTTPException(
            status_code=404, detail="Assembly not found, or nothing to update.",
        )
    return {"updated": True}
