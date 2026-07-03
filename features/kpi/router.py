"""KPI HTTP surface.

router.py is interface-layer code co-located with its domain
(docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.
Everything is gated by the one delegatable ``can_kpi`` flag — the matrix
decides which roles get the page.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from features.kpi import service
from infra.platform import get_tenant_db as _get_tenant_db
from interfaces.api.deps import require_permission

router = APIRouter(prefix="/kpi", tags=["kpi"])

_view_kpi = require_permission("can_kpi")


@router.get("/dispatchers")
async def dispatcher_kpis(
    days: int = Query(30, ge=1, le=365),
    user: dict = Depends(_view_kpi),
):
    """Per-dispatcher metrics + A–D grades over the window."""
    return await service.get_dispatcher_kpis(
        int(user["account_id"]), days=days,
    )


class ThresholdsUpdate(BaseModel):
    thresholds: dict[str, float] = Field(default_factory=dict)


@router.get("/thresholds")
async def get_thresholds(user: dict = Depends(_view_kpi)):
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    return {
        "thresholds": await service.get_kpi_thresholds(tenant, account_id),
        "defaults": service.DEFAULT_KPI_THRESHOLDS,
    }


@router.put("/thresholds")
async def put_thresholds(
    body: ThresholdsUpdate,
    user: dict = Depends(_view_kpi),
):
    """Set what counts as good/bad.  Grades everywhere recompute from the
    new values on the next read (metrics are computed live)."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    merged = await service.set_kpi_thresholds(
        tenant, account_id, body.thresholds,
    )
    return {"thresholds": merged}
