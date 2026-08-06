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
from interfaces.api.deps import (
    get_user_company_codes, require_permission,
)

router = APIRouter(prefix="/kpi", tags=["kpi"])

_view_kpi = require_permission("can_kpi")
# VIEW and CONFIG are two actions, not two strengths of one.
#
# can_kpi is the VIEW action: the grades themselves, already computed.
# The thresholds are the CONFIG action — the account_settings row
# ``kpi_thresholds``, which config.md's table puts squarely in the Config
# column's account scope, and which the blast-radius rule pins there
# ("anything a computation reads is account-wide, always").
#
# So BOTH verbs sit here, read included.  Gating the GET on can_kpi was
# the mixing in miniature: it looked like a harmless read, but the only
# caller is ThresholdsDialog — the editor.  The KPI page never asks for
# thresholds, because grades arrive already graded.  A read that exists
# solely to populate an editor is part of Config, not part of View.
#
# can_manage_account was here too and is gone: that is General settings'
# MANAGE action, a peer feature's operating permission.  It reached the
# same roles by seed (owner only), so this narrows nothing by default —
# it only stops an account that delegated General settings from picking
# up KPI grading rules it was never granted.
_config_thresholds = require_permission("can_manage_config_all")


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


class ThresholdsUpdate(BaseModel):
    thresholds: dict[str, float] = Field(default_factory=dict)


# ── Feature config ──────────────────────────────────────────────────
#
# ONE config endpoint per feature, at ``/<feature>/config`` — the same
# shape every feature uses, so "where do I change this?" has one answer
# instead of six.  KPI's whole config is its grading thresholds today;
# a second setting becomes another key in the same payload, not another
# endpoint and not another permission.
#
# ``/kpi/thresholds`` stays registered as a DEPRECATED ALIAS rather than
# being deleted.  The API and the dashboard deploy from one repo but not
# in one instant: a browser holding yesterday's JS bundle would 404 on
# every threshold read during the window.  Same recipe the wire-key
# renames use — alias the same object, and test that the alias and the
# primary resolve identically (tests/test_config_endpoint_convention.py).
@router.get("/config")
@router.get("/thresholds", deprecated=True)
async def get_config(user: dict = Depends(_config_thresholds)):
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    return {
        "thresholds": await service.get_kpi_thresholds(tenant, account_id),
        "defaults": service.DEFAULT_KPI_THRESHOLDS,
    }


@router.put("/config")
@router.put("/thresholds", deprecated=True)
async def put_config(
    body: ThresholdsUpdate,
    user: dict = Depends(_config_thresholds),
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
