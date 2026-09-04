"""Driver Pay config — the bonus rules the pay computation reads.

ONE config endpoint per feature at ``/<feature>/config``, in a file named
``config.py``.  Interface-layer, exactly as ``router.py`` is.

CONFIG, NOT MANAGE, and this one is a deliberate access change rather
than a refactor.  Bonus rules are account-wide ROWS that a computation
reads: they decide what every driver is paid on top of base.  That is the
blast-radius rule squarely — the same reasoning that put scorecard rules
and coaching rules behind ``can_manage_config_all``.

WHAT IT COSTS, stated plainly because it is a money feature: a holder of
``can_manage_driver_pay`` alone can no longer edit bonus rules.  They keep
everything else — creating and finalizing runs, recomputing, cancelling,
reading the roster, and setting each driver's own pay via
``PUT /driver-pay/settings/{driver_id}``.  Editing the rule set now needs
Config · account-wide as well.  Owner decision, taken knowingly.

WHAT DID **NOT** MOVE, and why.  ``/driver-pay/settings`` looks like
config and is not: ``list_driver_settings`` returns one row per driver —
base pay, pay model, rate — and ``PUT /settings/{driver_id}`` sets one
driver's. That is per-entity DATA, not an account-wide value, so it stays
on ``can_manage_driver_pay`` with the rest of the feature's operations.
The name is the only thing config-shaped about it.

LIKE SCORECARDS AND COACHING, the verbs stay per-row: rules are rows with
create / edit / delete, and one PUT over the whole set would let two
admins clobber each other.

⚠️  MOUNT THIS ROUTER BEFORE ``features.driver_pay.router``.
``/driver-pay/rules`` stays registered as a DEPRECATED ALIAS.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from features.driver_pay import service as svc
from features.driver_pay.service import DriverPayDisabledError
from features.driver_pay.router import disabled_to_403
from interfaces.api.deps import require_permission, resolve_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/driver-pay", tags=["driver-pay"])

_config = require_permission("can_manage_config_all")


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


@router.get("/config/rules")
@router.get("/rules", deprecated=True)
async def list_rules(
    active_only: bool = Query(False),
    user: dict = Depends(_config),
):
    try:
        return await svc.list_rules(user["account_id"], active_only=active_only)
    except DriverPayDisabledError as e:
        raise disabled_to_403(e)


@router.post("/config/rules", status_code=201)
@router.post("/rules", status_code=201, deprecated=True)
async def create_rule(
    body: BonusRuleIn,
    user: dict = Depends(_config),
):
    try:
        rule_id = await svc.create_rule(
            user["account_id"], user_id=await resolve_user_id(user),
            name=body.name, kind=body.kind,
            amount_cents=body.amount_cents,
            period_days=body.period_days,
            score_min=body.score_min,
            event_type=body.event_type,
            max_count=body.max_count,
            active=body.active,
        )
    except DriverPayDisabledError as e:
        raise disabled_to_403(e)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": rule_id}


@router.put("/config/rules/{rule_id}")
@router.put("/rules/{rule_id}", deprecated=True)
async def update_rule(
    rule_id: int,
    body: BonusRulePatch,
    user: dict = Depends(_config),
):
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if not fields:
        raise HTTPException(400, "no fields to update")
    try:
        ok = await svc.update_rule(
            user["account_id"], rule_id, user_id=await resolve_user_id(user), **fields,
        )
    except DriverPayDisabledError as e:
        raise disabled_to_403(e)
    if not ok:
        raise HTTPException(404, "rule not found")
    return {"ok": True}


@router.delete("/config/rules/{rule_id}")
@router.delete("/rules/{rule_id}", deprecated=True)
async def delete_rule(
    rule_id: int,
    user: dict = Depends(_config),
):
    try:
        await svc.delete_rule(
            user["account_id"], rule_id, user_id=await resolve_user_id(user),
        )
    except DriverPayDisabledError as e:
        raise disabled_to_403(e)
    return {"ok": True}
