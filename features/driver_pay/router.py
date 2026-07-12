"""Payroll API endpoints — bonus rules, runs, driver settings, paystubs."""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): ONLY router.py may import interfaces.api.deps;
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
    require_permission,
    require_permission_any,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/driver-pay", tags=["driver-pay"])


# ── Pydantic schemas ──────────────────────────────────────────────

class BonusRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern="^(score_threshold|incident_count)$")
    amount_cents: int = Field(gt=0, le=10_000_000)
    period_days: int = Field(default=30, ge=1, le=365)
    score_min: Optional[float] = Field(default=None, ge=0, le=100)
    event_type: Optional[str] = Field(default=None, max_length=64)
    max_count: Optional[int] = Field(default=None, ge=0, le=10_000)
    active: bool = True


class BonusRulePatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    amount_cents: Optional[int] = Field(default=None, gt=0, le=10_000_000)
    period_days: Optional[int] = Field(default=None, ge=1, le=365)
    score_min: Optional[float] = Field(default=None, ge=0, le=100)
    event_type: Optional[str] = Field(default=None, max_length=64)
    max_count: Optional[int] = Field(default=None, ge=0, le=10_000)
    active: Optional[bool] = None


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


def _disabled_to_403(exc: DriverPayDisabledError) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail="Payroll feature is not enabled for this account.",
    )


# ── Bonus Rules ────────────────────────────────────────────────────

@router.get("/rules")
async def list_rules(
    active_only: bool = Query(False),
    user: dict = Depends(require_permission("can_driver_pay_admin")),
):
    try:
        return await svc.list_rules(user["account_id"], active_only=active_only)
    except DriverPayDisabledError as e:
        raise _disabled_to_403(e)


@router.post("/rules", status_code=201)
async def create_rule(
    body: BonusRuleIn,
    user: dict = Depends(require_permission("can_driver_pay_admin")),
):
    try:
        rule_id = await svc.create_rule(
            user["account_id"], user_id=int(user["sub"]),
            name=body.name, kind=body.kind,
            amount_cents=body.amount_cents,
            period_days=body.period_days,
            score_min=body.score_min,
            event_type=body.event_type,
            max_count=body.max_count,
            active=body.active,
        )
    except DriverPayDisabledError as e:
        raise _disabled_to_403(e)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": rule_id}


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: int,
    body: BonusRulePatch,
    user: dict = Depends(require_permission("can_driver_pay_admin")),
):
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if not fields:
        raise HTTPException(400, "no fields to update")
    try:
        ok = await svc.update_rule(
            user["account_id"], rule_id, user_id=int(user["sub"]), **fields,
        )
    except DriverPayDisabledError as e:
        raise _disabled_to_403(e)
    if not ok:
        raise HTTPException(404, "rule not found")
    return {"ok": True}


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: int,
    user: dict = Depends(require_permission("can_driver_pay_admin")),
):
    try:
        await svc.delete_rule(
            user["account_id"], rule_id, user_id=int(user["sub"]),
        )
    except DriverPayDisabledError as e:
        raise _disabled_to_403(e)
    return {"ok": True}


# ── Driver pay settings ────────────────────────────────────────────

@router.get("/settings")
async def list_settings(
    user: dict = Depends(require_permission("can_driver_pay_admin")),
):
    try:
        return await svc.list_driver_settings(user["account_id"])
    except DriverPayDisabledError as e:
        raise _disabled_to_403(e)


@router.put("/settings/{driver_id}")
async def upsert_settings(
    driver_id: str,
    body: DriverPaySettingsIn,
    user: dict = Depends(require_permission("can_driver_pay_admin")),
):
    if not driver_id.strip():
        raise HTTPException(400, "driver_id required")
    try:
        await svc.upsert_driver_settings(
            user["account_id"], driver_id, user_id=int(user["sub"]),
            base_pay_cents=body.base_pay_cents, opt_in=body.opt_in,
            pay_model=body.pay_model, pay_rate=body.pay_rate,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except DriverPayDisabledError as e:
        raise _disabled_to_403(e)
    return {"ok": True}


# ── Runs ───────────────────────────────────────────────────────────

@router.get("/runs")
async def list_runs(
    limit: int = Query(50, ge=1, le=500),
    user: dict = Depends(require_permission("can_driver_pay_admin")),
):
    try:
        return await svc.list_runs(user["account_id"], limit=limit)
    except DriverPayDisabledError as e:
        raise _disabled_to_403(e)


@router.post("/runs", status_code=201)
async def create_run(
    body: CreateRunIn,
    user: dict = Depends(require_permission("can_driver_pay_admin")),
):
    ps = _parse_iso_date(body.period_start)
    pe = _parse_iso_date(body.period_end)
    try:
        run_id = await svc.create_run(
            user["account_id"], user_id=int(user["sub"]),
            period_start=ps, period_end=pe,
        )
    except DriverPayDisabledError as e:
        raise _disabled_to_403(e)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": run_id}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: int,
    user: dict = Depends(require_permission("can_driver_pay_admin")),
):
    detail = await svc.get_run_detail(user["account_id"], run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    return detail


@router.post("/runs/{run_id}/finalize")
async def finalize_run(
    run_id: int,
    user: dict = Depends(require_permission("can_driver_pay_admin")),
):
    try:
        ok = await svc.finalize_run(
            user["account_id"], run_id, user_id=int(user["sub"]),
        )
    except DriverPayDisabledError as e:
        raise _disabled_to_403(e)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "run not found")
    return {"ok": True}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: int,
    user: dict = Depends(require_permission("can_driver_pay_admin")),
):
    try:
        ok = await svc.cancel_run(
            user["account_id"], run_id, user_id=int(user["sub"]),
        )
    except DriverPayDisabledError as e:
        raise _disabled_to_403(e)
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
        "can_driver_pay_admin", "can_driver_pay_view_own",
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

