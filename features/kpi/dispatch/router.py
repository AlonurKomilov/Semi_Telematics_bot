"""Dispatch section HTTP surface.

router.py and config.py are the interface-layer pair — those two may
import interfaces.api.deps; nothing else in the feature may.  The
family's config stays at ``/kpi/config`` in the root ``config.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

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


# ── Incentive runs (COMPENSATION — its own permission) ───────────────
#
# Deliberately NOT can_kpi: grades are shared analytics, payout amounts
# are money.  can_kpi_incentives is seeded owner/admin and delegatable
# via the matrix; the config that FEEDS these runs stays on
# can_manage_config_all at /kpi/config/incentives.

from pydantic import BaseModel, Field  # noqa: E402

from features.kpi.dispatch import runs as runs_service  # noqa: E402
from interfaces.api.deps import resolve_user_id  # noqa: E402

_incentives = require_permission("can_kpi_incentives")


class RunCreate(BaseModel):
    period_start: str = Field(..., min_length=10, max_length=10)
    period_end: str = Field(..., min_length=10, max_length=10)


class RowPatch(BaseModel):
    window_start: str | None = None
    window_end: str | None = None
    inactive_days: int | None = Field(None, ge=0)
    inactive_reason: str | None = None
    extras: float | None = None
    extras_note: str | None = None


class ExceptionSet(BaseModel):
    # null clears the override and returns the row to its computed value.
    override_pct: float | None = None
    reason: str = ""


def _run_error(e: runs_service.RunError):
    detail = str(e)
    status = 404 if "not found" in detail else \
        409 if "finalized" in detail else 422
    raise HTTPException(status, detail)


@router.post("/dispatch/runs", status_code=201)
async def create_run(body: RunCreate, user: dict = Depends(_incentives)):
    if body.period_end < body.period_start:
        raise HTTPException(422, "period_end is before period_start")
    try:
        run_id = await runs_service.create_run(
            int(user["account_id"]),
            period_start=body.period_start, period_end=body.period_end,
            created_by=await resolve_user_id(user),
        )
    except runs_service.RunError as e:
        _run_error(e)
    return await runs_service.get_run_detail(int(user["account_id"]), run_id)


@router.get("/dispatch/runs")
async def list_runs(user: dict = Depends(_incentives)):
    from infra.platform import get_tenant_db
    tenant = await get_tenant_db(int(user["account_id"]))
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    return {"runs": await tenant.list_kpi_runs(int(user["account_id"]))}


@router.get("/dispatch/runs/{run_id}")
async def run_detail(run_id: int, user: dict = Depends(_incentives)):
    try:
        return await runs_service.get_run_detail(
            int(user["account_id"]), run_id)
    except runs_service.RunError as e:
        _run_error(e)


@router.patch("/dispatch/runs/{run_id}/rows/{row_id}")
async def patch_row(
    run_id: int, row_id: int, body: RowPatch,
    user: dict = Depends(_incentives),
):
    try:
        return await runs_service.update_row(
            int(user["account_id"]), run_id, row_id,
            body.model_dump(exclude_none=True),
            updated_by=await resolve_user_id(user),
        )
    except runs_service.RunError as e:
        _run_error(e)


@router.post("/dispatch/runs/{run_id}/rows/{row_id}/exception")
async def set_row_exception(
    run_id: int, row_id: int, body: ExceptionSet,
    user: dict = Depends(_incentives),
):
    try:
        return await runs_service.set_exception(
            int(user["account_id"]), run_id, row_id,
            override_pct=body.override_pct, reason=body.reason,
            confirmed_by=await resolve_user_id(user),
        )
    except runs_service.RunError as e:
        _run_error(e)


@router.post("/dispatch/runs/{run_id}/finalize")
async def finalize(run_id: int, user: dict = Depends(_incentives)):
    try:
        return await runs_service.finalize_run(
            int(user["account_id"]), run_id,
            finalized_by=await resolve_user_id(user),
        )
    except runs_service.RunError as e:
        _run_error(e)
