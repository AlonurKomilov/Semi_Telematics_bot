"""Dispatch section HTTP surface.

router.py and config.py are the interface-layer pair — those two may
import interfaces.api.deps; nothing else in the feature may.  The
family's config stays at ``/kpi/config`` in the root ``config.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from features.kpi.dispatch import service
from interfaces.api.deps import (
    get_user_company_codes, require_permission,
)

router = APIRouter(prefix="/kpi", tags=["kpi"])

_view_kpi = require_permission("can_kpi")


@router.get("/dispatchers")
async def dispatcher_kpis(
    days: int = Query(30, ge=1, le=365),
    user: dict = Depends(_view_kpi),
):
    """Per-dispatcher metrics + A–D grades over the window, respecting
    the caller's company restriction."""
    allowed = await get_user_company_codes(user)
    return await service.get_dispatcher_kpis(
        int(user["account_id"]), days=days,
        company_codes=allowed or None,
    )
