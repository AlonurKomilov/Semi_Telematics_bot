"""KPI HTTP surface.

router.py is interface-layer code co-located with its domain
(docs/FEATURES.md), as is config.py beside it — those two may import
interfaces.api.deps; nothing else in the feature may.
Everything is gated by the one delegatable ``can_kpi`` flag — the matrix
decides which roles get the page.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from features.kpi import service
from interfaces.api.deps import (
    get_user_company_codes, require_permission,
)

router = APIRouter(prefix="/kpi", tags=["kpi"])

_view_kpi = require_permission("can_kpi")

# The grading THRESHOLDS are not here: config is a separate action
# from view, so it is a separate file — features/kpi/config.py,
# serving /kpi/config. This module is the feature's data.



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
