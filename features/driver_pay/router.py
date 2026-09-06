"""Driver Pay API endpoints — bonus rules, runs, driver settings, paystubs."""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may;
# service/alert/ai_tool/signal modules never do.


from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from features.driver_pay import service as svc
from features.driver_pay.service import DriverPayDisabledError
from interfaces.api.deps import (
    resolve_user_id,
    require_permission,
    require_permission_any,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/driver-pay", tags=["driver-pay"])


# ── Pydantic schemas ──────────────────────────────────────────────



class DriverPaySettingsIn(BaseModel):
    # Per-load earnings model for loads with no stored driver pay:
    # 'percentage' (of load rate) | 'per_mile' | '' (no per-load math).
    pay_model: str = Field(default="", max_length=16)
    pay_rate: Optional[float] = Field(default=None, ge=0)
    base_pay_cents: int = Field(default=0, ge=0, le=100_000_000)
    opt_in: bool = True


class CreateRunIn(BaseModel):
    period_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


def _parse_iso_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).date()
    except ValueError as e:
        raise HTTPException(400, f"invalid date format: {e}")


def disabled_to_403(exc: DriverPayDisabledError) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail="Driver Pay feature is not enabled for this account.",
    )


# ── Bonus Rules ────────────────────────────────────────────────────



# ── Driver pay settings ────────────────────────────────────────────

@router.get("/settings")
async def list_settings(
    user: dict = Depends(require_permission("can_manage_driver_pay")),
):
    try:
        return await svc.list_driver_settings(user["account_id"])
    except DriverPayDisabledError as e:
        raise disabled_to_403(e)


@router.put("/settings/{driver_id}")
async def upsert_settings(
    driver_id: str,
    body: DriverPaySettingsIn,
    user: dict = Depends(require_permission("can_manage_driver_pay")),
):
    if not driver_id.strip():
        raise HTTPException(400, "driver_id required")
    try:
        await svc.upsert_driver_settings(
            user["account_id"], driver_id, user_id=await resolve_user_id(user),
            base_pay_cents=body.base_pay_cents, opt_in=body.opt_in,
            pay_model=body.pay_model, pay_rate=body.pay_rate,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except DriverPayDisabledError as e:
        raise disabled_to_403(e)
    return {"ok": True}


# ── Runs ───────────────────────────────────────────────────────────

def _require_wide(user: dict) -> None:
    """The account-wide reads: a self-width caller (a driver holding
    the view verb) has ``/me``; here they would read every paystub."""
    from capabilities.permissions.scope import person_width
    if person_width(user["role"], "driver_pay") != "all":
        raise HTTPException(403, "account-wide driver pay is outside your width; use /me")


@router.get("/runs")
async def list_runs(
    limit: int = Query(50, ge=1, le=500),
    user: dict = Depends(require_permission_any(
        "can_manage_driver_pay", "can_view_driver_pay",
    )),
):
    _require_wide(user)
    try:
        return await svc.list_runs(user["account_id"], limit=limit)
    except DriverPayDisabledError as e:
        raise disabled_to_403(e)


@router.post("/runs", status_code=201)
async def create_run(
    body: CreateRunIn,
    user: dict = Depends(require_permission("can_manage_driver_pay")),
):
    ps = _parse_iso_date(body.period_start)
    pe = _parse_iso_date(body.period_end)
    try:
        run_id = await svc.create_run(
            user["account_id"], user_id=await resolve_user_id(user),
            period_start=ps, period_end=pe,
        )
    except DriverPayDisabledError as e:
        raise disabled_to_403(e)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": run_id}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: int,
    user: dict = Depends(require_permission_any(
        "can_manage_driver_pay", "can_view_driver_pay",
    )),
):
    _require_wide(user)
    detail = await svc.get_run_detail(user["account_id"], run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    return detail


@router.post("/runs/{run_id}/finalize")
async def finalize_run(
    run_id: int,
    user: dict = Depends(require_permission("can_manage_driver_pay")),
):
    try:
        ok = await svc.finalize_run(
            user["account_id"], run_id, user_id=await resolve_user_id(user),
        )
    except DriverPayDisabledError as e:
        raise disabled_to_403(e)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "run not found")
    return {"ok": True}


@router.post("/runs/{run_id}/recompute")
async def recompute_run(
    run_id: int,
    user: dict = Depends(require_permission("can_manage_driver_pay")),
):
    """Refresh a DRAFT run's statements from the current loads — picks up
    additions/deductions added since it was created.  Finalized runs 400."""
    try:
        ok = await svc.recompute_run(
            user["account_id"], run_id, user_id=await resolve_user_id(user),
        )
    except DriverPayDisabledError as e:
        raise disabled_to_403(e)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "run not found")
    return {"ok": True}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: int,
    user: dict = Depends(require_permission("can_manage_driver_pay")),
):
    try:
        ok = await svc.cancel_run(
            user["account_id"], run_id, user_id=await resolve_user_id(user),
        )
    except DriverPayDisabledError as e:
        raise disabled_to_403(e)
    if not ok:
        raise HTTPException(404, "run not found or not cancellable")
    return {"ok": True}


# ── Driver self-service ────────────────────────────────────────────

async def _resolve_caller_driver_id(user: dict) -> Optional[str]:
    """Return the Samsara driver_id explicitly bound to the calling user
    (users.samsara_driver_id). Returns None if no admin has set the binding.

    Earlier versions inferred this from the most-recent safety event on
    the user's assigned truck, which leaked another driver's paystubs
    whenever a vehicle had been driven by anyone else in the prior 90
    days. The mapping must now be set explicitly by an admin via
    PUT /admin/users/{id}/samsara-driver-id.
    """
    from infra.platform import get_platform_db
    pdb = get_platform_db()
    if pdb is None:
        return None
    try:
        tid = int(user["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    me = await pdb.get_user_by_telegram_id(tid)
    if me is None or me.account_id != user["account_id"]:
        return None
    did = (me.samsara_driver_id or "").strip()
    return did or None


@router.get("/me")
async def my_paystubs(
    limit: int = Query(12, ge=1, le=60),
    user: dict = Depends(require_permission_any(
        "can_manage_driver_pay", "can_view_driver_pay",
    )),
):
    """Return paystub history for the calling driver."""
    driver_id = await _resolve_caller_driver_id(user)
    if not driver_id:
        return {"driver_id": None, "items": []}
    items = await svc.get_paystub_history(
        user["account_id"], driver_id, limit=limit,
    )
    return {"driver_id": driver_id, "items": items}
